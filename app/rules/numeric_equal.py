"""``numeric_equal`` 规则：抽取数值后按容差比较。

参数：
    tolerance (float, default 0.0)  绝对误差容忍
    unit_map  (dict, optional)      单位→倍率，例如 {"亿": 1e8, "万元": 1e4}
"""

from __future__ import annotations

from app.rules.base import CompareResult, Rule
from app.rules.registry import register
from app.utils.normalize import extract_number


@register
class NumericEqualRule(Rule):
    rule_type = "numeric_equal"

    def compare(self, a_value: str, b_value: str) -> CompareResult:
        tolerance = float(self.params.get("tolerance", 0.0))
        unit_map = self.params.get("unit_map") or {}

        na = extract_number(a_value, unit_map)
        nb = extract_number(b_value, unit_map)

        if na is None or nb is None:
            return CompareResult(
                match=False,
                reason=f"无法解析数值: a={a_value!r} -> {na}, b={b_value!r} -> {nb}",
                extra={"parsed_a": na, "parsed_b": nb},
            )

        diff = abs(na - nb)
        match = diff <= tolerance
        if match:
            reason = f"数值一致: {na} ≈ {nb}（误差 {diff} ≤ 容差 {tolerance}）"
        else:
            reason = f"数值不一致: {na} vs {nb}（误差 {diff} > 容差 {tolerance}）"
        return CompareResult(
            match=match,
            reason=reason,
            extra={"parsed_a": na, "parsed_b": nb, "diff": diff},
        )
