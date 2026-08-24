"""项目仓储。"""

from __future__ import annotations

from app.db import repo_projects as repo
from app.db.models import Rule


def test_create_and_get(db):
    p = repo.create(db, key="zhengfu", name="政府投资", description="d")
    db.commit()
    got = repo.get_by_key(db, "zhengfu")
    assert got is not None
    assert got.id == p.id
    assert got.name == "政府投资"


def test_get_missing_returns_none(db):
    assert repo.get_by_key(db, "nope") is None


def test_sort_order_increments(db):
    a = repo.create(db, key="a", name="A")
    b = repo.create(db, key="b", name="B")
    db.commit()
    assert b.sort_order == a.sort_order + 1


def test_list_ordered_by_sort_order(db):
    repo.create(db, key="b", name="B")
    repo.create(db, key="a", name="A")
    db.commit()
    assert [p.key for p in repo.list_projects(db)] == ["b", "a"]


def test_update_fields(db):
    p = repo.create(db, key="a", name="A")
    db.commit()
    repo.update(db, p, key="a2", name="A2", description="dd")
    db.commit()
    assert repo.get_by_key(db, "a2").description == "dd"
    assert repo.get_by_key(db, "a") is None


def test_delete_returns_affected_rule_count_and_keeps_rules(db):
    """删项目必须只解除归属，不能级联删规则。"""
    p = repo.create(db, key="a", name="A")
    db.flush()
    db.add(Rule(rule_id="R001", prompt_template="t", project_id=p.id))
    db.add(Rule(rule_id="R002", prompt_template="t", project_id=p.id))
    db.add(Rule(rule_id="R003", prompt_template="t"))
    db.commit()

    n = repo.delete(db, p)
    db.commit()

    assert n == 2
    assert db.query(Rule).count() == 3
    assert all(r.project_id is None for r in db.query(Rule).all())


def test_rule_counts(db):
    p = repo.create(db, key="a", name="A")
    db.flush()
    db.add(Rule(rule_id="R001", prompt_template="t", project_id=p.id))
    db.add(Rule(rule_id="R002", prompt_template="t"))
    db.commit()
    assert repo.rule_counts(db) == {p.id: 1}


def test_to_api_dict_shape(db):
    p = repo.create(db, key="a", name="A", description="d")
    db.commit()
    assert repo.to_api_dict(p, rule_count=3) == {
        "key": "a", "name": "A", "description": "d", "rule_count": 3,
    }
