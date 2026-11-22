#!/usr/bin/env python3
"""Summarize nginx combined logs and MySQL digest snapshots without modifying inputs."""
import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal

ACCESS = re.compile(r'^\S+ \S+ \S+ \[([^]]+)\] "(\S+) (\S+) HTTP/[^"]+" (\d{3}) (\d+|-) ')
WARNING = re.compile(r'\[warn\].*a client request body is buffered to a temporary file')
FIELDS = ('COUNT_STAR', 'SUM_TIMER_WAIT', 'SUM_ROWS_EXAMINED', 'SUM_ROWS_SENT')


def nginx(path):
    groups, seconds, statuses = Counter(), Counter(), Counter()
    examples = {}
    counts = Counter()
    total_bytes = 0
    dates = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            counts['lines'] += 1
            m = ACCESS.match(line)
            if not m:
                counts['warning' if WARNING.search(line) else 'unclassified'] += 1
                continue
            stamp, method, target, status, size = m.groups()
            try:
                date = datetime.strptime(stamp, '%d/%b/%Y:%H:%M:%S %z')
            except ValueError:
                counts['unclassified'] += 1
                continue
            counts['access'] += 1
            dates.append(date)
            seconds[date.isoformat()] += 1
            statuses[status] += 1
            path_only = target.split('?', 1)[0]
            normalized = re.sub(r'(?<=/)\d+(?=/|$|\.)', ':id', path_only)
            key = (method, status, normalized)
            groups[key] += 1
            examples.setdefault(key, path_only)
            if size == '-':
                counts['missing_bytes'] += 1
            else:
                total_bytes += int(size)
    return {'counts': dict(counts), 'time_min': min(dates).isoformat() if dates else None,
            'time_max': max(dates).isoformat() if dates else None, 'bytes_sum': total_bytes,
            'status_counts': dict(sorted(statuses.items())), 'per_second': dict(sorted(seconds.items())),
            'groups': [{'method': k[0], 'status': k[1], 'path': k[2], 'example': examples[k], 'count': v}
                       for k, v in sorted(groups.items(), key=lambda x: (-x[1], x[0]))]}


def read_digest(path):
    rows, problems = {}, []
    with open(path, encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        required = {'SCHEMA_NAME', 'DIGEST', 'DIGEST_TEXT', *FIELDS}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f'{path}: missing required columns')
        for number, row in enumerate(reader, 2):
            key = (row['SCHEMA_NAME'], row['DIGEST'])
            if key[1] in (None, '', 'NULL', r'\N') or key[0] in (None, ''):
                problems.append({'file': path, 'line': number, 'error': 'missing key', 'key': key})
                continue
            if key[0] in ('NULL', r'\N'):
                problems.append({'file': path, 'line': number, 'key': key, 'notice': 'NULL schema; retained as a key'})
            if key in rows:
                raise ValueError(f'{path}:{number}: duplicate key {key}')
            values = {}
            for field in FIELDS:
                value = row.get(field)
                try:
                    if value is None or not re.fullmatch(r'\d+', value):
                        raise ValueError()
                    values[field] = int(value)
                except ValueError:
                    values[field] = None
                    problems.append({'file': path, 'line': number, 'key': key, 'field': field, 'invalid': value})
            rows[key] = (row.get('DIGEST_TEXT'), values)
    return rows, problems


def digest(before_path, after_path):
    before, issues = read_digest(before_path)
    after, other = read_digest(after_path)
    issues.extend(other)
    entries = []
    for key in sorted(before.keys() | after.keys()):
        if key not in before or key not in after:
            issues.append({'key': key, 'error': 'missing before' if key not in before else 'missing after'})
            continue
        old_text, old = before[key]
        sql, new = after[key]
        if old_text != sql or sql in (None, '', 'NULL', r'\N'):
            issues.append({'key': key, 'error': 'missing or changed digest text', 'before': old_text, 'after': sql})
        delta = {f: None if old[f] is None or new[f] is None else new[f] - old[f] for f in FIELDS}
        for field, value in delta.items():
            if value is not None and value < 0:
                issues.append({'key': key, 'field': field, 'error': 'negative delta', 'delta': value})
        entries.append({'schema': key[0], 'digest': key[1], 'sql': sql, 'delta': delta,
                        'seconds': str(Decimal(delta['SUM_TIMER_WAIT']) / Decimal(10**12)) if delta['SUM_TIMER_WAIT'] is not None else None})
    rankings = {}
    for field in ('COUNT_STAR', 'SUM_TIMER_WAIT', 'SUM_ROWS_EXAMINED'):
        rankings[field] = sorted(entries, key=lambda x: (x['delta'][field] is None, -(x['delta'][field] or 0), x['schema'], x['digest']))
    return {'before_keys': len(before), 'after_keys': len(after), 'matched_keys': len(entries),
            'issues': issues, 'rankings': rankings,
            'matched_delta_totals': {f: sum(e['delta'][f] for e in entries if e['delta'][f] is not None) for f in FIELDS},
            'total_missing_values': {f: sum(e['delta'][f] is None for e in entries) for f in FIELDS}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nginx', required=True)
    parser.add_argument('--before', required=True)
    parser.add_argument('--after', required=True)
    args = parser.parse_args()
    print(json.dumps({'nginx': nginx(args.nginx), 'digest': digest(args.before, args.after)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
