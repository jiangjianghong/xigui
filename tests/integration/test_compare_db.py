"""/compare 从 DB 取规则后的新增行为：历史快照、DB 故障降级。"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from app.db import repo_history, repo_projects, repo_rules
from app.rules import llm_rule


@pytest.fixture
def stub_llm(monkeypatch):
    def _fake(messages, **kwargs):
        return ('{"match": true, "reason": "ok"}',
                {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4})
    monkeypatch.setattr(llm_rule, "chat_json", _fake)


@pytest.fixture
def rule_in_project(db):
    p = repo_projects.create(db, key="zf", name="政府投资")
    db.flush()
    repo_rules.create(
        db,
        {"id": "R001", "name": "总投资一致", "prompt_template": "A:{field_a} B:{field_b}"},
        project_id=p.id,
    )
    db.commit()


PAYLOAD = {
    "rule_id": "R001",
    "field_a": {"value": "1.2亿"},
    "field_b": {"value": "12000万元"},
}


def test_compare_reads_rule_from_db(client, rule_in_project, stub_llm):
    resp = client.post("/compare", json=PAYLOAD)
    assert resp.status_code == 200
    assert resp.json()["result"] is True


def test_compare_response_has_no_project_field(client, rule_in_project, stub_llm):
    """契约不变：比对响应不新增 project，下游无需改动。"""
    assert "project" not in client.post("/compare", json=PAYLOAD).json()


def test_history_records_project_snapshot(client, db, rule_in_project, stub_llm):
    client.post("/compare", json=PAYLOAD)
    item = repo_history.list_records(db)["items"][0]
    assert item["project_key"] == "zf"
    assert item["project_name"] == "政府投资"
    assert item["rule_name"] == "总投资一致"


def test_history_snapshot_survives_project_deletion(client, db, rule_in_project, stub_llm):
    """历史是快照：项目删了，历史里的项目名还在。"""
    client.post("/compare", json=PAYLOAD)
    client.delete("/admin/projects/zf")

    item = repo_history.list_records(db)["items"][0]
    assert item["project_name"] == "政府投资"


def test_history_write_failure_does_not_break_compare(
    client, rule_in_project, stub_llm, monkeypatch
):
    """LLM 已调用付费、结果已算出，因记流水失败而返 500 不合理。"""
    def _boom(*a, **kw):
        raise OperationalError("INSERT", {}, Exception("db down"))

    monkeypatch.setattr(repo_history, "add", _boom)

    resp = client.post("/compare", json=PAYLOAD)
    assert resp.status_code == 200
    assert resp.json()["result"] is True


def test_unknown_rule_returns_404(client, rule_in_project, stub_llm):
    assert client.post("/compare", json=dict(PAYLOAD, rule_id="NOPE")).status_code == 404


def test_db_unavailable_returns_503(client, rule_in_project, stub_llm, monkeypatch):
    """依赖不可用是 503，不是 500。"""
    def _boom(*a, **kw):
        raise OperationalError("SELECT", {}, Exception("db down"))

    monkeypatch.setattr(repo_rules, "get_by_rule_id", _boom)

    resp = client.post("/compare", json=PAYLOAD)
    assert resp.status_code == 503
    assert "数据库" in resp.json()["detail"]
