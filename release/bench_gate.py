#!/usr/bin/env python3
"""Fail a release whose native parser lost much of its lead over pyte.

Usage: release/bench_gate.py <run.json> [<results dir>] [--floor 0.5]
  <run.json>     the last line of `bench/parse_speed.py --json`, made on this runner
  <results dir>  where recorded runs live (default bench/results); the baseline is the
                 newest parse-*.json, by the YYYYMMDD-HHMM stamp in its name

The recorded runs come from a desktop, and CI runners are slower and noisy, so raw MB/s
from two machines can't be compared. What is compared is vterm's speedup over pyte, both
measured in the same run on the same machine: for each workload (seq, log) it must be at
least floor x the baseline's speedup (default half, so only a large drop counts).
Exit 0 = fast enough, 1 = too slow, 2 = no baseline or a malformed run.
"""
import argparse, json, re, sys
from pathlib import Path

ap = argparse.ArgumentParser(usage=__doc__)
ap.add_argument("run")
ap.add_argument("results", nargs="?", default="bench/results")
ap.add_argument("--floor", type=float, default=0.5)
a = ap.parse_args()


def last_json(path: Path) -> dict:
    return json.loads(path.read_text().strip().splitlines()[-1])


def stamp(path: Path) -> str:
    m = re.search(r"(\d{8}-\d{4})", path.name)
    return m.group(1) if m else ""


recorded = sorted(Path(a.results).glob("parse-*.json"), key=stamp)
if not recorded:
    print(f"bench gate: no parse-*.json in {a.results}", file=sys.stderr)
    sys.exit(2)
base_file = recorded[-1]
base, run = last_json(base_file), last_json(Path(a.run))

print(f"baseline {base_file.name} (commit {base.get('commit') or '?'}), floor {a.floor:.0%}")
print(f"{'load':<5} {'pyte':>8} {'vterm':>8} {'speedup':>8} {'baseline':>9} {'ratio':>6}")
slow = []
for load in base["mb_per_s"]["vterm"]:
    try:
        p, v = run["mb_per_s"]["pyte"][load], run["mb_per_s"]["vterm"][load]
        bp, bv = base["mb_per_s"]["pyte"][load], base["mb_per_s"]["vterm"][load]
    except KeyError as e:
        print(f"bench gate: no {e} in the run or the baseline", file=sys.stderr)
        sys.exit(2)
    now, then = v / p, bv / bp
    ratio = now / then
    mark = "  <- too slow" if ratio < a.floor else ""
    print(f"{load:<5} {p:>6.1f}MB {v:>6.1f}MB {now:>7.1f}x {then:>8.1f}x {ratio:>6.2f}{mark}")
    if ratio < a.floor:
        slow.append(load)
if slow:
    print(f"bench gate: vterm's speedup fell below {a.floor:.0%} of the baseline's: {', '.join(slow)}")
    sys.exit(1)
print("bench gate: ok")
