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


@pytest.mark.parametrize("headers", [
    {},
    XHR,
    {**XHR, "Host": "10.5.0.103:5033", "Origin": "http://59.203.14.205:8133",
     "Sec-Fetch-Site": "cross-site"},
])
@pytest.mark.parametrize("method,path", [
    ("POST", "/compare"), ("GET", "/admin/rules"), ("GET", "/admin/history"),
    ("POST", "/admin/import"), ("GET", "/admin/export"), ("GET", "/docs"),
    ("GET", "/redoc"), ("GET", "/openapi.json"), ("POST", "/admin/reload-config"),
    ("GET", "/admin/access-control"), ("DELETE", "/admin/history"),
])
def test_anonymous_denied_before_business(anonymous_client, method, path, headers):
    response = anonymous_client.request(method, path, headers=headers)
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


def test_public_surface_is_minimal(anonymous_client):
    assert anonymous_client.get("/health").json() == {"status": "ok", "service": "ai_cross_check", "version": "0.1.0"}
    assert anonymous_client.get("/auth/session").json() == {"authenticated": False, "authorized": False}
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
    assert anonymous_client.get("/auth/session").json()["authorized"] is True
    clock[0] += 1700
    assert anonymous_client.get("/admin/rules").status_code == 200
    assert anonymous_client.get("/auth/session").json()["expires_at"] == expires
    clock[0] += 101
    assert anonymous_client.get("/admin/rules").status_code == 401
    assert anonymous_client.get("/auth/session").json() == {"authenticated": False, "authorized": False}
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


@pytest.mark.parametrize("headers", [
    XHR,
    {**XHR, "Host": "10.5.0.103:5033", "Origin": "http://59.203.14.205:8133"},
])
def test_lockout_and_expiry_are_per_source_ip(anonymous_client, monkeypatch, headers):
    clock = [1000.0]
    monkeypatch.setattr(auth.admin_auth, "_clock", lambda: clock[0])
    for attempt in range(5):
        response = anonymous_client.post("/auth/login", json={**LOGIN, "password": "wrong123"}, headers=headers)
        assert response.status_code == (401 if attempt < 4 else 429)
    blocked = anonymous_client.post("/auth/login", json=LOGIN, headers=headers)
    assert blocked.status_code == 429 and blocked.headers["retry-after"] == "300"
    other = TestClient(app, client=("192.0.2.5", 5000))
    assert other.post("/auth/login", json=LOGIN, headers=XHR).status_code == 200
    assert anonymous_client.post("/auth/login", json=LOGIN, headers=headers).status_code == 429
    other.close()
    clock[0] += 301
    assert anonymous_client.post("/auth/login", json=LOGIN, headers=headers).status_code == 200


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://other.example"}, {"Sec-Fetch-Site": "cross-site"}])
def test_login_requires_xhr_header(anonymous_client, headers):
    response = anonymous_client.post("/auth/login", json=LOGIN, headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"] == "登录请求缺少安全请求头"


@pytest.mark.parametrize("headers", [
    {"Host": "10.5.0.103:5033", "Origin": "http://59.203.14.205:8133"},
    {"Origin": "https://testserver", "X-Forwarded-Proto": "https"},
    {"Origin": "https://other.example", "Sec-Fetch-Site": "cross-site"},
])
@pytest.mark.parametrize("valid_password", [True, False])
def test_login_uses_password_without_origin_key_or_ip_allowlist(anonymous_client, headers, valid_password):
    assert anonymous_client.get("/auth/session").json() == {"authenticated": False, "authorized": False}
    body = LOGIN if valid_password else {**LOGIN, "password": "wrong123"}
    response = anonymous_client.post("/auth/login", json=body, headers={**XHR, **headers})
    if valid_password:
        assert response.status_code == 200, response.text
        assert response.json()["authenticated"] is True
        assert auth.admin_auth.validate(response.cookies.get(auth.COOKIE_NAME)) is not None
    else:
        assert response.status_code == 401, response.text
        assert response.json()["detail"] == "用户名或密码错误"
        assert "set-cookie" not in response.headers


@pytest.mark.parametrize("method,path", [
    ("GET", "/ui/login"), ("HEAD", "/ui/login"),
    ("GET", "/ui/auth.js"), ("HEAD", "/ui/auth.js"),
    ("GET", "/ui/auth.css"), ("HEAD", "/ui/auth.css"),
    ("GET", "/auth/session"), ("GET", "/health"),
])
def test_public_login_surface_does_not_require_same_origin(anonymous_client, method, path):
    response = anonymous_client.request(method, path, headers={
        "Host": "10.5.0.103:5033",
        "Origin": "http://59.203.14.205:8133",
        "Sec-Fetch-Site": "cross-site",
    })
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("path", ["/", "/ui", "/ui/"])
def test_cross_site_entry_navigation_still_requires_login(anonymous_client, method, path):
    response = anonymous_client.request(method, path, follow_redirects=False, headers={
        "Origin": "https://other.example", "Sec-Fetch-Site": "cross-site",
    })
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/login"


@pytest.mark.parametrize("headers", [
    {},
    {"Origin": "http://59.203.14.205:8133"},
    {"Sec-Fetch-Site": "cross-site"},
])
def test_admin_mutation_requires_xhr(client, headers):
    client.headers.pop("X-Requested-With")
    for method, path in [("POST", "/compare"), ("DELETE", "/admin/history")]:
        response = client.request(method, path, headers=headers)
        assert response.status_code == 403
        assert response.json()["detail"] == "请求缺少安全请求头，请刷新页面"
        assert client.request(method, path, headers={
            **headers, "X-Requested-With": "fetch",
        }).status_code == 403
    assert client.delete("/admin/history", headers={**headers, **XHR}).status_code == 200


def test_logged_in_page_has_full_access_behind_proxy(anonymous_client):
    update_config(lambda data: data.setdefault("access_control", {}).update(trusted_proxies=["10.5.0.7"]))
    browser = TestClient(app, base_url="http://59.203.14.205:8133", client=("10.5.0.7", 40000), headers={
        **XHR, "Host": "10.5.0.103:5033", "Origin": "http://59.203.14.205:8133",
        "X-Forwarded-For": "192.0.2.50",
    })
    try:
        assert browser.get("/auth/session").json()["authorized"] is False
        assert browser.post("/auth/login", json=LOGIN).status_code == 200
        assert browser.get("/auth/session").json()["authenticated"] is True
        project = {"key": "demo", "name": "测试项目"}
        assert browser.post("/admin/projects", json=project).status_code == 201
        assert browser.put("/admin/projects/demo", json={**project, "name": "已修改"}).status_code == 200
        rule = {"id": "R1", "prompt_template": "{field_a} {field_b}", "project": "demo"}
        assert browser.post("/admin/rules", json=rule).status_code == 201
        assert browser.put("/admin/rules/R1", json={**rule, "name": "已修改"}).status_code == 200
        assert browser.get("/admin/rules").status_code == 200
        exported = browser.get("/admin/export")
        assert exported.status_code == 200
        assert browser.post("/admin/import", files={
            "file": ("rules.yaml", exported.content, "application/x-yaml"),
        }).status_code == 200
        assert browser.post("/compare", json={
            "rule_id": "missing", "field_a": {"value": "1"}, "field_b": {"value": "1"},
        }).status_code == 404  # Business validation, without contacting an LLM.
        data = create(browser)
        assert change(browser, data, "rotate").status_code == 200
        assert browser.delete("/admin/rules/R1").status_code == 200
        assert browser.delete("/admin/projects/demo").status_code == 200
        assert browser.delete("/admin/history").status_code == 200
    finally:
        browser.close()


@pytest.mark.parametrize("cookie", ["missing", "forged", "expired"])
@pytest.mark.parametrize("method,path", [
    ("GET", "/admin/rules"), ("POST", "/compare"),
    ("PUT", "/admin/rules/R1"), ("DELETE", "/admin/history"),
])
def test_invalid_session_and_xhr_cannot_authorize_api(anonymous_client, monkeypatch, cookie, method, path):
    clock = [1000.0]
    monkeypatch.setattr(auth.admin_auth, "_clock", lambda: clock[0])
    if cookie == "expired":
        assert anonymous_client.post("/auth/login", json=LOGIN, headers=XHR).status_code == 200
        clock[0] += 1801
    elif cookie == "forged":
        anonymous_client.cookies.set(auth.COOKIE_NAME, "forged-admin-session")
    response = anonymous_client.request(method, path, headers={
        **XHR, "Origin": "http://59.203.14.205:8133", "Sec-Fetch-Site": "cross-site",
    })
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


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


@pytest.fixture(params=["key", "ip"])
def program(client, request):
    if request.param == "key":
        key = create(client)["key"]
        caller = TestClient(app, headers={"Authorization": f"Bearer {key}"})
    else:
        create(client, "ip_allowlist", network="192.0.2.4")
        caller = TestClient(app, client=("192.0.2.4", 40000))
    try:
        yield caller
    finally:
        caller.close()


@pytest.mark.parametrize("method,path,expected", [
    ("POST", "/admin/rules", 422), ("PUT", "/admin/rules/R1", 422), ("DELETE", "/admin/rules/R1", 404),
    ("POST", "/admin/projects", 422), ("DELETE", "/admin/projects/demo", 404),
    ("DELETE", "/admin/history", 200), ("GET", "/admin/export", 200), ("POST", "/admin/import", 422),
    ("POST", "/admin/reload-config", 200), ("GET", "/admin/access-control", 200),
    ("POST", "/admin/access-control/api_keys", 422), ("GET", "/docs", 200), ("GET", "/redoc", 200),
    ("GET", "/openapi.json", 200), ("GET", "/future-api", 404), ("GET", "/ui/", 200), ("GET", "/ui/app.js", 200),
])
def test_program_has_full_route_access(program, method, path, expected):
    response = program.request(method, path)
    assert response.status_code == expected, response.text


def test_program_can_mutate_rules_projects_and_access_control(program):
    project = {"key": "demo", "name": "测试项目"}
    assert program.post("/admin/projects", json=project).status_code == 201
    assert program.put("/admin/projects/demo", json={**project, "name": "已修改"}).status_code == 200
    rule = {"id": "R1", "prompt_template": "{field_a} {field_b}", "project": "demo"}
    assert program.post("/admin/rules", json=rule).status_code == 201
    assert program.put("/admin/rules/R1", json={**rule, "name": "已修改"}).status_code == 200
    assert program.post("/admin/rules/unassign", json={"rule_ids": ["R1"]}).status_code == 200
    assert program.post("/admin/projects/demo/rules", json={"rule_ids": ["R1"]}).status_code == 200
    exported = program.get("/admin/export")
    assert exported.status_code == 200
    assert program.post("/admin/import", files={"file": ("rules.yaml", exported.content, "application/x-yaml")}).status_code == 200
    assert program.delete("/admin/rules/R1").status_code == 200
    assert program.delete("/admin/projects/demo").status_code == 200
    data = create(program)
    rotated = change(program, data, "rotate")
    assert rotated.status_code == 200
    program.headers["Authorization"] = "Bearer " + rotated.json()["key"]
    data = create(program, "ip_allowlist", network="198.51.100.8")
    assert change(program, data, "update", "ip_allowlist", enabled=False).status_code == 200


def test_program_session_status_and_health_include_full_access(program):
    status = program.get("/auth/session").json()
    assert status["authenticated"] is False
    assert status["authorized"] is True
    assert status["identity"] in {"API Key", "IP 白名单"}
    assert "expires_at" not in status
    assert program.get("/health").json() == {
        "status": "ok", "service": "ai_cross_check", "version": "0.1.0",
        "rules_loaded": 0, "rule_ids": [],
    }


def test_program_authorization_survives_session_expiry(program):
    assert program.post("/auth/login", json=LOGIN, headers=XHR).status_code == 200
    token = program.cookies.get(auth.COOKIE_NAME)
    assert program.delete("/admin/history").status_code == 200
    auth.admin_auth.reset()
    assert auth.admin_auth.validate(token) is None
    assert program.get("/auth/session").json()["authorized"] is True
    assert program.delete("/admin/history").status_code == 200


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Sec-Fetch-Site": "cross-site"},
])
def test_program_credentials_ignore_origin_headers(program, headers):
    assert program.delete("/admin/history", headers=headers).status_code == 200


def test_expired_key_denied(client):
    key = create(client)["key"]
    update_config(lambda data: data["access_control"]["api_keys"][0].update(expires_at="2000-01-01T00:00:00Z"))
    program = TestClient(app, headers={"Authorization": f"Bearer {key}"})
    assert program.get("/admin/rules").status_code == 401
    program.close()


@pytest.mark.parametrize("network,peer", [("192.0.2.4", "192.0.2.4"), ("192.0.2.0/24", "192.0.2.8"),
                                         ("2001:db8::/32", "2001:db8::1"), ("192.0.2.4", "::ffff:192.0.2.4")])
def test_whitelisted_ip_full_access_and_disable(client, network, peer):
    data = create(client, "ip_allowlist", network=network)
    program = TestClient(app, client=(peer, 40000))
    assert program.get("/admin/projects").status_code == 200
    assert program.delete("/admin/history").status_code == 200
    assert change(client, data, "update", "ip_allowlist", enabled=False).status_code == 200
    assert program.get("/admin/projects").status_code == 401
    assert program.delete("/admin/history").status_code == 401
    assert program.get("/auth/session").json() == {"authenticated": False, "authorized": False}
    program.close()


def test_whitelist_is_an_alternative_to_disabled_key(client):
    data = create(client)
    key = data["key"]
    assert change(client, data, "update", enabled=False).status_code == 200
    create(client, "ip_allowlist", network="192.0.2.4")
    program = TestClient(app, client=("192.0.2.4", 1111), headers={"Authorization": f"Bearer {key}"})
    assert program.get("/admin/rules").status_code == 200
    assert program.delete("/admin/history").status_code == 200
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


def test_proxy_headers_do_not_restrict_valid_session(client):
    update_config(lambda data: data.setdefault("access_control", {}).update(trusted_proxies=["10.0.0.1"]))
    program = TestClient(app, client=("10.0.0.1", 5000))
    headers = {**XHR, "Origin": "https://testserver", "X-Forwarded-Proto": "https"}
    assert program.post("/auth/login", headers=headers, json=LOGIN).status_code == 200
    assert program.delete("/admin/history", headers=headers).status_code == 200
    program.close()
    program = TestClient(app, client=("192.0.2.4", 5000))
    assert program.post("/auth/login", headers=headers, json=LOGIN).status_code == 200
    assert program.delete("/admin/history", headers=headers).status_code == 200
    program.close()


def test_uvicorn_rewritten_client_address_is_rejected(anonymous_client):
    with TestClient(app, client=("192.0.2.4", 0)) as program:
        response = program.get("/health", headers={"X-Forwarded-For": "192.0.2.4"})
        assert response.status_code == 503
        assert response.json()["detail"] == "代理配置冲突，请使用 --no-proxy-headers 启动服务"


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


@pytest.mark.parametrize("credential", ["key", "ip"])
def test_future_routes_accept_every_authorized_identity(client, credential):
    if credential == "key":
        headers, peer = {"Authorization": f"Bearer {create(client)['key']}"}, "127.0.0.1"
    else:
        create(client, "ip_allowlist", network="192.0.2.4")
        headers, peer = {}, "192.0.2.4"
    future = FastAPI()
    future.add_middleware(auth.AuthMiddleware)
    @future.get("/admin/rules/new-admin-operation")
    def added_later():
        return {"secret": True}
    @future.post("/future-api")
    def added_later_mutation():
        return {"status": "ok"}
    with TestClient(future, headers=headers, client=(peer, 40000)) as test:
        assert test.get("/admin/rules/new-admin-operation").json() == {"secret": True}
        assert test.post("/future-api").json() == {"status": "ok"}
    with TestClient(future) as anonymous:
        assert anonymous.get("/admin/rules/new-admin-operation").status_code == 401
        assert anonymous.post("/future-api").status_code == 401


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
