"""规则与项目的 Pydantic 模型。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.rules import ProjectIn, RuleIdsIn, RuleIn

BASE = {"id": "R001", "prompt_template": "A:{field_a}"}


def test_rule_minimal():
    r = RuleIn(**BASE)
    assert r.id == "R001"
    assert r.require_field_name is False
    assert r.project is None


def test_rule_accepts_project_key():
    assert RuleIn(**BASE, project="zf").project == "zf"


def test_rule_project_none_is_valid():
    assert RuleIn(**BASE, project=None).project is None


@pytest.mark.parametrize("bad", [
    ["建设单位A"], [""], ["has space"], ["1abc"], ["a-b"],
    ["same", "same"], ["rule_id"],
])
def test_invalid_field_defs(bad):
    with pytest.raises(ValidationError):
        RuleIn(**BASE, field_defs=bad)


@pytest.mark.parametrize("ok", [["a", "b"], ["_x"], ["A1"], []])
def test_valid_field_defs(ok):
    RuleIn(**BASE, field_defs=ok)


# ---------------------------------------------------------------------------
# 项目
# ---------------------------------------------------------------------------

def test_project_minimal():
    p = ProjectIn(key="zf", name="政府投资")
    assert p.description == ""


@pytest.mark.parametrize("bad", ["政府投资", "", "has space", "1abc", "a-b"])
def test_invalid_project_key(bad):
    """key 与 field_defs 用同一套正则，行为必须一致。"""
    with pytest.raises(ValidationError):
        ProjectIn(key=bad, name="x")


@pytest.mark.parametrize("ok", ["zf", "_x", "A1", "zhengfu_touzi"])
def test_valid_project_key(ok):
    ProjectIn(key=ok, name="x")


def test_project_name_required():
    with pytest.raises(ValidationError):
        ProjectIn(key="zf", name="")


def test_rule_ids_in():
    assert RuleIdsIn(rule_ids=["a", "b"]).rule_ids == ["a", "b"]


def test_rule_ids_rejects_empty():
    with pytest.raises(ValidationError):
        RuleIdsIn(rule_ids=[])
