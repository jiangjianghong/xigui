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
    "prompt_template": "A: {field_0}\nB: {field_1}\n输出 JSON。",
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
    res = rule.compare([("贵州省XX建设有限公司", None), ("XX建设公司", None)])
    assert res.match is True
    assert "同一公司" in res.reason
    assert res.usage == _USAGE


def test_llm_rule_mismatch(fake_chat):
    fake_chat["content"] = '{"match": false, "reason": "明显是两家公司"}'
    res = build_rule(_CFG).compare([("A公司", None), ("B集团", None)])
    assert res.match is False
    assert "明显" in res.reason


def test_invalid_json_fallback(fake_chat):
    fake_chat["content"] = "我觉得这两家基本一致。"
    res = build_rule(_CFG).compare([("A", None), ("B", None)])
    assert res.match is False
    assert "非法 JSON" in res.reason
    # usage 仍要带回,便于审计
    assert res.usage == _USAGE


def test_json_inside_text_recovered(fake_chat):
    # 模型偶发会加 ```json 包裹,确保兜底正则能挖出来
    fake_chat["content"] = '好的,结果是 {"match": true, "reason": "ok"} 。'
    res = build_rule(_CFG).compare([("A", None), ("A", None)])
    assert res.match is True
    assert res.reason == "ok"


def test_timeout_bubbles_up(fake_chat, monkeypatch):
    def _boom(messages, **kwargs):
        raise LLMTimeout("simulated")

    monkeypatch.setattr(llm_rule, "chat_json", _boom)
    with pytest.raises(LLMTimeout):
        build_rule(_CFG).compare([("A", None), ("B", None)])


def test_name_placeholders_rendered(monkeypatch):
    cfg = {
        "id": "T_NAME",
        "prompt_template": "判断字段 {field_0_name} 与 {field_1_name}: A={field_0} B={field_1}",
    }
    captured = {}

    def _capture(messages, **kwargs):
        captured["messages"] = messages
        return '{"match": true, "reason": "ok"}', _USAGE

    monkeypatch.setattr(llm_rule, "chat_json", _capture)
    build_rule(cfg).compare([("v1", "项目代码"), ("v2", "编号")])

    rendered = captured["messages"][-1]["content"]
    assert "项目代码" in rendered
    assert "编号" in rendered
    assert "v1" in rendered and "v2" in rendered


def test_should_require_field_name_explicit_true():
    assert should_require_field_name({"id": "X", "require_field_name": True}) is True


def test_should_require_field_name_explicit_false():
    assert should_require_field_name({"id": "X", "require_field_name": False}) is False


def test_should_require_field_name_missing_defaults_false():
    """未配置时默认 False(不校验);即使 prompt_template 含 {field_0_name} 也不会自动推导。"""
    cfg = {"id": "X", "prompt_template": "判断 {field_0_name}: {field_0} vs {field_1}"}
    assert should_require_field_name(cfg) is False


def test_system_prompt_explicit_sent(monkeypatch):
    """yaml 写了 system_prompt 时,messages 应含 system 角色消息。"""
    cfg = {
        "id": "T_SYS",
        "system_prompt": "你是测试专家。",
        "prompt_template": "A:{field_0} B:{field_1}",
    }
    captured: dict = {}

    def _capture(messages, **kwargs):
        captured["messages"] = messages
        return '{"match": true, "reason": "ok"}', _USAGE

    monkeypatch.setattr(llm_rule, "chat_json", _capture)
    build_rule(cfg).compare([("a", None), ("b", None)])

    msgs = captured["messages"]
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "你是测试专家。"
    assert msgs[1]["role"] == "user"


def test_system_prompt_empty_omits_system_message(monkeypatch):
    """system_prompt 缺失或为空时,messages 中不应有 system 角色消息。"""
    cfg = {
        "id": "T_NOSYS",
        "prompt_template": "A:{field_0} B:{field_1}",
    }
    captured: dict = {}

    def _capture(messages, **kwargs):
        captured["messages"] = messages
        return '{"match": true, "reason": "ok"}', _USAGE

    monkeypatch.setattr(llm_rule, "chat_json", _capture)
    build_rule(cfg).compare([("a", None), ("b", None)])

    msgs = captured["messages"]
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
