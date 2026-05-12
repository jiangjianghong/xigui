"""``TimeOrderRule`` 单元测试。"""

from __future__ import annotations

from app.rules.time_order import TimeOrderRule

_CFG = {"id": "T_TO", "type": "time_order", "params": {"operator": "lt"}}


def _rule(op: str = "lt"):
    return TimeOrderRule({**_CFG, "params": {"operator": op}})


def test_lt_match():
    r = _rule("lt").compare("2024年1月10日", "2024-02-01")
    assert r.match is True


def test_lt_mismatch():
    r = _rule("lt").compare("2024-02-01", "2024-01-10")
    assert r.match is False


def test_eq_match_across_formats():
    r = _rule("eq").compare("2024-01-10", "2024年1月10日")
    assert r.match is True


def test_gt_match():
    r = _rule("gt").compare("2024-02-01", "2024-01-10")
    assert r.match is True


def test_le_inclusive():
    r = _rule("le").compare("2024-01-10", "2024-01-10")
    assert r.match is True


def test_unparseable_returns_false():
    r = _rule("lt").compare("not a date", "2024-01-10")
    assert r.match is False
    assert "无法解析" in r.reason


def test_unknown_operator():
    r = TimeOrderRule({**_CFG, "params": {"operator": "neq"}}).compare("2024-01-10", "2024-01-11")
    assert r.match is False
    assert "非法 operator" in r.reason
