"""``POST /compare`` 路由实现。

业务流程：
1. 按 ``rule_id`` 在最新规则列表（mtime 自动 reload）里查规则配置。
2. 通过 registry 构造对应 ``Rule`` 实例，调 ``compare(a, b)``。
3. 计算耗时，把 ``CompareResult`` 转 ``CompareResponse``。
4. 异常按 plan 中的矩阵映射 HTTP 状态码。
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, status

from app.core.config import get_rules
from app.core.llm_client import LLMTimeout, LLMUpstreamError
from app.rules.registry import build_rule, should_require_field_name
from app.schemas.compare import CompareRequest, CompareResponse, Cost

router = APIRouter(tags=["compare"])
log = logging.getLogger("wanzi.compare")


def _find_rule_cfg(rule_id: str) -> dict | None:
    for r in get_rules():
        if r.get("id") == rule_id:
            return r
    return None


@router.post(
    "/compare",
    response_model=CompareResponse,
    summary="按指定规则比对两个字段",
)
def compare(req: CompareRequest) -> CompareResponse:
    t0 = time.perf_counter()
    log.info(
        "收到比对请求 rule_id=%s field_a.name=%s field_b.name=%s",
        req.rule_id, req.field_a.name, req.field_b.name,
    )

    rule_cfg = _find_rule_cfg(req.rule_id)
    if rule_cfg is None:
        log.warning("rule_id 不存在: %s", req.rule_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"rule_id 不存在: {req.rule_id}",
        )

    # 规则级别的 name 必填校验:仅当 yaml 显式 require_field_name=true 时生效。
    if should_require_field_name(rule_cfg):
        missing = []
        if not (req.field_a.name or "").strip():
            missing.append("field_a.name")
        if not (req.field_b.name or "").strip():
            missing.append("field_b.name")
        if missing:
            log.warning("rule %s 要求 name 必填,缺失: %s", req.rule_id, missing)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"rule {req.rule_id} 要求 {', '.join(missing)} 必填",
            )

    try:
        rule = build_rule(rule_cfg)
    except ValueError as e:
        # 规则配置写错了(缺 id 等),运维问题,返 500 让人去查 yaml
        log.error("rules.yaml 配置错误: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"规则配置错误: {e}",
        ) from e

    try:
        res = rule.compare(
            req.field_a.value,
            req.field_b.value,
            a_name=req.field_a.name,
            b_name=req.field_b.name,
        )
    except LLMTimeout as e:
        log.error("LLM 调用超时 rule_id=%s: %s", req.rule_id, e)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"LLM 调用超时: {e}",
        ) from e
    except LLMUpstreamError as e:
        log.error("LLM 上游错误 rule_id=%s: %s", req.rule_id, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM 上游错误: {e}",
        ) from e
    except Exception as e:  # noqa: BLE001
        log.exception("规则执行未捕获异常 rule_id=%s", req.rule_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    log.info(
        "比对完成 rule_id=%s match=%s ms=%d usage=%s reason=%r",
        req.rule_id, res.match, elapsed_ms, res.usage, res.reason[:200],
    )
    # extra 字段可能含 LLM 原始返回片段，仅 debug 级别打印
    log.debug("extra rule_id=%s extra=%s", req.rule_id, res.extra)

    return CompareResponse(
        rule_id=req.rule_id,
        result=res.match,
        reason=res.reason,
        time=elapsed_ms,
        cost=Cost(**(res.usage or {})),
    )
