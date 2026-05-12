"""调 LLM 做语义比对的规则共用基类。

把"渲染 prompt -> 调 chat_json -> 解析 JSON -> 兜底"这一套流程封装一次，
让 ``semantic_similar`` 和 ``semantic_exact`` 只需声明各自的 ``rule_type`` 和
``system_prompt``。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.core.llm_client import chat_json
from app.rules.base import CompareResult, Rule

log = logging.getLogger("wanzi.rules.semantic")

# 兜底：当模型没严格输出纯 JSON 时，从一段文本里挖第一个 {...} 块
_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


def _parse_llm_json(content: str) -> dict[str, Any] | None:
    """解析模型返回的 JSON。先严格 ``json.loads``，失败用正则兜底。"""
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


class SemanticRule(Rule):
    """语义类规则共用流程。子类需声明 ``rule_type`` 和 ``system_prompt``。"""

    # 子类覆盖：系统级角色定位
    system_prompt: str = ""

    def _render_user_prompt(self, a_value: str, b_value: str) -> str:
        tpl = self.cfg.get("prompt_template", "")
        if not tpl:
            # yaml 没给 template 时给一个最简兜底
            return (
                "请判断以下两个字段在语义上是否一致。\n"
                f"A: {a_value}\n"
                f"B: {b_value}\n"
                '严格输出 JSON: {"match": true|false, "reason": "<不超过30字>"}'
            )
        # 用 .format 渲染，允许 yaml 中写 {a_value} {b_value}
        try:
            return tpl.format(a_value=a_value, b_value=b_value)
        except (KeyError, IndexError) as e:
            log.warning("prompt_template 渲染失败 rule_id=%s err=%s", self.id, e)
            return tpl

    def compare(self, a_value: str, b_value: str) -> CompareResult:
        user_prompt = self._render_user_prompt(a_value, b_value)
        messages: list[dict[str, Any]] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        # LLMTimeout / LLMUpstreamError 直接向上抛，由 API 层映射 504/502
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
