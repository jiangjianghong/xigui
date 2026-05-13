"""规则模块入口。

所有规则统一走 ``LLMRule``,由 ``rules.yaml`` 的 ``prompt_template`` 驱动。
"""

from __future__ import annotations

from app.rules.base import CompareResult  # noqa: F401
from app.rules.llm_rule import LLMRule  # noqa: F401
from app.rules.registry import build_rule, should_require_field_name  # noqa: F401
