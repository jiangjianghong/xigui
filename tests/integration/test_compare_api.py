"""``POST /compare`` 端到端集成测试。

所有规则都走 LLM,因此所有 /compare 用例都需要 monkeypatch `chat_json`。

run:  pytest tests/integration/ -v
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.rules import llm_rule

client = TestClient(app)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health_reports_rules():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "ai_cross_check"
    assert body["rules_loaded"] >= 5
    assert {"R001", "R002", "R003", "R004", "R005"}.issubset(set(body["rule_ids"]))


# ---------------------------------------------------------------------------
# 不存在的 rule_id
# ---------------------------------------------------------------------------

def test_unknown_rule_id_returns_404():
    resp = client.post("/compare", json={
        "rule_id": "RXXX",
        "fields": [{"name": "x", "value": "1"}, {"name": "x", "value": "1"}],
    })
    assert resp.status_code == 404
    assert "rule_id 不存在" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 通用 LLM mock
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_llm(monkeypatch):
    """让所有 LLM 规则的 chat_json 返回可控值。"""
    state = {
        "content": '{"match": true, "reason": "基本一致"}',
        "usage": {"prompt_tokens": 100, "completion_tokens": 8, "total_tokens": 108},
    }

    def _fake(messages, **kwargs):
        return state["content"], state["usage"]

    monkeypatch.setattr(llm_rule, "chat_json", _fake)
    return state


# ---------------------------------------------------------------------------
# R001-R005 都走 LLM,统一用 mock 跑通即可
# ---------------------------------------------------------------------------

def test_R001_returns_true_when_llm_says_match(mock_llm):
    mock_llm["content"] = '{"match": true, "reason": "项目代码一致"}'
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "fields": [
            {"name": "项目代码", "value": "  2024-001 "},
            {"name": "项目代码", "value": "2024-001"},
        ],
    })
    body = resp.json()
    assert resp.status_code == 200
    assert body["rule_id"] == "R001"
    assert body["result"] is True
    assert body["cost"]["total_tokens"] == 108
    assert body["time"] >= 0
    assert len(body["fields"]) == 2


def test_R001_returns_false_when_llm_says_no_match(mock_llm):
    mock_llm["content"] = '{"match": false, "reason": "编号不同"}'
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "fields": [
            {"name": "项目代码", "value": "2024-001"},
            {"name": "项目代码", "value": "2024-002"},
        ],
    })
    body = resp.json()
    assert resp.status_code == 200
    assert body["result"] is False
    assert "编号不同" in body["reason"]


def test_R002_numeric_unit_conversion(mock_llm):
    mock_llm["content"] = '{"match": true, "reason": "1.2亿 = 12000万元"}'
    resp = client.post("/compare", json={
        "rule_id": "R002",
        "fields": [
            {"name": "总投资", "value": "1.2亿"},
            {"name": "总投资金额", "value": "12000万元"},
        ],
    })
    body = resp.json()
    assert resp.status_code == 200
    assert body["result"] is True


def test_R003_time_order_pass(mock_llm):
    mock_llm["content"] = '{"match": true, "reason": "1月10日早于2月1日"}'
    resp = client.post("/compare", json={
        "rule_id": "R003",
        "fields": [
            {"name": "批复时间", "value": "2024年1月10日"},
            {"name": "申报时间", "value": "2024-02-01"},
        ],
    })
    assert resp.json()["result"] is True


def test_R004_semantic_similar_pass(mock_llm):
    mock_llm["content"] = '{"match": true, "reason": "简称与全称指向同一主体"}'
    resp = client.post("/compare", json={
        "rule_id": "R004",
        "fields": [
            {"name": "建设单位", "value": "贵州省XX建设有限公司"},
            {"name": "建设单位", "value": "XX建设公司"},
        ],
    })
    body = resp.json()
    assert resp.status_code == 200
    assert body["result"] is True
    assert "简称" in body["reason"]
    assert body["cost"]["total_tokens"] == 108


def test_R005_invalid_json_returns_false(mock_llm):
    mock_llm["content"] = "我觉得这两段内容基本一致。"
    resp = client.post("/compare", json={
        "rule_id": "R005",
        "fields": [
            {"name": "建设内容", "value": "a; b; c"},
            {"name": "建设内容", "value": "a; b; c; d"},
        ],
    })
    body = resp.json()
    assert resp.status_code == 200
    assert body["result"] is False
    assert "非法 JSON" in body["reason"]


# ---------------------------------------------------------------------------
# /admin/reload-rules
# ---------------------------------------------------------------------------

def test_admin_reload_rules():
    resp = client.post("/admin/reload-rules")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["rules_loaded"] >= 5


# ---------------------------------------------------------------------------
# require_field_name 校验
# ---------------------------------------------------------------------------

def test_field_name_optional_for_normal_rule(mock_llm):
    """R001 没显式 require_field_name,name 可省略。"""
    mock_llm["content"] = '{"match": true, "reason": "一致"}'
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "fields": [{"value": "  2024-001 "}, {"value": "2024-001"}],
    })
    assert resp.status_code == 200
    assert resp.json()["result"] is True


def test_single_field_compare(mock_llm):
    """单字段校验场景。"""
    mock_llm["content"] = '{"match": true, "reason": "含明确规模数量"}'
    resp = client.post("/compare", json={
        "rule_id": "cross_rule_08",
        "fields": [{"value": "DN500管网5公里，新建"}],
    })
    assert resp.status_code == 200
    assert resp.json()["result"] is True


def test_require_field_name_explicit_missing_returns_422(monkeypatch):
    """yaml 显式 require_field_name=true 时,缺 name 必须 422。"""
    from app.api import compare as compare_module

    fake_rule = {
        "id": "RTEST",
        "name": "test",
        "require_field_name": True,
        "prompt_template": "A: {field_0} B: {field_1}",
    }
    monkeypatch.setattr(compare_module, "get_rules", lambda: [fake_rule])

    resp = client.post("/compare", json={
        "rule_id": "RTEST",
        "fields": [{"value": "x"}, {"value": "x"}],
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "fields[0].name" in detail
    assert "fields[1].name" in detail


def test_require_field_name_R004_missing_returns_422(mock_llm):
    """R004 yaml 显式 require_field_name: true,缺 name 应 422。"""
    resp = client.post("/compare", json={
        "rule_id": "R004",
        "fields": [
            {"value": "贵州省XX建设有限公司"},
            {"value": "XX建设公司"},
        ],
    })
    assert resp.status_code == 422
    assert "fields[0].name" in resp.json()["detail"]


def test_require_field_name_R004_with_name_passes(monkeypatch):
    """R004 提供了 name,应正常走通,且 prompt 渲染含字段名。"""
    captured: dict = {}

    def _capture(messages, **kwargs):
        captured["messages"] = messages
        return '{"match": true, "reason": "ok"}', {
            "prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7,
        }

    monkeypatch.setattr(llm_rule, "chat_json", _capture)

    resp = client.post("/compare", json={
        "rule_id": "R004",
        "fields": [
            {"name": "建设单位", "value": "贵州省XX建设有限公司"},
            {"name": "建设单位", "value": "XX建设公司"},
        ],
    })
    assert resp.status_code == 200
    assert resp.json()["result"] is True
    user_msg = captured["messages"][-1]["content"]
    assert "建设单位" in user_msg  # name 被渲染进 prompt
