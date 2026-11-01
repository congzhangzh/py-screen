"""Differential fuzz of the vterm engine: py-screen's store against libvterm's own screen
layer, the same random byte streams, every rendered row compared after every read
(tests/test_vterm.py's run(); the suite runs 400 + 400 seeds of it, this runs more).

    python tools/vterm_fuzz.py [--seeds 5000] [--start 100000] [--mode both|plain|resize]
                               [--time 600]

For memory errors build with tools/vterm_asan.sh first and run under it (see there).

Prints one JSON line at the end:
    {"seeds": 5000, "failures": 0, "first_failure": null, "seconds": 11.8, "mode": "both"}
Exit: 0 every seed passed; 1 a seed differs (details on stderr; seed numbers replay the
same bytes); 2 bad usage. A crash or a sanitizer report ends the process itself: with
the exitcode set in ASAN_OPTIONS / UBSAN_OPTIONS, or a signal.
--time stops starting new seeds after that many seconds (the JSON says how many ran).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
import test_vterm  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5000, help="seeds per mode")
    ap.add_argument("--start", type=int, default=100_000, help="first seed")
    ap.add_argument("--mode", choices=("both", "plain", "resize"), default="both")
    ap.add_argument("--time", type=float, default=None, help="time budget in seconds")
    a = ap.parse_args()
    modes = {"both": (False, True), "plain": (False,), "resize": (True,)}[a.mode]
    t0 = time.monotonic()
    ran = failures = 0
    first = None
    for resizes in modes:
        base = a.start + (1_000_000 if resizes else 0)
        for s in range(base, base + a.seeds):
            if a.time is not None and time.monotonic() - t0 > a.time:
                break
            try:
                test_vterm.run(s, resizes)
            except AssertionError as e:
                failures += 1
                first = first if first is not None else s
                print(f"seed {s} (resizes={resizes}): {str(e)[:2000]}", file=sys.stderr)
            ran += 1
    print(json.dumps({"seeds": ran, "failures": failures, "first_failure": first,
                      "seconds": round(time.monotonic() - t0, 1), "mode": a.mode}))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
