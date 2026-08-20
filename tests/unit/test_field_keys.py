"""字段键推导的单元测试。

覆盖 field_defs 有/无两条路径,以及模板占位符反推的各个分支。
"""

from __future__ import annotations

from app.rules.field_keys import FIELD_LETTERS, default_key, field_keys_for


def test_field_letters_covers_a_to_z():
    assert FIELD_LETTERS[0] == "a"
    assert FIELD_LETTERS[-1] == "z"
    assert len(FIELD_LETTERS) == 26


def test_default_key_maps_index_to_letter():
    assert default_key(0) == "field_a"
    assert default_key(1) == "field_b"
    assert default_key(25) == "field_z"


def test_field_defs_takes_precedence():
    cfg = {
        "field_defs": ["keyan_name", "zixun_name"],
        "prompt_template": "A:{field_a} B:{field_b}",
    }
    assert field_keys_for(cfg) == ["keyan_name", "zixun_name"]


def test_field_defs_preserves_order():
    cfg = {"field_defs": ["z_last", "a_first"], "prompt_template": "x"}
    assert field_keys_for(cfg) == ["z_last", "a_first"]


def test_empty_field_defs_falls_back_to_letters():
    cfg = {"field_defs": [], "prompt_template": "A:{field_a} B:{field_b}"}
    assert field_keys_for(cfg) == ["field_a", "field_b"]


def test_missing_field_defs_falls_back_to_letters():
    cfg = {"prompt_template": "A:{field_a} B:{field_b}"}
    assert field_keys_for(cfg) == ["field_a", "field_b"]


def test_single_letter_template():
    cfg = {"prompt_template": "只判断一个:{field_a}"}
    assert field_keys_for(cfg) == ["field_a"]


def test_three_letter_template():
    cfg = {"prompt_template": "{field_a}/{field_b}/{field_c}"}
    assert field_keys_for(cfg) == ["field_a", "field_b", "field_c"]


def test_name_placeholder_counts_toward_inference():
    """只出现 {field_b_name} 也要把 b 算进去。"""
    cfg = {"prompt_template": "{field_a} 与 {field_b_name}"}
    assert field_keys_for(cfg) == ["field_a", "field_b"]


def test_gap_in_template_is_filled():
    """模板只写了 {field_c},仍返回 a/b/c 三个键——键集合必须连续。"""
    cfg = {"prompt_template": "只引用 {field_c}"}
    assert field_keys_for(cfg) == ["field_a", "field_b", "field_c"]


def test_no_placeholder_returns_empty():
    cfg = {"prompt_template": "这个模板不引用任何字段"}
    assert field_keys_for(cfg) == []


def test_missing_template_uses_fallback():
    """prompt_template 缺失时走 fallback 模板,它引用 field_a/field_b。"""
    assert field_keys_for({}) == ["field_a", "field_b"]


def test_uppercase_placeholder_ignored():
    """{field_A} 不是合法占位符,不参与反推。"""
    cfg = {"prompt_template": "{field_A} 和 {field_a}"}
    assert field_keys_for(cfg) == ["field_a"]
