#!/usr/bin/env python3
"""Sample observed process CPU ticks; never read cmdline or environment."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import signal
import time


def parse_stat(data):
    # comm may contain spaces and parentheses; the final ')' ends field 2.
    end = data.rfind(')')
    start = data.find('(')
    if start < 0 or end <= start:
        raise ValueError('invalid stat comm')
    fields = data[end + 1:].split()  # starts with field 3
    if len(fields) < 20:
        raise ValueError('short stat')
    return int(fields[11]), int(fields[12]), int(fields[19])


def snapshot(proc, errors):
    result = {}
    try:
        entries = list(proc.iterdir())
    except OSError:
        errors['enumeration_failure'] += 1
        return result
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / 'stat').read_text()
            comm = (entry / 'comm').read_text().rstrip('\n')
            utime, stime, start = parse_stat(stat)
            result[(int(entry.name), start)] = (utime + stime, comm)
        except (FileNotFoundError, ProcessLookupError):
            errors['vanished'] += 1
        except PermissionError:
            errors['permission_denied'] += 1
        except (ValueError, IndexError, UnicodeError):
            errors['parse_failure'] += 1
        except OSError:
            errors['read_failure'] += 1
    return result


def accumulate(previous, current, elapsed, hz, processes, errors):
    for key, (ticks, comm) in current.items():
        if key not in previous:
            continue
        delta = ticks - previous[key][0]
        if delta < 0:
            errors['negative_delta'] += 1
            continue
        # Assign each interval to its current comm, including a rename.
        record = processes.setdefault(key, {'seconds': 0.0, 'observed_interval_seconds': 0.0, 'names': set(), 'by_name': Counter()})
        seconds = delta / hz
        record['seconds'] += seconds
        record['observed_interval_seconds'] += elapsed
        record['names'].update((previous[key][1], comm))
        record['by_name'][comm] += seconds


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--interval', type=float, default=0.1)
    parser.add_argument('--duration', type=float, default=0, help='seconds; 0 means until SIGINT or SIGTERM')
    args = parser.parse_args()
    if args.interval <= 0 or args.duration < 0:
        parser.error('interval must be positive and duration nonnegative')
    stop = False
    def finish(_signum, _frame):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGINT, finish)
    signal.signal(signal.SIGTERM, finish)
    hz = os.sysconf('SC_CLK_TCK')
    errors, processes = Counter(), {}
    started = time.monotonic()
    previous = snapshot(Path('/proc'), errors)
    last = time.monotonic()
    samples = 1
    while not stop and (not args.duration or time.monotonic() - started < args.duration):
        time.sleep(args.interval)
        current = snapshot(Path('/proc'), errors)
        now = time.monotonic()
        accumulate(previous, current, now - last, hz, processes, errors)
        previous, last = current, now
        samples += 1
    elapsed = last - started
    names = Counter()
    rows = []
    for (pid, start), record in sorted(processes.items()):
        names.update(record['by_name'])
        rows.append(dict(pid=pid, starttime_ticks=start, names=sorted(record['names']),
                         cpu_seconds=record['seconds'], observed_interval_seconds=record['observed_interval_seconds'],
                         average_percent_of_one_core=100 * record['seconds'] / record['observed_interval_seconds']))
    print(json.dumps(dict(clock_ticks_per_second=hz, elapsed_seconds=elapsed, samples=samples,
                          errors=dict(errors), processes=rows,
                          comm_cpu_seconds=dict(sorted(names.items()))), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
