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
    assert "{field_0_name}" in body["prompt_template"]


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
    "require_field_name": False,
    "system_prompt": "你是测试专家。",
    "prompt_template": "A:{field_0} B:{field_1} JSON:{{\"match\":true,\"reason\":\"\"}}",
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
                   prompt_template="新模板 A:{field_0} B:{field_1}")
    resp = client.put("/admin/rules/R001", json=payload)
    assert resp.status_code == 200
    assert resp.json()["rule_id"] == "R001"

    after = client.get("/admin/rules/R001").json()
    assert after["name"] == "改后名字"
    assert "新模板" in after["prompt_template"]


def test_update_id_clash_returns_422(client, isolated_rules):
    """PUT 时 body.id 改成另一条已存在规则的 id → 422 防覆盖。"""
    payload = dict(NEW_RULE, id="R002")
    resp = client.put("/admin/rules/R001", json=payload)
    assert resp.status_code == 422
    assert "已被占用" in resp.json()["detail"]


def test_update_with_rename(client, isolated_rules):
    """PUT 时 body.id 改成一个全新 id → rename 成功。"""
    payload = dict(NEW_RULE, id="R099", name="重命名后")
    resp = client.put("/admin/rules/R001", json=payload)
    assert resp.status_code == 200
    assert resp.json()["rule_id"] == "R099"

    assert client.get("/admin/rules/R001").status_code == 404
    after = client.get("/admin/rules/R099").json()
    assert after["name"] == "重命名后"


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
        "fields": [{"value": "v1"}, {"value": "v2"}],
    })
    assert resp.status_code == 200
    assert resp.json()["result"] is True

    # system_prompt 也被送进去了
    sys_msg = captured["messages"][0]
    assert sys_msg["role"] == "system"
    assert "测试专家" in sys_msg["content"]


# ---------------------------------------------------------------------------
# field_defs 持久化
# ---------------------------------------------------------------------------

def test_create_rule_with_field_defs(client, isolated_rules):
    """带 field_defs 创建 → 读取/落盘往返一致。"""
    payload = dict(NEW_RULE, field_defs=["申报金额", "批复金额", "合同金额"])
    resp = client.post("/admin/rules", json=payload)
    assert resp.status_code == 201

    body = client.get("/admin/rules/R999").json()
    assert body["field_defs"] == ["申报金额", "批复金额", "合同金额"]

    # yaml 文件里也确实写进去了
    with isolated_rules.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    saved = next(r for r in data["rules"] if r["id"] == "R999")
    assert saved["field_defs"] == ["申报金额", "批复金额", "合同金额"]


def test_create_rule_without_field_defs_omits_key(client, isolated_rules):
    """不带 field_defs 创建 → yaml 里不应出现 field_defs: null 的脏键。"""
    resp = client.post("/admin/rules", json=NEW_RULE)
    assert resp.status_code == 201

    body = client.get("/admin/rules/R999").json()
    assert "field_defs" not in body


def test_update_rule_field_defs(client, isolated_rules):
    """PUT 可以更新 field_defs。"""
    create = client.post(
        "/admin/rules", json=dict(NEW_RULE, field_defs=["甲", "乙"])
    )
    assert create.status_code == 201

    payload = dict(NEW_RULE, field_defs=["甲", "乙", "丙"])
    resp = client.put("/admin/rules/R999", json=payload)
    assert resp.status_code == 200

    after = client.get("/admin/rules/R999").json()
    assert after["field_defs"] == ["甲", "乙", "丙"]
