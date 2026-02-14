"""
HTML checker for auto-gui.
Checks if a port returns HTML content.
"""
import asyncio
import ssl

import httpx


async def check_port_returns_html(port: int, timeout: float = 5.0) -> bool:
    """
    Checks if the given port returns a usable HTML GUI.

    Tries both HTTP and HTTPS on localhost:{port}/ and checks:
    1. HTTP status is 200 OK
    2. Content-Type indicates HTML (text/html)
    3. Response body contains actual HTML structure

    Special case: If HTTPS requires a client certificate, we assume it's a GUI
    and return True (most client-cert-protected services are GUIs).

    Returns True if the port serves a usable HTML GUI, False otherwise.
    """
    # Try HTTP first (most common), then HTTPS
    for scheme in ["http", "https"]:
        url = f"{scheme}://localhost:{port}/"

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                verify=False,  # Self-signed certs are common for localhost
            ) as client:
                response = await client.get(url)

                # Must be HTTP 200
                if response.status_code != 200:
                    continue

                # Must have HTML content type
                content_type = response.headers.get("content-type", "")
                if "text/html" not in content_type.lower():
                    continue

                # Must contain actual HTML structure
                body = response.text.lower()
                has_html = "<!doctype html" in body or "<html" in body
                if has_html:
                    return True

        except ssl.SSLError as e:
            # Client certificate required - assume it's a GUI
            if "CERTIFICATE_REQUIRED" in str(e):
                return True
            continue
        except (httpx.RequestError, httpx.HTTPStatusError):
            continue

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
