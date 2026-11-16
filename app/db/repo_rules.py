"""规则仓储。

两个转换函数是本模块的重点：

- ``to_rule_cfg``  ORM 行 → ``app/rules/`` 期待的 plain dict。有它，规则领域层
  （``field_keys`` / ``llm_rule`` / ``registry``）才能对存储介质一无所知。
- ``to_api_dict``  ORM 行 → ``/admin/rules`` 的响应结构，带嵌套 ``project``。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import Project, Rule

# 前端提交的规则字段 → 表列名。``id`` 单列出来因为列名不同。
_FIELDS = (
    "name",
    "description",
    "rule_type",
    "field_defs",
    "require_field_name",
    "system_prompt",
    "prompt_template",
    "output_schema",
)


def list_rules(
    db: Session,
    *,
    project_key: str | None = None,
    unassigned: bool = False,
) -> list[Rule]:
    stmt = select(Rule).options(joinedload(Rule.project))
    if unassigned:
        stmt = stmt.where(Rule.project_id.is_(None))
    elif project_key is not None:
        stmt = stmt.join(Project).where(Project.key == project_key)
    stmt = stmt.order_by(Rule.sort_order, Rule.id)
    return list(db.scalars(stmt).unique().all())


def get_by_rule_id(db: Session, rule_id: str) -> Rule | None:
    return db.scalars(
        select(Rule).options(joinedload(Rule.project)).where(Rule.rule_id == rule_id)
    ).first()


def count(db: Session) -> int:
    return int(db.scalar(select(func.count(Rule.id))) or 0)


def next_sort_order(db: Session) -> int:
    cur = db.scalar(select(func.max(Rule.sort_order)))
    return (cur or 0) + 1


def _apply(rule: Rule, data: dict[str, Any]) -> None:
    """把前端 dict 写进 ORM 行。``sort_order`` 与 ``project_id`` 不在此处理。"""
    rule.rule_id = data["id"]
    for key in _FIELDS:
        if key not in data:
            continue
        val = data[key]
        if key in ("name", "description", "system_prompt"):
            val = val or ""
        if key == "require_field_name":
            val = bool(val)
        if key == "field_defs":
            val = list(val) if val else None
        if key == "rule_type":
            val = val or "judge"
        setattr(rule, key, val)
    effective_type = data.get("rule_type", rule.rule_type or "judge")
    if effective_type == "judge" or (
        effective_type == "custom" and "output_schema" not in data
    ):
        rule.output_schema = None


def create(db: Session, data: dict[str, Any], *, project_id: int | None = None) -> Rule:
    rule = Rule(prompt_template="", sort_order=next_sort_order(db))
    _apply(rule, data)
    rule.project_id = project_id
    db.add(rule)
    db.flush()
    return rule


def update(
    db: Session,
    rule: Rule,
    data: dict[str, Any],
    *,
    project_id: int | None = None,
    set_project: bool = False,
) -> Rule:
    """全量更新。``sort_order`` 保持不变——改内容不该改列表位置。

    ``set_project=False`` 时不动归属，便于「只改内容」的调用方少传参数。
    """
    _apply(rule, data)
    if set_project:
        rule.project_id = project_id
    db.flush()
    return rule


def delete(db: Session, rule: Rule) -> None:
    db.delete(rule)
    db.flush()


def assign_project(
    db: Session, rule_ids: list[str], project_id: int | None
) -> int:
    """批量改归属，返回实际命中的条数。不存在的 id 静默跳过。"""
    if not rule_ids:
        return 0
    n = (
        db.query(Rule)
        .filter(Rule.rule_id.in_(rule_ids))
        .update({Rule.project_id: project_id}, synchronize_session=False)
    )
    db.flush()
    return int(n)


def to_rule_cfg(rule: Rule) -> dict[str, Any]:
    """ORM 行 → ``app/rules/`` 的输入 dict。

    ``field_defs`` 为空时**不放这个键**：``field_keys.py`` 用
    ``cfg.get("field_defs")`` 判断，空列表与缺键必须等价。
    """
    cfg: dict[str, Any] = {
        "id": rule.rule_id,
        "name": rule.name or "",
        "description": rule.description or "",
        "rule_type": rule.rule_type or "judge",
        "require_field_name": bool(rule.require_field_name),
        "system_prompt": rule.system_prompt or "",
        "prompt_template": rule.prompt_template,
    }
    if rule.field_defs:
        cfg["field_defs"] = list(rule.field_defs)
    if rule.output_schema:
        cfg["output_schema"] = rule.output_schema
    return cfg


def to_api_dict(rule: Rule) -> dict[str, Any]:
    """ORM 行 → ``/admin/rules`` 响应。在 ``to_rule_cfg`` 基础上加 ``project``。"""
    out = to_rule_cfg(rule)
    out["project"] = (
        {"key": rule.project.key, "name": rule.project.name} if rule.project else None
    )
    return out
