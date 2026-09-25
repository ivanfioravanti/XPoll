import json
import logging
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
TURNSTILE_ORIGIN = "https://challenges.cloudflare.com"
CSP = "; ".join(
    [
        "default-src 'self'",
        f"script-src 'self' {TURNSTILE_ORIGIN}",
        f"frame-src {TURNSTILE_ORIGIN}",
        "connect-src 'self'",
        "img-src 'self' data:",
        "style-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"

access_logger = logging.getLogger("xpoll.access")


def security_headers(*, hsts: bool) -> list[tuple[str, str]]:
    headers = [
        ("content-security-policy", CSP),
        ("referrer-policy", "no-referrer"),
        ("x-content-type-options", "nosniff"),
        ("x-frame-options", "DENY"),
        ("permissions-policy", PERMISSIONS_POLICY),
        ("cross-origin-opener-policy", "same-origin"),
    ]
    if hsts:
        headers.append(("strict-transport-security", "max-age=31536000"))
    return headers


class SecurityMiddleware:
    """Security headers on every response; same-origin and body-size checks on writes."""

    def __init__(self, app: ASGIApp, *, allowed_origin: str, hsts: bool, max_body: int = 8192):
        self.app = app
        self.allowed_origin = allowed_origin
        self.headers = security_headers(hsts=hsts)
        self.max_body = max_body

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self.headers:
                    headers[name] = value
            await send(message)

        if scope["method"] in SAFE_METHODS:
            await self.app(scope, receive, send_with_headers)
            return

        headers = Headers(scope=scope)
        if not self._same_origin(headers):
            await self._reject(scope, receive, send_with_headers, 403, "cross-origin request")
            return
        declared = headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > self.max_body):
            await self._reject(scope, receive, send_with_headers, 413, "request body too large")
            return

        body = bytearray()
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.max_body:
                await self._reject(scope, receive, send_with_headers, 413, "request body too large")
                return
            more = message.get("more_body", False)

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send_with_headers)

    def _same_origin(self, headers: Headers) -> bool:
        origin = headers.get("origin")
        if origin is not None:
            return origin == self.allowed_origin
        return headers.get("sec-fetch-site") == "same-origin"

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, status: int, detail: str):
        await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)


class AccessLogMiddleware:
    """One JSON line per request with no IPs, cookies, query strings, bodies or tokens."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid.uuid4().hex[:16]
        start = time.perf_counter()
        status = 500
        error = None

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)["x-request-id"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            access_logger.info(
                json.dumps(
                    {
                        "request_id": request_id,
                        "method": scope["method"],
                        "route": _route_template(scope),
                        "status": status,
                        "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                        "error": error,
                    }
                )
            )


def _route_template(scope: Scope) -> str:
    route = scope.get("route")
    if route is not None and hasattr(route, "path"):
        return route.path
    if scope["path"].startswith("/static/"):
        return "/static/*"
    return "<unmatched>"
