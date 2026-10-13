"""Real socket tests for the auto-gui self-health probe."""
import socket
import threading

from health import http_ok, runtime_present, watch_port


def _closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class _HealthServer:
    """Tiny HTTP server that answers only GET /healthz."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self._sock.settimeout(0.1)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = self._sock.accept()
            except socket.timeout:
                continue
            try:
                conn.recv(4096)
                body = b"ok"
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Length: 2\r\n"
                    b"Connection: close\r\n"
                    b"\r\n" + body
                )
            finally:
                conn.close()

    def close(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        self._thread.join(2)
        self._sock.close()


def test_http_ok_reads_a_real_healthz():
    server = _HealthServer()
    try:
        assert http_ok(server.port, timeout=1.0) is True
        assert http_ok(_closed_port(), timeout=0.5) is False
    finally:
        server.close()


def test_watch_exits_only_after_repeated_failures():
    server = _HealthServer()
    exited = threading.Event()

    def exit_fn(code: int) -> None:
        assert code == 1
        exited.set()

    watcher = threading.Thread(
        target=watch_port,
        kwargs={
            "port": server.port,
            "interval": 0.05,
            "timeout": 0.2,
            "failures_required": 2,
            "exit_fn": exit_fn,
        },
        daemon=True,
    )
    watcher.start()
    try:
        # Several successful probes must not exit.
        assert not exited.wait(0.18)
        server.close()
        assert exited.wait(2)
    finally:
        server.close()
        watcher.join(2)


def test_runtime_present_tracks_a_real_stdlib_directory(tmp_path):
    stdlib = tmp_path / "lib" / "python3.x"
    stdlib.mkdir(parents=True)
    (stdlib / "os.py").write_text("")
    assert runtime_present(str(stdlib)) is True
    (stdlib / "os.py").unlink()
    stdlib.rmdir()
    assert runtime_present(str(stdlib)) is False


def test_runtime_present_accepts_this_interpreter():
    assert runtime_present() is True


def test_watch_exits_when_runtime_is_removed_even_if_http_answers(tmp_path):
    server = _HealthServer()
    stdlib = tmp_path / "stdlib"
    stdlib.mkdir()
    (stdlib / "os.py").write_text("")
    exited = threading.Event()

    def exit_fn(code: int) -> None:
        assert code == 1
        exited.set()

    watcher = threading.Thread(
        target=watch_port,
        kwargs={
            "port": server.port,
            "interval": 0.05,
            "timeout": 0.2,
            "failures_required": 2,
            "exit_fn": exit_fn,
            "runtime_ok": lambda: runtime_present(str(stdlib)),
        },
        daemon=True,
    )
    watcher.start()
    try:
        assert not exited.wait(0.18)
        (stdlib / "os.py").unlink()
        assert exited.wait(2)
    finally:
        server.close()
        watcher.join(2)
