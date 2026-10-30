"""Authorization regression tests use real middleware, cookies, hashes and YAML writes."""

from datetime import datetime, timedelta, timezone

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import auth
from app.core.access_control import initialize_auth
from app.core.config import update_config
from app.main import app

XHR = {"X-Requested-With": "XMLHttpRequest"}
LOGIN = {"username": "admin", "password": "TestAdmin123"}


def create(client, kind="api_keys", **values):
    version = client.get("/admin/access-control").json()["version"]
    response = client.post(f"/admin/access-control/{kind}", json={
        "version": version, "operation": "create", "values": {"name": "财务系统", **values},
    })
    assert response.status_code == 200, response.text
    return response.json()


def change(client, data, operation, kind="api_keys", **values):
    return client.post(f"/admin/access-control/{kind}", json={
        "version": data["version"], "operation": operation, "id": data[kind][0]["id"], "values": values,
    })


@pytest.mark.parametrize("method,path", [
    ("POST", "/compare"), ("GET", "/admin/rules"), ("GET", "/admin/history"),
    ("POST", "/admin/import"), ("GET", "/admin/export"), ("GET", "/docs"),
    ("GET", "/redoc"), ("GET", "/openapi.json"), ("POST", "/admin/reload-config"),
    ("GET", "/admin/access-control"), ("DELETE", "/admin/history"),
])
def test_anonymous_denied_before_business(anonymous_client, method, path):
    response = anonymous_client.request(method, path)
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


def test_public_surface_is_minimal(anonymous_client):
    assert anonymous_client.get("/health").json() == {"status": "ok", "service": "ai_cross_check", "version": "0.1.0"}
    assert anonymous_client.get("/auth/session").json() == {"authenticated": False}
    for path in ["/", "/ui/", "/ui/index.html", "/ui/app.js", "/ui/access.js"]:
        response = anonymous_client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/ui/login"
    for path in ["/ui/login", "/ui/auth.js", "/ui/auth.css"]:
        assert anonymous_client.get(path).status_code == 200


@pytest.mark.parametrize("authenticated", [False, True])
def test_login_page_has_canonical_url(anonymous_client, authenticated):
    if authenticated:
        assert anonymous_client.post("/auth/login", json=LOGIN, headers=XHR).status_code == 200
    for path in ("/ui/login.html", "/ui/login/"):
        response = anonymous_client.get(path, follow_redirects=False)
        assert response.status_code == 308
        assert response.headers["location"] == "/ui/login"
    response = anonymous_client.get("/ui/login", follow_redirects=False)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'id="login-form"' in response.text
    assert 'minlength="6" maxlength="12"' in response.text
    assert anonymous_client.head("/ui/login").status_code == 200


@pytest.mark.parametrize("password", ["12345", "1234567890123"])
def test_login_rejects_password_outside_length_range(anonymous_client, password):
    response = anonymous_client.post("/auth/login", json={**LOGIN, "password": password}, headers=XHR)
    assert response.status_code == 422
    assert response.json()["detail"] == "密码长度必须为 6–12 个字符"
    assert password not in response.text


def test_login_cookie_logout_and_fixed_expiry(anonymous_client, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(auth.admin_auth, "_clock", lambda: clock[0])
    response = anonymous_client.post("/auth/login", json=LOGIN, headers=XHR)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "max-age=1800" in cookie
    assert auth.COOKIE_NAME in cookie
    expires = response.json()["expires_at"]
    clock[0] += 1700
    assert anonymous_client.get("/admin/rules").status_code == 200
    assert anonymous_client.get("/auth/session").json()["expires_at"] == expires
    clock[0] += 101
    assert anonymous_client.get("/admin/rules").status_code == 401
    assert anonymous_client.get("/auth/session").json()["authenticated"] is False
    anonymous_client.post("/auth/login", json=LOGIN, headers=XHR)
    token = anonymous_client.cookies.get(auth.COOKIE_NAME)
    assert anonymous_client.post("/auth/logout", headers=XHR).status_code == 200
    assert auth.admin_auth.validate(token) is None


def test_secure_cookie_and_restart_invalidation(anonymous_client):
    update_config(lambda data: data["settings"].update(secure_cookie=True))
    response = anonymous_client.post("/auth/login", json=LOGIN, headers=XHR)
    assert "Secure" in response.headers["set-cookie"]
    token = anonymous_client.cookies.get(auth.COOKIE_NAME)
    assert auth.admin_auth.validate(token)
    auth.admin_auth.reset()
    assert auth.admin_auth.validate(token) is None


def test_credentials_change_invalidates_all_sessions(client):
    update_config(lambda data: data["settings"].update(username="new-admin"))
    assert client.get("/admin/rules").status_code == 401


def test_lockout_and_expiry_are_per_source_ip(anonymous_client, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(auth.admin_auth, "_clock", lambda: clock[0])
    for attempt in range(5):
        response = anonymous_client.post("/auth/login", json={**LOGIN, "password": "wrong123"}, headers=XHR)
        assert response.status_code == (401 if attempt < 4 else 429)
    blocked = anonymous_client.post("/auth/login", json=LOGIN, headers=XHR)
    assert blocked.status_code == 429 and blocked.headers["retry-after"] == "300"
    other = TestClient(app, client=("192.0.2.5", 5000))
    assert other.post("/auth/login", json=LOGIN, headers=XHR).status_code == 200
    assert anonymous_client.post("/auth/login", json=LOGIN, headers=XHR).status_code == 429
    other.close()
    clock[0] += 301
    assert anonymous_client.post("/auth/login", json=LOGIN, headers=XHR).status_code == 200


@pytest.mark.parametrize("headers", [{}, {**XHR, "Origin": "https://evil.example"}, {**XHR, "Sec-Fetch-Site": "cross-site"}])
def test_login_csrf(anonymous_client, headers):
    assert anonymous_client.post("/auth/login", json=LOGIN, headers=headers).status_code == 403


def test_admin_mutation_requires_xhr_and_same_origin(client):
    client.headers.pop("X-Requested-With")
    assert client.delete("/admin/history").status_code == 403
    assert client.delete("/admin/history", headers={**XHR, "Origin": "http://evil.test"}).status_code == 403
    assert client.delete("/admin/history", headers={**XHR, "Origin": "http://testserver"}).status_code == 200


def test_key_hash_only_lifecycle_and_immediate_revocation(client, isolated_config):
    expiry = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    data = create(client, expires_at=expiry)
    key = data["key"]
    assert key.startswith("xj_")
    assert "key_hash" not in str(data)
    assert key not in isolated_config.read_text(encoding="utf-8")
    assert "key" not in client.get("/admin/access-control").json()
    program = TestClient(app, headers={"Authorization": f"Bearer {key}"})
    try:
        assert program.get("/admin/rules").status_code == 200
        assert program.post("/compare", json={"rule_id": "missing", "field_a": {"value": "1"}, "field_b": {"value": "1"}}).status_code == 404
        data = change(client, data, "update", enabled=False).json()
        assert program.get("/admin/rules").status_code == 401
        data = change(client, data, "update", enabled=True).json()
        assert program.get("/admin/rules").status_code == 200
        data = change(client, data, "rotate").json()
        assert data["api_keys"][0]["expires_at"] == expiry.replace("+00:00", "Z")
        assert program.get("/admin/rules").status_code == 401
        program.headers["Authorization"] = "Bearer " + data["key"]
        assert program.get("/admin/rules").status_code == 200
        assert change(client, data, "delete").status_code == 200
        assert program.get("/admin/rules").status_code == 401
    finally:
        program.close()


@pytest.mark.parametrize("method,path", [
    ("POST", "/admin/rules"), ("PUT", "/admin/rules/R1"), ("DELETE", "/admin/rules/R1"),
    ("POST", "/admin/projects"), ("DELETE", "/admin/projects/demo"), ("DELETE", "/admin/history"),
    ("GET", "/admin/export"), ("POST", "/admin/import"), ("POST", "/admin/reload-config"),
    ("GET", "/admin/access-control"), ("POST", "/admin/access-control/api_keys"),
    ("GET", "/docs"), ("GET", "/openapi.json"), ("GET", "/future-api"),
])
def test_key_cannot_administer(client, method, path):
    key = create(client)["key"]
    with TestClient(app, headers={"Authorization": f"Bearer {key}"}) as program:
        assert program.request(method, path).status_code == 403


def test_expired_key_denied(client):
    key = create(client)["key"]
    update_config(lambda data: data["access_control"]["api_keys"][0].update(expires_at="2000-01-01T00:00:00Z"))
    program = TestClient(app, headers={"Authorization": f"Bearer {key}"})
    assert program.get("/admin/rules").status_code == 401
    program.close()


@pytest.mark.parametrize("network,peer", [("192.0.2.4", "192.0.2.4"), ("192.0.2.0/24", "192.0.2.8"),
                                         ("2001:db8::/32", "2001:db8::1"), ("192.0.2.4", "::ffff:192.0.2.4")])
def test_whitelisted_ip_business_only_and_disable(client, network, peer):
    data = create(client, "ip_allowlist", network=network)
    program = TestClient(app, client=(peer, 40000))
    assert program.get("/admin/projects").status_code == 200
    assert program.delete("/admin/history").status_code == 403
    assert change(client, data, "update", "ip_allowlist", enabled=False).status_code == 200
    assert program.get("/admin/projects").status_code == 401
    program.close()


def test_whitelist_is_an_alternative_to_disabled_key(client):
    data = create(client)
    key = data["key"]
    assert change(client, data, "update", enabled=False).status_code == 200
    create(client, "ip_allowlist", network="192.0.2.4")
    program = TestClient(app, client=("192.0.2.4", 1111), headers={"Authorization": f"Bearer {key}"})
    assert program.get("/admin/rules").status_code == 200
    program.close()


@pytest.mark.parametrize("peer,xff,expected", [
    ("198.51.100.2", "192.0.2.4", 401),  # Untrusted socket peer cannot spoof XFF.
    ("10.0.0.1", "192.0.2.4", 200),
    ("10.0.0.1", "192.0.2.4, 10.0.0.2", 200),
    ("10.0.0.1", "192.0.2.4, 198.51.100.2", 401),  # Stop at first untrusted hop.
    ("10.0.0.1", "garbage, 192.0.2.4", 401),
    ("10.0.0.1", "x" * 2049, 401),
])
def test_trusted_proxy_chain(client, peer, xff, expected):
    create(client, "ip_allowlist", network="192.0.2.4")
    update_config(lambda data: data["access_control"].update(trusted_proxies=["10.0.0.0/24"]))
    program = TestClient(app, client=(peer, 5000))
    assert program.get("/admin/rules", headers={"X-Forwarded-For": xff}).status_code == expected
    program.close()


def test_proxy_proto_and_uvicorn_rewrite(client):
    update_config(lambda data: data.setdefault("access_control", {}).update(trusted_proxies=["10.0.0.1"]))
    program = TestClient(app, client=("10.0.0.1", 5000))
    headers = {**XHR, "Origin": "https://testserver", "X-Forwarded-Proto": "https"}
    assert program.post("/auth/login", headers=headers, json=LOGIN).status_code == 200
    program.close()
    program = TestClient(app, client=("192.0.2.4", 0))
    assert program.get("/health", headers={"X-Forwarded-For": "192.0.2.4"}).status_code == 503
    assert program.post("/auth/login", headers=headers, json=LOGIN).status_code == 403
    program.close()


def test_config_conflict_validation_and_no_secret_echo(client, isolated_config):
    old = client.get("/admin/access-control").json()
    create(client)
    raw = isolated_config.read_bytes()
    response = client.post("/admin/access-control/ip_allowlist", json={"version": old["version"], "operation": "create", "values": {"name": "a", "network": "192.0.2.1"}})
    assert response.status_code == 409
    fresh = client.get("/admin/access-control").json()
    for values in [{"name": "a", "network": "not-an-ip"}, {"name": "", "network": "192.0.2.1"}, {"name": "a", "network": "192.0.2.1", "enabled": "false"}]:
        response = client.post("/admin/access-control/ip_allowlist", json={"version": fresh["version"], "operation": "create", "values": values})
        assert response.status_code == 422
        assert isolated_config.read_bytes() == raw


def test_plaintext_migration_and_missing_credentials(isolated_config):
    isolated_config.write_text("# preserve comment\nsettings:\n  password: legacy-pass\nllm:\n  model: keep-me\n", encoding="utf-8")
    initialize_auth()
    data = yaml.safe_load(isolated_config.read_text(encoding="utf-8"))
    assert "password" not in data["settings"]
    assert data["settings"]["password_hash"].startswith("$argon2id$")
    assert "# preserve comment" in isolated_config.read_text(encoding="utf-8")
    assert data["llm"]["model"] == "keep-me"
    assert auth.admin_auth.login("admin", "legacy-pass", "127.0.0.1")
    isolated_config.write_text("settings: {}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="管理员密码未配置"):
        initialize_auth()


def test_invalid_hot_reload_fails_closed(client, isolated_config):
    isolated_config.write_text("settings:\n  password_hash: invalid\n", encoding="utf-8")
    response = client.get("/admin/rules")
    assert response.status_code == 503
    assert "invalid" not in response.text


def test_future_static_route_does_not_inherit_parameter_grant(client):
    key = create(client)["key"]
    future = FastAPI()
    future.add_middleware(auth.AuthMiddleware)
    @future.get("/admin/rules/new-admin-operation")
    def added_later():
        return {"secret": True}
    test = TestClient(future, headers={"Authorization": f"Bearer {key}"})
    assert test.get("/admin/rules/new-admin-operation").status_code == 403
    test.close()


def test_config_lock_timeout_is_retryable(client, monkeypatch):
    from filelock import Timeout
    from app.api import auth as auth_api
    data = client.get('/admin/access-control').json()
    def locked(*args):
        raise Timeout('config.yaml.lock')
    monkeypatch.setattr(auth_api, 'mutate_access_control', locked)
    response = client.post('/admin/access-control/api_keys', json={
        'version': data['version'], 'operation': 'create', 'values': {'name': 'retry'},
    })
    assert response.status_code == 503
    assert '稍后重试' in response.json()['detail']
