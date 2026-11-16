"""规则比对结果的数据载体。

整个系统所有规则统一走 LLM(prompt_template 驱动),不再有"确定性 vs 语义"
之分。本文件只放结果 dataclass,不再有抽象基类。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class LLMOutputError(ValueError):
    """The model response does not satisfy the configured output contract."""


@dataclass
class CompareResult:
    """单次规则比对的结果。"""

    match: Any
    reason: str
    usage: dict[str, int] | None = None
    """LLM token 用量。理论上恒有值;若 LLM 返回 usage 为空,会被上层兜底成全 0。"""

    extra: dict[str, Any] = field(default_factory=dict)
    """规则实现可往里塞调试信息(LLM 原始返回片段等),会被记到 debug 日志。"""
