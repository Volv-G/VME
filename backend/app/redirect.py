"""Tiny ASGI app that redirects every request to its HTTPS equivalent.

If VME_PUBLIC_HOST is set, the redirect target uses that hostname instead of
echoing the incoming Host header. This is what you want when you have a real
TLS cert that only covers the public domain (e.g. satis2.duckdns.org): a client
that connects to the LAN IP gets redirected to the domain so the cert
validates.
"""

from __future__ import annotations

import os


async def http_to_https_app(scope, receive, send) -> None:
    if scope["type"] != "http":
        if scope["type"] == "lifespan":
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        return

    public_host = os.environ.get("VME_PUBLIC_HOST", "").strip()
    if public_host:
        host = public_host
    else:
        headers = dict(scope.get("headers", []))
        host_bytes = headers.get(b"host", b"localhost")
        host = host_bytes.decode("latin-1").split(":")[0]

    https_port = os.environ.get("VME_HTTPS_PORT", "443").strip() or "443"
    port_suffix = "" if https_port == "443" else f":{https_port}"

    path = scope.get("path", "/")
    qs = scope.get("query_string", b"")
    qs_str = ("?" + qs.decode("latin-1")) if qs else ""
    location = f"https://{host}{port_suffix}{path}{qs_str}"

    await send(
        {
            "type": "http.response.start",
            "status": 301,
            "headers": [
                (b"location", location.encode("latin-1")),
                (b"content-length", b"0"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": b""})
