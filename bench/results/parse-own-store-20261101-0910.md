# Parse speed: own-store (2026-11-01 09:10)

Measured on 7bf9173, Python 3.14.8, 200x50, 64 KB reads, parse + one render of every row.

| input | MB | pyte MB/s | vterm MB/s |
|--|--|--|--|
| seq 1 1000000 | 7.9 | 1.8 | 32.0 |
| coloured log 10 MB | 10.6 | 4.2 | 77.9 |
