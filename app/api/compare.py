"""``POST /compare`` 路由实现。

业务流程：
1. 按 ``rule_id`` 在最新规则列表（mtime 自动 reload）里查规则配置。
2. 由规则算出「有序合法字段键」，校验请求键与之完全相等（不等返 422）。
3. 通过 registry 构造 ``LLMRule``，调 ``compare([(key, value, name), ...])``。
4. 计算耗时，把 ``CompareResult`` 转 ``CompareResponse``，按同样的键平铺回显。
5. 异常按矩阵映射 HTTP 状态码。

字段键校验之所以在这里而不在 Pydantic 模型里，是因为键集合依赖 ``rule_id``
查出的规则配置，而模型校验跑在路由函数体之前，那时还不知道是哪条规则。
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, status

from app.core.config import get_rules
from app.core.history import get_history_store
from app.core.llm_client import LLMTimeout, LLMUpstreamError
from app.rules.field_keys import field_keys_for
from app.rules.registry import build_rule, should_require_field_name
from app.schemas.compare import CompareRequest, CompareResponse, Cost

router = APIRouter(tags=["compare"])
log = logging.getLogger("aicheck.compare")


def _find_rule_cfg(rule_id: str) -> dict | None:
    for r in get_rules():
        if r.get("id") == rule_id:
            return r
    return None


def _validate_field_keys(rule_id: str, expected: list[str], got: set[str]) -> None:
    """校验请求键与规则键集合完全相等。不等则抛 422。

    少传、多传、拼错都在这里拦下——若放行,拼错的键会让模板占位符渲染成
    字面量,LLM 拿到 ``{field_b}`` 这种垃圾输入却仍返回 200。
    """
    missing = [k for k in expected if k not in got]
    unexpected = sorted(got - set(expected))
    if not missing and not unexpected:
        return
    parts = []
    if missing:
        parts.append(f"缺少字段: {', '.join(missing)}")
    if unexpected:
        parts.append(f"不接受字段: {', '.join(unexpected)}")
    detail = f"rule {rule_id} " + "；".join(parts)
    if expected:
        detail += f"（本规则需要: {', '.join(expected)}）"
    log.warning("字段键不匹配 rule_id=%s %s", rule_id, detail)
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


@router.post(
    "/compare",
    response_model=CompareResponse,
    response_model_exclude_none=True,
    summary="按指定规则比对若干字段",
)
def compare(req: CompareRequest) -> CompareResponse:
    t0 = time.perf_counter()
    inputs = req.field_inputs()
    log.info("收到比对请求 rule_id=%s fields=%d", req.rule_id, len(inputs))

    rule_cfg = _find_rule_cfg(req.rule_id)
    if rule_cfg is None:
        log.warning("rule_id 不存在: %s", req.rule_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"rule_id 不存在: {req.rule_id}",
        )

    expected_keys = field_keys_for(rule_cfg)
    _validate_field_keys(req.rule_id, expected_keys, set(inputs))
    # 校验已保证两者相等,按规则顺序排列
    ordered = [(k, inputs[k]) for k in expected_keys]

    # 规则级别的 name 必填校验:仅当 yaml 显式 require_field_name=true 时生效。
    if should_require_field_name(rule_cfg):
        missing = [
            f"{key}.name" for key, f in ordered if not (f.name or "").strip()
        ]
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
        res = rule.compare([(k, f.value, f.name) for k, f in ordered])
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

    # 记录历史
    history = get_history_store()
    history.add({
        "rule_id": req.rule_id,
        "rule_name": rule_cfg.get("name", ""),
        "fields": [{"key": k, **f.model_dump()} for k, f in ordered],
        "result": res.match,
        "reason": res.reason,
        "time_ms": elapsed_ms,
        "cost": res.usage or {},
        "raw_request": req.model_dump(),
    })

    return CompareResponse(
        rule_id=req.rule_id,
        result=res.match,
        reason=res.reason,
        time=elapsed_ms,
        cost=Cost(**(res.usage or {})),
        fields=dict(ordered),
    )
