"""Local-first deployment policy and response hardening."""
import os
import re
from urllib.parse import urlsplit

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def positive_int(name, default):
    value = os.environ.get(name, str(default))
    if not value.isdecimal() or int(value) < 1:
        raise RuntimeError(f"{name} 必须是正整数")
    return int(value)


def public_origin():
    value = os.environ.get("YUXU_PUBLIC_ORIGIN", "").rstrip("/")
    if not value:
        return ""
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment):
        raise RuntimeError("YUXU_PUBLIC_ORIGIN 必须是完整的 HTTPS 网站源，例如 https://videos.example.com")
    return value


def allowed_hosts():
    origin = public_origin()
    return LOCAL_HOSTS | ({urlsplit(origin).hostname} if origin else set())


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app
        public_origin()  # Invalid deployment configuration fails at startup.

    async def __call__(self, scope, receive, send):
        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "same-origin"
                headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                headers["Content-Security-Policy"] = (
                    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                    "img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self'; "
                    "connect-src 'self'; object-src 'none'; frame-ancestors 'none'; "
                    "base-uri 'self'; form-action 'self'"
                )
                if scope.get("scheme") == "https" and public_origin():
                    headers["Strict-Transport-Security"] = "max-age=31536000"
            await send(message)
        if scope["type"] == "http" and scope.get("method") == "POST" and re.fullmatch(
                r"(?:/s/[0-9a-f]{32})?/api/auth/.*", scope["path"]):
            # Buffer only the small public authentication body, before JSON parsing.
            maximum, body = 16384, bytearray()
            headers = dict(scope.get("headers", []))
            length = headers.get(b"content-length", b"0")
            if not length.isdigit() or int(length) > maximum:
                await JSONResponse({"detail": "登录请求超过允许大小"}, status_code=413)(scope, receive, secure_send)
                return
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > maximum:
                    await JSONResponse({"detail": "登录请求超过允许大小"}, status_code=413)(scope, receive, secure_send)
                    return
                if not message.get("more_body", False):
                    break
            original_receive, sent = receive, False
            async def replay():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await original_receive()
            receive = replay
        await self.app(scope, receive, secure_send)
