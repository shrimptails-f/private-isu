#!/usr/bin/env python3
"""Summarize the repository's nginx and Go trace logs as JSON."""

import argparse
import collections
import json
import math
import re
import sys
from urllib.parse import urlsplit

ACCESS = re.compile(r'"(?P<method>\S+) (?P<url>\S+) HTTP/[^\"]+" (?P<status>\d{3}) request_time=(?P<duration>\d+(?:\.\d+)?) upstream_time=(?P<upstream>\S+) request_id=(?P<id>\S+)')
TRACE = re.compile(r'trace request_id=(?P<id>"(?:\\.|[^"\\])*") kind=(?P<kind>\S+) duration_ms=(?P<duration>\d+(?:\.\d+)?) error=(?P<error>true|false) detail=(?P<detail>"(?:\\.|[^"\\])*")')


def route(method, url):
    path = urlsplit(url).path
    path = re.sub(r'^/image/\d+\.(jpg|png|gif)$', '/image/:id.:ext', path)
    path = re.sub(r'^/posts/\d+$', '/posts/:id', path)
    path = re.sub(r'^/@[A-Za-z0-9_]+$', '/@:account_name', path)
    return method + ' ' + path


def detail_key(kind, detail):
    # Group SQL IN lists regardless of the number of parameters.
    if kind.startswith('sql_'):
        detail = re.sub(r'\?(?:\s*,\s*\?)+', '?, ...', detail)
    return kind, detail


def summary(values):
    values = sorted(values)
    return {'count': len(values), 'total_ms': round(sum(values), 3),
            'mean_ms': round(sum(values) / len(values), 3),
            'p50_ms': round(values[math.ceil(len(values) * .50) - 1], 3),
            'p95_ms': round(values[math.ceil(len(values) * .95) - 1], 3)}


def analyze(access_path, trace_path):
    requests = {}
    skipped_access = duplicate_ids = 0
    with open(access_path, encoding='utf-8', errors='replace') as f:
        for line in f:
            m = ACCESS.search(line)
            if not m:
                skipped_access += 1
                continue
            d = m.groupdict()
            if d['id'] in requests:
                duplicate_ids += 1
                continue
            requests[d['id']] = {'route': route(d['method'], d['url']), 'status': d['status'],
                                 'duration_ms': float(d['duration']) * 1000,
                                 'upstream_ms': None if d['upstream'] == '-' else d['upstream'],
                                 'operations': {}}
    if not requests:
        raise ValueError('No matching nginx access records found; check the log format and input file.')

    unmatched = malformed_trace = trace_count = 0
    with open(trace_path, encoding='utf-8', errors='replace') as f:
        for line in f:
            m = TRACE.search(line)
            if not m:
                if 'trace request_id=' in line:
                    malformed_trace += 1
                continue
            try:
                request_id = json.loads(m['id'])
                detail = json.loads(m['detail'])
            except json.JSONDecodeError:
                malformed_trace += 1
                continue
            if request_id not in requests:
                unmatched += 1
                continue
            trace_count += 1
            op = requests[request_id]['operations'].setdefault(detail_key(m['kind'], detail), [0, 0.0, 0])
            op[0] += 1
            op[1] += float(m['duration'])
            op[2] += m['error'] == 'true'

    groups = collections.defaultdict(list)
    for request_id, request in requests.items():
        groups[request['route']].append((request_id, request))
    routes = []
    for name, entries in groups.items():
        entries.sort(key=lambda item: item[1]['duration_ms'])
        durations = [r['duration_ms'] for _, r in entries]
        counts = collections.Counter(r['status'] for _, r in entries)
        operations = collections.defaultdict(lambda: [0, 0.0, 0, 0])
        for _, request in entries:
            for key, (count, total, errors) in request['operations'].items():
                aggregate = operations[key]
                aggregate[0] += count
                aggregate[1] += total
                aggregate[2] += errors
                aggregate[3] += 1
        def operation(key, val, denominator):
            count, total, errors, request_count = val
            return {'kind': key[0], 'detail': key[1], 'count': count,
                    'total_ms': round(total, 3), 'mean_ms': round(total / count, 3),
                    'errors': errors, 'requests_with_operation': request_count,
                    'per_request': round(count / denominator, 3)}
        median_id, median = entries[math.ceil(len(entries) * .5) - 1]
        routes.append({'route': name, **summary(durations), 'statuses': dict(sorted(counts.items())),
                       'requests_with_trace': sum(bool(r['operations']) for _, r in entries),
                       'operations': [operation(k, v, len(entries)) for k, v in sorted(operations.items(), key=lambda x: -x[1][1])],
                       'median_request': {'request_id': median_id, 'duration_ms': median['duration_ms'],
                                          'operations': [operation(k, [v[0], v[1], v[2], 1], 1) for k, v in sorted(median['operations'].items(), key=lambda x: -x[1][1])]}})
    routes.sort(key=lambda r: -r['total_ms'])
    return {'format_version': 1, 'inputs': {'access': access_path, 'trace': trace_path},
            'coverage': {'access_records': len(requests), 'ignored_access_lines': skipped_access,
                         'duplicate_request_ids': duplicate_ids, 'matched_trace_events': trace_count,
                         'unmatched_trace_events': unmatched, 'malformed_trace_lines': malformed_trace},
            'notes': ['Times are elapsed, may overlap, and include waiting; totals are not CPU time.',
                      'Inputs can include initialization and correctness checks; restrict input files to compare phases.',
                      'Detailed tracing adds overhead; missing trace coverage does not mean no work occurred.',
                      'Operations are grouped by route and normalized detail; argument values are not logged.'],
            'routes': routes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--access', required=True, help='nginx access log')
    parser.add_argument('--trace', required=True, help='Go application trace log')
    parser.add_argument('--output', default='-', help='JSON output path (default: stdout)')
    args = parser.parse_args()
    try:
        report = analyze(args.access, args.trace)
        if args.output == '-':
            json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
            print()
        else:
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
                f.write('\n')
    except (OSError, ValueError) as exc:
        parser.exit(1, f'error: {exc}\n')


if __name__ == '__main__':
    main()
