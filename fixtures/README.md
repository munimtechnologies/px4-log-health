# fixtures: finding real test flights for Flight Review v2's analyzers

PX4's Flight Review v2 ([PX4/flight-review-rs](https://github.com/PX4/flight-review-rs)) says in
`crates/converter/src/diagnostics/CONTRIBUTING.md` that "Positive field data is still needed for motor
commands, connected low batteries, GPS degradation and RC loss". Each of those analyzers wants a
`detects_real_*` test on a real, independently labelled flight log. This folder holds the tools MV2 of
Munim, Inc. uses to look for such logs among public PX4 flight logs he has already downloaded. Nothing has
been offered to the project yet.

| File | What it does |
|---|---|
| `fixscreen.py` 0.1.0 | Screens a folder of `.ulg` files for candidate RC loss, low connected battery, GPS degradation and motor-output drops, restating each analyzer's documented rule (flight-review-rs commit `3600132f`), and adds what a human label needs: airborne or not, flight mode, duration and the firmware's own messages around the event. Byte-identical files are counted once. It is a screen only. The authoritative check is flight-review-rs itself on the chosen files. |
| `ulogmini.py` 0.1.0 | A small reader for PX4 ULog files that needs only the Python standard library. It covers formats (arrays, nested types, padding), info, parameters, subscriptions, data, logged and tagged messages, dropouts, sync-marker recovery and truncated files. It mirrors the part of `pyulog.ULog` these tools use, so it can stand in where pyulog can't be installed (for example, a job with no network). [pyulog](https://github.com/PX4/pyulog) (BSD-3-Clause) remains the reference. |
| `run_lite.py` | Runs `fixscreen.py` with `ulogmini` in place of pyulog, after an inventory of the data folder and ulogmini's tests. It also records the reader's corruption and truncation counts for each file. |
| `test_*.py` | Synthetic tests. Every log is built byte by byte in the test. They use no real logs and no network. |

```
python -m unittest -v test_ulogmini test_fixscreen
python run_lite.py <folder of .ulg files> <out dir>
```

Status (2026-11-13): the synthetic tests pass. A pyulog-based screen run and a no-network `ulogmini` screen run
over the same cached logs are queued. Once both have run, their outputs will be compared file by file before
`ulogmini` is trusted on real logs.

Apache-2.0. Made with AI agents working in the name of MV2, Munim, Inc. (mv2@munimtech.com).
