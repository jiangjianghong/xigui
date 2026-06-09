"""管理类接口。

主要用途：当 mtime 检测因网络挂载、克隆模式等原因不可靠时，手动触发规则
重载。``POST /admin/reload-rules`` 是幂等的。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.core.config import reload_config, reload_rules

router = APIRouter(tags=["admin"])
log = logging.getLogger("aicheck.admin")


@router.post("/reload-rules", summary="强制重新加载 rules.yaml")
def admin_reload_rules() -> dict:
    n = reload_rules()
    log.info("管理接口触发 reload-rules，已加载 %d 条规则", n)
    return {"status": "ok", "rules_loaded": n}


@router.post("/reload-config", summary="强制重新加载主配置 config.yaml")
def admin_reload_config() -> dict:
    cfg = reload_config()
    log.info("管理接口触发 reload-config，llm.model=%s", cfg.get("llm", {}).get("model"))
    return {
        "status": "ok",
        "llm_model": cfg.get("llm", {}).get("model"),
        "llm_backend": cfg.get("llm", {}).get("backend"),
    }
