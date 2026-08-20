"""字段键推导:由规则配置算出本规则的「有序合法字段键」。

这个列表是三处的唯一真相源:
- ``app/api/compare.py``   请求键必须与它完全相等
- ``app/rules/llm_rule.py`` 每个键注入 ``{键}`` 与 ``{键_name}`` 两个占位符
- ``app/api/compare.py``   响应按同样的键平铺回显

本模块是纯函数,不 import schemas / api,以免规则层反向依赖接口层。
"""

from __future__ import annotations

import re
import string
from typing import Any

# 默认字段键的字母后缀:field_a .. field_z
FIELD_LETTERS = string.ascii_lowercase

# 没有 prompt_template 时的最后兜底,避免崩溃。
# 放在本模块而非 llm_rule,是因为键推导也要读它(缺模板时按它反推出 a/b)。
FALLBACK_TEMPLATE = (
    "请判断以下字段在语义上是否一致。\n"
    "A: {field_a}\n"
    "B: {field_b}\n"
    '严格输出 JSON: {{"match": true|false, "reason": "<不超过30字>"}}'
)

# 匹配 {field_a} / {field_a_name},只认小写单字母
_LETTER_PLACEHOLDER_RE = re.compile(r"\{field_([a-z])(?:_name)?\}")


def default_key(index: int) -> str:
    """下标 → 默认字段键。``0`` → ``field_a``。"""
    return f"field_{FIELD_LETTERS[index]}"


def field_keys_for(rule_cfg: dict[str, Any]) -> list[str]:
    """返回这条规则的有序合法字段键。

    - 配了非空 ``field_defs`` → 直接用它,顺序即请求/渲染顺序
    - 否则 → 从 ``prompt_template`` 反推:取出现过的最大字母,
      返回 ``field_a`` 到该字母的**连续**列表(模板跳号也补齐,
      因为键集合必须连续才能与下标一一对应)
    - 模板不含任何字母占位符 → 返回空列表(该规则不接受字段)
    """
    defs = rule_cfg.get("field_defs") or []
    if defs:
        return [str(d) for d in defs]

    template = rule_cfg.get("prompt_template") or FALLBACK_TEMPLATE
    letters = _LETTER_PLACEHOLDER_RE.findall(template)
    if not letters:
        return []
    max_index = max(FIELD_LETTERS.index(c) for c in letters)
    return [default_key(i) for i in range(max_index + 1)]
