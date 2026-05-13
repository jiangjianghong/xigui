"""规则构造与 require_field_name 读取。

所有规则统一构造为 ``LLMRule``。本模块只剩两个公开函数:
- ``build_rule(cfg)``  从 yaml 单条规则配置构造 ``LLMRule``
- ``should_require_field_name(cfg)``  读取 yaml 显式声明的 require_field_name
"""

from __future__ import annotations

from typing import Any

from app.rules.llm_rule import LLMRule


def build_rule(cfg: dict[str, Any]) -> LLMRule:
    """从一条 yaml 规则配置构造 ``LLMRule`` 实例。"""
    if not cfg.get("id"):
        raise ValueError("rule 缺少 id 字段")
    return LLMRule(cfg)


def should_require_field_name(rule_cfg: dict[str, Any]) -> bool:
    """读取 yaml 中显式声明的 ``require_field_name``。

    - 显式 ``true``  → True
    - 显式 ``false`` → False
    - 未配置          → False(等价于不校验,但建议每条规则都显式写出来)
    """
    return bool(rule_cfg.get("require_field_name"))
