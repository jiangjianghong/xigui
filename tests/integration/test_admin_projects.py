"""``/admin/projects`` CRUD 与批量移入移出。"""

from __future__ import annotations

import pytest

from app.db import repo_rules

NEW_PROJECT = {"key": "zf", "name": "政府投资", "description": "政府投资项目审核"}


@pytest.fixture
def seeded(db):
    for i in range(1, 4):
        repo_rules.create(db, {"id": f"R{i:03d}", "prompt_template": "t"})
    db.commit()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def test_list_empty(client):
    assert client.get("/admin/projects").json() == {"count": 0, "projects": []}


def test_create_and_list(client):
    assert client.post("/admin/projects", json=NEW_PROJECT).status_code == 201
    body = client.get("/admin/projects").json()
    assert body["count"] == 1
    assert body["projects"][0]["key"] == "zf"
    assert body["projects"][0]["rule_count"] == 0


def test_rule_count_reflects_assignment(client, seeded):
    client.post("/admin/projects", json=NEW_PROJECT)
    client.post("/admin/projects/zf/rules", json={"rule_ids": ["R001", "R002"]})
    assert client.get("/admin/projects").json()["projects"][0]["rule_count"] == 2


def test_get_single(client):
    client.post("/admin/projects", json=NEW_PROJECT)
    assert client.get("/admin/projects/zf").json()["name"] == "政府投资"


def test_get_unknown_returns_404(client):
    assert client.get("/admin/projects/ghost").status_code == 404


def test_create_duplicate_key_returns_422(client):
    client.post("/admin/projects", json=NEW_PROJECT)
    resp = client.post("/admin/projects", json=NEW_PROJECT)
    assert resp.status_code == 422
    assert "已存在" in resp.json()["detail"]


@pytest.mark.parametrize("bad_key", ["政府投资", "has space", "1abc", "a-b"])
def test_create_illegal_key_returns_422(client, bad_key):
    assert client.post(
        "/admin/projects", json=dict(NEW_PROJECT, key=bad_key)
    ).status_code == 422


def test_update(client):
    client.post("/admin/projects", json=NEW_PROJECT)
    resp = client.put("/admin/projects/zf", json={
        "key": "zf", "name": "政府投资(改)", "description": "新描述"
    })
    assert resp.status_code == 200
    assert client.get("/admin/projects/zf").json()["name"] == "政府投资(改)"


def test_update_can_change_key_and_keeps_rules(client, seeded):
    """改 key 不影响归属——规则通过 project_id 关联。"""
    client.post("/admin/projects", json=NEW_PROJECT)
    client.post("/admin/projects/zf/rules", json={"rule_ids": ["R001"]})

    resp = client.put("/admin/projects/zf", json={"key": "zf2", "name": "政府投资"})
    assert resp.status_code == 200

    assert client.get("/admin/projects/zf").status_code == 404
    assert client.get("/admin/projects/zf2").json()["rule_count"] == 1
    assert client.get("/admin/rules/R001").json()["project"]["key"] == "zf2"


def test_update_key_clash_returns_422(client):
    client.post("/admin/projects", json=NEW_PROJECT)
    client.post("/admin/projects", json={"key": "other", "name": "另一个"})
    resp = client.put("/admin/projects/zf", json={"key": "other", "name": "x"})
    assert resp.status_code == 422
    assert "已被占用" in resp.json()["detail"]


def test_update_unknown_returns_404(client):
    assert client.put(
        "/admin/projects/ghost", json={"key": "ghost", "name": "x"}
    ).status_code == 404


# ---------------------------------------------------------------------------
# 删除：只解除归属，绝不级联删规则
# ---------------------------------------------------------------------------

def test_delete_unassigns_rules_but_keeps_them(client, seeded):
    client.post("/admin/projects", json=NEW_PROJECT)
    client.post("/admin/projects/zf/rules", json={"rule_ids": ["R001", "R002"]})

    resp = client.delete("/admin/projects/zf")
    assert resp.status_code == 200
    assert resp.json()["unassigned_rules"] == 2

    assert client.get("/admin/rules").json()["count"] == 3
    assert client.get("/admin/rules/R001").json()["project"] is None


def test_delete_unknown_returns_404(client):
    assert client.delete("/admin/projects/ghost").status_code == 404


# ---------------------------------------------------------------------------
# 批量移入移出
# ---------------------------------------------------------------------------

def test_batch_assign(client, seeded):
    client.post("/admin/projects", json=NEW_PROJECT)
    resp = client.post("/admin/projects/zf/rules", json={"rule_ids": ["R001", "R002"]})
    assert resp.status_code == 200
    assert resp.json()["moved"] == 2
    assert client.get("/admin/rules?project=zf").json()["count"] == 2


def test_batch_assign_unknown_project_returns_404(client, seeded):
    assert client.post(
        "/admin/projects/ghost/rules", json={"rule_ids": ["R001"]}
    ).status_code == 404


def test_batch_assign_ignores_unknown_rule_ids(client, seeded):
    client.post("/admin/projects", json=NEW_PROJECT)
    resp = client.post("/admin/projects/zf/rules", json={"rule_ids": ["R001", "NOPE"]})
    assert resp.json()["moved"] == 1


def test_batch_unassign(client, seeded):
    client.post("/admin/projects", json=NEW_PROJECT)
    client.post("/admin/projects/zf/rules", json={"rule_ids": ["R001", "R002"]})

    resp = client.post("/admin/rules/unassign", json={"rule_ids": ["R001"]})
    assert resp.status_code == 200
    assert resp.json()["moved"] == 1
    assert client.get("/admin/rules?project=zf").json()["count"] == 1
    assert client.get("/admin/rules/R001").json()["project"] is None


def test_batch_empty_list_returns_422(client):
    assert client.post("/admin/rules/unassign", json={"rule_ids": []}).status_code == 422
