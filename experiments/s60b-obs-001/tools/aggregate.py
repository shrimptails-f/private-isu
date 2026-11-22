#!/usr/bin/env python3
import argparse, collections, csv, json, re
from decimal import Decimal

p = argparse.ArgumentParser()
p.add_argument('log'); p.add_argument('before'); p.add_argument('after')
a = p.parse_args()
access = re.compile(r'^\S+ \S+ \S+ \[[^]]+\] "([^ ]+) ([^ ]+) HTTP/[^" ]+" (\d{3}) \d+ ')
counts = collections.Counter(); statuses = collections.Counter(); nonaccess = collections.Counter(); unknown = 0; total = 0
with open(a.log, encoding='utf-8') as f:
    for line in f:
        total += 1
        m = access.match(line)
        if m:
            method, path, status = m.groups(); counts[(method, path.split('?', 1)[0], status)] += 1; statuses[status] += 1
        elif re.search(r'\[warn\]', line): nonaccess['warning'] += 1
        else: unknown += 1
print('LOG', json.dumps({'lines': total, 'access': sum(counts.values()), 'nonaccess': nonaccess, 'unparsed': unknown, 'accounted': sum(counts.values()) + sum(nonaccess.values()) + unknown, 'status': statuses}, ensure_ascii=False))
for (method, path, status), n in counts.most_common(): print('ACCESS', n, method, path, status, sep='\t')
fields = ('COUNT_STAR', 'SUM_TIMER_WAIT', 'SUM_ROWS_EXAMINED', 'SUM_ROWS_SENT')
def read(path):
    with open(path, encoding='utf-8', newline='') as f:
        rows = {}
        for r in csv.DictReader(f, delimiter='\t'):
            k = (r['SCHEMA_NAME'], r['DIGEST'])
            if k in rows: raise ValueError('duplicate digest key: ' + repr(k))
            rows[k] = r
        return rows
b, c = read(a.before), read(a.after); rows = []
for k in sorted(b.keys() | c.keys()):
    x, y = b.get(k), c.get(k)
    if not x or not y:
        print('MISSING', *k, 'before' if not x else 'after', sep='\t'); continue
    delta = [int(y[f]) - int(x[f]) for f in fields]
    flags = []
    if any(v < 0 for v in delta): flags.append('negative_delta')
    if x['DIGEST_TEXT'] != y['DIGEST_TEXT']: flags.append('text_mismatch')
    rows.append((k, delta, flags, y['DIGEST_TEXT']))
print('DIGEST_KEYS', json.dumps({'before': len(b), 'after': len(c), 'paired': len(rows), 'before_only': len(b.keys()-c.keys()), 'after_only': len(c.keys()-b.keys())}))
for index, name in [(0, 'count'), (1, 'time_ps'), (2, 'examined')]:
    for k, d, flags, sql in sorted(rows, key=lambda r: (-r[1][index], r[0])):
        print('DIGEST', name, *k, d[0], str(Decimal(d[1]) / Decimal(10**12)), d[2], d[3], ','.join(flags) or '-', json.dumps(sql, ensure_ascii=False), sep='\t')
