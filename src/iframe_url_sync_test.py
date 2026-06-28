"""E2E test for automatic iframe URL tracking via reverse proxy.

Spins up the real auto-gui server (with a patched state pointing at a child
HTTP server) and uses Playwright to verify that navigating inside a cross-origin
iframe automatically updates the dashboard URL — with ZERO per-app changes
(no bridge script needed, since the proxy makes everything same-origin).
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


def _child_page(label: str) -> bytes:
    """A child app page with NO auto-gui bridge script — pure vanilla HTML."""
    return f"""<!DOCTYPE html>
<html>
<head><title>Child</title></head>
<body>
<h1 id="label">{label}</h1>
<a id="page2-link" href="/page2?x=1">page2</a>
<a id="page3-link" href="/page3#sec">page3</a>
<button id="spa-btn" onclick="spaNav()">SPA nav</button>
<button id="replace-btn" onclick="replaceNav()">Replace nav</button>
<script>
function spaNav() {{
  history.pushState(null, '', '/spa-route');
  document.getElementById('label').textContent = 'SPA';
}}
function replaceNav() {{
  history.replaceState(null, '', '/replace-route');
  document.getElementById('label').textContent = 'Replace';
}}
</script>
</body>
</html>""".encode()


def _start_child_server(port: int) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0].split("#")[0]
            labels = {"/": "Home", "/page2": "Page2", "/page3": "Page3"}
            body = _child_page(labels.get(path, "Other"))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args) -> None:  # type: ignore[override]
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _start_dashboard(port: int):
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
def live_setup():
    child_port = _free_port()
    dashboard_port = _free_port()
    child_url = f"http://127.0.0.1:{child_port}"

    mock_processes = [
        {
            "name": "child-app",
            "port": child_port,
            "is_html": True,
            "visible": True,
            "icon_status": "pending",
            "protocol": "http",
        },
    ]

    patches = [
        patch("server.get_all_visible_items", return_value=mock_processes),
        patch("proxy.get_all_visible_items", return_value=mock_processes),
        patch("server.get_last_scan", return_value="2026-01-01T00:00:00"),
        patch("server.scan_and_update_processes", new_callable=AsyncMock),
        patch("server.background_scanner", new_callable=AsyncMock),
    ]
    for p in patches:
        p.start()

    child_server = _start_child_server(child_port)
    dashboard_server = _start_dashboard(dashboard_port)

    import urllib.request
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{dashboard_port}", timeout=1)
            urllib.request.urlopen(child_url, timeout=1)
            break
        except Exception:
            time.sleep(0.2)

    yield {
        "dashboard": f"http://127.0.0.1:{dashboard_port}",
        "child": child_url,
        "dashboard_port": dashboard_port,
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


def _wait_for_iframe_content(page, selector="#label", timeout=15000):
    """Wait for iframe content to load using FrameLocator API (reliable across all tests)."""
    frame_loc = page.frame_locator(".iframe-container.active iframe")
    frame_loc.locator(selector).wait_for(timeout=timeout)
    return frame_loc


class TestIframeUrlSync:
    def test_click_navigation_updates_dashboard_url(self, live_setup, browser_context):
        """Clicking a link inside a proxied iframe updates the dashboard URL."""
        page = browser_context.new_page()
        page.goto(f"{live_setup['dashboard']}/child-app", wait_until="domcontentloaded")
        frame_loc = _wait_for_iframe_content(page)
        frame_loc.locator("#page2-link").click()

        page.wait_for_url("**/page2*", timeout=10000)
        assert "page2" in page.url
        # Dashboard URL must not contain the proxy prefix — no duplicate
        assert "/proxy/" not in page.url

    def test_click_navigation_preserves_query_string(self, live_setup, browser_context):
        """Query string from a link click round-trips through the dashboard URL."""
        page = browser_context.new_page()
        page.goto(f"{live_setup['dashboard']}/child-app", wait_until="domcontentloaded")
        frame_loc = _wait_for_iframe_content(page)
        # page2 link has href="/page2?x=1"
        frame_loc.locator("#page2-link").click()

        page.wait_for_url("**/page2*", timeout=10000)
        assert "x=1" in page.url
        assert "/proxy/" not in page.url

    def test_spa_navigation_updates_dashboard_url(self, live_setup, browser_context):
        """SPA pushState inside a proxied iframe updates the dashboard URL."""
        page = browser_context.new_page()
        page.goto(f"{live_setup['dashboard']}/child-app", wait_until="domcontentloaded")
        frame_loc = _wait_for_iframe_content(page)
        frame_loc.locator("#spa-btn").click()

        page.wait_for_url("**/spa-route*", timeout=10000)
        assert "spa-route" in page.url
        # Must not contain the proxy app name inside the path (no double prefix)
        assert "/proxy/" not in page.url

    def test_spa_navigation_no_duplicate_proxy_prefix(self, live_setup, browser_context):
        """SPA pushState must not produce /child-app/proxy/child-app/... URLs."""
        page = browser_context.new_page()
        page.goto(f"{live_setup['dashboard']}/child-app", wait_until="domcontentloaded")
        frame_loc = _wait_for_iframe_content(page)
        frame_loc.locator("#spa-btn").click()

        page.wait_for_url("**/spa-route*", timeout=10000)
        # The URL should be /child-app/spa-route, NOT /child-app/proxy/child-app/spa-route
        url_path = page.url.split(f"127.0.0.1:{live_setup['dashboard_port']}")[1]
        assert url_path == "/child-app/spa-route", f"Expected /child-app/spa-route, got {url_path}"

    def test_replace_state_updates_dashboard_url(self, live_setup, browser_context):
        """replaceState inside a proxied iframe updates the dashboard URL."""
        page = browser_context.new_page()
        page.goto(f"{live_setup['dashboard']}/child-app", wait_until="domcontentloaded")
        frame_loc = _wait_for_iframe_content(page)
        frame_loc.locator("#replace-btn").click()

        page.wait_for_url("**/replace-route*", timeout=10000)
        assert "replace-route" in page.url
        assert "/proxy/" not in page.url

    def test_refresh_restores_path(self, live_setup, browser_context):
        """After navigating inside the iframe, reload restores the same path."""
        page = browser_context.new_page()
        page.goto(f"{live_setup['dashboard']}/child-app", wait_until="domcontentloaded")
        frame_loc = _wait_for_iframe_content(page)
        # page3 link has href="/page3#sec" — hash and path both survive
        frame_loc.locator("#page3-link").click()

        page.wait_for_url("**/page3*", timeout=10000)
        assert "/proxy/" not in page.url

        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".iframe-container.active iframe", timeout=10000)

        iframe_src = page.locator(".iframe-container.active iframe").get_attribute("src")
        assert "/page3" in iframe_src
        # Iframe src must not have a duplicate proxy segment
        assert "/proxy/child-app/proxy/" not in iframe_src

    def test_initial_load_url_correct(self, live_setup, browser_context):
        """Loading /{app} keeps the dashboard URL as /{app} — no /proxy/ leaks in."""
        page = browser_context.new_page()
        page.goto(f"{live_setup['dashboard']}/child-app", wait_until="domcontentloaded")
        _wait_for_iframe_content(page)  # wait for iframe to load

        # Give the URL update a moment to stabilise
        time.sleep(1.5)  # longer than LOCATION_POLL_INTERVAL

        url_path = page.url.split(f"127.0.0.1:{live_setup['dashboard_port']}")[1]
        assert "/proxy/" not in url_path, f"Proxy prefix leaked into dashboard URL: {url_path}"
        # URL should be /child-app (possibly with trailing /)
        assert url_path.rstrip("/") == "/child-app", f"Unexpected URL: {url_path}"

    def test_proxy_serves_content(self, live_setup):
        """The proxy route serves the child app's HTML."""
        import urllib.request
        resp = urllib.request.urlopen(
            f"{live_setup['dashboard']}/proxy/child-app/", timeout=5
        )
        body = resp.read().decode()
        assert "Child" in body or "Home" in body
