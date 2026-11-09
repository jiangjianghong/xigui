"""``GET /health`` 健康检查。返回服务状态、版本、已加载规则数。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import repo_rules
from app.db.session import get_db

router = APIRouter(tags=["health"])


@router.get("/health", summary="健康检查")
def health(request: Request, db: Session = Depends(get_db)) -> dict:
    result: dict[str, object] = {
        "status": "ok",
        "service": "ai_cross_check",
        "version": "0.1.0",
    }
    if request.state.auth_identity:
        rules = repo_rules.list_rules(db)
        result.update(rules_loaded=len(rules), rule_ids=[r.rule_id for r in rules])
    return result
