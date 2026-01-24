"""
HTML checker for auto-gui.
Checks if a port returns HTML content.
"""
import asyncio

import httpx


async def check_port_returns_html(port: int, timeout: float = 5.0) -> bool:
    """
    Checks if the given port returns a usable HTML GUI.

    Makes a GET request to http://localhost:{port}/ and checks:
    1. HTTP status is 200 OK
    2. Content-Type indicates HTML (text/html)
    3. Response body contains actual HTML structure

    Returns True if the port serves a usable HTML GUI, False otherwise.
    """
    url = f"http://localhost:{port}/"

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url)

            # Must be HTTP 200
            if response.status_code != 200:
                return False

            # Must have HTML content type
            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type.lower():
                return False

            # Must contain actual HTML structure
            body = response.text.lower()
            has_html = "<!doctype html" in body or "<html" in body
            return has_html

    except (httpx.RequestError, httpx.HTTPStatusError):
        return False


def check_port_returns_html_sync(port: int, timeout: float = 5.0) -> bool:
    """Synchronous wrapper for check_port_returns_html."""
    return asyncio.run(check_port_returns_html(port, timeout))


async def check_multiple_ports(ports: list[int], timeout: float = 5.0) -> dict[int, bool]:
    """
    Checks multiple ports concurrently.

    Returns a dict mapping port numbers to whether they return HTML.
    """
    tasks = [check_port_returns_html(port, timeout) for port in ports]
    results = await asyncio.gather(*tasks)
    return dict(zip(ports, results))
