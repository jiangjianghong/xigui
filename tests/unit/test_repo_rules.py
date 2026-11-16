"""规则仓储。重点是 to_rule_cfg —— app/rules/ 全靠它保持零改动。"""

from __future__ import annotations

from app.db import repo_projects, repo_rules as repo

BASE = {
    "id": "R001",
    "name": "总投资一致",
    "description": "d",
    "require_field_name": False,
    "system_prompt": "sys",
    "prompt_template": "A:{field_a} B:{field_b}",
}


def test_create_and_get(db):
    repo.create(db, BASE)
    db.commit()
    got = repo.get_by_rule_id(db, "R001")
    assert got is not None
    assert got.name == "总投资一致"


def test_sort_order_increments(db):
    a = repo.create(db, BASE)
    b = repo.create(db, dict(BASE, id="R002"))
    db.commit()
    assert b.sort_order == a.sort_order + 1


def test_list_ordered_by_sort_order(db):
    repo.create(db, dict(BASE, id="R002"))
    repo.create(db, dict(BASE, id="R001"))
    db.commit()
    assert [r.rule_id for r in repo.list_rules(db)] == ["R002", "R001"]


def test_list_filter_by_project(db):
    p = repo_projects.create(db, key="zf", name="政府")
    db.flush()
    repo.create(db, BASE, project_id=p.id)
    repo.create(db, dict(BASE, id="R002"))
    db.commit()

    assert [r.rule_id for r in repo.list_rules(db, project_key="zf")] == ["R001"]
    assert [r.rule_id for r in repo.list_rules(db, unassigned=True)] == ["R002"]
    assert len(repo.list_rules(db)) == 2


def test_list_filter_unknown_project_returns_empty(db):
    repo.create(db, BASE)
    db.commit()
    assert repo.list_rules(db, project_key="nope") == []


def test_update_replaces_all_fields(db):
    r = repo.create(db, BASE)
    db.commit()
    repo.update(db, r, dict(BASE, id="R001", name="改后", field_defs=["a", "b"]))
    db.commit()
    got = repo.get_by_rule_id(db, "R001")
    assert got.name == "改后"
    assert got.field_defs == ["a", "b"]


def test_update_can_rename(db):
    r = repo.create(db, BASE)
    db.commit()
    repo.update(db, r, dict(BASE, id="R099"))
    db.commit()
    assert repo.get_by_rule_id(db, "R001") is None
    assert repo.get_by_rule_id(db, "R099") is not None


def test_update_keeps_sort_order(db):
    """改规则不应改变它在列表里的位置。"""
    r = repo.create(db, BASE)
    db.commit()
    before = r.sort_order
    repo.update(db, r, dict(BASE, name="改后"))
    db.commit()
    assert repo.get_by_rule_id(db, "R001").sort_order == before


def test_assign_project(db):
    p = repo_projects.create(db, key="zf", name="政府")
    db.flush()
    repo.create(db, BASE)
    repo.create(db, dict(BASE, id="R002"))
    db.commit()

    n = repo.assign_project(db, ["R001", "R002"], p.id)
    db.commit()
    assert n == 2
    assert all(r.project_id == p.id for r in repo.list_rules(db))


def test_assign_project_none_unassigns(db):
    p = repo_projects.create(db, key="zf", name="政府")
    db.flush()
    repo.create(db, BASE, project_id=p.id)
    db.commit()

    repo.assign_project(db, ["R001"], None)
    db.commit()
    assert repo.get_by_rule_id(db, "R001").project_id is None


def test_assign_project_ignores_unknown_ids(db):
    repo.create(db, BASE)
    db.commit()
    assert repo.assign_project(db, ["R001", "NOPE"], None) == 1


# ---------------------------------------------------------------------------
# to_rule_cfg：app/rules/ 的输入契约，改坏了 /compare 就跟着坏
# ---------------------------------------------------------------------------

def test_to_rule_cfg_shape(db):
    r = repo.create(db, BASE)
    db.commit()
    assert repo.to_rule_cfg(r) == {
        "id": "R001",
        "name": "总投资一致",
        "description": "d",
        "rule_type": "judge",
        "require_field_name": False,
        "system_prompt": "sys",
        "prompt_template": "A:{field_a} B:{field_b}",
    }


def test_to_rule_cfg_omits_empty_field_defs(db):
    """field_keys.py 用 cfg.get('field_defs') 判断，空列表和缺键必须等价。"""
    r = repo.create(db, dict(BASE, field_defs=[]))
    db.commit()
    assert "field_defs" not in repo.to_rule_cfg(r)


def test_to_rule_cfg_includes_field_defs(db):
    r = repo.create(db, dict(BASE, field_defs=["x", "y"]))
    db.commit()
    assert repo.to_rule_cfg(r)["field_defs"] == ["x", "y"]


# ---------------------------------------------------------------------------
# to_api_dict：/admin/rules 的输出契约
# ---------------------------------------------------------------------------

def test_to_api_dict_without_project(db):
    r = repo.create(db, BASE)
    db.commit()
    d = repo.to_api_dict(r)
    assert d["id"] == "R001"
    assert d["project"] is None
    assert "field_defs" not in d


def test_to_api_dict_with_project(db):
    p = repo_projects.create(db, key="zf", name="政府投资")
    db.flush()
    r = repo.create(db, BASE, project_id=p.id)
    db.commit()
    assert repo.to_api_dict(r)["project"] == {"key": "zf", "name": "政府投资"}
