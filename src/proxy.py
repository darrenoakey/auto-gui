"""
Transparent reverse proxy for Auto-GUI iframe embedding.

Routes all iframe traffic through Auto-GUI itself (``/proxy/{name}/...``) so
that every embedded app becomes same-origin with the dashboard.  This lets
the parent page read ``iframe.contentWindow.location`` directly — enabling
automatic URL tracking for ALL apps with zero per-app changes.

The proxy:
  * Forwards HTTP requests (all methods) to the backend.
  * Rewrites absolute / root-relative URLs in HTML so navigation stays inside
    the proxy.
  * Injects a small script that monkeypatches ``fetch``, ``XMLHttpRequest``,
    ``history.pushState/replaceState`` and the ``WebSocket`` constructor so
    dynamic (SPA) requests and client-side routing also stay inside the proxy.
  * Rewrites ``Location`` redirect headers.
  * Rewrites ``url()`` references in CSS.
  * Proxies WebSocket upgrades.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import Request, WebSocket
from fastapi.responses import Response

# ---------------------------------------------------------------------------
# Backend lookup
# ---------------------------------------------------------------------------

from state_manager import get_all_visible_items


def _items_by_name() -> dict[str, dict]:
    return {item["name"]: item for item in get_all_visible_items()}


def resolve_backend(name: str) -> Optional[str]:
    """Return the backend base URL (no trailing slash) for *name*, or None.

    For websites the backend is the ORIGIN only (scheme://netloc).  The
    configured URL path is used by the frontend as the default landing
    relative URL, so root-relative assets like /css/style.css resolve
    against the host root rather than being double-prefixed with the
    sub-path.
    """
    item = _items_by_name().get(name)
    if not item:
        return None
    if item.get("is_website"):
        url = (item.get("url") or "").strip()
        if not url:
            return None
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"
    port = item.get("port")
    protocol = item.get("protocol") or "http"
    if not port:
        return None
    return f"{protocol}://localhost:{port}"


# ---------------------------------------------------------------------------
# URL rewriting helpers
# ---------------------------------------------------------------------------

# Hop-by-hop headers that must not be forwarded.
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        # httpx auto-decompresses the upstream body; forwarding the original
        # encoding header would tell the browser to decompress already-plain
        # bytes → net::ERR_CONTENT_DECODING_FAILED.
        "content-encoding",
    }
)


def proxy_prefix(name: str) -> str:
    """The URL prefix under which *name* is proxied (no trailing slash)."""
    return f"/proxy/{name}"


def _rewrite_url_attr(raw_url: str, prefix: str, backend_origin: str) -> str:
    """Rewrite a single URL string found in an HTML attribute.

    * Root-relative (``/path``)  → ``{prefix}/path``
    * Protocol-relative (``//host/path``) with same host → ``{prefix}/path``
    * Absolute (``http://host/path``) with same origin → ``{prefix}/path``
    * Everything else (data:, #, mailto:, different host, relative) → unchanged
    """
    if not raw_url:
        return raw_url
    stripped = raw_url.strip()
    if not stripped or stripped.startswith("#"):
        return raw_url
    if stripped.startswith("data:") or stripped.startswith("mailto:"):
        return raw_url
    if stripped.startswith("javascript:"):
        return raw_url

    # Root-relative
    if stripped.startswith("/"):
        # Protocol-relative (//host/path)
        if stripped.startswith("//"):
            parsed = urlparse("https:" + stripped)
            backend_host = urlparse(backend_origin).netloc
            if parsed.netloc == backend_host:
                return f"{prefix}{parsed.path}{'?' + parsed.query if parsed.query else ''}{'#' + parsed.fragment if parsed.fragment else ''}"
            return raw_url
        parsed = urlparse(stripped)
        return f"{prefix}{parsed.path}{'?' + parsed.query if parsed.query else ''}{'#' + parsed.fragment if parsed.fragment else ''}"

    # Absolute URL
    if "://" in stripped:
        parsed = urlparse(stripped)
        if parsed.scheme + "://" + parsed.netloc == backend_origin:
            return f"{prefix}{parsed.path}{'?' + parsed.query if parsed.query else ''}{'#' + parsed.fragment if parsed.fragment else ''}"
        return raw_url

    # Genuinely relative (foo, ./foo, ../foo) — leave alone, browser resolves
    # against the current proxy URL.
    return raw_url


# Regex to find href/src/action attributes in HTML tags.
_ATTR_RE = re.compile(
    r"""((?:href|src|action|poster|data-src|formaction)\s*=\s*)(["'])(.*?)\2""",
    re.IGNORECASE | re.DOTALL,
)


def rewrite_html(html: str, prefix: str, backend_origin: str) -> str:
    """Rewrite URLs in HTML and inject the proxy-shim script."""

    def _attr_replacer(m: re.Match) -> str:
        attr = m.group(1)
        quote = m.group(2)
        raw_url = m.group(3)
        new_url = _rewrite_url_attr(raw_url, prefix, backend_origin)
        return f"{attr}{quote}{new_url}{quote}"

    html = _ATTR_RE.sub(_attr_replacer, html)

    shim = _build_shim(prefix)
    # Inject as early as possible — before any app script runs.
    head_match = re.search(r"<head[^>]*>", html, re.IGNORECASE)
    if head_match:
        pos = head_match.end()
        html = html[:pos] + shim + html[pos:]
    else:
        html = shim + html
    return html


def _build_shim(prefix: str) -> str:
    """Build the JS shim injected into every proxied HTML page."""
    return (
        f'<script id="__auto_gui_proxy_shim" data-prefix="{prefix}">'
        "(function(){"
        f'var P="{prefix}";'
        # --- history: keep SPA routing inside the proxy ---
        "function prefixed(p){var e=p.search(/[?#]/);"
        "if(e!==-1)p=p.slice(0,e);return p===P||p.indexOf(P+'/')===0;}"
        "function rw(u){"
        "if(typeof u!=='string'||!u)return u;"
        "if(u.charAt(0)==='/'&&u.charAt(1)!=='/'&&!prefixed(u)){"
        f"return P+'/'+(u.charAt(1)==='/'?u.slice(2):u.slice(1));"
        "}"
        "if(u.indexOf('http')===0||u.indexOf('//')===0){"
        "try{var p=new URL(u,location.origin);"
        "if(p.origin===location.origin&&prefixed(p.pathname))return u;"
        "if(p.origin===location.origin){"
        "return P+'/'+(p.pathname.charAt(0)==='/'?p.pathname.slice(1):p.pathname)"
        "+p.search+p.hash;}"
        "return u;}catch(e){return u;}"
        "}"
        "return u;}"
        "var hs=history.pushState,hr=history.replaceState;"
        "history.pushState=function(s,t,u){return hs.call(this,s,t,rw(u));};"
        "history.replaceState=function(s,t,u){return hr.call(this,s,t,rw(u));};"
        # --- fetch ---
        "if(window.fetch){var of=window.fetch;"
        "window.fetch=function(i,init){"
        "if(typeof i==='string')i=rw(i);"
        "else if(i&&i.url)i=new Request(rw(i.url),i);"
        "return of.call(this,i,init);};}"
        # --- XHR ---
        "if(window.XMLHttpRequest){var oo=XMLHttpRequest.prototype.open;"
        "XMLHttpRequest.prototype.open=function(m,u){"
        "arguments[1]=rw(u);return oo.apply(this,arguments);};}"
        # --- WebSocket ---
        "if(window.WebSocket){var OW=window.WebSocket;"
        "window.WebSocket=function(u,protocols){"
        "try{if(typeof u==='string'&&u.charAt(0)==='/'){"
        "u=location.origin.replace('http','ws')+P+'/'+(u.charAt(1)==='/'?u.slice(2):u.slice(1));"
        "}}catch(e){}"
        "return protocols!==undefined?new OW(u,protocols):new OW(u);};"
        "window.WebSocket.prototype=OW.prototype;}"
        # --- Notify parent of navigation (belt-and-suspenders) ---
        # Send app-relative path (strip proxy prefix) so the parent's message
        # handler receives e.g. '/settings' not '/proxy/appname/settings'.
        "function notify(){"
        "try{"
        "var np=location.pathname;"
        "if(np.indexOf(P)===0)np=np.slice(P.length)||'/';"
        "window.parent.postMessage({type:'auto-gui:navigate',path:np+location.search+location.hash},'*');"
        "}catch(e){}}"
        "window.addEventListener('popstate',notify);"
        "window.addEventListener('hashchange',notify);"
        "var ohp=history.pushState,ohr=history.replaceState;"
        "history.pushState=function(){var r=ohp.apply(this,arguments);notify();return r;};"
        "history.replaceState=function(){var r=ohr.apply(this,arguments);notify();return r;};"
        "})();"
        "</script>"
    )


_CSS_URL_RE = re.compile(r"""url\(\s*(['"]?)([^'")]+)\1\s*\)""")


def rewrite_css(css: str, prefix: str, backend_origin: str) -> str:
    """Rewrite ``url()`` references in CSS to stay inside the proxy."""

    def _replacer(m: re.Match) -> str:
        quote = m.group(1)
        raw_url = m.group(2)
        new_url = _rewrite_url_attr(raw_url, prefix, backend_origin)
        return f"url({quote}{new_url}{quote})"

    return _CSS_URL_RE.sub(_replacer, css)


# ---------------------------------------------------------------------------
# HTTP proxy
# ---------------------------------------------------------------------------

# Reusable async client — created lazily on first use.
_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            follow_redirects=False,
            verify=False,  # local dev servers often use self-signed certs
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
    return _client


def _filter_headers(headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


async def proxy_http_request(
    name: str, path: str, request: Request
) -> Response:
    """Forward an HTTP request to the backend and return the rewritten response."""
    backend = resolve_backend(name)
    if backend is None:
        return Response(
            content=f"Unknown process: {name}", status_code=404
        )

    backend_url = f"{backend}/{path}" if path else f"{backend}/"

    # Build forwarded headers
    fwd_headers = _filter_headers(request.headers)
    parsed_backend = urlparse(backend)
    fwd_headers["host"] = parsed_backend.netloc

    body = await request.body()

    client = await _get_client()
    try:
        upstream = await client.request(
            method=request.method,
            url=backend_url,
            headers=fwd_headers,
            content=body,
            params=request.query_params,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        return Response(
            content=f"Backend unreachable: {exc}",
            status_code=502,
        )

    prefix = proxy_prefix(name)
    backend_origin = f"{parsed_backend.scheme}://{parsed_backend.netloc}"

    # Rewrite Location header on redirects
    resp_headers = _filter_headers(upstream.headers)
    # Drop content-length (body may be rewritten) and content-encoding
    # (httpx already decoded; re-sending would cause ERR_CONTENT_DECODING_FAILED).
    resp_headers.pop("content-length", None)
    resp_headers.pop("Content-Length", None)
    resp_headers.pop("content-encoding", None)
    resp_headers.pop("Content-Encoding", None)
    location = resp_headers.get("location") or resp_headers.get("Location")
    if location:
        resp_headers["location"] = _rewrite_url_attr(location, prefix, backend_origin)
        resp_headers.pop("Location", None)

    content_type = upstream.headers.get("content-type", "")
    content = upstream.content

    if "text/html" in content_type:
        content = rewrite_html(
            content.decode("utf-8", errors="replace"),
            prefix,
            backend_origin,
        ).encode("utf-8")
    elif "css" in content_type:
        content = rewrite_css(
            content.decode("utf-8", errors="replace"),
            prefix,
            backend_origin,
        ).encode("utf-8")

    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=content_type or None,
    )


# ---------------------------------------------------------------------------
# WebSocket proxy
# ---------------------------------------------------------------------------

async def proxy_websocket(name: str, path: str, ws: WebSocket) -> None:
    """Proxy a WebSocket connection to the backend."""
    import websockets

    backend = resolve_backend(name)
    if backend is None:
        await ws.close(code=1000)
        return

    parsed = urlparse(backend)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    backend_host = parsed.netloc
    ws_url = f"{ws_scheme}://{backend_host}/{path}"
    if ws.query_params:
        from urllib.parse import urlencode

        ws_url += "?" + urlencode(ws.query_params)

    try:
        async with websockets.connect(
            ws_url,
            additional_headers={"Host": backend_host},
            open_timeout=10,
        ) as upstream_ws:
            await ws.accept()

            async def _client_to_server():
                try:
                    while True:
                        data = await ws.receive()
                        if data.get("type") == "websocket.disconnect":
                            break
                        if "bytes" in data and data["bytes"] is not None:
                            await upstream_ws.send(data["bytes"])
                        elif "text" in data and data["text"] is not None:
                            await upstream_ws.send(data["text"])
                except Exception:
                    pass

            async def _server_to_client():
                try:
                    while True:
                        msg = await upstream_ws.recv()
                        if isinstance(msg, bytes):
                            await ws.send_bytes(msg)
                        else:
                            await ws.send_text(msg)
                except Exception:
                    pass

            import asyncio

            task = asyncio.create_task(_client_to_server())
            try:
                await _server_to_client()
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    except Exception:
        try:
            await ws.close(code=1011)
        except Exception:
            pass
