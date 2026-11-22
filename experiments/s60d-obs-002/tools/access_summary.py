#!/usr/bin/env python3
"""Count nginx access records without emitting request queries."""
import argparse
from collections import Counter
import json
import re

ACCESS = re.compile(r'^\S+\s+-\s+\S+\s+\[[^\]]+\]\s+"([A-Z]+)\s+(\S+)\s+HTTP/\d(?:\.\d)?"\s+(\d{3})(?:\s|$)')
WARN = re.compile(r'^\d{4}/\d\d/\d\d \d\d:\d\d:\d\d \[warn\]')


def summarize(lines):
    methods, statuses, paths = Counter(), Counter(), Counter()
    total = access = warn = malformed = 0
    for line in lines:
        total += 1
        match = ACCESS.match(line)
        if match:
            method, target, status = match.groups()
            methods[method] += 1
            statuses[status] += 1
            paths[target.split('?', 1)[0]] += 1
            access += 1
        elif WARN.match(line):
            warn += 1
        else:
            malformed += 1
    assert total == access + warn + malformed
    assert sum(methods.values()) == sum(statuses.values()) == sum(paths.values()) == access
    return dict(total=total, access=access, warn=warn, malformed_or_unclassified=malformed,
                methods=dict(sorted(methods.items())), statuses=dict(sorted(statuses.items())),
                paths=dict(sorted(paths.items())))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('log')
    args = parser.parse_args()
    with open(args.log, encoding='utf-8', errors='replace') as source:
        print(json.dumps(summarize(source), ensure_ascii=False, indent=2))
