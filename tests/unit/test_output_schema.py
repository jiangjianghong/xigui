"""Custom structured-output schema validation and prompt generation."""

from __future__ import annotations

import pytest

from app.rules.output_schema import (
    OutputSchemaError,
    build_output_instruction,
    validate_output,
    validate_output_schema,
)


SCHEMA = {
    "type": "object",
    "properties": {
        "classification": {"type": "string", "enum": ["通过", "不通过"]},
        "details": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"field": {"type": "string"}},
                "required": ["field"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["classification"],
    "additionalProperties": False,
}


def test_valid_nested_schema_and_value():
    validate_output_schema(SCHEMA)
    validate_output({"classification": "通过", "details": [{"field": "金额"}]}, SCHEMA)


@pytest.mark.parametrize("schema", [
    [],
    {"type": "array", "items": {"type": "string"}},
    {"type": "object", "properties": {}},
    {"type": "object", "properties": {"x": {"type": "unknown"}}},
    {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["y"]},
])
def test_invalid_schema_is_rejected(schema):
    with pytest.raises(OutputSchemaError):
        validate_output_schema(schema)


@pytest.mark.parametrize("value", [
    {"classification": "其他"},
    {"classification": "通过", "extra": 1},
    {"classification": "通过", "details": [{"field": 3}]},
])
def test_invalid_values_are_rejected(value):
    with pytest.raises(OutputSchemaError):
        validate_output(value, SCHEMA)


def test_json_schema_additional_properties_default_is_permissive():
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    validate_output({"name": "项目", "other": True}, schema)


def test_instruction_includes_reason_first_contract_and_schema():
    instruction = build_output_instruction("custom", SCHEMA)
    assert instruction.index("reason") < instruction.index("value")
    assert '"classification"' in instruction
    assert "不要输出完整的内部推理过程" in instruction
