#!/usr/bin/env python3
"""Summarize nginx access records and MySQL digest snapshots without modifying inputs."""
import argparse
import collections
import csv
import re
from decimal import Decimal
from urllib.parse import urlsplit

FIELDS = ('COUNT_STAR', 'SUM_TIMER_WAIT', 'SUM_ROWS_EXAMINED', 'SUM_ROWS_SENT')
ACCESS = re.compile(r'^\S+ \S+ \S+ \[[^\]]+\] "([A-Z]+) (\S+) HTTP/\d(?:\.\d)?" (\d{3}) (?:\d+|-)')


def route(target):
    path = urlsplit(target).path
    path = re.sub(r'^/@[^/]+$', '/@:user', path)
    return re.sub(r'(?<=/)\d+(?=/|\.|$)', ':id', path)


def nginx(path):
    counts = collections.Counter()
    methods, routes, statuses = (collections.Counter() for _ in range(3))
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            counts['total'] += 1
            match = ACCESS.match(line)
            if not match:
                counts['non-access/unclassified'] += 1
                continue
            counts['access'] += 1
            method, target, status = match.groups()
            methods[method] += 1
            routes[(method, route(target))] += 1
            statuses[status] += 1
    print('NGINX lines:', dict(counts))
    for name, counter in [('methods', methods), ('method routes', routes), ('statuses', statuses)]:
        print('\n' + name + ':')
        for key, count in counter.most_common():
            print(count, *key if isinstance(key, tuple) else (key,))
    print('\nHTTP request/upstream time: not measured by this log format.')


def snapshot(path):
    rows, duplicates = {}, []
    with open(path, encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE)
        required = {'SCHEMA_NAME', 'DIGEST', 'DIGEST_TEXT', *FIELDS}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f'{path}: missing required columns')
        for row in reader:
            key = (row['SCHEMA_NAME'], row['DIGEST'])
            if key in rows:
                duplicates.append(key)
            rows.setdefault(key, []).append(row)
    return rows, duplicates


def digest(before_path, after_path):
    before, bd = snapshot(before_path)
    after, ad = snapshot(after_path)
    print('\nDIGEST snapshot keys: before', len(before), 'after', len(after))
    print('duplicate rows: before', len(bd), 'after', len(ad))
    for label, keys in [('before only', before.keys() - after.keys()), ('after only', after.keys() - before.keys())]:
        print(label + ':', len(keys))
        for key in sorted(keys): print(' ', repr(key))
    anomalies = collections.Counter()
    null_schema = 0
    totals = [0] * len(FIELDS)
    records = []
    for key in sorted(before.keys() & after.keys()):
        if len(before[key]) != 1 or len(after[key]) != 1:
            anomalies['duplicate key'] += 1
            print('ANOMALY duplicate key', repr(key))
            continue
        b, a = before[key][0], after[key][0]
        problems = []
        if key[0] in ('NULL', '\\N', '', None):
            null_schema += 1
        if key[1] in ('NULL', '\\N', '', None):
            problems.append('NULL/empty digest')
        if b['DIGEST_TEXT'] != a['DIGEST_TEXT']:
            problems.append('digest text mismatch')
        if b['DIGEST_TEXT'] in ('NULL', '\\N', '', None) or a['DIGEST_TEXT'] in ('NULL', '\\N', '', None):
            problems.append('NULL/empty digest text')
        try:
            values = [int(a[f]) - int(b[f]) for f in FIELDS]
            if any(v < 0 for v in values): problems.append('negative delta')
        except (ValueError, TypeError):
            values = None
            problems.append('NULL/invalid numeric value')
        if problems:
            for problem in problems: anomalies[problem] += 1
            print('ANOMALY', repr(key), ', '.join(problems))
            continue
        totals = [x + y for x, y in zip(totals, values)]
        records.append((values, key, a['DIGEST_TEXT']))
    print('common keys with NULL/empty schema:', null_schema, '(included if digest and values are valid)')
    print('anomaly counts:', dict(anomalies), '(affected keys excluded from totals)')
    print('valid common keys:', len(records))
    print('totals: count=%d wait_ps=%d wait_s=%s examined_rows=%d sent_rows=%d' %
          (totals[0], totals[1], Decimal(totals[1]) / Decimal(10**12), totals[2], totals[3]))
    print('\nDigest deltas sorted by wait_ps descending:')
    for v, key, sql in sorted(records, key=lambda x: -x[0][1]):
        print('count=%d wait_ps=%d wait_s=%s examined_rows=%d sent_rows=%d key=%r sql=%s' %
              (v[0], v[1], Decimal(v[1]) / Decimal(10**12), v[2], v[3], key, sql))
    print('\nTimes are cumulative statement wait, not elapsed or request latency; concurrency, initialization and management are included. PREPARE and EXECUTE can overlap in interpretation; do not sum them as independent work. Missing keys may indicate eviction, reset, or capacity limits.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nginx', required=True)
    parser.add_argument('--before', required=True)
    parser.add_argument('--after', required=True)
    args = parser.parse_args()
    nginx(args.nginx)
    digest(args.before, args.after)
