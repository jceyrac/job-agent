"""api/middleware.py — request-body size cap (spec 042, FR-003).

FastAPI/Starlette has no built-in body cap, so a write could stream an unbounded
body. This ASGI middleware rejects bodies above `MAX_BODY_BYTES` with `413` before
routing: `Content-Length` is honoured when present (reject without reading), and a
chunked or absent length is counted as the body is read and rejected the moment it
passes the cap. Pure Starlette — no new dependency.
"""
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 64 * 1024

_413_BODY = b'{"error":"payload_too_large","message":"Request body too large"}'


class RequestSizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        content_length = headers.get(b"content-length")
        if content_length is not None and content_length.isdigit():
            if int(content_length) > self.max_bytes:
                await self._send_413(send)
                return

        body = bytearray()
        too_large = False
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.request":
                if not too_large:
                    body.extend(message.get("body", b""))
                    if len(body) > self.max_bytes:
                        too_large = True
                        body.clear()
                more_body = message.get("more_body", False)
            elif message["type"] == "http.disconnect":
                return

        if too_large:
            await self._send_413(send)
            return

        async def _receive() -> Message:
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, _receive, send)

    async def _send_413(self, send: Send) -> None:
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_413_BODY)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": _413_BODY})
