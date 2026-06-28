"""Unit tests for proxy.py helper functions."""
from unittest.mock import patch

from proxy import resolve_backend, _rewrite_url_attr, rewrite_css


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
