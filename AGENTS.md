# Auto-GUI

Operating notes live in `claude.md`. Production is `auto -q restart auto-gui` from this checkout (`./run serve`, port 2000). Do not start a second server by hand.

A live process whose listen socket has vanished is not restarted by auto. `src/health.py` probes `/healthz` and exits so watch can respawn it. Keep `/healthz` free of state reads and process scans.

## Operations

The first `./run` after a requirements change or a Python upgrade rebuilds `.venv` and pip-installs everything (playwright included). That can take several minutes: agents must start it detached and sleep, never hold a turn on it.
