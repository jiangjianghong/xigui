"""管理类接口。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import reload_config
from app.db import repo_rules
from app.db.session import get_db

router = APIRouter(tags=["admin"])
log = logging.getLogger("aicheck.admin")


@router.post("/reload-rules", summary="[已降级] 规则改由 DB 实时读取,本接口仅返回当前条数")
def admin_reload_rules(db: Session = Depends(get_db)) -> dict:
    """规则落库后每次请求都实时查库,没有缓存可 reload。

    端点保留而非删除:怕有运维脚本还在调它,突然 404 比空操作更难排查。
    """
    n = repo_rules.count(db)
    log.info("reload-rules 已降级为空操作,当前 %d 条规则", n)
    return {"status": "ok", "rules_loaded": n, "note": "规则已实时读库,无需 reload"}


@router.post("/reload-config", summary="强制重新加载主配置 config.yaml")
def admin_reload_config() -> dict:
    cfg = reload_config()
    log.info("管理接口触发 reload-config，llm.model=%s", cfg.get("llm", {}).get("model"))
    return {
        "status": "ok",
        "llm_model": cfg.get("llm", {}).get("model"),
        "llm_backend": cfg.get("llm", {}).get("backend"),
    }
