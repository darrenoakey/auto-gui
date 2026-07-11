"""Tests for proxy.py helpers and browser URL rewriting."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

from proxy import (
    _HOP_BY_HOP,
    _filter_headers,
    _rewrite_url_attr,
    resolve_backend,
    rewrite_css,
    rewrite_html,
)


def _mock_items(items):
    return patch("proxy.get_all_visible_items", return_value=items)


class TestResolveBackend:
    def test_website_returns_origin_only(self):
        items = [{"name": "myapp", "is_website": True, "url": "http://example.com/sub/path"}]
        with _mock_items(items):
            assert resolve_backend("myapp") == "http://example.com"

    def test_website_with_port_returns_origin_only(self):
        items = [{"name": "myapp", "is_website": True, "url": "http://10.0.0.1:9000/daily-digest"}]
        with _mock_items(items):
            assert resolve_backend("myapp") == "http://10.0.0.1:9000"

    def test_website_with_no_path_returns_origin(self):
        items = [{"name": "myapp", "is_website": True, "url": "http://example.com"}]
        with _mock_items(items):
            assert resolve_backend("myapp") == "http://example.com"

    def test_website_with_trailing_slash_returns_origin(self):
        items = [{"name": "myapp", "is_website": True, "url": "http://example.com/"}]
        with _mock_items(items):
            assert resolve_backend("myapp") == "http://example.com"

    def test_website_empty_url_returns_none(self):
        items = [{"name": "myapp", "is_website": True, "url": ""}]
        with _mock_items(items):
            assert resolve_backend("myapp") is None

    def test_port_app_returns_localhost_url(self):
        items = [{"name": "myapp", "is_website": False, "port": 8080, "protocol": "http"}]
        with _mock_items(items):
            assert resolve_backend("myapp") == "http://localhost:8080"

    def test_port_app_https_protocol(self):
        items = [{"name": "myapp", "is_website": False, "port": 8443, "protocol": "https"}]
        with _mock_items(items):
            assert resolve_backend("myapp") == "https://localhost:8443"

    def test_unknown_name_returns_none(self):
        with _mock_items([]):
            assert resolve_backend("nonexistent") is None

    def test_port_app_missing_port_returns_none(self):
        items = [{"name": "myapp", "is_website": False, "port": None, "protocol": "http"}]
        with _mock_items(items):
            assert resolve_backend("myapp") is None


class TestRewriteUrlAttr:
    def test_root_relative_is_prefixed(self):
        assert _rewrite_url_attr("/css/style.css", "/proxy/app", "http://example.com") == "/proxy/app/css/style.css"

    def test_absolute_same_origin_is_prefixed(self):
        assert _rewrite_url_attr("http://example.com/img/logo.png", "/proxy/app", "http://example.com") == "/proxy/app/img/logo.png"

    def test_absolute_different_origin_unchanged(self):
        assert _rewrite_url_attr("http://cdn.example.com/lib.js", "/proxy/app", "http://example.com") == "http://cdn.example.com/lib.js"

    def test_data_url_unchanged(self):
        url = "data:image/png;base64,abc"
        assert _rewrite_url_attr(url, "/proxy/app", "http://example.com") == url

    def test_hash_only_unchanged(self):
        assert _rewrite_url_attr("#section", "/proxy/app", "http://example.com") == "#section"


class TestRewriteCss:
    def test_root_relative_url_in_css(self):
        css = "body { background: url('/images/bg.png'); }"
        result = rewrite_css(css, "/proxy/app", "http://example.com")
        assert "url('/proxy/app/images/bg.png')" in result

    def test_absolute_same_origin_url_in_css(self):
        css = "body { background: url('http://example.com/images/bg.png'); }"
        result = rewrite_css(css, "/proxy/app", "http://example.com")
        assert "/proxy/app/images/bg.png" in result


class TestContentEncodingStrip:
    """Verify that Content-Encoding is never forwarded to the browser.

    httpx auto-decompresses upstream bodies, so forwarding the original
    Content-Encoding header would cause ERR_CONTENT_DECODING_FAILED.
    """

    def test_content_encoding_in_hop_by_hop(self):
        assert "content-encoding" in _HOP_BY_HOP

    def test_filter_headers_strips_content_encoding_lowercase(self):
        result = _filter_headers(
            {"content-type": "text/html", "content-encoding": "gzip", "x-custom": "foo"}
        )
        assert "content-encoding" not in result
        assert "content-type" in result
        assert "x-custom" in result

    def test_filter_headers_strips_content_encoding_mixedcase(self):
        result = _filter_headers(
            {"Content-Type": "text/html", "Content-Encoding": "br", "X-Custom": "foo"}
        )
        assert "Content-Encoding" not in result
        assert "content-encoding" not in {k.lower(): v for k, v in result.items()}
        assert "Content-Type" in result


@pytest.fixture
def shim_browser_site():
    received_paths = []
    external_paths = []

    class ExternalHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            external_paths.append(self.path)
            body = b'{"source":"external"}'
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            pass

    external_server = ThreadingHTTPServer(("127.0.0.1", 0), ExternalHandler)
    external_origin = f"http://127.0.0.1:{external_server.server_port}"

    class AppHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/proxy/app/":
                origin = f"http://127.0.0.1:{self.server.server_port}"
                app_script = f"""<script>
Promise.all([
  fetch(origin + '/api/fetch?kind=string#fetch-hash').then(r => r.json()),
  new Promise((resolve, reject) => {{
    const request = new XMLHttpRequest();
    request.open('GET', origin + '/api/xhr?kind=xhr#xhr-hash');
    request.onload = () => resolve(JSON.parse(request.responseText));
    request.onerror = reject;
    request.send();
  }}),
  fetch(new Request(origin + '/api/request?kind=request#request-hash')).then(r => r.json()),
  fetch(origin + '/proxy/app/api/already?kind=prefixed#prefixed-hash').then(r => r.json()),
  fetch('/proxy/app?state=ready#active').then(r => r.json()),
  fetch('/proxy/application?kind=sibling#sibling-hash').then(r => r.json()),
  fetch('{external_origin}/api/external?kind=external#external-hash').then(r => r.json())
]).then(results => {{
  document.querySelector('#results').textContent = JSON.stringify(results);
}}).catch(error => {{
  document.querySelector('#results').textContent = 'ERROR:' + error;
}});
</script>"""
                html = rewrite_html(
                    f'<html><head></head><body><div id="results"></div>{app_script}</body></html>',
                    "/proxy/app",
                    origin,
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
                return

            received_paths.append(self.path)
            body = json.dumps({"path": self.path}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            pass

    app_server = ThreadingHTTPServer(("127.0.0.1", 0), AppHandler)
    threads = [
        threading.Thread(target=external_server.serve_forever, daemon=True),
        threading.Thread(target=app_server.serve_forever, daemon=True),
    ]
    for thread in threads:
        thread.start()

    try:
        yield {
            "origin": f"http://127.0.0.1:{app_server.server_port}",
            "received_paths": received_paths,
            "external_paths": external_paths,
        }
    finally:
        app_server.shutdown()
        external_server.shutdown()
        app_server.server_close()
        external_server.server_close()
        for thread in threads:
            thread.join()


class TestShimBrowserRewriting:
    def test_same_origin_absolute_requests_are_proxied_and_external_origin_is_not(
        self, shim_browser_site
    ):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(f"{shim_browser_site['origin']}/proxy/app/")
            page.locator("#results").wait_for(state="visible")
            page.wait_for_function(
                "document.querySelector('#results').textContent.length > 0"
            )
            results = json.loads(page.locator("#results").text_content())
            browser.close()

        assert {result["path"] for result in results[:6]} == {
            "/proxy/app/api/fetch?kind=string",
            "/proxy/app/api/xhr?kind=xhr",
            "/proxy/app/api/request?kind=request",
            "/proxy/app/api/already?kind=prefixed",
            "/proxy/app?state=ready",
            "/proxy/app/proxy/application?kind=sibling",
        }
        assert shim_browser_site["external_paths"] == ["/api/external?kind=external"]
        assert not any(path.startswith("/api/") for path in shim_browser_site["received_paths"])

    def test_same_origin_absolute_history_url_preserves_query_and_hash(self, shim_browser_site):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(f"{shim_browser_site['origin']}/proxy/app/")
            page.evaluate(
                "history.pushState(null, '', location.origin + '/connections?state=ready#active')"
            )
            assert page.url == (
                f"{shim_browser_site['origin']}/proxy/app/connections?state=ready#active"
            )
            browser.close()
