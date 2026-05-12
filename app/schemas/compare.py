"""``/compare`` 接口的请求 / 响应数据模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FieldInput(BaseModel):
    """单个字段的输入：名称 + 值。"""

    name: str = Field(..., description="字段名称，例如 '项目代码'、'总投资金额'")
    value: str = Field(..., description="字段的文本值，可以包含单位/日期/中文/空白")


class CompareRequest(BaseModel):
    """``POST /compare`` 请求体。"""

    rule_id: str = Field(..., description="规则 ID，对应 config/rules.yaml 中的 id")
    field_a: FieldInput
    field_b: FieldInput


class Cost(BaseModel):
    """LLM token 用量。确定性规则全为 0。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class CompareResponse(BaseModel):
    """``POST /compare`` 响应体。"""

    rule_id: str
    result: bool = Field(..., description="比对结果。True 表示通过/一致")
    reason: str = Field(..., description="判断理由（中文）")
    time: int = Field(..., description="本次调用耗时，毫秒")
    cost: Cost = Field(default_factory=Cost)
