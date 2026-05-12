"""``time_order`` 规则：解析两个日期后按 operator 比较先后。

参数：
    operator (str, default "lt")    lt | le | gt | ge | eq
"""

from __future__ import annotations

import operator as _op

from app.rules.base import CompareResult, Rule
from app.rules.registry import register
from app.utils.normalize import parse_date

_OPS = {
    "lt": (_op.lt, "早于"),
    "le": (_op.le, "不晚于"),
    "gt": (_op.gt, "晚于"),
    "ge": (_op.ge, "不早于"),
    "eq": (_op.eq, "等于"),
}


@register
class TimeOrderRule(Rule):
    rule_type = "time_order"

    def compare(self, a_value: str, b_value: str, **_: object) -> CompareResult:
        op_name = str(self.params.get("operator", "lt")).lower()
        if op_name not in _OPS:
            return CompareResult(
                match=False,
                reason=f"非法 operator: {op_name}（支持: {sorted(_OPS)}）",
            )
        op_fn, op_text = _OPS[op_name]

        da = parse_date(a_value)
        db = parse_date(b_value)
        if da is None or db is None:
            return CompareResult(
                match=False,
                reason=f"无法解析日期: a={a_value!r} -> {da}, b={b_value!r} -> {db}",
                extra={"parsed_a": str(da), "parsed_b": str(db)},
            )

        match = op_fn(da, db)
        if match:
            reason = f"a({da.date()}) {op_text} b({db.date()})"
        else:
            reason = f"a({da.date()}) 不{op_text} b({db.date()})"
        return CompareResult(
            match=match,
            reason=reason,
            extra={"parsed_a": str(da), "parsed_b": str(db), "operator": op_name},
        )
