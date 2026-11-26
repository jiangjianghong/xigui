"""Single-worker sessions and equivalent session, API Key and IP authorization."""

from __future__ import annotations

import hashlib
import ipaddress
import math
import secrets
import threading
import time
import yaml
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from starlette.datastructures import MutableHeaders
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from app.core.config import get_config
from app.schemas.security import (
    PASSWORD_LENGTH_MESSAGE, PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, validate_document,
)

COOKIE_NAME = "xigui_session"  # Do not collide with the sibling xijuan service on the same host.
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC_FILES = {"/ui/login", "/ui/login.html", "/ui/auth.js", "/ui/auth.css"}


class LoginError(Exception):
    def __init__(self, status: int, detail: str, retry_after: int | None = None):
        self.status, self.detail, self.retry_after = status, detail, retry_after


class AdminAuth:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._login_lock = threading.Lock()
        self._hasher = PasswordHasher()
        self._credentials = None
        self._sessions: dict[str, dict] = {}
        self._failures: dict[str, tuple[int, float]] = {}

    def reset(self):
        with self._lock:
            self._sessions.clear()
            self._failures.clear()
            self._credentials = None

    def _settings(self):
        settings = validate_document(get_config()).settings
        identity = (settings.username, settings.password_hash)
        if identity != self._credentials:
            self._sessions.clear()
            self._credentials = identity
        return settings

    def _prune(self):
        now = self._clock()
        self._sessions = {key: item for key, item in self._sessions.items() if item["deadline"] > now}
        self._failures = {key: item for key, item in self._failures.items() if item[1] > now}

    def validate(self, token: str | None) -> dict | None:
        with self._lock:
            self._settings()
            self._prune()
            if not token or len(token) > 256:
                return None
            item = self._sessions.get(hashlib.sha256(token.encode()).hexdigest())
            return dict(item) if item else None

    def login(self, username: str, password: str, ip: str, old_token: str | None = None) -> tuple[str, dict, bool]:
        if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
            raise LoginError(422, PASSWORD_LENGTH_MESSAGE)
        # Bound Argon2 work without holding the session lock or queueing all of
        # Starlette's workers. Session checks remain responsive during login.
        if not self._login_lock.acquire(blocking=False):
            raise LoginError(429, "登录服务繁忙，请稍后重试", 1)
        try:
            with self._lock:
                settings = self._settings()
                generation = self._credentials
                self._prune()
                if not settings.password_hash:
                    raise LoginError(503, "管理员尚未初始化，请在服务器运行管理员密码初始化命令")
                attempts, until = self._failures.get(ip, (0, 0))
                if attempts >= 5:
                    raise LoginError(429, "登录失败次数过多，请稍后重试", max(1, math.ceil(until - self._clock())))
                if ip not in self._failures and len(self._failures) >= 10000:
                    raise LoginError(429, "登录服务繁忙，请稍后重试", 300)
            try:
                valid_password = self._hasher.verify(settings.password_hash, password)
            except (VerificationError, InvalidHashError):
                valid_password = False
            with self._lock:
                settings = self._settings()
                if self._credentials != generation:
                    raise LoginError(401, "管理员配置已变化，请重新登录")
                if not (valid_password and secrets.compare_digest(username.encode(), settings.username.encode())):
                    attempts += 1
                    self._failures[ip] = (attempts, self._clock() + 300)
                    if attempts >= 5:
                        raise LoginError(429, "连续登录失败 5 次，已锁定 5 分钟", 300)
                    raise LoginError(401, "用户名或密码错误")
                self._failures.pop(ip, None)
                if old_token:
                    self._sessions.pop(hashlib.sha256(old_token.encode()).hexdigest(), None)
                if len(self._sessions) >= 1000:
                    self._sessions.pop(next(iter(self._sessions)))
                token = secrets.token_urlsafe(32)
                seconds = settings.session_minutes * 60
                session = {
                    "username": settings.username,
                    "deadline": self._clock() + seconds,
                    "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(),
                    "max_age": seconds,
                }
                self._sessions[hashlib.sha256(token.encode()).hexdigest()] = session
                return token, dict(session), settings.secure_cookie
        finally:
            self._login_lock.release()

    def logout(self, token: str | None):
        with self._lock:
            if token:
                self._sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)


admin_auth = AdminAuth()


def address(value: str):
    try:
        if "%" in value:
            return None
        parsed = ipaddress.ip_address(value)
        return getattr(parsed, "ipv4_mapped", None) or parsed
    except ValueError:
        return None


def in_networks(value: str, networks) -> bool:
    parsed = address(value)
    return parsed is not None and any(parsed in ipaddress.ip_network(net) for net in networks)


def client_ip(request: Request, trusted: list[str]) -> str:
    peer = request.client.host if request.client else "unknown"
    if not in_networks(peer, trusted):
        return peer
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return peer
    if len(forwarded) > 2048:
        return "unknown"
    chain = [part.strip() for part in forwarded.split(",")]
    if any(address(part) is None for part in chain):
        return "unknown"
    for candidate in reversed(chain):
        peer = candidate
        if not in_networks(peer, trusted):
            break
    return peer


def api_key_identity(header: str | None, keys) -> str | None:
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.startswith("xj_") or len(token) > 256:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    for key in keys:
        if key.enabled and (key.expires_at is None or key.expires_at > now) and secrets.compare_digest(key.key_hash, digest):
            return key.id
    return None


class AuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4403})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)

        async def secured_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "same-origin"
            await send(message)

        async def reject(status, detail, **kwargs):
            await JSONResponse({"detail": detail}, status_code=status, **kwargs)(scope, receive, secured_send)

        try:
            def authenticate():
                return validate_document(get_config()), admin_auth.validate(request.cookies.get(COOKIE_NAME))
            cfg, session = await run_in_threadpool(authenticate)
        except (ValueError, OSError, yaml.YAMLError):
            # Never fall back to stale credentials or echo secrets from validation errors.
            await reject(503, "服务配置无效，请管理员检查配置文件")
            return
        if request.client and request.client.port == 0 and request.headers.get("x-forwarded-for"):
            await reject(503, "代理配置冲突，请使用 --no-proxy-headers 启动服务")
            return
        trusted = cfg.access_control.trusted_proxies
        state = scope.setdefault("state", {})
        state["client_ip"] = client_ip(request, trusted)
        key_id = api_key_identity(request.headers.get("authorization"), cfg.access_control.api_keys)
        allowed_ip = in_networks(state["client_ip"], (item.network for item in cfg.access_control.ip_allowlist if item.enabled))

        path = request.url.path
        canonical = path.rstrip("/") or "/"
        method = request.method
        public = method in {"GET", "HEAD"} and canonical in PUBLIC_FILES | {"/health", "/auth/session"}
        if canonical == "/auth/login" and method == "POST":
            if request.headers.get("x-requested-with") != "XMLHttpRequest":
                await reject(403, "登录请求缺少安全请求头")
                return
            public = True
        # A valid session, API Key or allowlisted IP authorizes every route.
        # Do not compare browser Origin with an upstream Host/scheme that a
        # reverse proxy may rewrite. Cookie-only mutations still require XHR;
        # the header alone never authenticates a caller. Independent Key/IP
        # credentials also work with an expired or invalid session cookie.
        state["auth_identity"] = (f"key:{key_id}" if key_id else
                                  f"ip:{state['client_ip']}" if allowed_ip else
                                  "admin" if session else None)
        if not public and not key_id and not allowed_ip:
            if session:
                if method not in SAFE_METHODS and request.headers.get("x-requested-with") != "XMLHttpRequest":
                    await reject(403, "请求缺少安全请求头，请刷新页面")
                    return
            elif canonical == "/" or canonical == "/ui" or path.startswith("/ui/"):
                await RedirectResponse("/ui/login", status_code=303)(scope, receive, secured_send)
                return
            else:
                await reject(401, "请登录、提供有效 API Key 或使用白名单 IP",
                             headers={"WWW-Authenticate": "Bearer"})
                return
        await self.app(scope, receive, secured_send)
