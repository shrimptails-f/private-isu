#!/usr/bin/env python3
import argparse, csv, collections, decimal, sys

FIELDS = ['COUNT_STAR', 'SUM_TIMER_WAIT', 'SUM_ROWS_EXAMINED', 'SUM_ROWS_SENT']
REQUIRED = ['SCHEMA_NAME', 'DIGEST', 'DIGEST_TEXT'] + FIELDS

def read(path):
    rows = {}
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t')
        if not reader.fieldnames or any(reader.fieldnames.count(c) != 1 for c in REQUIRED):
            raise ValueError('missing or duplicate required headers')
        for line, r in enumerate(reader, 2):
            if None in r or any(r[c] is None for c in REQUIRED):
                raise ValueError(f'{path}: malformed row {line}')
            key = (r['SCHEMA_NAME'], r['DIGEST'])
            if not key[1] or key[1].upper() == 'NULL':
                raise ValueError(f'{path}: empty/NULL digest at row {line}; incomplete data')
            if key in rows:
                raise ValueError(f'{path}: duplicate key at row {line}')
            try:
                values = tuple(int(r[c]) for c in FIELDS)
                if any(v < 0 for v in values): raise ValueError()
            except ValueError:
                raise ValueError(f'{path}: invalid counter at row {line}') from None
            rows[key] = (r['DIGEST_TEXT'], values)
    return rows

def main():
    p = argparse.ArgumentParser(); p.add_argument('before'); p.add_argument('after'); a = p.parse_args()
    before, after = read(a.before), read(a.after)
    problems = []
    for k in before.keys() - after.keys(): problems.append('before-only digest (missing/reset)')
    totals = [0]*4; groups = collections.defaultdict(lambda: [0]*4)
    print('schema\tdigest\ttype\tsql\tcount\ttimer_ps\tseconds\trows_examined\trows_sent\tstatus')
    for k in sorted(after):
        sql, vals = after[k]; status = 'common'
        if k not in before:
            old = (0,)*4; status = 'after-only (before assumed zero)'
        else:
            oldsql, old = before[k]
            if oldsql != sql:
                problems.append('digest text mismatch'); continue
        diff = [x-y for x,y in zip(vals,old)]
        if any(x < 0 for x in diff):
            problems.append('negative delta (reset/missing)'); continue
        kind = sql.split(None, 1)[0].upper() if sql.strip() else '(empty)'
        for i, v in enumerate(diff): totals[i] += v; groups[kind][i] += v
        print('\t'.join(map(str, [*k, kind, sql, diff[0], diff[1], decimal.Decimal(diff[1])/10**12, diff[2], diff[3], status])))
    for kind, values in sorted(groups.items()): print('TYPE', kind, *values, sep='\t', file=sys.stderr)
    print('TOTAL', *totals, sep='\t', file=sys.stderr)
    print('CHECK type sums:', 'OK' if [sum(v[i] for v in groups.values()) for i in range(4)] == totals else 'FAIL', file=sys.stderr)
    for problem, count in collections.Counter(problems).items(): print('INCOMPLETE', problem, count, file=sys.stderr)
    if problems: return 1
    return 0

if __name__ == '__main__':
    try: sys.exit(main())
    except (ValueError, OSError, csv.Error) as e:
        print('INCOMPLETE:', e, file=sys.stderr); sys.exit(1)
