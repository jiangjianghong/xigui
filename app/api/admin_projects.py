"""项目 CRUD 与规则的批量移入移出。

删项目只解除规则归属,绝不级联删规则——删项目在 UI 上点两下就能干,
让它有能力抹掉几十条精心调过的 prompt 太危险。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import repo_projects, repo_rules
from app.db.models import Project
from app.db.session import get_db
from app.schemas.rules import ProjectIn, RuleIdsIn

router = APIRouter(tags=["admin-projects"])
log = logging.getLogger("aicheck.admin.projects")


def _must_get(db: Session, key: str) -> Project:
    project = repo_projects.get_by_key(db, key)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目不存在: {key}",
        )
    return project


@router.get("/projects", summary="列出所有项目")
def list_all_projects(db: Session = Depends(get_db)) -> dict[str, Any]:
    counts = repo_projects.rule_counts(db)
    items = [
        repo_projects.to_api_dict(p, rule_count=counts.get(p.id, 0))
        for p in repo_projects.list_projects(db)
    ]
    return {"count": len(items), "projects": items}


@router.get("/projects/{key}", summary="按 key 查询单个项目")
def get_single_project(key: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    project = _must_get(db, key)
    counts = repo_projects.rule_counts(db)
    return repo_projects.to_api_dict(project, rule_count=counts.get(project.id, 0))


@router.post("/projects", summary="新增项目", status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    if repo_projects.get_by_key(db, payload.key) is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"项目 key 已存在: {payload.key}",
        )

    repo_projects.create(
        db, key=payload.key, name=payload.name, description=payload.description
    )
    db.commit()
    log.info("新增项目 key=%s name=%s", payload.key, payload.name)
    return {"status": "ok", "key": payload.key}


@router.put("/projects/{key}", summary="按 key 修改项目(支持改 key)")
def update_project(
    key: str, payload: ProjectIn, db: Session = Depends(get_db)
) -> dict[str, Any]:
    project = _must_get(db, key)

    if payload.key != key:
        clash = repo_projects.get_by_key(db, payload.key)
        if clash is not None and clash.id != project.id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"目标 key 已被占用: {payload.key}",
            )

    repo_projects.update(
        db, project, key=payload.key, name=payload.name,
        description=payload.description,
    )
    db.commit()
    log.info("更新项目 key=%s → %s", key, payload.key)
    return {"status": "ok", "key": payload.key}


@router.delete("/projects/{key}", summary="删除项目(其下规则变为未分组)")
def delete_project(key: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    project = _must_get(db, key)
    n = repo_projects.delete(db, project)
    db.commit()
    log.info("删除项目 key=%s,%d 条规则变为未分组", key, n)
    return {"status": "ok", "key": key, "unassigned_rules": n}


# ---------------------------------------------------------------------------
# 批量移入 / 移出
# ---------------------------------------------------------------------------

@router.post("/projects/{key}/rules", summary="批量把规则移入项目")
def assign_rules(
    key: str, payload: RuleIdsIn, db: Session = Depends(get_db)
) -> dict[str, Any]:
    project = _must_get(db, key)
    n = repo_rules.assign_project(db, payload.rule_ids, project.id)
    db.commit()
    log.info("移入项目 key=%s,%d 条规则", key, n)
    return {"status": "ok", "key": key, "moved": n}


@router.post("/rules/unassign", summary="批量把规则移出项目(置为未分组)")
def unassign_rules(payload: RuleIdsIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    """不设计成 ``DELETE /projects/{key}/rules``:带 body 的 DELETE 虽合法,
    但部分客户端与代理会静默丢弃 body;且移出本质是置 NULL,不需要知道原项目。
    """
    n = repo_rules.assign_project(db, payload.rule_ids, None)
    db.commit()
    log.info("移出项目,%d 条规则变为未分组", n)
    return {"status": "ok", "moved": n}
