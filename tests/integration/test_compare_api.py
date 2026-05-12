"""``POST /compare`` 端到端集成测试。

覆盖：
- ``/health`` 报告规则数与已注册类型
- 5 种规则各跑 1 正 1 负
- 不存在的 rule_id 返 404
- LLM 调用全部用 monkeypatch 替换 ``chat_json``，不依赖外网

run:  pytest tests/integration/ -v
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.rules import semantic_base

client = TestClient(app)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health_reports_rules():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "wanzi_guizhe"
    assert body["rules_loaded"] >= 5
    assert {"R001", "R002", "R003", "R004", "R005"}.issubset(set(body["rule_ids"]))
    assert {"exact_match", "numeric_equal", "time_order",
            "semantic_similar", "semantic_exact"}.issubset(set(body["registered_types"]))


# ---------------------------------------------------------------------------
# 不存在的 rule_id
# ---------------------------------------------------------------------------

def test_unknown_rule_id_returns_404():
    resp = client.post("/compare", json={
        "rule_id": "RXXX",
        "field_a": {"name": "x", "value": "1"},
        "field_b": {"name": "x", "value": "1"},
    })
    assert resp.status_code == 404
    assert "rule_id 不存在" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# R001 exact_match
# ---------------------------------------------------------------------------

def test_R001_exact_match_pass():
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "field_a": {"name": "项目代码", "value": "  2024-001 "},
        "field_b": {"name": "项目代码", "value": "2024-001"},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["rule_id"] == "R001"
    assert body["result"] is True
    assert body["cost"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    assert body["time"] >= 0


def test_R001_exact_match_fail():
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "field_a": {"name": "项目代码", "value": "2024-001"},
        "field_b": {"name": "项目代码", "value": "2024-002"},
    })
    body = resp.json()
    assert resp.status_code == 200
    assert body["result"] is False
    assert "2024-001" in body["reason"] and "2024-002" in body["reason"]


# ---------------------------------------------------------------------------
# R002 numeric_equal
# ---------------------------------------------------------------------------

def test_R002_numeric_unit_conversion():
    """业务核心场景：1.2亿 ≡ 12000万元。"""
    resp = client.post("/compare", json={
        "rule_id": "R002",
        "field_a": {"name": "总投资", "value": "1.2亿"},
        "field_b": {"name": "总投资金额", "value": "12000万元"},
    })
    body = resp.json()
    assert resp.status_code == 200
    assert body["result"] is True


def test_R002_numeric_mismatch():
    resp = client.post("/compare", json={
        "rule_id": "R002",
        "field_a": {"name": "总投资", "value": "1亿"},
        "field_b": {"name": "总投资金额", "value": "2亿"},
    })
    assert resp.json()["result"] is False


# ---------------------------------------------------------------------------
# R003 time_order
# ---------------------------------------------------------------------------

def test_R003_time_order_pass():
    resp = client.post("/compare", json={
        "rule_id": "R003",
        "field_a": {"name": "批复时间", "value": "2024年1月10日"},
        "field_b": {"name": "申报时间", "value": "2024-02-01"},
    })
    assert resp.json()["result"] is True


def test_R003_time_order_fail():
    resp = client.post("/compare", json={
        "rule_id": "R003",
        "field_a": {"name": "批复时间", "value": "2024-03-01"},
        "field_b": {"name": "申报时间", "value": "2024-02-01"},
    })
    assert resp.json()["result"] is False


# ---------------------------------------------------------------------------
# R004 / R005 semantic (mocked LLM)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_llm(monkeypatch):
    """让所有语义规则的 chat_json 返回可控值。"""
    state = {
        "content": '{"match": true, "reason": "基本一致"}',
        "usage": {"prompt_tokens": 100, "completion_tokens": 8, "total_tokens": 108},
    }

    def _fake(messages, **kwargs):
        return state["content"], state["usage"]

    monkeypatch.setattr(semantic_base, "chat_json", _fake)
    return state


def test_R004_semantic_similar_pass(mock_llm):
    mock_llm["content"] = '{"match": true, "reason": "简称与全称指向同一主体"}'
    resp = client.post("/compare", json={
        "rule_id": "R004",
        "field_a": {"name": "建设单位", "value": "贵州省XX建设有限公司"},
        "field_b": {"name": "建设单位", "value": "XX建设公司"},
    })
    body = resp.json()
    assert resp.status_code == 200
    assert body["result"] is True
    assert "简称" in body["reason"]
    assert body["cost"]["total_tokens"] == 108


def test_R004_semantic_similar_fail(mock_llm):
    mock_llm["content"] = '{"match": false, "reason": "明显是两家不同公司"}'
    resp = client.post("/compare", json={
        "rule_id": "R004",
        "field_a": {"name": "建设单位", "value": "A公司"},
        "field_b": {"name": "建设单位", "value": "B集团"},
    })
    body = resp.json()
    assert body["result"] is False
    assert body["cost"]["total_tokens"] > 0


def test_R005_semantic_exact_invalid_json_returns_false(mock_llm):
    mock_llm["content"] = "我觉得这两段内容基本一致。"
    resp = client.post("/compare", json={
        "rule_id": "R005",
        "field_a": {"name": "建设内容", "value": "a; b; c"},
        "field_b": {"name": "建设内容", "value": "a; b; c; d"},
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

def test_field_name_optional_for_normal_rule():
    """R001 既没显式 require_field_name 也不引用 {a_name},name 可省略。"""
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "field_a": {"value": "  2024-001 "},
        "field_b": {"value": "2024-001"},
    })
    assert resp.status_code == 200
    assert resp.json()["result"] is True


def test_require_field_name_explicit_missing_returns_422(monkeypatch):
    """yaml 显式 require_field_name=true 时,缺 name 必须 422。"""
    from app.api import compare as compare_module

    fake_rule = {
        "id": "RTEST",
        "name": "test",
        "type": "exact_match",
        "require_field_name": True,
        "params": {},
    }
    monkeypatch.setattr(compare_module, "get_rules", lambda: [fake_rule])

    resp = client.post("/compare", json={
        "rule_id": "RTEST",
        "field_a": {"value": "x"},
        "field_b": {"value": "x"},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "field_a.name" in detail
    assert "field_b.name" in detail


def test_require_field_name_imply_from_template_missing_returns_422(mock_llm):
    """R004 的 prompt_template 含 {a_name},自动 imply,缺 name 应 422。"""
    resp = client.post("/compare", json={
        "rule_id": "R004",
        "field_a": {"value": "贵州省XX建设有限公司"},
        "field_b": {"value": "XX建设公司"},
    })
    assert resp.status_code == 422
    assert "field_a.name" in resp.json()["detail"]


def test_require_field_name_imply_with_name_passes(mock_llm):
    """R004 提供了 name,应正常走通,且 prompt 渲染含字段名。"""
    captured: dict = {}

    def _capture(messages, **kwargs):
        captured["messages"] = messages
        return '{"match": true, "reason": "ok"}', {
            "prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7,
        }

    # 替换 mock 的实现以捕获 prompt
    from app.rules import semantic_base as sb
    sb.chat_json = _capture  # type: ignore[assignment]

    resp = client.post("/compare", json={
        "rule_id": "R004",
        "field_a": {"name": "建设单位", "value": "贵州省XX建设有限公司"},
        "field_b": {"name": "建设单位", "value": "XX建设公司"},
    })
    assert resp.status_code == 200
    assert resp.json()["result"] is True
    user_msg = captured["messages"][-1]["content"]
    assert "建设单位" in user_msg  # name 被渲染进 prompt
