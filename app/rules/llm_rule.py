"""LLM 规则:本项目唯一的规则实现。

所有规则统一走"渲染 prompt_template → 调 LLM → 解析 JSON → 兜底"流程,
具体业务边界由 ``rules.yaml`` 的 ``prompt_template`` 自行描述。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.core.llm_client import chat_json
from app.rules.base import CompareResult

log = logging.getLogger("aicheck.rules.llm")

# 兜底:当模型没严格输出纯 JSON 时,从一段文本里挖第一个 {...} 块
_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")

# 没有 prompt_template 时的最后兜底,避免崩溃
_FALLBACK_TEMPLATE = (
    "请判断以下字段在语义上是否一致。\n"
    "A: {field_0}\n"
    "B: {field_1}\n"
    '严格输出 JSON: {{"match": true|false, "reason": "<不超过30字>"}}'
)


def _parse_llm_json(content: str) -> dict[str, Any] | None:
    """解析模型返回的 JSON。先严格 ``json.loads``,失败用正则兜底。"""
    if not content:
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    m = _JSON_BLOCK_RE.search(content)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


class LLMRule:
    """通用 LLM 规则。

    构造时接受 ``rules.yaml`` 中的整条规则配置。``compare`` 拿到字段列表,
    渲染 prompt 后调用 LLM,返回 ``CompareResult``。

    prompt_template 中使用 ``{field_0}`` ``{field_1}`` ... 按索引引用字段值,
    ``{field_0_name}`` ``{field_1_name}`` ... 引用字段名。
    """

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.id: str = cfg["id"]
        self.name: str = cfg.get("name", "")
        self.system_prompt: str = cfg.get("system_prompt") or ""
        self.prompt_template: str = cfg.get("prompt_template") or _FALLBACK_TEMPLATE

    def _render_user_prompt(
        self,
        fields: list[tuple[str, str | None]],
    ) -> str:
        """渲染 prompt_template。

        ``fields`` 为 ``[(value, name), ...]`` 列表。
        构建 ``{field_0}`` ``{field_0_name}`` ... 等占位符的替换字典。
        name 缺失时占位空串。
        """
        replacements: dict[str, str] = {}
        for i, (value, name) in enumerate(fields):
            replacements[f"field_{i}"] = value
            replacements[f"field_{i}_name"] = name or ""
        try:
            return self.prompt_template.format(**replacements)
        except (KeyError, IndexError) as e:
            log.warning("prompt_template 渲染失败 rule_id=%s err=%s", self.id, e)
            return self.prompt_template

    def compare(
        self,
        fields: list[tuple[str, str | None]],
    ) -> CompareResult:
        """执行比对。

        ``fields`` 为 ``[(value, name), ...]`` 列表,
        与请求中 ``fields`` 数组一一对应。
        """
        user_prompt = self._render_user_prompt(fields)
        messages: list[dict[str, Any]] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        # LLMTimeout / LLMUpstreamError 直接向上抛,由 API 层映射 504/502
        content, usage = chat_json(messages)

        parsed = _parse_llm_json(content)
        if parsed is None or "match" not in parsed:
            log.warning(
                "LLM 输出无法解析为合法 JSON rule_id=%s content=%r",
                self.id, content[:200],
            )
            return CompareResult(
                match=False,
                reason="LLM 输出非法 JSON",
                usage=usage,
                extra={"raw_content": content[:500]},
            )

        match = bool(parsed.get("match"))
        reason = str(parsed.get("reason") or ("基本一致" if match else "不一致"))
        return CompareResult(
            match=match,
            reason=reason,
            usage=usage,
            extra={"raw_content": content[:500]},
        )
