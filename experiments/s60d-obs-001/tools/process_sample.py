#!/usr/bin/env python3
"""Sample proc stat/comm only. Counts are observed identities, not executions."""
import argparse
import collections
import json
import signal
import time

stop = False
def halt(*_):
    global stop
    stop = True

def read_stat(pid):
    with open(f'/proc/{pid}/stat', encoding='utf-8') as f: s = f.read()
    end = s.rfind(')'); fields = s[end+2:].split()
    return int(fields[1]), int(fields[19])  # PPID and starttime

def main():
    p = argparse.ArgumentParser(); p.add_argument('root_pid', type=int); p.add_argument('--interval', type=float, default=0.1); p.add_argument('--duration', type=float, default=10); args = p.parse_args()
    if args.interval <= 0 or args.duration <= 0: p.error('interval and duration must be positive')
    import os
    signal.signal(signal.SIGINT, halt); signal.signal(signal.SIGTERM, halt)
    start = time.monotonic(); root_start = read_stat(args.root_pid)[1]; seen = {}; samples = collections.Counter(); failures = 0; scans = 0; root_lost = False
    while not stop and time.monotonic() - start < args.duration:
        processes = {}
        for pid in os.listdir('/proc'):
            if not pid.isdigit(): continue
            try:
                ppid, birth = read_stat(pid)
                with open(f'/proc/{pid}/comm', encoding='utf-8', errors='replace') as f: comm = f.read().strip()
                processes[int(pid)] = (ppid, birth, comm)
            except (OSError, ValueError, IndexError): failures += 1
        scans += 1
        root = processes.get(args.root_pid)
        if root is None or root[1] != root_start:
            root_lost = True; break
        descendants = {args.root_pid}; changed = True
        while changed:
            new = {pid for pid, (ppid, _, _) in processes.items() if ppid in descendants} - descendants
            changed = bool(new); descendants.update(new)
        for pid in descendants:
            _, birth, comm = processes[pid]; seen[(pid, birth)] = comm; samples[comm] += 1
        time.sleep(min(args.interval, max(0, args.duration - (time.monotonic() - start))))
    identities = collections.Counter(seen.values())
    print(json.dumps({'root_pid': args.root_pid, 'root_starttime_ticks': root_start, 'requested_interval_seconds': args.interval, 'requested_duration_seconds': args.duration, 'elapsed_seconds': time.monotonic()-start, 'scans': scans, 'read_failures': failures, 'root_lost_or_reused': root_lost, 'interrupted': stop, 'observed_unique_pid_starttime_by_comm': identities, 'samples_by_comm': samples, 'limitations': 'Sampling misses short-lived processes; proc scan has CPU/I/O overhead. Permission and PID namespace restrict visibility; failed reads may be exiting processes. Counts are not execution counts.'}, indent=2))
if __name__ == '__main__': main()
