"""Parse speed of each screen engine on the same bytes, no pty and no brain.

    uv run python bench/parse_speed.py [--mb 10] [--label <what changed>] [--json]

Feeds `seq` output and a coloured log (m0_speed.make_log) in 64 KB reads, as the
brain does, then renders every row once, and prints MB/s per engine. With --label
the table is also saved as bench/results/parse-<label>-<YYYYMMDD-HHMM>.md, and the
numbers beside it as .json (never over an existing file); add its row to
bench/README.md. --json prints the numbers as one JSON line instead of the table:

    {"commit": "7bf9173", "dirty": false, "cols": 200, "rows": 50, "mb": {"seq": 7.9, "log": 10.6},
     "mb_per_s": {"pyte": {"seq": 1.8, "log": 4.2}, "vterm": {"seq": 32.0, "log": 77.9}}}

A CI job compares mb_per_s with the newest bench/results/parse-*.json.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import m0_speed  # noqa: E402

from py_screen import term  # noqa: E402


def run(engine: str, data: bytes, cols: int = 200, rows: int = 50) -> float:
    t = term.ENGINES[engine](cols, rows)
    start = time.perf_counter()
    for i in range(0, len(data), 65536):
        t.feed(data[i:i + 65536])
    for y in range(rows):
        t.row(y, cols)
    return time.perf_counter() - start


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mb", type=int, default=10, help="size of the coloured log")
    ap.add_argument("--label", help="save the result under bench/results with this name")
    ap.add_argument("--json", action="store_true", help="print one JSON line, no table")
    a = ap.parse_args()
    seq = subprocess.run(["seq", "1", "1000000"], capture_output=True, check=True).stdout
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "log"
        m0_speed.make_log(log, a.mb * 1024 * 1024)
        logb = log.read_bytes()
    inputs = {"seq": ("seq 1 1000000", seq.replace(b"\n", b"\r\n")),
              "log": (f"coloured log {a.mb} MB", logb.replace(b"\n", b"\r\n"))}
    engines = list(term.ENGINES)
    speed = {e: {} for e in engines}
    lines = ["| input | MB | " + " | ".join(f"{e} MB/s" for e in engines) + " |",
             "|--|--|" + "--|" * len(engines)]
    for key, (name, data) in inputs.items():
        mb = len(data) / 1e6
        for e in engines:
            speed[e][key] = round(mb / run(e, data), 1)
        lines.append(f"| {name} | {mb:.1f} | "
                     + " | ".join(f"{speed[e][key]:.1f}" for e in engines) + " |")
    head, dirty = commit()
    record = {"commit": head, "dirty": dirty, "cols": 200, "rows": 50,
              "mb": {k: round(len(d) / 1e6, 1) for k, (_, d) in inputs.items()},
              "mb_per_s": speed}
    print(json.dumps(record) if a.json else "\n".join(lines))
    if a.label:
        save(a.label, lines, record)


def commit() -> tuple[str, bool]:
    git = ["git", "-C", str(Path(__file__).parent)]
    head = subprocess.run(git + ["rev-parse", "--short", "HEAD"], capture_output=True,
                          text=True).stdout.strip()
    dirty = bool(subprocess.run(git + ["status", "--porcelain", "--untracked-files=no"],
                                capture_output=True, text=True).stdout.strip())
    return head, dirty


def save(label: str, table: list[str], record: dict) -> None:
    now = datetime.now()
    out = Path(__file__).parent / "results" / f"parse-{label}-{now:%Y%m%d-%H%M}.md"
    data = out.with_suffix(".json")
    if out.exists() or data.exists():
        raise SystemExit(f"{out} exists: results are never overwritten")
    head, dirty = record["commit"], record["dirty"]
    data.write_text(json.dumps(record) + "\n")
    out.write_text(f"# Parse speed: {label} ({now:%Y-%m-%d %H:%M})\n\n"
                   f"Measured on {head}{' + uncommitted changes' if dirty else ''}, "
                   f"Python {sys.version.split()[0]}, 200x50, 64 KB reads, parse + one render "
                   f"of every row.\n\n" + "\n".join(table) + "\n")
    print(f"saved {out}")


if __name__ == "__main__":
    main()
