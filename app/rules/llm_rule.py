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

log = logging.getLogger("wanzi.rules.llm")

# 兜底:当模型没严格输出纯 JSON 时,从一段文本里挖第一个 {...} 块
_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")

# 默认系统提示。若 yaml 里没显式写,就用这个;yaml 写了 system_prompt 字段则覆盖。
DEFAULT_SYSTEM_PROMPT = (
    "你是一位资深的投资项目文件合规审核专家。任务:按用户给出的判定标准,比较两个"
    "字段值是否一致,并严格按 JSON 输出结论,不要任何额外说明、Markdown 标记或注释。"
)

# 没有 prompt_template 时的最后兜底,避免崩溃
_FALLBACK_TEMPLATE = (
    "请判断以下两个字段在语义上是否一致。\n"
    "A: {a_value}\n"
    "B: {b_value}\n"
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

    构造时接受 ``rules.yaml`` 中的整条规则配置。``compare`` 拿到两个字段值,
    渲染 prompt 后调用 LLM,返回 ``CompareResult``。
    """

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.id: str = cfg["id"]
        self.name: str = cfg.get("name", "")
        self.system_prompt: str = cfg.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
        self.prompt_template: str = cfg.get("prompt_template") or _FALLBACK_TEMPLATE

    def _render_user_prompt(
        self,
        a_value: str,
        b_value: str,
        *,
        a_name: str | None = None,
        b_name: str | None = None,
    ) -> str:
        # 允许 yaml 中写 {a_value} {b_value} {a_name} {b_name}。
        # name 缺失时占位空串,避免渲染出 "None"——若规则强依赖 name,应在
        # API 入口已经做过 422 校验,理论上走不到这里。
        try:
            return self.prompt_template.format(
                a_value=a_value,
                b_value=b_value,
                a_name=a_name or "",
                b_name=b_name or "",
            )
        except (KeyError, IndexError) as e:
            log.warning("prompt_template 渲染失败 rule_id=%s err=%s", self.id, e)
            return self.prompt_template

    def compare(
        self,
        a_value: str,
        b_value: str,
        *,
        a_name: str | None = None,
        b_name: str | None = None,
    ) -> CompareResult:
        user_prompt = self._render_user_prompt(
            a_value, b_value, a_name=a_name, b_name=b_name,
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_prompt},
        ]

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
