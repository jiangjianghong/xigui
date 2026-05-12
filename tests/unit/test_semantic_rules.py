"""语义规则单元测试。

不真调 LLM，monkeypatch 替换 ``app.rules.semantic_base.chat_json`` 控制返回值。
"""

from __future__ import annotations

import pytest

from app.rules import semantic_base
from app.rules.registry import build_rule, list_registered_types
from app.core.llm_client import LLMTimeout

_USAGE = {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}


_SIMILAR_CFG = {
    "id": "T_SS",
    "type": "semantic_similar",
    "params": {"strictness": "loose"},
    "prompt_template": "A: {a_value}\nB: {b_value}\n输出 JSON。",
}


@pytest.fixture
def fake_chat(monkeypatch):
    """允许测试用例预设 chat_json 的返回值。"""
    state = {"content": '{"match": true, "reason": "基本一致"}', "usage": _USAGE}

    def _fake(messages, **kwargs):
        return state["content"], state["usage"]

    monkeypatch.setattr(semantic_base, "chat_json", _fake)
    return state


def test_semantic_similar_parses_json(fake_chat):
    fake_chat["content"] = '{"match": true, "reason": "同一公司不同简称"}'
    rule = build_rule(_SIMILAR_CFG)
    res = rule.compare("贵州省XX建设有限公司", "XX建设公司")
    assert res.match is True
    assert "同一公司" in res.reason
    assert res.usage == _USAGE


def test_semantic_similar_mismatch(fake_chat):
    fake_chat["content"] = '{"match": false, "reason": "明显是两家公司"}'
    res = build_rule(_SIMILAR_CFG).compare("A公司", "B集团")
    assert res.match is False
    assert "明显" in res.reason


def test_invalid_json_fallback(fake_chat):
    fake_chat["content"] = "我觉得这两家基本一致。"
    res = build_rule(_SIMILAR_CFG).compare("A", "B")
    assert res.match is False
    assert "非法 JSON" in res.reason
    # usage 仍要带回，便于审计
    assert res.usage == _USAGE


def test_json_inside_text_recovered(fake_chat):
    # 模型偶发会加 ```json 包裹，确保兜底正则能挖出来
    fake_chat["content"] = '好的，结果是 {"match": true, "reason": "ok"} 。'
    res = build_rule(_SIMILAR_CFG).compare("A", "A")
    assert res.match is True
    assert res.reason == "ok"


def test_timeout_bubbles_up(fake_chat, monkeypatch):
    def _boom(messages, **kwargs):
        raise LLMTimeout("simulated")

    monkeypatch.setattr(semantic_base, "chat_json", _boom)
    with pytest.raises(LLMTimeout):
        build_rule(_SIMILAR_CFG).compare("A", "B")


def test_all_rules_registered():
    types = list_registered_types()
    assert set(types) == {
        "exact_match",
        "numeric_equal",
        "time_order",
        "semantic_similar",
        "semantic_exact",
    }
