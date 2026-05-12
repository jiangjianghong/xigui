"""``exact_match`` 规则：归一化后严格相等。

参数（来自 yaml ``params``）：
    ignore_whitespace (bool, default True)
    ignore_case       (bool, default True)
    full_to_half      (bool, default True)
"""

from __future__ import annotations

from app.rules.base import CompareResult, Rule
from app.rules.registry import register
from app.utils.normalize import normalize_text


@register
class ExactMatchRule(Rule):
    rule_type = "exact_match"

    def compare(self, a_value: str, b_value: str) -> CompareResult:
        opts = {
            "ignore_whitespace": self.params.get("ignore_whitespace", True),
            "ignore_case": self.params.get("ignore_case", True),
            "full_to_half": self.params.get("full_to_half", True),
        }
        a = normalize_text(a_value, **opts)
        b = normalize_text(b_value, **opts)
        match = a == b
        if match:
            reason = "两值归一化后完全一致"
        else:
            reason = f"归一化后不一致: '{a}' != '{b}'"
        return CompareResult(match=match, reason=reason, extra={"normalized_a": a, "normalized_b": b})
