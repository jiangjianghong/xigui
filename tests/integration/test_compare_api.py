"""``POST /compare`` 端到端集成测试。

所有规则都走 LLM,因此所有 /compare 用例都需要 monkeypatch `chat_json`。

规则数据源是测试自有 fixture(``tests/fixtures/rules.yaml``,固定 R001-R005
共 5 条),与业务活配置 ``config/rules.yaml`` 解耦——业务侧改活配置不影响测试。
本文件只读规则、不写规则,因此直接指向 fixture 文件,无需拷贝到 tmp_path。

run:  pytest tests/integration/ -v
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core import config as cfg_module
from app.rules import llm_rule

client = TestClient(app)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_RULES = PROJECT_ROOT / "tests" / "fixtures" / "rules.yaml"


@pytest.fixture(autouse=True)
def isolated_rules(monkeypatch):
    """让所有用例都从 tests/fixtures/rules.yaml 读规则(只读,不拷贝)。"""
    real_get_config = cfg_module.get_config

    def _fake_get_config():
        c = dict(real_get_config())
        c["rules_config_path"] = str(FIXTURE_RULES)
        return c

    monkeypatch.setattr(cfg_module, "get_config", _fake_get_config)
    cfg_module.reload_rules()
    yield
    # 缓存自愈靠 get_rules 的 path/mtime 比对;此处 monkeypatch 仍生效,reload 只是兜底
    cfg_module.reload_rules()


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health_reports_rules():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "ai_cross_check"
    assert body["rules_loaded"] == 5
    assert set(body["rule_ids"]) == {"R001", "R002", "R003", "R004", "R005"}


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
        "field_a": {"name": "项目代码", "value": "  2024-001 "},
        "field_b": {"name": "项目代码", "value": "2024-001"},
    })
    body = resp.json()
    assert resp.status_code == 200
    assert body["rule_id"] == "R001"
    assert body["result"] is True
    assert body["cost"]["total_tokens"] == 108
    assert body["time"] >= 0
    assert body["field_a"]["value"] == "  2024-001 "
    assert body["field_b"]["value"] == "2024-001"


def test_R001_returns_false_when_llm_says_no_match(mock_llm):
    mock_llm["content"] = '{"match": false, "reason": "编号不同"}'
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "field_a": {"name": "项目代码", "value": "2024-001"},
        "field_b": {"name": "项目代码", "value": "2024-002"},
    })
    body = resp.json()
    assert resp.status_code == 200
    assert body["result"] is False
    assert "编号不同" in body["reason"]


def test_R002_numeric_unit_conversion(mock_llm):
    mock_llm["content"] = '{"match": true, "reason": "1.2亿 = 12000万元"}'
    resp = client.post("/compare", json={
        "rule_id": "R002",
        "field_a": {"name": "总投资", "value": "1.2亿"},
        "field_b": {"name": "总投资金额", "value": "12000万元"},
    })
    body = resp.json()
    assert resp.status_code == 200
    assert body["result"] is True


def test_R003_time_order_pass(mock_llm):
    mock_llm["content"] = '{"match": true, "reason": "1月10日早于2月1日"}'
    resp = client.post("/compare", json={
        "rule_id": "R003",
        "field_a": {"name": "批复时间", "value": "2024年1月10日"},
        "field_b": {"name": "申报时间", "value": "2024-02-01"},
    })
    assert resp.json()["result"] is True


def test_R004_semantic_similar_pass(mock_llm):
    mock_llm["content"] = '{"match": true, "reason": "简称与全称指向同一主体"}'
    resp = client.post("/compare", json={
        "rule_id": "R004",
        "danwei_shenbao": {"name": "建设单位", "value": "贵州省XX建设有限公司"},
        "danwei_pifu": {"name": "建设单位", "value": "XX建设公司"},
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
    assert body["rules_loaded"] == 5


# ---------------------------------------------------------------------------
# require_field_name 校验
# ---------------------------------------------------------------------------

def test_field_name_optional_for_normal_rule(mock_llm):
    """R001 没显式 require_field_name,name 可省略。"""
    mock_llm["content"] = '{"match": true, "reason": "一致"}'
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "field_a": {"value": "  2024-001 "},
        "field_b": {"value": "2024-001"},
    })
    assert resp.status_code == 200
    assert resp.json()["result"] is True


def test_single_field_compare(mock_llm, monkeypatch):
    """单字段校验场景:注入一条只引用 {field_a} 的规则,只传 field_a。"""
    from app.api import compare as compare_module

    single_rule = {
        "id": "RSINGLE",
        "name": "单字段规模数量校验",
        "require_field_name": False,
        "prompt_template": "请判断以下建设规模描述是否含明确数量:{field_a}",
    }
    monkeypatch.setattr(compare_module, "get_rules", lambda: [single_rule])

    mock_llm["content"] = '{"match": true, "reason": "含明确规模数量"}'
    resp = client.post("/compare", json={
        "rule_id": "RSINGLE",
        "field_a": {"value": "DN500管网5公里，新建"},
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
        "prompt_template": "A: {field_a} B: {field_b}",
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


def test_require_field_name_R004_missing_returns_422(mock_llm):
    """R004 yaml 显式 require_field_name: true,缺 name 应 422。"""
    resp = client.post("/compare", json={
        "rule_id": "R004",
        "danwei_shenbao": {"value": "贵州省XX建设有限公司"},
        "danwei_pifu": {"value": "XX建设公司"},
    })
    assert resp.status_code == 422
    assert "danwei_shenbao.name" in resp.json()["detail"]


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
        "danwei_shenbao": {"name": "建设单位", "value": "贵州省XX建设有限公司"},
        "danwei_pifu": {"name": "建设单位", "value": "XX建设公司"},
    })
    assert resp.status_code == 200
    assert resp.json()["result"] is True
    user_msg = captured["messages"][-1]["content"]
    assert "建设单位" in user_msg  # name 被渲染进 prompt
# ---------------------------------------------------------------------------
# 字段键一致性校验
# ---------------------------------------------------------------------------

def test_legacy_fields_array_returns_422():
    """旧版 fields 数组已不再受理:值不是 {name?, value} 对象。"""
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "fields": [{"value": "2024-001"}, {"value": "2024-001"}],
    })
    assert resp.status_code == 422


def test_no_field_key_returns_422():
    resp = client.post("/compare", json={"rule_id": "R001"})
    assert resp.status_code == 422
    assert "field_a" in resp.text


def test_missing_one_key_returns_422():
    """只传 field_a,缺 field_b。"""
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "field_a": {"value": "2024-001"},
    })
    assert resp.status_code == 422
    assert "缺少字段" in resp.json()["detail"]
    assert "field_b" in resp.json()["detail"]


def test_extra_key_returns_422():
    """多传一个未知键。"""
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "field_a": {"value": "1"},
        "field_b": {"value": "1"},
        "field_c": {"value": "1"},
    })
    assert resp.status_code == 422
    assert "不接受字段" in resp.json()["detail"]
    assert "field_c" in resp.json()["detail"]


def test_typo_key_returns_422():
    """拼错键名:同时报缺少与不接受,不会静默当成空串发给 LLM。"""
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "field_a": {"value": "1"},
        "field_bb": {"value": "1"},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "field_b" in detail and "field_bb" in detail


def test_letter_keys_rejected_for_custom_id_rule():
    """R004 配了 field_defs,传 field_a/field_b 必须 422。"""
    resp = client.post("/compare", json={
        "rule_id": "R004",
        "field_a": {"name": "建设单位", "value": "甲"},
        "field_b": {"name": "建设单位", "value": "乙"},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "danwei_shenbao" in detail
    assert "field_a" in detail


def test_custom_id_renders_into_prompt(mock_llm, monkeypatch):
    """自定义 ID 端到端:键名进请求、占位符进 prompt、键名回显。"""
    from app.api import compare as compare_module

    triple_rule = {
        "id": "RTRIPLE",
        "name": "三方金额一致",
        "field_defs": ["shenbao_amount", "pifu_amount", "hetong_amount"],
        "require_field_name": False,
        "prompt_template": "申报:{shenbao_amount} 批复:{pifu_amount} 合同:{hetong_amount}",
    }
    monkeypatch.setattr(compare_module, "get_rules", lambda: [triple_rule])

    captured: dict = {}

    def _capture(messages, **kwargs):
        captured["messages"] = messages
        return '{"match": true, "reason": "三方一致"}', {
            "prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7,
        }

    monkeypatch.setattr(llm_rule, "chat_json", _capture)

    resp = client.post("/compare", json={
        "rule_id": "RTRIPLE",
        "shenbao_amount": {"value": "500万"},
        "pifu_amount": {"value": "500万"},
        "hetong_amount": {"value": "5000000元"},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"] is True
    assert body["shenbao_amount"]["value"] == "500万"
    assert body["hetong_amount"]["value"] == "5000000元"

    user_msg = captured["messages"][-1]["content"]
    assert "申报:500万" in user_msg
    assert "批复:500万" in user_msg
    assert "合同:5000000元" in user_msg


def test_response_omits_unused_field_keys(mock_llm):
    """response_model_exclude_none:未使用的键不出现在响应里。"""
    mock_llm["content"] = '{"match": true, "reason": "项目代码一致"}'
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "field_a": {"name": "项目代码", "value": "  2024-001 "},
        "field_b": {"name": "项目代码", "value": "2024-001"},
    })
    body = resp.json()
    assert resp.status_code == 200
    assert body["field_a"] == {"name": "项目代码", "value": "  2024-001 "}
    assert body["field_b"] == {"name": "项目代码", "value": "2024-001"}
    for letter in "cdefghijklmnopqrstuvwxyz":
        assert f"field_{letter}" not in body
    assert "fields" not in body


def test_history_records_field_key(mock_llm):
    """历史落库的 fields 每项带 key,供 UI 显示键名。"""
    from app.core.history import get_history_store

    mock_llm["content"] = '{"match": true, "reason": "一致"}'
    client.post("/compare", json={
        "rule_id": "R001",
        "field_a": {"value": "2024-001"},
        "field_b": {"value": "2024-001"},
    })
    items = get_history_store().list(rule_id="R001", limit=1)["items"]
    assert items, "历史里应有刚写入的记录"
    fields = items[0]["fields"]
    assert [f["key"] for f in fields] == ["field_a", "field_b"]
    assert fields[0]["value"] == "2024-001"
