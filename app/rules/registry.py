"""规则注册表。

每个 ``Rule`` 子类通过 ``@register`` 把自己登记进 ``_REGISTRY``。``build_rule(cfg)``
按 ``cfg["type"]`` 查表实例化对应规则。
"""

from __future__ import annotations

from typing import Any

from app.rules.base import Rule

_REGISTRY: dict[str, type[Rule]] = {}


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
