"""导入导出编排:解析 → 校验 → 事务内 upsert。

事务边界:本模块只 ``flush`` 不 ``commit``。提交或回滚由调用方(路由)决定,
这样「全文件单事务」才成立——校验失败时路由 rollback,库里什么都不会留下。

校验一律跑在所有写操作**之前**。若边写边校验,第 5 条出错时前 4 条已经
flush 进会话,虽然最终会回滚,但错误定位和资源消耗都没必要。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.rules_io import dump_bundle, parse_bundle
from app.db import repo_projects, repo_rules
from app.schemas.rules import ProjectIn, RuleIn

log = logging.getLogger("aicheck.rules_sync")


class BundleValidationError(ValueError):
    """导入内容校验失败。消息里指明是第几条、错在哪。"""


@dataclass
class ImportResult:
    projects_created: int = 0
    projects_updated: int = 0
    rules_created: int = 0
    rules_updated: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "projects": {
                "created": self.projects_created,
                "updated": self.projects_updated,
            },
            "rules": {
                "created": self.rules_created,
                "updated": self.rules_updated,
            },
            "warnings": self.warnings,
        }


def _validate(
    raw_projects: list[dict[str, Any]], raw_rules: list[dict[str, Any]]
) -> tuple[list[ProjectIn], list[RuleIn]]:
    projects: list[ProjectIn] = []
    for i, raw_p in enumerate(raw_projects, start=1):
        try:
            projects.append(ProjectIn(**raw_p))
        except (ValidationError, TypeError) as e:
            raise BundleValidationError(f"第 {i} 个项目校验失败: {e}") from e

    seen_keys: set[str] = set()
    for p in projects:
        if p.key in seen_keys:
            raise BundleValidationError(f"文件内项目 key 重复: {p.key}")
        seen_keys.add(p.key)

    rules: list[RuleIn] = []
    for i, raw_r in enumerate(raw_rules, start=1):
        try:
            rules.append(RuleIn(**raw_r))
        except (ValidationError, TypeError) as e:
            raise BundleValidationError(f"第 {i} 条规则校验失败: {e}") from e

    seen_ids: set[str] = set()
    for r in rules:
        if r.id in seen_ids:
            raise BundleValidationError(f"文件内规则 id 重复: {r.id}")
        seen_ids.add(r.id)

    return projects, rules


def import_bundle(db: Session, text: str) -> ImportResult:
    """合并 upsert。**不提交**,由调用方 commit / rollback。"""
    raw_projects, raw_rules = parse_bundle(text)
    projects, rules = _validate(raw_projects, raw_rules)

    res = ImportResult()

    # 项目按 key upsert
    for p in projects:
        existing_project = repo_projects.get_by_key(db, p.key)
        if existing_project is None:
            repo_projects.create(db, key=p.key, name=p.name, description=p.description)
            res.projects_created += 1
        else:
            repo_projects.update(
                db, existing_project, name=p.name, description=p.description
            )
            res.projects_updated += 1

    # 规则按 id upsert
    for r in rules:
        data = r.model_dump(exclude_none=True)
        data.pop("project", None)

        project_id: int | None = None
        if r.project:
            target = repo_projects.get_by_key(db, r.project)
            if target is None:
                res.warnings.append(
                    f"规则 {r.id} 引用的项目 {r.project} 未在文件中定义,已置为未分组"
                )
            else:
                project_id = target.id

        existing_rule = repo_rules.get_by_rule_id(db, r.id)
        if existing_rule is None:
            repo_rules.create(db, data, project_id=project_id)
            res.rules_created += 1
        else:
            repo_rules.update(
                db, existing_rule, data, project_id=project_id, set_project=True
            )
            res.rules_updated += 1

    log.info(
        "导入完成 项目 +%d/~%d 规则 +%d/~%d 警告 %d",
        res.projects_created, res.projects_updated,
        res.rules_created, res.rules_updated, len(res.warnings),
    )
    return res


def export_bundle(
    db: Session,
    *,
    project_key: str | None = None,
    unassigned: bool = False,
) -> str:
    """导出 yaml 文本。按项目导出时 ``projects:`` 只含该项目。"""
    rules = repo_rules.list_rules(db, project_key=project_key, unassigned=unassigned)

    if unassigned:
        projects = []
    elif project_key:
        one = repo_projects.get_by_key(db, project_key)
        projects = [one] if one is not None else []
    else:
        projects = repo_projects.list_projects(db)

    project_dicts = [
        {"key": p.key, "name": p.name, "description": p.description or ""}
        for p in projects
    ]
    rule_dicts = []
    for r in rules:
        d = repo_rules.to_rule_cfg(r)
        if r.project is not None:
            d = {
                "id": d["id"],
                "project": r.project.key,
                **{k: v for k, v in d.items() if k != "id"},
            }
        rule_dicts.append(d)

    return dump_bundle(project_dicts, rule_dicts)
