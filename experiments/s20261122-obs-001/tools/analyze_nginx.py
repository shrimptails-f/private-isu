#!/usr/bin/env python3
"""Summarize nginx combined access logs with an optional forwarded-for field."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import re

ACCESS = re.compile(r'^(\S+) \S+ \S+ \[([^]]+)\] "([^"\\]*(?:\\.[^"\\]*)*)" (\d{3}|-) (\d+|-) "([^"\\]*(?:\\.[^"\\]*)*)" "([^"\\]*(?:\\.[^"\\]*)*)"(?: "([^"\\]*(?:\\.[^"\\]*)*)")?$')
DIAGNOSTIC = re.compile(r'^\d{4}/\d\d/\d\d \d\d:\d\d:\d\d \[(?:emerg|alert|crit|error|warn|notice|info|debug)\] ')
REQUEST = re.compile(r'^(\S+) (\S+) HTTP/\S+$')


def normalize(path):
    path = path.split('?', 1)[0]
    path = re.sub(r'^/image/\d+\.([A-Za-z0-9]+)$', r'/image/:id.\1', path)
    return re.sub(r'^/posts/\d+$', '/posts/:id', path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', help='nginx log to read')
    args = parser.parse_args()
    counts = Counter()
    statuses = Counter()
    agents = Counter()
    routes = defaultdict(lambda: {'count': 0, 'body_bytes': 0, 'missing_body_bytes': 0})
    times = []
    with open(args.log, encoding='utf-8', errors='replace') as source:
        for line in source:
            counts['lines'] += 1
            line = line.rstrip('\r\n')
            match = ACCESS.fullmatch(line)
            if not match:
                counts['diagnostic' if DIAGNOSTIC.match(line) else 'unparsed'] += 1
                continue
            counts['access'] += 1
            _, timestamp, request, status, size, _, agent, _ = match.groups()
            try:
                times.append(datetime.strptime(timestamp, '%d/%b/%Y:%H:%M:%S %z').astimezone(timezone.utc))
            except ValueError:
                counts['invalid_timestamp'] += 1
            statuses[status] += 1
            agents[agent] += 1
            req = REQUEST.fullmatch(request)
            method, path = (req.group(1), normalize(req.group(2))) if req else ('<unparsed>', '<unparsed>')
            if not req:
                counts['unparsed_request'] += 1
            route = routes[(method, path)]
            route['count'] += 1
            if size == '-':
                counts['missing_body_bytes'] += 1
                route['missing_body_bytes'] += 1
            else:
                value = int(size)
                counts['body_bytes'] += value
                route['body_bytes'] += value
    output = {
        'lines': counts['lines'], 'access': counts['access'],
        'diagnostic': counts['diagnostic'], 'unparsed': counts['unparsed'],
        'body_bytes': counts['body_bytes'], 'missing_body_bytes': counts['missing_body_bytes'],
        'invalid_timestamp': counts['invalid_timestamp'], 'unparsed_request': counts['unparsed_request'],
        'first_timestamp_utc': min(times).isoformat() if times else None,
        'last_timestamp_utc': max(times).isoformat() if times else None,
        'status': dict(sorted(statuses.items())), 'user_agent': dict(sorted(agents.items())),
        'routes': [dict(method=method, path=path, **data) for (method, path), data in sorted(routes.items())],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
