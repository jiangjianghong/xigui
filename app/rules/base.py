"""规则比对引擎抽象基类。

每条业务规则继承 ``Rule``，声明 ``rule_type`` 字符串，实现 ``compare`` 方法。
``compare`` 拿到两个字段值，返回 ``CompareResult``（统一含 ``match`` / ``reason``
/ ``usage``）。``usage`` 仅语义类规则有；确定性规则可以留 None，上层会兜底为 0。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CompareResult:
    """单次规则比对的结果。"""

    match: bool
    reason: str
    usage: dict[str, int] | None = None
    """LLM token 用量，确定性规则填 None；上层 API 兜底为全 0。"""

    extra: dict[str, Any] = field(default_factory=dict)
    """规则实现可往里塞调试信息，会被记到日志。比如 normalize 后的值、解析后的数字等。"""


class Rule(ABC):
    """规则抽象基类。

    子类约定：
    - 类属性 ``rule_type`` 是 yaml 里 ``type`` 字段的值，全局唯一。
    - 通过 ``@register`` 装饰器登记到 registry。
    - ``compare`` 不应抛 LLM 异常，由调用方在更高层捕获并映射 HTTP 状态码。
    """

    rule_type: str = ""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.id: str = cfg["id"]
        self.name: str = cfg.get("name", "")
        self.params: dict[str, Any] = cfg.get("params", {}) or {}

    @abstractmethod
    def compare(
        self,
        a_value: str,
        b_value: str,
        *,
        a_name: str | None = None,
        b_name: str | None = None,
    ) -> CompareResult:
        """对两个字段值做比较，返回 ``CompareResult``。

        ``a_name`` / ``b_name`` 是上游请求传入的字段名（可选）。确定性规则
        通常用不到，可用 ``**_`` 吃掉；语义类规则需要把它们注入 prompt。
        """
