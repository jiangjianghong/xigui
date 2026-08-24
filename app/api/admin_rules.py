"""规则 CRUD 后端,服务 UI 的增删改查。

数据流:
    UI <--HTTP--> 本模块 <--repo_rules--> MySQL

规则真源已是 MySQL。``config/rules.yaml`` 只在导入导出时参与,见
``app/api/admin_import_export.py``。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import repo_projects, repo_rules
from app.db.session import get_db
from app.schemas.rules import RuleIn, RuleOut  # noqa: F401  (RuleOut 供文档引用)

router = APIRouter(tags=["admin-rules"])
log = logging.getLogger("aicheck.admin.rules")

UNASSIGNED = "__none__"


def _resolve_project_id(db: Session, key: str | None) -> int | None:
    """项目 key → id。key 为空返 None(未分组);key 不存在返 422。"""
    if not key:
        return None
    project = repo_projects.get_by_key(db, key)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"项目不存在: {key}",
        )
    return project.id


@router.get("/rules", summary="列出所有规则")
def list_all_rules(
    project: str | None = Query(
        None, description=f"按项目 key 筛选；传 {UNASSIGNED} 查未分组"
    ),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if project == UNASSIGNED:
        rows = repo_rules.list_rules(db, unassigned=True)
    else:
        rows = repo_rules.list_rules(db, project_key=project)
    items = [repo_rules.to_api_dict(r) for r in rows]
    return {"count": len(items), "rules": items}


@router.get("/rules/{rule_id}", summary="按 id 查询单条规则")
def get_single_rule(rule_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = repo_rules.get_by_rule_id(db, rule_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"rule_id 不存在: {rule_id}",
        )
    return repo_rules.to_api_dict(row)


@router.post("/rules", summary="新增规则", status_code=status.HTTP_201_CREATED)
def create_rule(payload: RuleIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    if repo_rules.get_by_rule_id(db, payload.id) is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"rule_id 已存在: {payload.id}",
        )

    project_id = _resolve_project_id(db, payload.project)
    data = payload.model_dump(exclude_none=True)
    data.pop("project", None)

    repo_rules.create(db, data, project_id=project_id)
    db.commit()

    n = repo_rules.count(db)
    log.info("新增规则 id=%s,当前共 %d 条", payload.id, n)
    return {"status": "ok", "rule_id": payload.id, "rules_loaded": n}


@router.put("/rules/{rule_id}", summary="按 id 全量修改规则(支持 rename:body.id != url id)")
def update_rule(
    rule_id: str, payload: RuleIn, db: Session = Depends(get_db)
) -> dict[str, Any]:
    row = repo_rules.get_by_rule_id(db, rule_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"rule_id 不存在: {rule_id}",
        )

    if payload.id != rule_id:
        clash = repo_rules.get_by_rule_id(db, payload.id)
        if clash is not None and clash.id != row.id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"目标 id 已被占用: {payload.id}",
            )

    project_id = _resolve_project_id(db, payload.project)
    data = payload.model_dump(exclude_none=True)
    data.pop("project", None)

    repo_rules.update(db, row, data, project_id=project_id, set_project=True)
    db.commit()

    n = repo_rules.count(db)
    if payload.id != rule_id:
        log.info("更新规则 id=%s → %s,当前共 %d 条", rule_id, payload.id, n)
    else:
        log.info("更新规则 id=%s,当前共 %d 条", rule_id, n)
    return {"status": "ok", "rule_id": payload.id, "rules_loaded": n}


@router.delete("/rules/{rule_id}", summary="按 id 删除规则")
def delete_rule(rule_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = repo_rules.get_by_rule_id(db, rule_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"rule_id 不存在: {rule_id}",
        )

    repo_rules.delete(db, row)
    db.commit()

    n = repo_rules.count(db)
    log.info("删除规则 id=%s,当前共 %d 条", rule_id, n)
    return {"status": "ok", "rule_id": rule_id, "rules_loaded": n}
