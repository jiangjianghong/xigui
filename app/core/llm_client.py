"""LLM 客户端封装。

统一为业务层提供一个 ``chat_json(messages) -> (content, usage)`` 接口：

- 复用 ``app.core.config.build_extra_body``，自动屏蔽 dashscope / vllm 之间
  ``enable_thinking`` 的传参差异。
- 走非流式调用：``response.usage`` 是 OpenAI 兼容接口的标准字段，dashscope
  与 vLLM 非流式都会返回，不需要 ``stream_options={"include_usage": True}``。
- 抛出本模块自定义的 ``LLMTimeout`` / ``LLMUpstreamError``，让上层 API 路由
  直接映射到 504 / 502，不必感知 openai SDK 的异常族。
- 用 ``response_format={"type": "json_object"}`` 强约束模型输出 JSON。
  部分模型不支持时可在 config 里关掉。
"""

from __future__ import annotations

import logging
from typing import Any

import openai
from openai import OpenAI

from app.core.config import build_extra_body, get_config

log = logging.getLogger("aicheck.llm")


class LLMTimeout(Exception):
    """LLM 调用超时（映射 504）。"""


class LLMUpstreamError(Exception):
    """LLM 上游错误：鉴权、网络、5xx 等（映射 502）。"""


def _make_client(llm_cfg: dict[str, Any]) -> OpenAI:
    return OpenAI(
        api_key=llm_cfg["api_key"],
        base_url=llm_cfg["api_base"],
        timeout=llm_cfg.get("timeout", 300),
    )


def _extract_usage(resp: Any) -> dict[str, int]:
    """从响应中提取 usage，缺失字段兜底为 0。

    OpenAI 兼容接口非流式调用下 ``response.usage`` 是稳定字段；但万一某些
    模型返回了 ``None`` 或缺字段，这里都不让上层崩。
    """
    u = getattr(resp, "usage", None)
    return {
        "prompt_tokens": getattr(u, "prompt_tokens", 0) or 0 if u else 0,
        "completion_tokens": getattr(u, "completion_tokens", 0) or 0 if u else 0,
        "total_tokens": getattr(u, "total_tokens", 0) or 0 if u else 0,
    }


def chat_json(messages: list[dict[str, Any]], *, use_json_mode: bool | None = None) -> tuple[str, dict[str, int]]:
    """非流式 chat 调用，返回 ``(content, usage)``。

    Args:
        messages: OpenAI 风格的 messages 列表。
        use_json_mode: 是否启用 ``response_format=json_object``。默认从配置读
            ``llm.use_json_mode``，未配置时按 True。若某些模型不支持，传 False
            或在 config 中关闭，prompt 里依然要强约束 JSON 输出。

    Raises:
        LLMTimeout: 调用超时。
        LLMUpstreamError: 鉴权失败、网络错误、上游 5xx 等。
    """
    cfg = get_config()["llm"]
    client = _make_client(cfg)

    create_kwargs: dict[str, Any] = {
        "model": cfg["model"],
        "messages": messages,
        "max_tokens": cfg.get("max_tokens", 1024),
        "temperature": cfg.get("temperature", 0.1),
        "extra_body": build_extra_body(cfg),
    }
    enable_json = cfg.get("use_json_mode", True) if use_json_mode is None else use_json_mode
    if enable_json:
        create_kwargs["response_format"] = {"type": "json_object"}

    log.debug(
        "LLM 请求 model=%s backend=%s json_mode=%s msg_count=%d",
        cfg["model"], cfg.get("backend"), enable_json, len(messages),
    )

    try:
        resp = client.chat.completions.create(**create_kwargs)
    except openai.APITimeoutError as e:
        log.error("LLM 调用超时: %s", e)
        raise LLMTimeout(str(e)) from e
    except (openai.AuthenticationError, openai.APIConnectionError, openai.APIStatusError) as e:
        log.error("LLM 上游错误: %s: %s", type(e).__name__, e)
        raise LLMUpstreamError(f"{type(e).__name__}: {e}") from e

    content = ""
    if resp.choices:
        content = resp.choices[0].message.content or ""
    usage = _extract_usage(resp)

    log.debug("LLM 响应 usage=%s content_len=%d", usage, len(content))
    return content, usage
