"""``ExactMatchRule`` 单元测试。"""

from __future__ import annotations

from app.rules.exact_match import ExactMatchRule

_CFG = {
    "id": "T_EM",
    "name": "test exact",
    "type": "exact_match",
    "params": {"ignore_whitespace": True, "ignore_case": True, "full_to_half": True},
}


def _rule(**override_params):
    cfg = dict(_CFG)
    cfg["params"] = {**_CFG["params"], **override_params}
    return ExactMatchRule(cfg)


def test_match_after_whitespace_normalization():
    r = _rule().compare("  2024-001 ", "2024-001")
    assert r.match is True
    assert "一致" in r.reason


def test_match_after_case_normalization():
    r = _rule().compare("ABC-01", "abc-01")
    assert r.match is True


def test_match_full_to_half():
    r = _rule().compare("ＡＢＣ１２３", "ABC123")
    assert r.match is True


def test_mismatch_reason_shows_diff():
    r = _rule().compare("foo", "bar")
    assert r.match is False
    assert "foo" in r.reason and "bar" in r.reason


def test_case_sensitive_when_disabled():
    r = _rule(ignore_case=False).compare("ABC", "abc")
    assert r.match is False


def test_usage_is_none_for_deterministic():
    # 确定性规则不调 LLM，usage 应为 None（由上层填 0）
    assert _rule().compare("x", "x").usage is None


def test_extra_carries_normalized_values():
    r = _rule().compare("  X ", "x")
    assert r.extra["normalized_a"] == "x"
    assert r.extra["normalized_b"] == "x"
