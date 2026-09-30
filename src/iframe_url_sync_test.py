"""E2E test for automatic iframe URL tracking via reverse proxy.

Spins up the real auto-gui server (with a patched state pointing at a child
HTTP server) and uses Playwright to verify that navigating inside a proxied
iframe automatically updates the dashboard URL. The iframe is on the other
loopback host so its streams cannot exhaust the dashboard connection pool;
the injected shim reports location changes.
"""
import gzip as gzip_module
import socket
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
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
    first_sse_event = threading.Event()
    sse_disconnected = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0].split("#")[0]
            if path == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-SSE-Test", "streaming")
                self.end_headers()
                try:
                    self.wfile.write(b"data: first\n\n")
                    self.wfile.flush()
                    first_sse_event.set()
                    while True:
                        time.sleep(0.05)
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    sse_disconnected.set()
                return
            if path == "/hold":
                body = (
                    b"<!DOCTYPE html><html><body><h1 id=\"label\">Hold</h1>"
                    b"<script>new EventSource('/events');</script></body></html>"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            labels = {"/": "Home", "/page2": "Page2", "/page3": "Page3"}
            body = _child_page(labels.get(path, "Other"))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args) -> None:  # type: ignore[override]
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.first_sse_event = first_sse_event
    server.sse_disconnected = sse_disconnected
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _start_dashboard(port: int):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root / "src"))
    import uvicorn

    # localhost resolves to ::1 before 127.0.0.1. The iframe uses the other
    # loopback host, so the test server must accept both or the frame never loads.
    sockets = []
    for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        sock.bind((address, port))
        sock.listen(128)
        sockets.append(sock)
    config = uvicorn.Config(
        "server:app",
        log_level="error",
        ws="none",
    )
    server = uvicorn.Server(config)
    server._test_sockets = sockets

    def _run() -> None:
        import asyncio

        asyncio.run(server.serve(sockets=sockets))

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return server, thread


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
    dashboard_server, dashboard_thread = _start_dashboard(dashboard_port)

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
        "child_server": child_server,
    }

    dashboard_server.should_exit = True
    for sock in getattr(dashboard_server, "_test_sockets", ()):
        try:
            sock.close()
        except OSError:
            pass
    dashboard_thread.join(timeout=1)
    # shutdown() waits for in-flight SSE handlers and can pin the suite.
    closer = threading.Thread(target=child_server.shutdown, daemon=True)
    closer.start()
    closer.join(timeout=1)
    try:
        child_server.server_close()
    except OSError:
        pass
    for p in patches:
        p.stop()


@pytest.fixture
def browser_context():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch()
    except Exception:
        shells = sorted(
            Path.home().glob(
                "Library/Caches/ms-playwright/chromium_headless_shell-*"
                "/chrome-headless-shell-mac-arm64/chrome-headless-shell"
            )
        )
        if not shells:
            raise
        browser = pw.chromium.launch(executable_path=str(shells[-1]))
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    yield context
    def _close() -> None:
        try:
            context.close()
            browser.close()
            pw.stop()
        except Exception:
            pass
    closer = threading.Thread(target=_close, daemon=True)
    closer.start()
    closer.join(timeout=2)


def _wait_for_iframe_content(page, selector="#label", timeout=15000):
    """Wait for iframe content to load using FrameLocator API (reliable across all tests)."""
    frame_loc = page.frame_locator(".iframe-container.active iframe")
    frame_loc.locator(selector).wait_for(timeout=timeout)
    return frame_loc


class TestIframeUrlSync:
    def test_port_app_uses_canonical_proxy_directory_url(self, live_setup, browser_context):
        page = browser_context.new_page()
        page.goto(f"{live_setup['dashboard']}/child-app", wait_until="domcontentloaded")
        _wait_for_iframe_content(page)
        iframe_src = page.locator(".iframe-container.active iframe").get_attribute("src")
        assert iframe_src == f"http://localhost:{live_setup['dashboard_port']}/proxy/child-app/"
        assert urlparse(iframe_src).hostname != urlparse(page.url).hostname

    def test_sse_streams_first_byte_with_headers_and_closes_upstream(self, live_setup):
        import httpx

        url = f"{live_setup['dashboard']}/proxy/child-app/events?channel=chat"
        started = time.monotonic()
        with httpx.stream("GET", url, timeout=5) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["cache-control"] == "no-cache"
            assert response.headers["x-sse-test"] == "streaming"
            assert next(response.iter_lines()) == "data: first"
            assert time.monotonic() - started < 2

        assert live_setup["child_server"].first_sse_event.is_set()
        assert live_setup["child_server"].sse_disconnected.wait(2)

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

    def test_leaving_app_closes_upstream_event_stream(self, live_setup, browser_context):
        """Parking a hidden iframe closes the proxied event stream it opened."""
        page = browser_context.new_page()
        page.goto(
            f"{live_setup['dashboard']}/child-app/hold",
            wait_until="domcontentloaded",
        )
        assert live_setup["child_server"].first_sse_event.wait(10)
        page.evaluate("showWelcome()")
        assert live_setup["child_server"].sse_disconnected.wait(5)
        src = page.locator(".iframe-container iframe").get_attribute("src")
        assert src == "about:blank"

    def test_polling_does_not_cache_bust_icons(self, live_setup, browser_context):
        """Rebuilding an unchanged process list must not mint a new icon URL."""
        page = browser_context.new_page()
        page.goto(live_setup["dashboard"], wait_until="domcontentloaded")
        first, second = page.evaluate(
            """() => {
                const item = {name:'child-app', port:1, icon_status:'ready', is_html:true};
                window.CHANGE_VERSION = 3;
                updateProcessList([item]);
                const a = document.querySelector('img.process-icon').getAttribute('src');
                updateProcessList([Object.assign({}, item)]);
                const b = document.querySelector('img.process-icon').getAttribute('src');
                return [a, b];
            }"""
        )
        assert first == second
        assert "v=3" in first
        assert "Date.now" not in first


# ---------------------------------------------------------------------------
# Path-style website fixture and tests
# ---------------------------------------------------------------------------

def _path_site_page(label: str, asset_href: str = "/asset.css") -> bytes:
    """A minimal HTML page at a sub-path that references a root-relative asset."""
    return f"""<!DOCTYPE html>
<html>
<head>
  <title>PathSite</title>
  <link rel="stylesheet" href="{asset_href}">
</head>
<body>
<h1 id="label">{label}</h1>
<a id="inner-link" href="/sub/inner">inner page</a>
</body>
</html>""".encode()


def _start_path_site_server(port: int) -> HTTPServer:
    """HTTP server that:
    - serves HTML at /sub (the configured website path)
    - serves an asset at /asset.css (host-root, NOT at /sub/asset.css)
    - serves HTML at /sub/inner (a sub-page of the site)
    """
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0].split("#")[0]
            if path == "/asset.css":
                body = b"body { color: red; }"
                self.send_response(200)
                self.send_header("Content-Type", "text/css")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/sub":
                body = _path_site_page("Sub Home")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/sub/inner":
                body = _path_site_page("Inner Page")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found")

        def log_message(self, _format: str, *_args) -> None:
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


@pytest.fixture
def path_site_setup():
    """A website item configured at http://127.0.0.1:PORT/sub (path-style)."""
    site_port = _free_port()
    dashboard_port = _free_port()
    site_url = f"http://127.0.0.1:{site_port}/sub"

    mock_items = [
        {
            "name": "path-site",
            "is_website": True,
            "is_html": True,
            "url": site_url,
            "visible": True,
            "icon_status": "pending",
            "port": None,
            "protocol": "http",
        }
    ]

    patches = [
        patch("server.get_all_visible_items", return_value=mock_items),
        patch("proxy.get_all_visible_items", return_value=mock_items),
        patch("server.get_last_scan", return_value="2026-01-01T00:00:00"),
        patch("server.scan_and_update_processes", new_callable=AsyncMock),
        patch("server.background_scanner", new_callable=AsyncMock),
    ]
    for p in patches:
        p.start()

    site_server = _start_path_site_server(site_port)
    dashboard_server, dashboard_thread = _start_dashboard(dashboard_port)

    import urllib.request
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{dashboard_port}", timeout=1)
            urllib.request.urlopen(site_url, timeout=1)
            break
        except Exception:
            time.sleep(0.2)

    yield {
        "dashboard": f"http://127.0.0.1:{dashboard_port}",
        "site": site_url,
        "site_port": site_port,
        "dashboard_port": dashboard_port,
    }

    dashboard_server.should_exit = True
    dashboard_thread.join(timeout=5)
    site_server.shutdown()
    site_server.server_close()
    for p in patches:
        p.stop()


class TestPathStyleWebsite:
    def test_proxy_serves_sub_path(self, path_site_setup):
        """The proxy serves the configured sub-path without doubling it."""
        import urllib.request
        url = f"{path_site_setup['dashboard']}/proxy/path-site/sub"
        resp = urllib.request.urlopen(url, timeout=5)
        body = resp.read().decode()
        assert "Sub Home" in body

    def test_proxy_root_asset_accessible(self, path_site_setup):
        """Root-relative assets are accessible via /proxy/name/asset, not /proxy/name/sub/asset."""
        import urllib.request
        # /asset.css lives at the host root, NOT at /sub/asset.css
        root_asset_url = f"{path_site_setup['dashboard']}/proxy/path-site/asset.css"
        resp = urllib.request.urlopen(root_asset_url, timeout=5)
        assert resp.status == 200
        body = resp.read().decode()
        assert "color" in body  # actual CSS content

    def test_proxy_subpath_asset_404(self, path_site_setup):
        """Confirms /sub/asset.css does NOT exist on the backend (validates our test setup)."""
        import urllib.request
        import urllib.error
        bad_url = f"{path_site_setup['dashboard']}/proxy/path-site/sub/asset.css"
        try:
            urllib.request.urlopen(bad_url, timeout=5)
            assert False, "Expected 404"
        except urllib.error.HTTPError as exc:
            try:
                assert exc.code == 404
            finally:
                exc.close()

    def test_landing_iframe_src_is_sub_path(self, path_site_setup, browser_context):
        """Clicking a path-style website loads the configured sub-path in the iframe."""
        page = browser_context.new_page()
        page.goto(f"{path_site_setup['dashboard']}/path-site", wait_until="domcontentloaded")

        frame_loc = page.frame_locator(".iframe-container.active iframe")
        frame_loc.locator("#label").wait_for(timeout=15000)

        iframe_src = page.locator(".iframe-container.active iframe").get_attribute("src")
        assert iframe_src.endswith("/sub") or "/sub" in iframe_src, f"Expected /sub in iframe src: {iframe_src}"
        assert "Sub Home" in frame_loc.locator("#label").text_content()

    def test_dashboard_url_is_sub_path(self, path_site_setup, browser_context):
        """Dashboard URL for a path-style website includes the configured sub-path."""
        page = browser_context.new_page()
        page.goto(f"{path_site_setup['dashboard']}/path-site", wait_until="domcontentloaded")

        frame_loc = page.frame_locator(".iframe-container.active iframe")
        frame_loc.locator("#label").wait_for(timeout=15000)
        time.sleep(1.5)  # let location sync settle

        url_path = page.url.split(f"127.0.0.1:{path_site_setup['dashboard_port']}")[1]
        assert url_path == "/path-site/sub", f"Expected /path-site/sub, got {url_path}"

    def test_refresh_restores_sub_path(self, path_site_setup, browser_context):
        """After refresh, the iframe loads the configured sub-path (not 404 or root)."""
        page = browser_context.new_page()
        page.goto(f"{path_site_setup['dashboard']}/path-site", wait_until="domcontentloaded")

        frame_loc = page.frame_locator(".iframe-container.active iframe")
        frame_loc.locator("#label").wait_for(timeout=15000)
        time.sleep(1.0)

        # Reload and verify the iframe still shows the sub-path page
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".iframe-container.active iframe", timeout=10000)

        frame_loc2 = page.frame_locator(".iframe-container.active iframe")
        frame_loc2.locator("#label").wait_for(timeout=15000)
        assert "Sub Home" in frame_loc2.locator("#label").text_content()

        iframe_src = page.locator(".iframe-container.active iframe").get_attribute("src")
        assert "/sub" in iframe_src, f"/sub missing from refreshed iframe src: {iframe_src}"
        assert "/sub/sub" not in iframe_src, f"Double sub-path in iframe src: {iframe_src}"


# ---------------------------------------------------------------------------
# Compressed upstream: verify browser can actually render gzip-encoded content
# ---------------------------------------------------------------------------

def _gzip_page(label: str) -> bytes:
    raw = f"""<!DOCTYPE html>
<html>
<head><title>GzipSite</title></head>
<body>
<h1 id="label">{label}</h1>
<p id="body-text">This page was served with gzip Content-Encoding.</p>
</body>
</html>""".encode("utf-8")
    return gzip_module.compress(raw)


def _start_gzip_site_server(port: int) -> HTTPServer:
    """HTTP server that serves every page with Content-Encoding: gzip.

    This exercises the fix for ERR_CONTENT_DECODING_FAILED: if the proxy
    forwards the upstream Content-Encoding header on an already-decoded body,
    the browser fails to render the page entirely.
    """
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0].split("#")[0]
            label = {"/": "Gzip Home", "/page2": "Gzip Page2"}.get(path, "Gzip Other")
            body = _gzip_page(label)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args) -> None:  # noqa: A002
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


@pytest.fixture
def gzip_site_setup():
    """Port-app item whose upstream always sends gzip-compressed HTML."""
    site_port = _free_port()
    dashboard_port = _free_port()

    mock_items = [
        {
            "name": "gzip-app",
            "port": site_port,
            "is_html": True,
            "is_website": False,
            "visible": True,
            "icon_status": "pending",
            "protocol": "http",
        }
    ]

    patches = [
        patch("server.get_all_visible_items", return_value=mock_items),
        patch("proxy.get_all_visible_items", return_value=mock_items),
        patch("server.get_last_scan", return_value="2026-01-01T00:00:00"),
        patch("server.scan_and_update_processes", new_callable=AsyncMock),
        patch("server.background_scanner", new_callable=AsyncMock),
    ]
    for p in patches:
        p.start()

    gzip_server = _start_gzip_site_server(site_port)
    dashboard_server, dashboard_thread = _start_dashboard(dashboard_port)

    import urllib.request
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{dashboard_port}", timeout=1)
            urllib.request.urlopen(f"http://127.0.0.1:{site_port}/", timeout=1)
            break
        except Exception:
            time.sleep(0.2)

    yield {
        "dashboard": f"http://127.0.0.1:{dashboard_port}",
        "site_port": site_port,
        "dashboard_port": dashboard_port,
    }

    dashboard_server.should_exit = True
    dashboard_thread.join(timeout=5)
    gzip_server.shutdown()
    gzip_server.server_close()
    for p in patches:
        p.stop()


class TestCompressedUpstream:
    """Verify that gzip-compressed upstream content renders in the browser.

    The proxy must strip Content-Encoding before forwarding the (already
    decoded) body; otherwise the browser gets ERR_CONTENT_DECODING_FAILED
    and the iframe shows a blank page.
    """

    def test_proxy_strips_content_encoding_header(self, gzip_site_setup):
        """Proxy response must NOT carry Content-Encoding even if upstream sends it."""
        import urllib.request
        resp = urllib.request.urlopen(
            f"{gzip_site_setup['dashboard']}/proxy/gzip-app/", timeout=5
        )
        # urllib auto-decompresses gzip, but only if the header is present;
        # if the proxy correctly strips Content-Encoding the body is plain text.
        body = resp.read().decode("utf-8")
        assert "Gzip Home" in body
        # Response must not carry Content-Encoding
        ce = resp.headers.get("Content-Encoding")
        assert ce is None, f"Content-Encoding leaked to client: {ce}"

    def test_iframe_renders_gzip_content(self, gzip_site_setup, browser_context):
        """Gzip-compressed upstream must render visible text in the iframe."""
        page = browser_context.new_page()
        page.goto(f"{gzip_site_setup['dashboard']}/gzip-app", wait_until="domcontentloaded")
        frame_loc = _wait_for_iframe_content(page, "#label")

        # Must show actual text, not a blank/broken page
        label_text = frame_loc.locator("#label").text_content()
        assert "Gzip Home" in label_text, f"iframe body blank or wrong: {label_text!r}"

        body_text = frame_loc.locator("#body-text").text_content()
        assert len(body_text) > 0, "iframe body-text paragraph is empty"

    def test_iframe_gzip_content_survives_reload(self, gzip_site_setup, browser_context):
        """After a page reload, gzip-compressed content still renders (not ERR_CONTENT_DECODING_FAILED)."""
        page = browser_context.new_page()
        page.goto(f"{gzip_site_setup['dashboard']}/gzip-app", wait_until="domcontentloaded")
        _wait_for_iframe_content(page, "#label")
        time.sleep(0.5)

        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".iframe-container.active iframe", timeout=10000)
        frame_loc2 = page.frame_locator(".iframe-container.active iframe")
        frame_loc2.locator("#label").wait_for(timeout=15000)

        label_text = frame_loc2.locator("#label").text_content()
        assert "Gzip Home" in label_text, f"Post-reload iframe body blank or wrong: {label_text!r}"
