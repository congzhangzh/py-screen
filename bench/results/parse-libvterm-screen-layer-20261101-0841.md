# Parse speed: libvterm-screen-layer (2026-11-01 08:41)

Measured on 5ebe254 + uncommitted changes, Python 3.14.8, 200x50, 64 KB reads, parse + one render of every row.

| input | MB | pyte MB/s | vterm MB/s |
|--|--|--|--|
| seq 1 1000000 | 7.9 | 1.8 | 1.7 |
| coloured log 10 MB | 10.6 | 4.3 | 20.7 |
