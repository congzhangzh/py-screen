# Speed: py-screen vs screen (2026-11-01 08:03)

- Machine: local desk, 8 CPUs, Python 3.14.8
- screen: Screen version 5.0.2 (build on 2026-11-01 03:06:17), minimal screenrc (status line, 10000 scrollback)
- Both attached to a headless 200x50 pty that only drains output

| Test | screen | py-screen | py-screen / screen |
|---|---|---|---|
| `seq 1 5000000` wall | 14.24 s | 58.42 s | 4.1× |
| `seq` server CPU | 8.28 s | 60.36 s | 7.3× |
| `cat` 50 MB coloured log, wall | 8.32 s | 46.68 s | 5.6× |
| `cat` server CPU | 2.79 s | 48.28 s | 17.3× |
| Key → echo, idle (median / p95) | 0.2 / 0.4 ms | 1.7 / 5.3 ms | |
| Key → echo, other window floods (median / p95) | 1.8 / 2.7 ms | 1.7 / 5.9 ms | |
| Idle CPU, 10 windows, attached | 0.00 % | 0.00 % | |
