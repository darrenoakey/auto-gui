"""E2E test for iframe URL propagation into the dashboard address bar.

Spins up two real HTTP servers (the dashboard + a cross-origin child app with
the bridge script) and uses Playwright to verify that when the iframe
navigates, the dashboard URL updates to match — so a page refresh restores
the same screen.
"""
import socket
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _child_html(dashboard_base: str, label: str) -> bytes:
    return f"""<!DOCTYPE html>
<html>
<head><title>Child</title></head>
<body>
<h1 id="label">{label}</h1>
<a id="page2-link" href="/page2?x=1">page2</a>
<a id="page3-link" href="/page3#sec">page3</a>
<script src="{dashboard_base}/static/js/iframe-bridge.js"></script>
</body>
</html>""".encode()


def _start_child_server(port: int, dashboard_base: str) -> HTTPServer:
    """Start a cross-origin child server that embeds the bridge script."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0].split("#")[0]
            labels = {"/": "Home", "/page2": "Page2", "/page3": "Page3"}
            body = _child_html(dashboard_base, labels.get(path, "Other"))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_a):
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _start_dashboard(port: int, child_url: str):
    """Start the real auto-gui server with patched state pointing at child."""
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root / "src"))

    import uvicorn

    config = uvicorn.Config(
        "server:app",
        host="127.0.0.1",
        port=port,
        log_level="error",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server


@pytest.fixture
def live_setup(tmp_path):
    """Start dashboard + child servers, yield URLs, tear down."""
    child_port = _free_port()
    dashboard_port = _free_port()
    dashboard_base = f"http://127.0.0.1:{dashboard_port}"
    child_url = f"http://127.0.0.1:{child_port}"

    mock_processes = [
        {
            "name": "child-app",
            "url": child_url,
            "is_html": True,
            "visible": True,
            "is_website": True,
            "icon_status": "pending",
            "port": "",
            "protocol": "http",
        },
    ]

    patches = [
        patch("server.get_all_visible_items", return_value=mock_processes),
        patch("server.get_last_scan", return_value="2026-01-01T00:00:00"),
        patch("server.scan_and_update_processes", new_callable=AsyncMock),
        patch("server.background_scanner", new_callable=AsyncMock),
    ]
    for p in patches:
        p.start()

    child_server = _start_child_server(child_port, dashboard_base)
    dashboard_server = _start_dashboard(dashboard_port, child_url)

    # Wait for servers to be reachable.
    import urllib.request

    for _ in range(50):
        try:
            urllib.request.urlopen(dashboard_base, timeout=1)
            urllib.request.urlopen(child_url, timeout=1)
            break
        except Exception:
            time.sleep(0.2)

    yield {
        "dashboard": dashboard_base,
        "child": child_url,
    }

    dashboard_server.should_exit = True
    child_server.shutdown()
    for p in patches:
        p.stop()


@pytest.fixture
def browser_context():
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch()
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    yield context
    context.close()
    browser.close()
    pw.stop()


def _child_frame(page):
    """Return the Playwright Frame object for the active child iframe.

    Cross-origin iframes still appear in page.frames; we pick the one whose
    URL matches the child origin.
    """
    for frame in page.frames:
        if frame.url and "127.0.0.1" in frame.url:
            # Skip the dashboard frame (also 127.0.0.1) by checking it is not
            # the top-level page.
            if frame != page.main_frame:
                return frame
    raise RuntimeError("No child iframe found in page.frames")


class TestIframeUrlSync:
    def test_navigation_updates_dashboard_url(self, live_setup, browser_context):
        """Clicking inside a cross-origin iframe updates the dashboard URL."""
        page = browser_context.new_page()
        page.goto(f"{live_setup['dashboard']}/child-app", wait_until="domcontentloaded")

        # Wait for iframe to exist
        page.wait_for_selector(".iframe-container.active iframe", timeout=10000)
        # Wait for the frame to load its content
        frame = _child_frame(page)
        frame.wait_for_selector("#page2-link", timeout=10000)

        frame.click("#page2-link", timeout=10000)

        # Dashboard URL should now include page2
        page.wait_for_url("**/page2*", timeout=10000)
        assert "page2" in page.url

    def test_refresh_restores_path(self, live_setup, browser_context):
        """After navigating inside the iframe, a reload restores the same path."""
        page = browser_context.new_page()
        page.goto(f"{live_setup['dashboard']}/child-app", wait_until="domcontentloaded")
        page.wait_for_selector(".iframe-container.active iframe", timeout=10000)
        frame = _child_frame(page)
        frame.wait_for_selector("#page3-link", timeout=10000)

        frame.click("#page3-link", timeout=10000)
        page.wait_for_url("**/page3*", timeout=10000)

        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".iframe-container.active iframe", timeout=10000)

        iframe_src = (
            page.locator(".iframe-container.active iframe").get_attribute("src")
        )
        assert "/page3" in iframe_src
