"""Self-health probe for the auto-gui server.

auto's watcher only restarts a process whose PID is dead. A live process whose
listen socket has vanished, or whose event loop no longer answers, stays up and
the dashboard hangs. This probe runs outside the event loop and exits the
process after repeated failed checks so auto can start a fresh one.
"""
from __future__ import annotations

import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable

# A brief stall must not kill a healthy server. Three misses at this interval
# means the dashboard has been unreachable for about a minute.
CHECK_INTERVAL_SECONDS = 15.0
CHECK_TIMEOUT_SECONDS = 2.0
FAILURES_REQUIRED = 3


def http_ok(port: int, timeout: float) -> bool:
    """Returns True only when this process answers GET /healthz on localhost."""
    url = f"http://127.0.0.1:{port}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200 and response.read(16).startswith(b"ok")
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def watch_port(
    port: int,
    *,
    interval: float,
    timeout: float,
    failures_required: int,
    exit_fn: Callable[[int], None] = os._exit,
    sleep_fn: Callable[[float], None] = time.sleep,
    probe: Callable[[int, float], bool] = http_ok,
) -> None:
    """Exits the process after failures_required consecutive failed probes."""
    misses = 0
    while misses < failures_required:
        sleep_fn(interval)
        if probe(port, timeout):
            misses = 0
        else:
            misses += 1
    print(
        f"[health] port {port} failed {failures_required} checks; "
        "exiting so auto can restart"
    )
    exit_fn(1)


def start_health_watch(port: int) -> threading.Thread:
    """Starts the production probe. os._exit so a wedged loop cannot block it."""
    thread = threading.Thread(
        target=watch_port,
        kwargs={
            "port": port,
            "interval": CHECK_INTERVAL_SECONDS,
            "timeout": CHECK_TIMEOUT_SECONDS,
            "failures_required": FAILURES_REQUIRED,
        },
        name="auto-gui-health",
        daemon=True,
    )
    thread.start()
    return thread
