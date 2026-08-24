"""``/admin/history`` 落库后的行为。响应结构与内存版保持一致。"""

from __future__ import annotations

import pytest

from app.db import repo_history

REC = {
    "rule_id": "R001",
    "rule_name": "总投资一致",
    "project_key": "zf",
    "project_name": "政府投资",
    "fields": [{"key": "field_a", "value": "1"}],
    "result": True,
    "reason": "ok",
    "time_ms": 120,
    "cost": {"total_tokens": 4},
    "raw_request": {"rule_id": "R001"},
}


@pytest.fixture
def seeded(db):
    ids = [
        repo_history.add(db, dict(REC, rule_id="R001", result=True)),
        repo_history.add(db, dict(REC, rule_id="R002", result=False)),
    ]
    db.commit()
    return ids


def test_list_newest_first(client, seeded):
    body = client.get("/admin/history").json()
    assert body["total"] == 2
    assert body["items"][0]["rule_id"] == "R002"


def test_survives_restart_semantics(client, db):
    """落库的意义：数据不随进程内状态消失。"""
    repo_history.add(db, REC)
    db.commit()
    assert client.get("/admin/history").json()["total"] == 1


def test_filter_by_rule_id(client, seeded):
    assert client.get("/admin/history?rule_id=R002").json()["total"] == 1


def test_filter_by_result(client, seeded):
    body = client.get("/admin/history?result=false").json()
    assert body["total"] == 1
    assert body["items"][0]["rule_id"] == "R002"


def test_pagination(client, seeded):
    body = client.get("/admin/history?limit=1&offset=0").json()
    assert body["total"] == 2
    assert len(body["items"]) == 1


def test_get_detail(client, seeded):
    rid = seeded[0]
    body = client.get(f"/admin/history/{rid}").json()
    assert body["id"] == rid
    assert body["raw_request"] == {"rule_id": "R001"}


def test_get_unknown_returns_404(client, seeded):
    assert client.get("/admin/history/99999").status_code == 404


def test_get_non_numeric_id_returns_404_not_500(client, seeded):
    assert client.get("/admin/history/abc").status_code == 404


def test_clear(client, seeded):
    resp = client.delete("/admin/history")
    assert resp.status_code == 200
    assert "2" in resp.json()["message"]
    assert client.get("/admin/history").json()["total"] == 0
