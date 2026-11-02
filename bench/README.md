# Benchmarks: how py-screen's speed evolved

Every run is kept in [`results/`](results/), one file per run, never overwritten, and gets a
row here, oldest first. The commit is the one measured, which is also the commit that adds
its result file. A change that touches the data path (keeper → brain → screen → client)
comes with a new run; the row says what changed and why the numbers moved.

| Bench | What it measures | Run |
|---|---|---|
| [`m0_speed.py`](m0_speed.py) | End to end, against GNU screen in the same terminal: `seq 1 5000000`, `cat` of a 50 MB coloured log, key → echo idle and while another window floods, idle CPU (spec §8.1) | `uv run python bench/m0_speed.py --work <scratch dir> --out results/<name>-<YYYYMMDD-HHMM>.md` |
| [`parse_speed.py`](parse_speed.py) | The screen engines alone (`term.ENGINES`), same bytes, no pty: MB/s for scrolling text and a coloured log, 200×50 | `uv run python bench/parse_speed.py --label <what changed>` |

## End to end, against GNU screen (`m0_speed.py`)

Wall times; screen took 12.7–13.5 s (`seq`) and 8.0–8.3 s (`cat`) in every run.

| When | Commit | Change | `seq` | `cat` | echo while flooding (median / p95) | Result |
|---|---|---|---|---|---|---|
| 11-01 01:24 | `fa4434c` | M0: keeper, brain, client; pyte parses everything | 50.9 s (3.9×) | 37.4 s (4.5×) | 5000 / 5000 ms: typing starves | [m0](results/m0-20261101-0124.md) |
| 11-01 02:06 | `dd0f07b` | Tail parsing: a flooding window parses only its last bytes, keys first | 4.1 s (0.3×) | 1.6 s (0.2×) | 5000 / 5000 ms | [flood-fix 1](results/m1-flood-fix-20261101-0206.md) |
| 11-01 02:14 | `dd0f07b` | Same, the head-of-line fix in place | 4.3 s (0.3×) | 2.1 s (0.3×) | 1.1 / 4.3 ms | [flood-fix 2](results/m1-flood-fix-20261101-0214.md) |
| 11-01 03:44 | `05c6ead` | **Lossless**: every byte parsed, backpressure instead of dropping (tail parsing left wrong cells in Claude Code's diff-drawn screen) | 59.6 s (4.4×) | 46.0 s (5.5×) | 1.7 / 4.4 ms | [lossless](results/m1-lossless-20261101-0344.md) |
| 11-01 06:27 | `d4c94c1` | Pure-Python fast paths in the parser, equal to pyte cell for cell | 25.3 s (2.0×) | 14.7 s (1.8×) | 1.9 / 5.9 ms | [fastparse](results/m1-fastparse-20261101-0627.md) |
| 11-01 09:12 | `62b0753` | **vterm engine**: libvterm's parser and state in C, our own row-pointer store; lossless as before | **1.56 s (0.1×)** | **0.85 s (0.1×)** | 1.4 / 1.8 ms | [vterm-store](results/m1-vterm-store-20261101-0912.md) |
| 11-01 20:03 | `96c8cf8` | **Scrollback**: 5000 rows per window; each row that leaves the screen is copied into a ring (GNU screen here keeps 10000) | 2.41 s (0.2×) | 1.04 s (0.1×) | 1.4 / 1.8 ms | [scrollback](results/m1-scrollback-20261101-2003.md) |

The scrollback row pays for keeping history: about 0.2 µs a line on short lines at
200×50 (the row is trimmed to its last visible cell, then copied), measured on the engine
alone as `seq` 32 → ~19.5 MB/s and the coloured log 79 → ~62 MB/s for vterm, pyte
unchanged (1.7 / 4.0–4.3). With `SCROLLBACK = 0` the engine runs as before. An ASan fuzz
run overlapped part of this bench, so its times may be a little high.

The fast rows of 02:06 and 02:14 were fast because they skipped bytes. That was wrong, not
quick: the lossless row is the honest baseline, and the work since is about parse speed.

The vterm row parses every byte, as the lossless row does, and is now faster than GNU
screen itself (screen spends its time drawing each update to its terminal; py-screen draws
at most 60 frames a second). Checked on a live session: 2 million lines, the capture says
`engine: vterm` and ends on the right last lines. Idle CPU for 10 windows read 0.10 % in
this run (0.00 % before); not looked into yet.

## Screen engines alone (`parse_speed.py`)

MB/s at 200×50 (higher is better); the commit is the one measured.

| When | Commit | Change | `seq`: pyte / vterm | coloured log: pyte / vterm | Result |
|---|---|---|---|---|---|
| 11-01 08:41 | `5ebe254` | First vterm engine: libvterm's own screen layer | 1.8 / 1.7 | 4.3 / 20.7 | [libvterm-screen-layer](results/parse-libvterm-screen-layer-20261101-0841.md) |
| 11-01 09:10 | `7bf9173` | Our own screen store on libvterm's state layer: rows as pointers, a scroll rotates them | 1.8 / **32.0** | 4.2 / **77.9** | [own-store](results/parse-own-store-20261101-0910.md) |

Why `seq` didn't gain: libvterm's screen layer keeps the screen as one flat array of 36-byte
cells and moves every row on each line feed (`deps/libvterm/src/screen.c`, `getcell` and
`moverect_internal`), about 350 KB at 200×50. The cost grows with columns × rows (0.19 µs a
line at 20×5, 4.6 µs at 200×50). The store that replaced it keeps libvterm's parser and
state layers and holds the rows as pointers: 0.25 µs a line at 200×50, 0.15 µs at 80×24,
and the size of the screen barely matters any more.
