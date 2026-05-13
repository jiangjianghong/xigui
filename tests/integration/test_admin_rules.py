"""``/admin/rules`` CRUD 集成测试。

每条测试都把 ``rules_config_path`` 临时指向 tmp_path 下的副本,避免污染仓库里
真实的 ``config/rules.yaml``。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.main import app
from app.core import config as cfg_module
from app.core import rules_io
from app.rules import llm_rule


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_RULES = PROJECT_ROOT / "config" / "rules.yaml"


@pytest.fixture
def isolated_rules(tmp_path, monkeypatch):
    """把 rules.yaml 隔离到 tmp_path,测完原文件零变动。"""
    fake = tmp_path / "rules.yaml"
    shutil.copy(REAL_RULES, fake)

    # 让 _rules_yaml_path / get_rules 都读 fake
    real_get_config = cfg_module.get_config

    def _fake_get_config():
        c = dict(real_get_config())
        c["rules_config_path"] = str(fake)
        return c

    monkeypatch.setattr(cfg_module, "get_config", _fake_get_config)
    # admin_rules 模块通过 from app.core.config import get_config 也要替换
    from app.api import admin_rules as ar
    monkeypatch.setattr(ar, "get_config", _fake_get_config)
    # 顺带清规则缓存,免得 get_rules 读到老的
    cfg_module.reload_rules()
    yield fake
    cfg_module.reload_rules()


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# 读
# ---------------------------------------------------------------------------

def test_list_rules_returns_5(client, isolated_rules):
    resp = client.get("/admin/rules")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 5
    assert [r["id"] for r in body["rules"]] == ["R001", "R002", "R003", "R004", "R005"]


def test_get_single_rule(client, isolated_rules):
    resp = client.get("/admin/rules/R004")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "R004"
    assert body["require_field_name"] is True
    assert "{a_name}" in body["prompt_template"]


def test_get_unknown_rule_returns_404(client, isolated_rules):
    resp = client.get("/admin/rules/RXXX")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 增
# ---------------------------------------------------------------------------

NEW_RULE = {
    "id": "R999",
    "name": "测试规则",
    "description": "集成测试用",
    "expected_field_names": ["X", "Y"],
    "require_field_name": False,
    "system_prompt": "你是测试专家。",
    "prompt_template": "A:{a_value} B:{b_value} JSON:{{\"match\":true,\"reason\":\"\"}}",
}


def test_create_rule(client, isolated_rules):
    resp = client.post("/admin/rules", json=NEW_RULE)
    assert resp.status_code == 201
    body = resp.json()
    assert body["rule_id"] == "R999"
    assert body["rules_loaded"] == 6

    # 再次列表能看到
    listing = client.get("/admin/rules").json()
    assert any(r["id"] == "R999" for r in listing["rules"])

    # 文件里也确实多了
    with isolated_rules.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert any(r["id"] == "R999" for r in data["rules"])


def test_create_duplicate_id_returns_422(client, isolated_rules):
    payload = dict(NEW_RULE, id="R001")
    resp = client.post("/admin/rules", json=payload)
    assert resp.status_code == 422
    assert "已存在" in resp.json()["detail"]


def test_create_missing_prompt_template_returns_422(client, isolated_rules):
    payload = dict(NEW_RULE)
    payload.pop("prompt_template")
    resp = client.post("/admin/rules", json=payload)
    assert resp.status_code == 422  # Pydantic 自身校验


# ---------------------------------------------------------------------------
# 改
# ---------------------------------------------------------------------------

def test_update_rule(client, isolated_rules):
    payload = dict(NEW_RULE, id="R001", name="改后名字",
                   prompt_template="新模板 A:{a_value} B:{b_value}")
    resp = client.put("/admin/rules/R001", json=payload)
    assert resp.status_code == 200
    assert resp.json()["rule_id"] == "R001"

    after = client.get("/admin/rules/R001").json()
    assert after["name"] == "改后名字"
    assert "新模板" in after["prompt_template"]


def test_update_id_mismatch_returns_422(client, isolated_rules):
    payload = dict(NEW_RULE, id="R002")
    resp = client.put("/admin/rules/R001", json=payload)
    assert resp.status_code == 422
    assert "不一致" in resp.json()["detail"]


def test_update_unknown_returns_404(client, isolated_rules):
    payload = dict(NEW_RULE, id="RNONE")
    resp = client.put("/admin/rules/RNONE", json=payload)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 删
# ---------------------------------------------------------------------------

def test_delete_rule(client, isolated_rules):
    resp = client.delete("/admin/rules/R005")
    assert resp.status_code == 200
    assert resp.json()["rules_loaded"] == 4

    after = client.get("/admin/rules/R005")
    assert after.status_code == 404


def test_delete_unknown_returns_404(client, isolated_rules):
    resp = client.delete("/admin/rules/RXXX")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 注释与字段顺序保留(round-trip)
# ---------------------------------------------------------------------------

def test_yaml_comments_preserved_after_write(client, isolated_rules):
    """新增一条规则后,文件顶部的字段说明注释不应消失。"""
    before = isolated_rules.read_text(encoding="utf-8")
    assert "# 规则活配置" in before  # 头部注释存在

    resp = client.post("/admin/rules", json=NEW_RULE)
    assert resp.status_code == 201

    after = isolated_rules.read_text(encoding="utf-8")
    assert "# 规则活配置" in after
    assert "# 字段说明" in after
    # 新规则确实被追加在末尾
    assert "R999" in after


# ---------------------------------------------------------------------------
# 写入后 /compare 立即生效
# ---------------------------------------------------------------------------

def test_create_then_compare_uses_new_rule(client, isolated_rules, monkeypatch):
    """新建一条规则后,直接 POST /compare 应该能用上,无需重启或手动 reload。"""
    captured: dict = {}

    def _fake(messages, **kwargs):
        captured["messages"] = messages
        return ('{"match": true, "reason": "ok"}',
                {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4})

    monkeypatch.setattr(llm_rule, "chat_json", _fake)

    # 新建
    create = client.post("/admin/rules", json=NEW_RULE)
    assert create.status_code == 201

    # 比对
    resp = client.post("/compare", json={
        "rule_id": "R999",
        "field_a": {"value": "v1"},
        "field_b": {"value": "v2"},
    })
    assert resp.status_code == 200
    assert resp.json()["result"] is True

    # system_prompt 也被送进去了
    sys_msg = captured["messages"][0]
    assert sys_msg["role"] == "system"
    assert "测试专家" in sys_msg["content"]
