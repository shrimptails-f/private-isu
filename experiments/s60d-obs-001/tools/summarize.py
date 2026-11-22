#!/usr/bin/env python3
"""Summarize supplied logs and MySQL digest snapshots without modifying them."""
import argparse
import collections
import csv
import json
import os
import re
from decimal import Decimal
from urllib.parse import urlsplit

ACCESS = re.compile(r'^\S+ \S+ \S+ \[([^]]+)\] "([A-Z]+) (\S+) HTTP/\d(?:\.\d)?" (\d{3}) \S+ "[^"]*" "[^"]*" "[^"]*"\s*$')
FIELDS = ('COUNT_STAR', 'SUM_TIMER_WAIT', 'SUM_ROWS_EXAMINED', 'SUM_ROWS_SENT')

def nginx(path):
    counts = collections.Counter(); raw = collections.Counter(); normalized = collections.Counter(); seconds = collections.Counter(); status = collections.Counter()
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            m = ACCESS.fullmatch(line.rstrip('\n'))
            if not m:
                counts['warning' if '[warn]' in line else 'unrecognized'] += 1
                continue
            date, method, target, code = m.groups()
            pathpart = urlsplit(target).path
            # Only numeric path segments are normalized; raw paths are retained.
            norm = re.sub(r'(?<=/)\d+(?=/|$)', ':id', pathpart)
            counts['access'] += 1; status[code] += 1; seconds[date] += 1
            raw[(method, pathpart, code)] += 1; normalized[(method, norm, code)] += 1
    def rows(c):
        return [{'method': k[0], 'path': k[1], 'status': k[2], 'count': v} for k, v in sorted(c.items())]
    return {'counts': dict(counts), 'status': dict(sorted(status.items())), 'seconds': dict(sorted(seconds.items())), 'raw_paths': rows(raw), 'normalized_paths': rows(normalized), 'normalization': 'numeric complete path segments become :id; query omitted', 'timing': 'unavailable'}

def snapshot(path):
    data = {}; duplicates = []; nulls = []
    with open(path, encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        required = {'SCHEMA_NAME', 'DIGEST', 'DIGEST_TEXT', *FIELDS}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f'{path}: missing required headers')
        for number, row in enumerate(reader, 2):
            key = (row['SCHEMA_NAME'], row['DIGEST'])
            if key in data:
                duplicates.append({'line': number, 'key': key}); data[key] = None; continue
            values = {}
            for field in FIELDS:
                try: values[field] = int(row[field])
                except (ValueError, TypeError): values[field] = None
                if values[field] is None: nulls.append({'line': number, 'field': field, 'value': row[field]})
            if 'NULL' in key or row['DIGEST_TEXT'] == 'NULL': nulls.append({'line': number, 'field': 'key_or_text', 'value': list(key)})
            data[key] = {'text': row['DIGEST_TEXT'], 'values': values}
    return data, duplicates, nulls

def digest(before, after):
    b, bd, bn = snapshot(before); a, ad, an = snapshot(after); rows = []
    for key in sorted(b.keys() | a.keys()):
        x, y = b.get(key), a.get(key)
        state = 'matched' if key in b and key in a else ('new' if key in a else 'disappeared')
        row = {'schema': key[0], 'digest': key[1], 'state': state, 'before_text': x['text'] if x else None, 'after_text': y['text'] if y else None, 'delta': {}}
        for field in FIELDS:
            v, w = (x['values'][field] if x else None), (y['values'][field] if y else None)
            row['delta'][field] = w-v if v is not None and w is not None else None
        t = row['delta']['SUM_TIMER_WAIT']
        row['timer_seconds'] = str(Decimal(t) / Decimal(10**12)) if t is not None else None
        row['negative_reset_suspected'] = any(v is not None and v < 0 for v in row['delta'].values())
        row['ambiguous_duplicate'] = (key in b and x is None) or (key in a and y is None)
        rows.append(row)
    return {'before_keys': len(b), 'after_keys': len(a), 'duplicates': {'before': bd, 'after': ad}, 'missing_or_null': {'before': bn, 'after': an}, 'units': {'COUNT_STAR': 'executions', 'SUM_TIMER_WAIT': 'picoseconds; aggregate, not wall time', 'SUM_ROWS_EXAMINED': 'rows', 'SUM_ROWS_SENT': 'rows'}, 'rows': rows}

def main():
    p = argparse.ArgumentParser(); p.add_argument('--nginx', required=True); p.add_argument('--app', required=True); p.add_argument('--before', required=True); p.add_argument('--after', required=True); args = p.parse_args()
    size = os.path.getsize(args.app)
    print(json.dumps({'nginx': nginx(args.nginx), 'app': {'bytes': size, 'interpretation': 'missing observation' if size == 0 else 'not parsed'}, 'digest': digest(args.before, args.after)}, ensure_ascii=False, indent=2))
if __name__ == '__main__': main()
