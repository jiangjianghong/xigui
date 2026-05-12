"""规则模块入口。

导入所有具体规则触发它们的 ``@register`` 装饰器，让 registry 在模块被
首次 import 时就装齐。

API 层只需要 ``from app.rules import registry`` 或 ``from app.rules.registry
import build_rule``，无需关心具体实现位置。
"""

from __future__ import annotations

from app.rules import (  # noqa: F401
    exact_match,
    numeric_equal,
    time_order,
    semantic_similar,
    semantic_exact,
)
from app.rules.base import CompareResult, Rule  # noqa: F401
from app.rules.registry import build_rule, list_registered_types  # noqa: F401
