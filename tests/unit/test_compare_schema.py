"""``CompareRequest`` / ``CompareResponse`` 的单元测试。

请求模型只静态声明 rule_id,其余键走 extra。一致性校验在路由层做
(见 tests/integration/test_compare_api.py),这里只覆盖形状解析。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.compare import CompareRequest, CompareResponse, Cost, FieldInput


def test_rule_id_required():
    with pytest.raises(ValidationError):
        CompareRequest(field_a={"value": "v1"})


def test_extra_keys_parsed_into_field_inputs():
    req = CompareRequest(rule_id="R001", field_a={"value": "v1"}, field_b={"value": "v2"})
    fields = req.field_inputs()
    assert set(fields) == {"field_a", "field_b"}
    assert fields["field_a"].value == "v1"
    assert isinstance(fields["field_a"], FieldInput)


def test_custom_id_keys_parsed():
    req = CompareRequest(rule_id="X", keyan_name={"value": "A"}, zixun_name={"value": "B"})
    assert set(req.field_inputs()) == {"keyan_name", "zixun_name"}


def test_field_input_name_optional():
    req = CompareRequest(rule_id="R001", field_a={"value": "v1"})
    assert req.field_inputs()["field_a"].name is None


def test_field_input_name_carried():
    req = CompareRequest(rule_id="R001", field_a={"name": "项目代码", "value": "v1"})
    assert req.field_inputs()["field_a"].name == "项目代码"


def test_value_missing_rejected():
    with pytest.raises(ValidationError) as exc:
        CompareRequest(rule_id="R001", field_a={"name": "只有名字"})
    assert "value" in str(exc.value)


def test_non_object_field_rejected():
    """字段值必须是对象,传字符串要报错。"""
    with pytest.raises(ValidationError):
        CompareRequest(rule_id="R001", field_a="裸字符串")


def test_legacy_fields_array_rejected():
    """旧版 fields 数组:值不是对象,解析失败。"""
    with pytest.raises(ValidationError):
        CompareRequest(rule_id="R001", fields=[{"value": "v1"}, {"value": "v2"}])


def test_no_field_keys_gives_empty_dict():
    """一致性校验在路由层,schema 层允许零字段。"""
    assert CompareRequest(rule_id="R001").field_inputs() == {}


def test_field_inputs_preserves_request_order():
    req = CompareRequest(rule_id="X", b_key={"value": "2"}, a_key={"value": "1"})
    assert list(req.field_inputs()) == ["b_key", "a_key"]


def test_response_carries_dynamic_keys():
    resp = CompareResponse(
        rule_id="R001", result=True, reason="一致", time=12, cost=Cost(),
        fields={"field_a": FieldInput(value="v1")},
    )
    dumped = resp.model_dump()
    assert dumped["field_a"] == {"name": None, "value": "v1"}
    assert "fields" not in dumped


def test_response_omits_absent_keys():
    resp = CompareResponse(
        rule_id="R001", result=True, reason="一致", time=1, cost=Cost(),
        fields={"field_a": FieldInput(value="v1")},
    )
    assert "field_b" not in resp.model_dump()
