"""``NumericEqualRule`` 单元测试。"""

from __future__ import annotations

from app.rules.numeric_equal import NumericEqualRule

_UNIT_MAP = {"亿": 1e8, "万元": 1e4, "万": 1e4, "元": 1.0}
_CFG = {
    "id": "T_NE",
    "type": "numeric_equal",
    "params": {"tolerance": 0.0, "unit_map": _UNIT_MAP},
}


def _rule(tol: float = 0.0):
    cfg = dict(_CFG)
    cfg["params"] = {**_CFG["params"], "tolerance": tol}
    return NumericEqualRule(cfg)


def test_yi_equals_wan_yuan():
    """1.2亿 ≡ 12000万元（业务核心场景）。"""
    r = _rule().compare("1.2亿", "12000万元")
    assert r.match is True
    assert r.extra["parsed_a"] == r.extra["parsed_b"] == 1.2e8


def test_comma_thousand_sep():
    r = _rule().compare("1,200,000元", "120万")
    assert r.match is True


def test_within_tolerance():
    r = _rule(tol=100.0).compare("10050元", "10000元")
    assert r.match is True


def test_outside_tolerance():
    r = _rule(tol=10.0).compare("10100元", "10000元")
    assert r.match is False


def test_unparseable_left():
    r = _rule().compare("无数据", "100元")
    assert r.match is False
    assert "无法解析" in r.reason


def test_unparseable_both():
    r = _rule().compare("空", "无")
    assert r.match is False


def test_zero_equals_zero():
    r = _rule().compare("0元", "0")
    assert r.match is True
