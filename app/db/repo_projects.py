"""项目仓储。只认 ``projects`` 表，不碰 HTTP，不管事务提交。

提交由调用方（API 路由或 rules_sync）决定——仓储无从判断一次请求里
还有没有别的写操作要一起成败。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Project, Rule


def list_projects(db: Session) -> list[Project]:
    return list(
        db.scalars(select(Project).order_by(Project.sort_order, Project.id)).all()
    )


def get_by_key(db: Session, key: str) -> Project | None:
    return db.scalars(select(Project).where(Project.key == key)).first()


def next_sort_order(db: Session) -> int:
    cur = db.scalar(select(func.max(Project.sort_order)))
    return (cur or 0) + 1


def create(db: Session, *, key: str, name: str, description: str = "") -> Project:
    p = Project(
        key=key,
        name=name,
        description=description or "",
        sort_order=next_sort_order(db),
    )
    db.add(p)
    db.flush()
    return p


def update(
    db: Session,
    project: Project,
    *,
    key: str | None = None,
    name: str | None = None,
    description: str | None = None,
) -> Project:
    if key is not None:
        project.key = key
    if name is not None:
        project.name = name
    if description is not None:
        project.description = description
    db.flush()
    return project


def delete(db: Session, project: Project) -> int:
    """删项目，返回被解除归属的规则数。

    显式先把规则置 NULL 而不是依赖数据库的 ON DELETE SET NULL：后者在
    SQLite 需要 PRAGMA、在 MySQL 需要引擎支持，显式做一次两边行为一致，
    也才能拿到准确的受影响条数返回给 UI。
    """
    n = db.query(Rule).filter(Rule.project_id == project.id).update(
        {Rule.project_id: None}, synchronize_session=False
    )
    db.delete(project)
    db.flush()
    return int(n)


def rule_counts(db: Session) -> dict[int, int]:
    """``{project_id: 规则数}``，未分组不计入。"""
    rows = db.execute(
        select(Rule.project_id, func.count(Rule.id))
        .where(Rule.project_id.is_not(None))
        .group_by(Rule.project_id)
    ).all()
    return {int(pid): int(cnt) for pid, cnt in rows}


def unassigned_count(db: Session) -> int:
    return int(
        db.scalar(select(func.count(Rule.id)).where(Rule.project_id.is_(None))) or 0
    )


def to_api_dict(project: Project, *, rule_count: int = 0) -> dict[str, Any]:
    return {
        "key": project.key,
        "name": project.name,
        "description": project.description or "",
        "rule_count": rule_count,
    }
