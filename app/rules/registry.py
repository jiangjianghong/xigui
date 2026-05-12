"""规则注册表。

每个 ``Rule`` 子类通过 ``@register`` 把自己登记进 ``_REGISTRY``。``build_rule(cfg)``
按 ``cfg["type"]`` 查表实例化对应规则。
"""

from __future__ import annotations

import re
from typing import Any

from app.rules.base import Rule

_REGISTRY: dict[str, type[Rule]] = {}

# 用于自动推导 require_field_name:prompt_template 引用了 {a_name} 或 {b_name}
# 时,该规则就一定要求请求里带上 name(否则只会渲染出空串,LLM 拿不到上下文)。
_NAME_PLACEHOLDER_RE = re.compile(r"\{[ab]_name\}")


def register(cls: type[Rule]) -> type[Rule]:
    """类装饰器：把 ``cls.rule_type`` -> ``cls`` 注册进表。"""
    rt = getattr(cls, "rule_type", "")
    if not rt:
        raise ValueError(f"{cls.__name__} 未声明 rule_type")
    if rt in _REGISTRY:
        raise ValueError(f"rule_type 冲突: {rt} 已被 {_REGISTRY[rt].__name__} 注册")
    _REGISTRY[rt] = cls
    return cls


class UnknownRuleType(ValueError):
    """yaml 里写了未知 type 时抛出。"""


def build_rule(cfg: dict[str, Any]) -> Rule:
    """根据 cfg["type"] 实例化对应的 Rule 子类。"""
    t = cfg.get("type")
    if not t:
        raise ValueError(f"rule 缺少 type 字段: {cfg.get('id')}")
    if t not in _REGISTRY:
        raise UnknownRuleType(
            f"未知规则类型 type={t}（已注册: {sorted(_REGISTRY.keys())}）"
        )
    return _REGISTRY[t](cfg)


def list_registered_types() -> list[str]:
    """便于 /health 接口或调试查看已注册类型。"""
    return sorted(_REGISTRY.keys())


def should_require_field_name(rule_cfg: dict[str, Any]) -> bool:
    """判断该规则是否要求请求里 ``field_a.name`` / ``field_b.name`` 必填。

    判定优先级:
    1. yaml 显式 ``require_field_name: true`` → True
    2. ``prompt_template`` 引用了 ``{a_name}`` 或 ``{b_name}`` → 自动推导 True
       (避免业务忘记设 yaml 但 prompt 又依赖 name,运行时拿到空串)
    3. yaml 显式 ``require_field_name: false`` → False
    4. 都没说 → False(默认放宽,简化业务调用)
    """
    explicit = rule_cfg.get("require_field_name")
    if explicit is True:
        return True
    if explicit is False:
        return False
    tpl = rule_cfg.get("prompt_template") or ""
    return bool(_NAME_PLACEHOLDER_RE.search(tpl))
