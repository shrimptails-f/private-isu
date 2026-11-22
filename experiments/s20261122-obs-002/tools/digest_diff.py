#!/usr/bin/env python3
"""Compare cumulative MySQL digest snapshots without modifying the inputs."""
import argparse
import csv
import json
from decimal import Decimal
from pathlib import Path

KEY = ('SCHEMA_NAME', 'DIGEST')
COUNTERS = ('COUNT_STAR', 'SUM_TIMER_WAIT', 'SUM_ROWS_EXAMINED', 'SUM_ROWS_SENT')
REQUIRED = (*KEY, 'DIGEST_TEXT', *COUNTERS)


def read(path):
    rows = {}
    with open(path, encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)) or not set(REQUIRED).issubset(reader.fieldnames):
            raise ValueError(f'{path}: missing or duplicate header columns')
        for line, row in enumerate(reader, 2):
            if None in row or any(row.get(c) is None for c in REQUIRED):
                raise ValueError(f'{path}:{line}: malformed row')
            key = tuple(row[c] for c in KEY)
            if key in rows:
                raise ValueError(f'{path}:{line}: duplicate key {key!r}')
            for c in COUNTERS:
                if not row[c].isascii() or not row[c].isdigit():
                    raise ValueError(f'{path}:{line}: invalid {c}: {row[c]!r}')
                row[c] = int(row[c])
            rows[key] = row
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before')
    parser.add_argument('after')
    parser.add_argument('output', help='output JSON path, distinct from inputs')
    args = parser.parse_args()
    output = Path(args.output)
    inputs = (Path(args.before), Path(args.after))
    if any(output.resolve() == item.resolve() or (output.exists() and item.exists() and output.samefile(item)) for item in inputs):
        parser.error('output must differ from inputs')
    try:
        before, after = read(args.before), read(args.after)
    except (OSError, ValueError, csv.Error) as exc:
        parser.exit(1, f'input error: {exc}\n')
    results, diagnostics = [], []
    for key in sorted(before.keys() | after.keys()):
        b, a = before.get(key), after.get(key)
        identity = dict(zip(KEY, key))
        if a is None:
            diagnostics.append({**identity, 'issue': 'before_only', 'DIGEST_TEXT': b['DIGEST_TEXT']})
            continue
        if b is not None and b['DIGEST_TEXT'] != a['DIGEST_TEXT']:
            diagnostics.append({**identity, 'issue': 'text_mismatch', 'before_text': b['DIGEST_TEXT'], 'after_text': a['DIGEST_TEXT']})
            continue
        delta = {c: a[c] - (b[c] if b else 0) for c in COUNTERS}
        if any(v < 0 for v in delta.values()):
            diagnostics.append({**identity, 'issue': 'counter_decrease', 'deltas': delta})
            continue
        seconds = str(Decimal(delta['SUM_TIMER_WAIT']) / Decimal(10**12))
        results.append({**identity, 'DIGEST_TEXT': a['DIGEST_TEXT'], 'status': 'common' if b else 'after_only_assumed_zero', **delta, 'seconds': seconds})
    results.sort(key=lambda r: (-r['SUM_TIMER_WAIT'], r['SCHEMA_NAME'], r['DIGEST']))
    document = {'units': {'SUM_TIMER_WAIT': 'picoseconds', 'seconds': 'seconds (10^12 ps)'},
                'limitations': 'Cumulative snapshots; after-only assumes zero before. Missing/evicted rows and resets cannot be ruled out, even with monotonic counters. Includes initialization, administration and snapshot queries; summed SQL time is not elapsed time.',
                'before_rows': len(before), 'after_rows': len(after), 'results': results, 'diagnostics': diagnostics}
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(document, f, ensure_ascii=False, indent=2)
        f.write('\n')


if __name__ == '__main__':
    main()
