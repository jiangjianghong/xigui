"""``/compare`` 接口的请求 / 响应数据模型。

字段以平铺键承载,键名由规则决定(``field_a``/``field_b``... 或规则
``field_defs`` 里的自定义 ID)。因为键集合依赖 ``rule_id`` 查出的规则配置,
而 Pydantic 校验跑在路由函数体之前,这里只能 ``extra="allow"`` 收下所有键,
一致性校验由 ``app/api/compare.py`` 在查到规则后完成。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class FieldInput(BaseModel):
    """单个字段的输入:名称(可选)+ 值。

    ``name`` 默认是可选的,简化业务侧调用。是否强制要求由具体规则的
    ``require_field_name`` 决定(在 ``/compare`` 入口校验,缺则返 422)。
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None,
        description="字段名称，例如 '项目代码'、'总投资金额'。是否必填由规则配置决定。",
    )
    value: str = Field(..., description="字段的文本值，可以包含单位/日期/中文/空白")


class CompareRequest(BaseModel):
    """``POST /compare`` 请求体。

    除 ``rule_id`` 外的每个顶层键都是一个待比对字段,值为 ``{name?, value}``。
    键名必须与规则的字段键集合**完全相等**——少传、多传、拼错都返 422。

    示例(默认字母键)::

        {"rule_id": "R001",
         "field_a": {"value": "2024-001"},
         "field_b": {"value": "2024-001"}}

    示例(规则 field_defs 自定义 ID)::

        {"rule_id": "xiangmu_rule_01",
         "keyan_name_baogao": {"value": "XX供水工程"},
         "zixun_name_baogao": {"value": "XX供水项目"}}
    """

    model_config = ConfigDict(extra="allow")

    rule_id: str = Field(..., description="规则 ID，对应 config/rules.yaml 中的 id")

    @model_validator(mode="after")
    def _parse_extra_fields(self) -> CompareRequest:
        """把 extra 键逐个解析成 ``FieldInput``,形状不对就地报错。

        解析结果原地替换回 ``__pydantic_extra__``,供 ``field_inputs()`` 读取。
        """
        extra = self.__pydantic_extra__ or {}
        for key, raw in list(extra.items()):
            if isinstance(raw, FieldInput):
                continue
            if not isinstance(raw, dict):
                raise ValueError(
                    f"字段 {key} 的值必须是对象 {{name?, value}},收到 {type(raw).__name__}"
                )
            try:
                extra[key] = FieldInput(**raw)
            except ValidationError as e:
                raise ValueError(f"字段 {key} 解析失败: {e}") from e
        return self

    def field_inputs(self) -> dict[str, FieldInput]:
        """返回 ``{键: FieldInput}``,顺序即请求 JSON 里的出现顺序。"""
        return dict(self.__pydantic_extra__ or {})


class Cost(BaseModel):
    """LLM token 用量。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class CompareResponse(BaseModel):
    """``POST /compare`` 响应体。

    字段按请求同样的键平铺回显。构造时把 ``fields`` 字典摊平进 extra,
    ``fields`` 本身不出现在输出里。路由上配了 ``response_model_exclude_none=True``,
    未传 ``name`` 的字段不会输出 ``"name": null``。
    """

    model_config = ConfigDict(extra="allow")

    rule_id: str
    rule_type: str = "judge"
    result: Any = Field(..., description="判断模式为布尔值；custom 模式为字符串或 JSON 结果")
    reason: str = Field(..., description="判断理由（中文）")
    time: int = Field(..., description="本次调用耗时，毫秒")
    cost: Cost = Field(default_factory=Cost)

    def __init__(self, **data: Any) -> None:
        fields: dict[str, FieldInput] = data.pop("fields", None) or {}
        super().__init__(**data, **fields)
