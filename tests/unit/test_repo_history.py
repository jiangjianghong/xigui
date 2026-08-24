"""历史仓储。id 对外仍是字符串，避免前端跟着改。"""

from __future__ import annotations

from app.db import repo_history as repo

REC = {
    "rule_id": "R001",
    "rule_name": "总投资一致",
    "project_key": "zf",
    "project_name": "政府投资",
    "fields": [{"key": "field_a", "value": "1", "name": None}],
    "result": True,
    "reason": "ok",
    "time_ms": 120,
    "cost": {"total_tokens": 4},
    "raw_request": {"rule_id": "R001"},
}


def test_add_returns_str_id(db):
    """对外 id 是字符串：内存版曾用 uuid[:8]，改成数字会波及前端。"""
    rid = repo.add(db, REC)
    db.commit()
    assert isinstance(rid, str)


def test_get_roundtrip(db):
    rid = repo.add(db, REC)
    db.commit()
    got = repo.get(db, rid)
    assert got is not None
    assert repo.to_api_dict(got)["reason"] == "ok"


def test_get_non_numeric_id_returns_none(db):
    """前端传脏 id 不该 500。"""
    assert repo.get(db, "abc") is None


def test_newest_first(db):
    repo.add(db, dict(REC, rule_id="R001"))
    repo.add(db, dict(REC, rule_id="R002"))
    db.commit()
    items = repo.list_records(db)["items"]
    assert [i["rule_id"] for i in items] == ["R002", "R001"]


def test_filter_by_rule_id(db):
    repo.add(db, dict(REC, rule_id="R001"))
    repo.add(db, dict(REC, rule_id="R002"))
    db.commit()
    out = repo.list_records(db, rule_id="R002")
    assert out["total"] == 1


def test_filter_by_result(db):
    repo.add(db, dict(REC, result=True))
    repo.add(db, dict(REC, result=False))
    db.commit()
    assert repo.list_records(db, result=False)["total"] == 1


def test_pagination(db):
    for i in range(5):
        repo.add(db, dict(REC, rule_id=f"R{i}"))
    db.commit()
    out = repo.list_records(db, limit=2, offset=1)
    assert out["total"] == 5
    assert len(out["items"]) == 2


def test_clear(db):
    repo.add(db, REC)
    db.commit()
    assert repo.clear(db) == 1
    db.commit()
    assert repo.list_records(db)["total"] == 0


def test_to_api_dict_keeps_legacy_shape(db):
    """字段名与内存版一致，前端历史页零改动。"""
    rid = repo.add(db, REC)
    db.commit()
    d = repo.to_api_dict(repo.get(db, rid))
    assert set(d) >= {
        "id", "rule_id", "rule_name", "fields", "result",
        "reason", "time_ms", "cost", "created_at",
    }
    assert d["id"] == rid
