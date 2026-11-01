"""Parse speed of each screen engine on the same bytes, no pty and no brain.

    uv run python bench/parse_speed.py [--mb 10] [--label <what changed>]

Feeds `seq` output and a coloured log (m0_speed.make_log) in 64 KB reads, as the
brain does, then renders every row once, and prints MB/s per engine. With --label
the table is also saved as bench/results/parse-<label>-<YYYYMMDD-HHMM>.md (never
over an existing file); add its row to bench/README.md.
"""

from __future__ import annotations

import argparse
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
    a = ap.parse_args()
    seq = subprocess.run(["seq", "1", "1000000"], capture_output=True, check=True).stdout
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "log"
        m0_speed.make_log(log, a.mb * 1024 * 1024)
        logb = log.read_bytes()
    inputs = {"seq 1 1000000": seq.replace(b"\n", b"\r\n"),
              f"coloured log {a.mb} MB": logb.replace(b"\n", b"\r\n")}
    engines = list(term.ENGINES)
    lines = ["| input | MB | " + " | ".join(f"{e} MB/s" for e in engines) + " |",
             "|--|--|" + "--|" * len(engines)]
    for name, data in inputs.items():
        mb = len(data) / 1e6
        speeds = [mb / run(e, data) for e in engines]
        lines.append(f"| {name} | {mb:.1f} | " + " | ".join(f"{s:.1f}" for s in speeds) + " |")
    print("\n".join(lines))
    if a.label:
        save(a.label, lines)


def save(label: str, table: list[str]) -> None:
    now = datetime.now()
    out = Path(__file__).parent / "results" / f"parse-{label}-{now:%Y%m%d-%H%M}.md"
    if out.exists():
        raise SystemExit(f"{out} exists: results are never overwritten")
    git = ["git", "-C", str(Path(__file__).parent)]
    head = subprocess.run(git + ["rev-parse", "--short", "HEAD"], capture_output=True,
                          text=True).stdout.strip()
    dirty = subprocess.run(git + ["status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True).stdout.strip()
    out.write_text(f"# Parse speed: {label} ({now:%Y-%m-%d %H:%M})\n\n"
                   f"Measured on {head}{' + uncommitted changes' if dirty else ''}, "
                   f"Python {sys.version.split()[0]}, 200x50, 64 KB reads, parse + one render "
                   f"of every row.\n\n" + "\n".join(table) + "\n")
    print(f"saved {out}")


if __name__ == "__main__":
    main()
