"""yaml ↔ dict 纯转换。不碰 DB、不碰文件系统。"""

from __future__ import annotations

import pytest
import yaml as pyyaml

from app.core.rules_io import BundleParseError, dump_bundle, parse_bundle

PROJECTS = [{"key": "zf", "name": "政府投资", "description": "d"}]
RULES = [
    {
        "id": "R001",
        "project": "zf",
        "name": "总投资一致",
        "description": "d",
        "require_field_name": False,
        "system_prompt": "你是会计师。",
        "prompt_template": "请判断:\nA: {field_a}\nB: {field_b}\n",
    }
]


def test_dump_has_header_comment():
    text = dump_bundle(PROJECTS, RULES)
    assert text.startswith("#")
    assert "导入语义" in text


def test_dump_is_valid_yaml():
    data = pyyaml.safe_load(dump_bundle(PROJECTS, RULES))
    assert data["projects"][0]["key"] == "zf"
    assert data["rules"][0]["id"] == "R001"


def test_multiline_prompt_uses_block_style():
    """几百字的 prompt 挤成一行带 \\n 的字符串就没法看也没法改。"""
    text = dump_bundle([], RULES)
    assert "prompt_template: |" in text
    assert "\\n" not in text


def test_project_key_follows_id():
    """字段顺序:project 紧跟 id，视觉上归属信息在最前面。"""
    text = dump_bundle([], RULES)
    assert text.index("id: R001") < text.index("project: zf")
    assert text.index("project: zf") < text.index("name:")


def test_unassigned_rule_omits_project_key():
    """未分组不该写成 project: null。"""
    text = dump_bundle([], [dict(RULES[0], project=None)])
    assert "project:" not in text


def test_empty_projects_omits_node():
    assert "projects:" not in dump_bundle([], RULES)


def test_roundtrip():
    projects, rules = parse_bundle(dump_bundle(PROJECTS, RULES))
    assert projects == PROJECTS
    assert rules[0]["id"] == "R001"
    assert rules[0]["project"] == "zf"
    assert rules[0]["prompt_template"] == RULES[0]["prompt_template"]


def test_parse_legacy_file_without_projects():
    """现有 rules.yaml 没有 projects 节点、没有 project 字段，必须能直接导入。"""
    legacy = """
rules:
  - id: cross_rule_01
    name: 总投资一致
    prompt_template: "A:{field_a}"
"""
    projects, rules = parse_bundle(legacy)
    assert projects == []
    assert rules[0]["id"] == "cross_rule_01"
    assert "project" not in rules[0]


def test_parse_empty_text():
    assert parse_bundle("") == ([], [])


def test_parse_syntax_error_reports_line():
    bad = "rules:\n  - id: R001\n   bad_indent: x\n"
    with pytest.raises(BundleParseError) as ei:
        parse_bundle(bad)
    assert "第 3 行" in str(ei.value)


def test_parse_rejects_non_mapping_root():
    with pytest.raises(BundleParseError, match="顶层"):
        parse_bundle("- just\n- a\n- list\n")


def test_parse_rejects_non_list_rules():
    with pytest.raises(BundleParseError, match="rules"):
        parse_bundle("rules: not-a-list\n")
