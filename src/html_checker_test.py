"""Tests for html_checker module."""
import ssl

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from html_checker import (
    check_multiple_ports,
    check_port_returns_html,
    check_port_returns_html_sync,
)


def make_response(status_code: int, content_type: str, body: str = ""):
    """Helper to create mock response."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.headers = {"content-type": content_type}
    mock.text = body
    return mock


@pytest.mark.asyncio
async def test_check_port_returns_html_true():
    """Test that valid HTML GUI returns True."""
    mock_response = make_response(
        200,
        "text/html; charset=utf-8",
        "<!DOCTYPE html><html><body>Hello</body></html>",
    )

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("html_checker.httpx.AsyncClient", return_value=mock_client):
        result = await check_port_returns_html(8080)
        assert result is True


@pytest.mark.asyncio
async def test_check_port_returns_html_false_for_json():
    """Test that JSON content-type returns False."""
    mock_response = make_response(200, "application/json", '{"key": "value"}')

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("html_checker.httpx.AsyncClient", return_value=mock_client):
        result = await check_port_returns_html(8080)
        assert result is False


@pytest.mark.asyncio
async def test_check_port_returns_html_false_on_error():
    """Test that connection errors return False."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("html_checker.httpx.AsyncClient", return_value=mock_client):
        result = await check_port_returns_html(9999)
        assert result is False


@pytest.mark.asyncio
async def test_check_port_returns_html_false_for_404():
    """Test that 404 errors return False even with HTML content-type."""
    mock_response = make_response(
        404,
        "text/html; charset=utf-8",
        "<!DOCTYPE html><html><body>Not Found</body></html>",
    )

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("html_checker.httpx.AsyncClient", return_value=mock_client):
        result = await check_port_returns_html(8080)
        assert result is False


@pytest.mark.asyncio
async def test_check_port_returns_html_false_for_501():
    """Test that 501 errors return False even with HTML content-type."""
    mock_response = make_response(
        501,
        "text/html; charset=utf-8",
        "<html><body>Not Implemented</body></html>",
    )

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("html_checker.httpx.AsyncClient", return_value=mock_client):
        result = await check_port_returns_html(8080)
        assert result is False


@pytest.mark.asyncio
async def test_check_port_returns_html_false_for_non_html_body():
    """Test that non-HTML body returns False even with HTML content-type."""
    mock_response = make_response(
        200,
        "text/html",
        "Just some plain text with no HTML structure",
    )

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("html_checker.httpx.AsyncClient", return_value=mock_client):
        result = await check_port_returns_html(8080)
        assert result is False


@pytest.mark.asyncio
async def test_check_port_returns_html_case_insensitive():
    """Test that content-type check is case insensitive."""
    mock_response = make_response(
        200,
        "TEXT/HTML",
        "<!DOCTYPE html><html><body>Test</body></html>",
    )

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("html_checker.httpx.AsyncClient", return_value=mock_client):
        result = await check_port_returns_html(8080)
        assert result is True


def test_check_port_returns_html_sync():
    """Test the sync wrapper."""
    mock_response = make_response(
        200,
        "text/html",
        "<html><head></head><body>Hello</body></html>",
    )

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("html_checker.httpx.AsyncClient", return_value=mock_client):
        result = check_port_returns_html_sync(8080)
        assert result is True


@pytest.mark.asyncio
async def test_check_port_returns_html_https_fallback():
    """Test that HTTPS is tried when HTTP fails."""
    call_count = 0

    async def mock_get(url: str):
        nonlocal call_count
        call_count += 1

        # First call is HTTP (returns 400)
        if "http://" in url:
            return make_response(400, "text/html", "Bad Request")
        # Second call is HTTPS (returns 200 with HTML)
        elif "https://" in url:
            return make_response(
                200,
                "text/html",
                "<!DOCTYPE html><html><body>Secure</body></html>",
            )

    mock_client = AsyncMock()
    mock_client.get = mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("html_checker.httpx.AsyncClient", return_value=mock_client):
        result = await check_port_returns_html(8900)
        assert result is True
        assert call_count == 2  # Tried both HTTP and HTTPS


@pytest.mark.asyncio
async def test_check_port_returns_html_https_only():
    """Test that HTTPS works when HTTP throws connection error."""
    async def mock_get(url: str):
        # HTTP fails with connection error
        if "http://" in url:
            raise httpx.ConnectError("Connection refused")
        # HTTPS works
        elif "https://" in url:
            return make_response(
                200,
                "text/html; charset=utf-8",
                "<html><head><title>Secure Site</title></head></html>",
            )

    mock_client = AsyncMock()
    mock_client.get = mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("html_checker.httpx.AsyncClient", return_value=mock_client):
        result = await check_port_returns_html(8443)
        assert result is True


@pytest.mark.asyncio
async def test_check_port_client_certificate_required_returns_true():
    """Test that HTTPS requiring client certificate is assumed to be a GUI."""
    async def mock_get(url: str):
        # HTTP fails
        if "http://" in url:
            return make_response(400, "text/plain", "Bad Request")
        # HTTPS requires client certificate
        elif "https://" in url:
            raise ssl.SSLError("[SSL: TLSV13_ALERT_CERTIFICATE_REQUIRED] tlsv13 alert certificate required")

    mock_client = AsyncMock()
    mock_client.get = mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("html_checker.httpx.AsyncClient", return_value=mock_client):
        result = await check_port_returns_html(8900)
        assert result is True


@pytest.mark.asyncio
async def test_check_multiple_ports():
    """Test checking multiple ports concurrently."""
    responses = {
        8080: make_response(200, "text/html", "<!DOCTYPE html><html></html>"),
        9000: make_response(200, "application/json", '{}'),
        3000: make_response(200, "text/html; charset=utf-8", "<html><body></body></html>"),
    }

    async def mock_get(url: str):
        port = int(url.split(":")[2].rstrip("/"))
        return responses[port]

    mock_client = AsyncMock()
    mock_client.get = mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("html_checker.httpx.AsyncClient", return_value=mock_client):
        result = await check_multiple_ports([8080, 9000, 3000])
        assert result == {8080: True, 9000: False, 3000: True}
