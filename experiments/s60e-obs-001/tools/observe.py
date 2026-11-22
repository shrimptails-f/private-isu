#!/usr/bin/env python3
"""Summarize nginx combined logs and MySQL digest snapshots as JSON."""
import argparse
import collections
import csv
import json
import re

FIELDS = ('COUNT_STAR', 'SUM_TIMER_WAIT', 'SUM_ROWS_EXAMINED', 'SUM_ROWS_SENT')
ACCESS = re.compile(r'^\S+ \S+ \S+ \[[^]]+\] "(\S+) (\S+) [^"]+" (\d{3}) \S+')
CANDIDATE = re.compile(r'^\S+ \S+ \S+ \[[^]]+\] "')


def access(path):
    counts = collections.Counter()
    statuses = collections.Counter()
    non_access = malformed = total = 0
    with open(path, encoding='utf-8', errors='replace') as stream:
        for line in stream:
            total += 1
            match = ACCESS.match(line)
            if not match:
                if CANDIDATE.match(line):
                    malformed += 1
                else:
                    non_access += 1
                continue
            method, target, status = match.groups()
            counts[(method, target.split('?', 1)[0], status)] += 1
            statuses[status] += 1
    parsed = sum(counts.values())
    return {'lines': total, 'parsed': parsed, 'non_access': non_access,
            'malformed_access_candidates': malformed,
            'status_counts': dict(sorted(statuses.items())),
            'checks': {'line_partition': total == parsed + non_access + malformed,
                       'status_sum': parsed == sum(statuses.values())},
            'by_method_path_status': [{'method': m, 'path': p, 'status': s, 'count': n}
                                      for (m, p, s), n in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]}


def snapshot(path):
    rows = collections.defaultdict(list)
    with open(path, encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream, delimiter='\t')
        required = ('SCHEMA_NAME', 'DIGEST', 'DIGEST_TEXT') + FIELDS
        if not reader.fieldnames or any(f not in reader.fieldnames for f in required):
            raise ValueError(f'{path}: missing required headers')
        for row in reader:
            key = (row['SCHEMA_NAME'], row['DIGEST'])
            row['values'] = {f: int(row[f]) for f in FIELDS}
            rows[key].append(row)
    return rows


def event(sql):
    first = sql.split(maxsplit=1)
    return first[0].upper() if first else 'UNKNOWN'


def digest(before_path, after_path):
    before, after = snapshot(before_path), snapshot(after_path)
    keys = sorted(before.keys() | after.keys())
    anomalies = {'only_before': [], 'only_after': [], 'null_digest': [],
                 'duplicate_keys': [], 'text_mismatch': [], 'negative_delta': []}
    rows = []
    for key in keys:
        ident = {'schema': key[0], 'digest': key[1]}
        b, a = before.get(key, []), after.get(key, [])
        if key[1] in ('NULL', ''):
            anomalies['null_digest'].append(ident)
        if len(b) > 1 or len(a) > 1:
            anomalies['duplicate_keys'].append({**ident, 'before': len(b), 'after': len(a)})
        if not b:
            anomalies['only_after'].append(ident)
        if not a:
            anomalies['only_before'].append(ident)
        if len(b) != 1 or len(a) != 1:
            continue
        if b[0]['DIGEST_TEXT'] != a[0]['DIGEST_TEXT']:
            anomalies['text_mismatch'].append({**ident, 'before': b[0]['DIGEST_TEXT'], 'after': a[0]['DIGEST_TEXT']})
        delta = {f: a[0]['values'][f] - b[0]['values'][f] for f in FIELDS}
        if any(v < 0 for v in delta.values()):
            anomalies['negative_delta'].append({**ident, 'delta': delta})
        sql = a[0]['DIGEST_TEXT']
        rows.append({**ident, 'sql': sql, 'event': event(sql), 'delta': delta,
                     'wait_seconds': delta['SUM_TIMER_WAIT'] / 10**12})
    totals = collections.Counter()
    pairs = collections.defaultdict(dict)
    for row in rows:
        kind = row['event']
        totals[kind] += row['delta']['COUNT_STAR']
        if kind in ('PREPARE', 'EXECUTE', 'DEALLOCATE'):
            rest = row['sql'].split(maxsplit=1)
            text = rest[1] if len(rest) > 1 else ''
            pairs[(row['schema'], text)][kind] = pairs[(row['schema'], text)].get(kind, 0) + row['delta']['COUNT_STAR']
    comparisons = []
    for (schema, sql), counts in sorted(pairs.items()):
        comparisons.append({'schema': schema, 'sql_suffix': sql, 'counts': counts,
                            'missing_events': [e for e in ('PREPARE', 'EXECUTE', 'DEALLOCATE') if e not in counts],
                            'execute_minus_prepare': counts['EXECUTE'] - counts['PREPARE'] if 'EXECUTE' in counts and 'PREPARE' in counts else None})
    return {'before_rows': sum(map(len, before.values())), 'after_rows': sum(map(len, after.values())),
            'matched_unique_rows': len(rows), 'anomalies': anomalies,
            'event_count_deltas': dict(sorted(totals.items())), 'text_event_comparisons': comparisons,
            'by_count': sorted(rows, key=lambda r: (-r['delta']['COUNT_STAR'], r['schema'], r['digest'])),
            'by_wait': sorted(rows, key=lambda r: (-r['delta']['SUM_TIMER_WAIT'], r['schema'], r['digest']))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--access', required=True)
    parser.add_argument('--before', required=True)
    parser.add_argument('--after', required=True)
    args = parser.parse_args()
    print(json.dumps({'access': access(args.access), 'digest': digest(args.before, args.after)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
