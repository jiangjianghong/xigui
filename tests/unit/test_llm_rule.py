"""LLM 规则单元测试。

不真调 LLM,monkeypatch 替换 ``app.rules.llm_rule.chat_json`` 控制返回值。
"""

from __future__ import annotations

import pytest

from app.rules import llm_rule
from app.rules.registry import build_rule, should_require_field_name
from app.core.llm_client import LLMTimeout

_USAGE = {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}


_CFG = {
    "id": "T_LLM",
    "name": "test rule",
    "prompt_template": "A: {a_value}\nB: {b_value}\n输出 JSON。",
}


@pytest.fixture
def fake_chat(monkeypatch):
    """允许测试用例预设 chat_json 的返回值。"""
    state = {"content": '{"match": true, "reason": "基本一致"}', "usage": _USAGE}

    def _fake(messages, **kwargs):
        return state["content"], state["usage"]

    monkeypatch.setattr(llm_rule, "chat_json", _fake)
    return state


def test_llm_rule_parses_json(fake_chat):
    fake_chat["content"] = '{"match": true, "reason": "同一公司不同简称"}'
    rule = build_rule(_CFG)
    res = rule.compare("贵州省XX建设有限公司", "XX建设公司")
    assert res.match is True
    assert "同一公司" in res.reason
    assert res.usage == _USAGE


def test_llm_rule_mismatch(fake_chat):
    fake_chat["content"] = '{"match": false, "reason": "明显是两家公司"}'
    res = build_rule(_CFG).compare("A公司", "B集团")
    assert res.match is False
    assert "明显" in res.reason


def test_invalid_json_fallback(fake_chat):
    fake_chat["content"] = "我觉得这两家基本一致。"
    res = build_rule(_CFG).compare("A", "B")
    assert res.match is False
    assert "非法 JSON" in res.reason
    # usage 仍要带回,便于审计
    assert res.usage == _USAGE


def test_json_inside_text_recovered(fake_chat):
    # 模型偶发会加 ```json 包裹,确保兜底正则能挖出来
    fake_chat["content"] = '好的,结果是 {"match": true, "reason": "ok"} 。'
    res = build_rule(_CFG).compare("A", "A")
    assert res.match is True
    assert res.reason == "ok"


def test_timeout_bubbles_up(fake_chat, monkeypatch):
    def _boom(messages, **kwargs):
        raise LLMTimeout("simulated")

    monkeypatch.setattr(llm_rule, "chat_json", _boom)
    with pytest.raises(LLMTimeout):
        build_rule(_CFG).compare("A", "B")


def test_name_placeholders_rendered(monkeypatch):
    cfg = {
        "id": "T_NAME",
        "prompt_template": "判断字段 {a_name} 与 {b_name}: A={a_value} B={b_value}",
    }
    captured = {}

    def _capture(messages, **kwargs):
        captured["messages"] = messages
        return '{"match": true, "reason": "ok"}', _USAGE

    monkeypatch.setattr(llm_rule, "chat_json", _capture)
    build_rule(cfg).compare("v1", "v2", a_name="项目代码", b_name="编号")

    rendered = captured["messages"][-1]["content"]
    assert "项目代码" in rendered
    assert "编号" in rendered
    assert "v1" in rendered and "v2" in rendered


def test_should_require_field_name_explicit_true():
    assert should_require_field_name({"id": "X", "require_field_name": True}) is True


def test_should_require_field_name_explicit_false():
    assert should_require_field_name({"id": "X", "require_field_name": False}) is False


def test_should_require_field_name_missing_defaults_false():
    """未配置时默认 False(不校验);即使 prompt_template 含 {a_name} 也不会自动推导。"""
    cfg = {"id": "X", "prompt_template": "判断 {a_name}: {a_value} vs {b_value}"}
    assert should_require_field_name(cfg) is False
