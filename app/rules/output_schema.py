"""Custom-mode output schemas and the fixed prompt contract."""

from __future__ import annotations

import json
from typing import Any

SCHEMA_TYPES = {"array", "boolean", "integer", "null", "number", "object", "string"}
SCHEMA_KEYS = {"additionalProperties", "description", "enum", "items", "properties", "required", "title", "type"}


class OutputSchemaError(ValueError):
    pass


def validate_output_schema(schema: Any) -> None:
    """Accept a small, explicit JSON Schema subset used by custom rules."""
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise OutputSchemaError("输出结构必须是 type=object 的 JSON Schema")
    _validate_schema_node(schema, "output_schema")


def _validate_schema_node(schema: Any, path: str) -> None:
    if not isinstance(schema, dict):
        raise OutputSchemaError(f"{path} 必须是对象")
    unknown = set(schema) - SCHEMA_KEYS
    if unknown:
        raise OutputSchemaError(f"{path} 不支持字段: {', '.join(sorted(unknown))}")

    kind = schema.get("type")
    if kind not in SCHEMA_TYPES:
        raise OutputSchemaError(f"{path}.type 必须是 {', '.join(sorted(SCHEMA_TYPES))} 之一")

    for key in ("description", "title"):
        if key in schema and not isinstance(schema[key], str):
            raise OutputSchemaError(f"{path}.{key} 必须是字符串")

    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise OutputSchemaError(f"{path}.enum 必须是非空数组")

    if kind == "object":
        properties = schema.get("properties")
        if not isinstance(properties, dict) or not properties:
            raise OutputSchemaError(f"{path}.properties 必须至少包含一个字段")
        required = schema.get("required", [])
        if not isinstance(required, list) or any(not isinstance(key, str) for key in required):
            raise OutputSchemaError(f"{path}.required 必须是字符串数组")
        if len(set(required)) != len(required) or set(required) - set(properties):
            raise OutputSchemaError(f"{path}.required 只能包含 properties 中不重复的字段")
        if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
            raise OutputSchemaError(f"{path}.additionalProperties 必须是布尔值")
        for key, child in properties.items():
            if not isinstance(key, str) or not key:
                raise OutputSchemaError(f"{path}.properties 字段名不能为空")
            _validate_schema_node(child, f"{path}.properties.{key}")
    elif kind == "array":
        if "items" not in schema:
            raise OutputSchemaError(f"{path}.items 必须定义数组元素结构")
        _validate_schema_node(schema["items"], f"{path}.items")
    elif "properties" in schema or "required" in schema or "items" in schema:
        raise OutputSchemaError(f"{path} 的 type={kind} 不能包含 properties/required/items")


def validate_output(value: Any, schema: dict[str, Any], path: str = "value") -> None:
    """Raise when a decoded model value does not satisfy the configured schema."""
    kind = schema["type"]
    matches = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": type(value) is bool,
        "integer": type(value) is int,
        "number": type(value) in (int, float),
        "null": value is None,
    }[kind]
    if not matches:
        raise OutputSchemaError(f"{path} 应为 {kind}")

    if "enum" in schema and value not in schema["enum"]:
        raise OutputSchemaError(f"{path} 不在允许值范围内")

    if kind == "object":
        properties = schema["properties"]
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise OutputSchemaError(f"{path} 缺少必需字段: {', '.join(missing)}")
        if schema.get("additionalProperties", True) is False:
            extra = set(value) - set(properties)
            if extra:
                raise OutputSchemaError(f"{path} 包含未定义字段: {', '.join(sorted(extra))}")
        for key, child_value in value.items():
            if key in properties:
                validate_output(child_value, properties[key], f"{path}.{key}")
    elif kind == "array":
        for index, item in enumerate(value):
            validate_output(item, schema["items"], f"{path}[{index}]")


def build_output_instruction(rule_type: str, output_schema: dict[str, Any] | None = None) -> str:
    rationale = "先给出简短、可核验的中文依据，再给出最终结果；不要输出完整的内部推理过程。"
    if rule_type == "judge":
        return (
            "请严格遵守以下输出格式。"
            f"{rationale}"
            "只输出一个合法 JSON 对象，不要 Markdown 或额外文字。"
            '格式为：{"reason":"<中文判断依据>","match":true}。'
            "match 必须是 JSON 布尔值 true 或 false。"
        )

    if output_schema is None:
        value_shape = '"value":"<字符串结果>"'
        value_hint = "value 必须是字符串。"
    else:
        value_shape = '"value":<符合下方 JSON Schema 的对象>'
        value_hint = (
            "value 必须符合以下 JSON Schema；Schema 只约束 value，不要把 Schema 原样当成结果：\n"
            + json.dumps(output_schema, ensure_ascii=False, indent=2)
        )
    return (
        "请严格遵守以下输出格式。"
        f"{rationale}"
        "只输出一个合法 JSON 对象，不要 Markdown 或额外文字。"
        f'对象必须按此结构包含 reason 和 value：{{"reason":"<中文生成依据>",{value_shape}}}。'
        f"{value_hint}"
    )
