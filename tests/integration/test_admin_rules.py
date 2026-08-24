"""``/admin/rules`` CRUD 集成测试。数据源已从 yaml 切到 DB。

夹具见 tests/conftest.py:SQLite in-memory + 依赖覆盖,不碰真实 MySQL。
"""

from __future__ import annotations

import pytest

from app.db import repo_projects, repo_rules
from app.rules import llm_rule

NEW_RULE = {
    "id": "R999",
    "name": "测试规则",
    "description": "集成测试用",
    "require_field_name": False,
    "system_prompt": "你是测试专家。",
    "prompt_template": 'A:{field_a} B:{field_b} JSON:{{"match":true,"reason":""}}',
}


@pytest.fixture
def seeded(db):
    """预置 5 条规则 R001-R005，R004 带 field_defs。"""
    for i in range(1, 6):
        data = {
            "id": f"R{i:03d}",
            "name": f"规则{i}",
            "prompt_template": "A:{field_a} B:{field_b}",
        }
        if i == 4:
            data["field_defs"] = ["danwei_shenbao", "danwei_pifu"]
            data["require_field_name"] = True
            data["prompt_template"] = "{danwei_shenbao_name} {danwei_pifu}"
        repo_rules.create(db, data)
    db.commit()


# ---------------------------------------------------------------------------
# 读
# ---------------------------------------------------------------------------

def test_list_rules_returns_5(client, seeded):
    body = client.get("/admin/rules").json()
    assert body["count"] == 5
    assert [r["id"] for r in body["rules"]] == [
        "R001", "R002", "R003", "R004", "R005"
    ]


def test_get_single_rule(client, seeded):
    body = client.get("/admin/rules/R004").json()
    assert body["id"] == "R004"
    assert body["require_field_name"] is True
    assert body["field_defs"] == ["danwei_shenbao", "danwei_pifu"]


def test_get_unknown_rule_returns_404(client, seeded):
    assert client.get("/admin/rules/RXXX").status_code == 404


def test_every_rule_carries_project_field(client, seeded):
    for r in client.get("/admin/rules").json()["rules"]:
        assert "project" in r
        assert r["project"] is None


# ---------------------------------------------------------------------------
# 项目筛选
# ---------------------------------------------------------------------------

@pytest.fixture
def with_project(db, seeded):
    p = repo_projects.create(db, key="zf", name="政府投资")
    db.flush()
    repo_rules.assign_project(db, ["R001", "R002"], p.id)
    db.commit()
    return p


def test_filter_by_project(client, with_project):
    body = client.get("/admin/rules?project=zf").json()
    assert [r["id"] for r in body["rules"]] == ["R001", "R002"]
    assert body["rules"][0]["project"] == {"key": "zf", "name": "政府投资"}


def test_filter_unassigned(client, with_project):
    body = client.get("/admin/rules?project=__none__").json()
    assert [r["id"] for r in body["rules"]] == ["R003", "R004", "R005"]


def test_filter_unknown_project_returns_empty(client, with_project):
    assert client.get("/admin/rules?project=ghost").json()["count"] == 0


# ---------------------------------------------------------------------------
# 增
# ---------------------------------------------------------------------------

def test_create_rule(client, seeded):
    resp = client.post("/admin/rules", json=NEW_RULE)
    assert resp.status_code == 201
    assert resp.json()["rule_id"] == "R999"
    assert resp.json()["rules_loaded"] == 6
    assert any(r["id"] == "R999" for r in client.get("/admin/rules").json()["rules"])


def test_create_with_project(client, with_project):
    resp = client.post("/admin/rules", json=dict(NEW_RULE, project="zf"))
    assert resp.status_code == 201
    assert client.get("/admin/rules/R999").json()["project"]["key"] == "zf"


def test_create_with_unknown_project_returns_422(client, seeded):
    resp = client.post("/admin/rules", json=dict(NEW_RULE, project="ghost"))
    assert resp.status_code == 422
    assert "ghost" in resp.json()["detail"]


def test_create_duplicate_id_returns_422(client, seeded):
    resp = client.post("/admin/rules", json=dict(NEW_RULE, id="R001"))
    assert resp.status_code == 422
    assert "已存在" in resp.json()["detail"]


def test_create_missing_prompt_template_returns_422(client, seeded):
    payload = dict(NEW_RULE)
    payload.pop("prompt_template")
    assert client.post("/admin/rules", json=payload).status_code == 422


# ---------------------------------------------------------------------------
# 改
# ---------------------------------------------------------------------------

def test_update_rule(client, seeded):
    payload = dict(NEW_RULE, id="R001", name="改后名字",
                   prompt_template="新模板 A:{field_a} B:{field_b}")
    assert client.put("/admin/rules/R001", json=payload).status_code == 200

    after = client.get("/admin/rules/R001").json()
    assert after["name"] == "改后名字"
    assert "新模板" in after["prompt_template"]


def test_update_keeps_list_position(client, seeded):
    """改内容不该把规则挪到列表末尾。"""
    client.put("/admin/rules/R001", json=dict(NEW_RULE, id="R001", name="改后"))
    assert client.get("/admin/rules").json()["rules"][0]["id"] == "R001"


def test_update_can_change_project(client, with_project):
    client.put("/admin/rules/R003", json=dict(NEW_RULE, id="R003", project="zf"))
    assert client.get("/admin/rules/R003").json()["project"]["key"] == "zf"


def test_update_can_clear_project(client, with_project):
    client.put("/admin/rules/R001", json=dict(NEW_RULE, id="R001", project=None))
    assert client.get("/admin/rules/R001").json()["project"] is None


def test_update_id_clash_returns_422(client, seeded):
    resp = client.put("/admin/rules/R001", json=dict(NEW_RULE, id="R002"))
    assert resp.status_code == 422
    assert "已被占用" in resp.json()["detail"]


def test_update_with_rename(client, seeded):
    resp = client.put("/admin/rules/R001", json=dict(NEW_RULE, id="R099", name="重命名后"))
    assert resp.status_code == 200
    assert client.get("/admin/rules/R001").status_code == 404
    assert client.get("/admin/rules/R099").json()["name"] == "重命名后"


def test_update_unknown_returns_404(client, seeded):
    assert client.put("/admin/rules/RNONE", json=dict(NEW_RULE, id="RNONE")).status_code == 404


# ---------------------------------------------------------------------------
# 删
# ---------------------------------------------------------------------------

def test_delete_rule(client, seeded):
    resp = client.delete("/admin/rules/R005")
    assert resp.status_code == 200
    assert resp.json()["rules_loaded"] == 4
    assert client.get("/admin/rules/R005").status_code == 404


def test_delete_unknown_returns_404(client, seeded):
    assert client.delete("/admin/rules/RXXX").status_code == 404


# ---------------------------------------------------------------------------
# field_defs
# ---------------------------------------------------------------------------

def test_create_rule_with_field_defs(client, seeded):
    payload = dict(NEW_RULE, field_defs=["shenbao", "pifu", "hetong"])
    assert client.post("/admin/rules", json=payload).status_code == 201
    assert client.get("/admin/rules/R999").json()["field_defs"] == [
        "shenbao", "pifu", "hetong"
    ]


def test_create_rule_without_field_defs_omits_key(client, seeded):
    """不带 field_defs 时响应里不应出现该键。"""
    client.post("/admin/rules", json=NEW_RULE)
    assert "field_defs" not in client.get("/admin/rules/R999").json()


def test_update_rule_field_defs(client, seeded):
    client.post("/admin/rules", json=dict(NEW_RULE, field_defs=["jia", "yi"]))
    client.put("/admin/rules/R999", json=dict(NEW_RULE, field_defs=["jia", "yi", "bing"]))
    assert client.get("/admin/rules/R999").json()["field_defs"] == ["jia", "yi", "bing"]


@pytest.mark.parametrize("bad_defs, why", [
    (["建设单位A", "建设单位B"], "中文不能当 JSON 键"),
    (["", ""], "空串"),
    (["has space", "b"], "含空格"),
    (["1abc", "b"], "数字开头"),
    (["a-b", "c"], "含连字符"),
    (["same", "same"], "规则内重复"),
    (["rule_id", "b"], "占用顶层保留字"),
])
def test_invalid_field_defs_rejected(client, seeded, bad_defs, why):
    resp = client.post("/admin/rules", json=dict(NEW_RULE, field_defs=bad_defs))
    assert resp.status_code == 422, f"{why} 应被拒绝: {bad_defs}"


@pytest.mark.parametrize("good_defs", [
    ["keyan_name", "zixun_name"], ["_leading_underscore", "b"],
    ["a1", "b2"], ["ONE", "two"], [],
])
def test_valid_field_defs_accepted(client, seeded, good_defs):
    resp = client.post("/admin/rules", json=dict(NEW_RULE, field_defs=good_defs))
    assert resp.status_code in (200, 201), resp.text


# ---------------------------------------------------------------------------
# 写入后 /compare 立即生效
# ---------------------------------------------------------------------------

def test_create_then_compare_uses_new_rule(client, seeded, monkeypatch):
    captured: dict = {}

    def _fake(messages, **kwargs):
        captured["messages"] = messages
        return ('{"match": true, "reason": "ok"}',
                {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4})

    monkeypatch.setattr(llm_rule, "chat_json", _fake)

    assert client.post("/admin/rules", json=NEW_RULE).status_code == 201

    resp = client.post("/compare", json={
        "rule_id": "R999",
        "field_a": {"value": "v1"},
        "field_b": {"value": "v2"},
    })
    assert resp.status_code == 200
    assert resp.json()["result"] is True
    assert "测试专家" in captured["messages"][0]["content"]
