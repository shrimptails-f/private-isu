# Digest snapshot difference

From the repository root, regenerate an output file outside the input directory:

```sh
python3 experiments/s20261122-obs-002/tools/digest_diff.py \
  experiments/s20261122-obs-002/input/digest-before.tsv \
  experiments/s20261122-obs-002/input/digest-after.tsv /tmp/digest-diff.json
```

The JSON `results` contains all valid common keys, including zero differences, and after-only keys marked `after_only_assumed_zero`. Four counter fields are differences; `SUM_TIMER_WAIT` is in picoseconds and `seconds` is an exact decimal string (10^12 ps per second). Rows are ordered by decreasing time, then schema and digest. `NULL` is treated as a literal key, and PREPARE, EXECUTE, management and collection SQL are not filtered or combined.

`diagnostics` lists before-only keys, text mismatches and counter decreases; those keys are excluded from results. Duplicate keys, missing or duplicate required headers, malformed rows and nonnegative-integer violations cause an error and no output. An after-only row assumes zero before, which is not proof it was absent. Resets, evictions and missing data may go undetected even when counters increase; snapshots lack server identity and collection completeness. The interval includes initialization, management and collection; total SQL time includes parallel work and is not wall time. Neither input is modified.
