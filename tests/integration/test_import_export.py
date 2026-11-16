"""导入导出端点。事务回滚与警告是重点。"""

from __future__ import annotations

import yaml as pyyaml

from app.db import repo_rules

BUNDLE = """
projects:
  - key: zf
    name: 政府投资

rules:
  - id: R001
    project: zf
    name: 总投资一致
    prompt_template: "A:{field_a} B:{field_b}"
  - id: R002
    name: 未分组的
    prompt_template: "A:{field_a}"
"""


def _upload(client, text: str, filename: str = "rules.yaml"):
    return client.post(
        "/admin/import",
        files={"file": (filename, text.encode("utf-8"), "application/x-yaml")},
    )


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------

def test_import_creates_everything(client):
    resp = _upload(client, BUNDLE)
    assert resp.status_code == 200
    body = resp.json()
    assert body["projects"] == {"created": 1, "updated": 0}
    assert body["rules"] == {"created": 2, "updated": 0}
    assert body["warnings"] == []

    assert client.get("/admin/rules").json()["count"] == 2
    assert client.get("/admin/rules/R001").json()["project"]["key"] == "zf"


def test_import_is_merge_not_replace(client, db):
    repo_rules.create(db, {"id": "KEEP", "prompt_template": "t"})
    db.commit()

    _upload(client, BUNDLE)
    assert client.get("/admin/rules/KEEP").status_code == 200
    assert client.get("/admin/rules").json()["count"] == 3


def test_import_second_time_updates(client):
    _upload(client, BUNDLE)
    body = _upload(client, BUNDLE).json()
    assert body["rules"] == {"created": 0, "updated": 2}


def test_import_undefined_project_warns(client):
    text = 'rules:\n  - id: R001\n    project: ghost\n    prompt_template: "x"\n'
    body = _upload(client, text).json()
    assert len(body["warnings"]) == 1
    assert "ghost" in body["warnings"][0]
    assert client.get("/admin/rules/R001").json()["project"] is None


def test_import_syntax_error_returns_422_with_line(client):
    resp = _upload(client, "rules:\n  - id: R001\n   bad: x\n")
    assert resp.status_code == 422
    assert "第 3 行" in resp.json()["detail"]


def test_import_invalid_rule_rolls_back_everything(client):
    """一条非法 → 库里什么都不该留下，连合法的那条也不留。"""
    text = """
projects:
  - key: zf
    name: 政府投资
rules:
  - id: GOOD
    prompt_template: "ok"
  - id: BAD
    prompt_template: ""
"""
    assert _upload(client, text).status_code == 422
    assert client.get("/admin/rules").json()["count"] == 0
    assert client.get("/admin/projects").json()["count"] == 0


def test_import_rejects_non_yaml_extension(client):
    resp = _upload(client, BUNDLE, filename="rules.txt")
    assert resp.status_code == 422
    assert "yaml" in resp.json()["detail"].lower()


def test_import_empty_file_returns_422(client):
    resp = _upload(client, "")
    assert resp.status_code == 422


def test_import_preserves_existing_sort_order(client, db):
    repo_rules.create(db, {"id": "R001", "prompt_template": "t"})
    repo_rules.create(db, {"id": "ZZZ", "prompt_template": "t"})
    db.commit()

    _upload(client, BUNDLE)
    ids = [r["id"] for r in client.get("/admin/rules").json()["rules"]]
    assert ids == ["R001", "ZZZ", "R002"]


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

def test_export_returns_yaml_attachment(client):
    _upload(client, BUNDLE)
    resp = client.get("/admin/export")
    assert resp.status_code == 200
    assert "yaml" in resp.headers["content-type"]
    assert "attachment" in resp.headers["content-disposition"]
    assert ".yaml" in resp.headers["content-disposition"]


def test_export_content_is_parseable(client):
    _upload(client, BUNDLE)
    data = pyyaml.safe_load(client.get("/admin/export").text)
    assert [p["key"] for p in data["projects"]] == ["zf"]
    assert [r["id"] for r in data["rules"]] == ["R001", "R002"]


def test_export_filtered_by_project(client):
    _upload(client, BUNDLE)
    data = pyyaml.safe_load(client.get("/admin/export?project=zf").text)
    assert [r["id"] for r in data["rules"]] == ["R001"]


def test_export_unassigned(client):
    _upload(client, BUNDLE)
    data = pyyaml.safe_load(client.get("/admin/export?project=__none__").text)
    assert [r["id"] for r in data["rules"]] == ["R002"]
    assert "projects" not in data


def test_export_unknown_project_returns_404(client):
    assert client.get("/admin/export?project=ghost").status_code == 404


def test_export_then_import_roundtrip(client):
    """导出→导入必须完整还原，否则备份没有意义。"""
    _upload(client, BUNDLE)
    text = client.get("/admin/export").text

    client.delete("/admin/rules/R001")
    client.delete("/admin/rules/R002")
    client.delete("/admin/projects/zf")
    assert client.get("/admin/rules").json()["count"] == 0

    _upload(client, text)
    assert client.get("/admin/rules/R001").json()["project"]["key"] == "zf"
    assert client.get("/admin/rules/R002").json()["project"] is None


def test_custom_rule_schema_survives_import_export_roundtrip(client):
    schema = {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
        "additionalProperties": False,
    }
    bundle = '''
rules:
  - id: RCUSTOM
    rule_type: custom
    prompt_template: "生成摘要: {field_a}"
    output_schema:
      type: object
      properties:
        summary:
          type: string
      required: [summary]
      additionalProperties: false
'''

    assert _upload(client, bundle).status_code == 200
    exported_text = client.get("/admin/export").text
    exported = pyyaml.safe_load(exported_text)
    rule = exported["rules"][0]
    assert rule["rule_type"] == "custom"
    assert rule["output_schema"] == schema

    client.delete("/admin/rules/RCUSTOM")
    assert _upload(client, exported_text).status_code == 200
    restored = client.get("/admin/rules/RCUSTOM").json()
    assert restored["rule_type"] == "custom"
    assert restored["output_schema"] == schema
