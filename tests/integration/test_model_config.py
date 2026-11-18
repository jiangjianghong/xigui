"""The global model settings are versioned, secret-safe, and hot-reloaded."""

import yaml


def test_model_config_read_hides_api_key(client):
    response = client.get("/admin/model-config")
    assert response.status_code == 200
    body = response.json()
    assert body["config"]["api_key_configured"] is True
    assert "api_key" not in body["config"]
    assert "test-only" not in response.text


def test_model_config_save_is_immediate_and_preserves_key(client, isolated_config):
    before = client.get("/admin/model-config").json()
    cfg = before["config"]
    cfg.pop("api_key_configured")
    cfg.update(model="new-model", temperature=0.35, extra_params={"top_p": 0.8})
    response = client.put("/admin/model-config", json={
        "version": before["version"], "config": cfg, "api_key_action": "keep",
    })
    assert response.status_code == 200
    assert response.json()["config"]["model"] == "new-model"
    assert yaml.safe_load(isolated_config.read_text(encoding="utf-8"))["llm"]["api_key"] == "test-only"

    from app.core.config import get_config
    assert get_config()["llm"]["extra_params"] == {"top_p": 0.8}


def test_model_config_replace_and_clear_secret(client):
    initial = client.get("/admin/model-config").json()
    cfg = initial["config"]
    cfg.pop("api_key_configured")
    replaced = client.put("/admin/model-config", json={
        "version": initial["version"], "config": cfg,
        "api_key_action": "replace", "api_key": "replacement-secret",
    })
    assert replaced.status_code == 200
    assert "replacement-secret" not in replaced.text

    cfg = replaced.json()["config"]
    cleared = client.put("/admin/model-config", json={
        "version": replaced.json()["version"], "config": cfg, "api_key_action": "clear",
    })
    assert cleared.status_code == 200
    assert cleared.json()["config"]["api_key_configured"] is False


def test_model_config_rejects_conflict_and_reserved_extra_params(client):
    first = client.get("/admin/model-config").json()
    second = client.get("/admin/model-config").json()
    cfg = first["config"]
    cfg.pop("api_key_configured")
    cfg["extra_params"] = {"messages": []}
    invalid = client.put("/admin/model-config", json={
        "version": first["version"], "config": cfg, "api_key_action": "keep",
    })
    assert invalid.status_code == 422

    cfg = second["config"]
    cfg.pop("api_key_configured")
    cfg["model"] = "changed"
    changed = client.put("/admin/model-config", json={
        "version": second["version"], "config": cfg, "api_key_action": "keep",
    })
    assert changed.status_code == 200
    stale = client.put("/admin/model-config", json={
        "version": first["version"], "config": cfg, "api_key_action": "keep",
    })
    assert stale.status_code == 409


def test_model_config_requires_admin(client):
    client.post("/auth/logout")
    assert client.get("/admin/model-config").status_code == 401


def test_chat_json_forwards_configured_extra_params(monkeypatch):
    from types import SimpleNamespace

    from app.core import llm_client

    calls = {}

    class Completions:
        def create(self, **kwargs):
            calls["kwargs"] = kwargs
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"match":true}'))],
                usage=None,
            )

    class Client:
        chat = SimpleNamespace(completions=Completions())

    config = {
        "api_key": "key", "api_base": "https://model.invalid/v1", "model": "model-a",
        "max_tokens": 12, "temperature": 0.1, "top_p": 0.7,
        "max_retries": 1, "backend": "vllm", "enable_thinking": False,
        "extra_params": {"top_k": 20, "extra_body": {"chat_template_kwargs": {"custom": True}}},
    }
    monkeypatch.setattr(llm_client, "get_config", lambda: {"llm": config})
    monkeypatch.setattr(llm_client, "_make_client", lambda _cfg: Client())
    monkeypatch.setattr(llm_client, "build_extra_body", lambda _cfg: {"chat_template_kwargs": {"enable_thinking": False}})
    llm_client.chat_json([{"role": "user", "content": "test"}], use_json_mode=False)

    assert calls["kwargs"]["top_p"] == 0.7
    assert calls["kwargs"]["extra_body"]["top_k"] == 20
    assert calls["kwargs"]["extra_body"]["chat_template_kwargs"] == {
        "enable_thinking": False, "custom": True,
    }
