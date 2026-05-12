"""``GET /health`` 健康检查。返回服务状态、版本、已加载规则数。"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_rules
from app.rules.registry import list_registered_types

router = APIRouter(tags=["health"])


@router.get("/health", summary="健康检查")
def health() -> dict:
    rules = get_rules()
    return {
        "status": "ok",
        "service": "wanzi_guizhe",
        "version": "0.1.0",
        "rules_loaded": len(rules),
        "rule_ids": [r.get("id") for r in rules],
        "registered_types": list_registered_types(),
    }
