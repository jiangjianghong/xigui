"""导入导出编排。事务与校验的边界在这里。"""

from __future__ import annotations

import pytest

from app.core import rules_sync
from app.core.rules_io import parse_bundle
from app.db import repo_projects, repo_rules
from app.db.models import Rule

BUNDLE = """
projects:
  - key: zf
    name: 政府投资
    description: 政府投资项目审核

rules:
  - id: R001
    project: zf
    name: 总投资一致
    prompt_template: "A:{field_a} B:{field_b}"
  - id: R002
    name: 未分组的
    prompt_template: "A:{field_a}"
"""


def test_import_creates_projects_and_rules(db):
    res = rules_sync.import_bundle(db, BUNDLE)
    db.commit()

    assert res.projects_created == 1
    assert res.rules_created == 2
    assert res.rules_updated == 0
    assert repo_projects.get_by_key(db, "zf") is not None
    assert repo_rules.get_by_rule_id(db, "R001").project.key == "zf"
    assert repo_rules.get_by_rule_id(db, "R002").project_id is None


def test_import_is_upsert_not_replace(db):
    """库里有而文件里没有的规则必须原封不动。"""
    repo_rules.create(db, {"id": "KEEP", "prompt_template": "t"})
    db.commit()

    rules_sync.import_bundle(db, BUNDLE)
    db.commit()

    assert repo_rules.get_by_rule_id(db, "KEEP") is not None
    assert repo_rules.count(db) == 3


def test_import_updates_existing_rule(db):
    repo_rules.create(db, {"id": "R001", "name": "旧名", "prompt_template": "old"})
    db.commit()

    res = rules_sync.import_bundle(db, BUNDLE)
    db.commit()

    assert res.rules_created == 1
    assert res.rules_updated == 1
    assert repo_rules.get_by_rule_id(db, "R001").name == "总投资一致"


def test_import_preserves_existing_sort_order(db):
    """导入片段不能打乱现有顺序。"""
    repo_rules.create(db, {"id": "R001", "prompt_template": "t"})
    repo_rules.create(db, {"id": "ZZZ", "prompt_template": "t"})
    db.commit()
    before = repo_rules.get_by_rule_id(db, "R001").sort_order

    rules_sync.import_bundle(db, BUNDLE)
    db.commit()

    assert repo_rules.get_by_rule_id(db, "R001").sort_order == before
    assert [r.rule_id for r in repo_rules.list_rules(db)] == ["R001", "ZZZ", "R002"]


def test_import_updates_existing_project(db):
    repo_projects.create(db, key="zf", name="旧名")
    db.commit()

    res = rules_sync.import_bundle(db, BUNDLE)
    db.commit()

    assert res.projects_created == 0
    assert res.projects_updated == 1
    assert repo_projects.get_by_key(db, "zf").name == "政府投资"


def test_undefined_project_warns_and_unassigns(db):
    text = """
rules:
  - id: R001
    project: ghost
    prompt_template: "A:{field_a}"
"""
    res = rules_sync.import_bundle(db, text)
    db.commit()

    assert repo_rules.get_by_rule_id(db, "R001").project_id is None
    assert len(res.warnings) == 1
    assert "ghost" in res.warnings[0]


def test_existing_project_in_db_counts_as_defined(db):
    """文件没定义但库里已有同 key 项目 → 直接关联，不算警告。"""
    repo_projects.create(db, key="zf", name="政府投资")
    db.commit()

    text = """
rules:
  - id: R001
    project: zf
    prompt_template: "A:{field_a}"
"""
    res = rules_sync.import_bundle(db, text)
    db.commit()

    assert res.warnings == []
    assert repo_rules.get_by_rule_id(db, "R001").project.key == "zf"


# ---------------------------------------------------------------------------
# 校验与回滚
# ---------------------------------------------------------------------------

def test_invalid_rule_raises_before_any_write(db):
    """一条非法 → 整体不落地。校验必须跑在所有写操作之前。"""
    text = """
rules:
  - id: GOOD
    prompt_template: "A:{field_a}"
  - id: BAD
    prompt_template: ""
"""
    with pytest.raises(rules_sync.BundleValidationError) as ei:
        rules_sync.import_bundle(db, text)

    assert "第 2 条" in str(ei.value)
    assert db.query(Rule).count() == 0


def test_invalid_field_defs_rejected(db):
    text = """
rules:
  - id: R001
    field_defs: ["建设单位A"]
    prompt_template: "A:{field_a}"
"""
    with pytest.raises(rules_sync.BundleValidationError):
        rules_sync.import_bundle(db, text)


def test_invalid_project_key_rejected(db):
    text = """
projects:
  - key: "有空格 key"
    name: x
rules: []
"""
    with pytest.raises(rules_sync.BundleValidationError, match="第 1 个项目"):
        rules_sync.import_bundle(db, text)


def test_duplicate_rule_id_in_file_rejected(db):
    text = """
rules:
  - id: DUP
    prompt_template: "a"
  - id: DUP
    prompt_template: "b"
"""
    with pytest.raises(rules_sync.BundleValidationError, match="重复"):
        rules_sync.import_bundle(db, text)


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

def test_export_roundtrip(db):
    rules_sync.import_bundle(db, BUNDLE)
    db.commit()

    text = rules_sync.export_bundle(db)
    projects, rules = parse_bundle(text)

    assert [p["key"] for p in projects] == ["zf"]
    assert [r["id"] for r in rules] == ["R001", "R002"]
    assert rules[0]["project"] == "zf"
    assert "project" not in rules[1]


def test_export_reimport_into_empty_db_restores_everything(db, engine):
    """导出→导入必须完整还原，否则备份没有意义。"""
    from sqlalchemy.orm import sessionmaker

    rules_sync.import_bundle(db, BUNDLE)
    db.commit()
    text = rules_sync.export_bundle(db)

    from app.db.models import Base
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    fresh = sessionmaker(bind=engine)()

    rules_sync.import_bundle(fresh, text)
    fresh.commit()

    assert repo_rules.get_by_rule_id(fresh, "R001").project.key == "zf"
    assert repo_rules.get_by_rule_id(fresh, "R002").project_id is None
    fresh.close()


def test_export_filtered_by_project(db):
    rules_sync.import_bundle(db, BUNDLE)
    db.commit()

    projects, rules = parse_bundle(rules_sync.export_bundle(db, project_key="zf"))
    assert [r["id"] for r in rules] == ["R001"]
    assert [p["key"] for p in projects] == ["zf"]


def test_export_unassigned_only(db):
    rules_sync.import_bundle(db, BUNDLE)
    db.commit()

    projects, rules = parse_bundle(rules_sync.export_bundle(db, unassigned=True))
    assert [r["id"] for r in rules] == ["R002"]
    assert projects == []
