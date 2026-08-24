"""规则与项目的请求模型。

放在 schemas 层而非 ``api/admin_rules.py`` 里，是因为 ``core/rules_sync.py``
导入 yaml 时也要复用同一套校验；若模型留在 API 层，core 层反向 import 就成了
循环依赖。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

# 标识符的合法形态。字段 ID 与项目 key 共用——两者都会成为对外的键/路径，
# 规则一致才不会让人记两套。
# 放模块级而非类体内：Pydantic v2 会把类体里以下划线开头的属性包成
# ModelPrivateAttr，validator 里再取就不是 Pattern 了。
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class RuleIn(BaseModel):
    """前端提交的规则结构。"""

    id: str = Field(..., min_length=1, description="规则唯一编号，全局唯一")
    project: str | None = Field(
        default=None,
        description="所属项目的 key。null 或省略表示未分组",
    )
    name: str | None = Field(default="", description="中文规则名(用于日志/展示)")
    description: str | None = Field(default="", description="规则描述，仅文档用")
    field_defs: list[str] | None = Field(
        default=None,
        description=(
            "自定义字段 ID 列表，决定本规则请求体里的字段键。"
            "不写/为空则用默认的 field_a/field_b/...。"
            "元素须匹配 ^[A-Za-z_][A-Za-z0-9_]*$、规则内不重复、不得为 rule_id"
        ),
    )
    require_field_name: bool = Field(
        default=False, description="是否要求请求里每个 field.name 都必填"
    )
    system_prompt: str | None = Field(
        default="", description="LLM 系统提示。留空则不向模型发送 system 消息"
    )
    prompt_template: str = Field(
        ...,
        min_length=1,
        description="prompt 模板，占位符与字段键同名:{field_a} 或 {keyan_name}",
    )

    @field_validator("field_defs")
    @classmethod
    def _check_field_defs(cls, v: list[str] | None) -> list[str] | None:
        """字段 ID 会直接成为请求体的 JSON 键，必须是合法标识符且不撞保留字。"""
        if not v:
            return v
        seen: set[str] = set()
        for item in v:
            if not IDENT_RE.match(item or ""):
                raise ValueError(
                    f"field_defs 元素非法: {item!r}，"
                    "须匹配 ^[A-Za-z_][A-Za-z0-9_]*$（字母或下划线开头，只含字母数字下划线）"
                )
            if item == "rule_id":
                raise ValueError("field_defs 不能包含 rule_id，它是请求体顶层保留字")
            if item in seen:
                raise ValueError(f"field_defs 元素重复: {item!r}")
            seen.add(item)
        return v


class RuleOut(RuleIn):
    """读取返回的规则结构，字段同 RuleIn。"""


class ProjectIn(BaseModel):
    """前端提交的项目结构。"""

    key: str = Field(..., min_length=1, description="项目稳定标识，yaml 导入导出引用它")
    name: str = Field(..., min_length=1, description="项目中文名")
    description: str = Field(default="", description="项目描述")

    @field_validator("key")
    @classmethod
    def _check_key(cls, v: str) -> str:
        if not IDENT_RE.match(v or ""):
            raise ValueError(
                f"项目 key 非法: {v!r}，"
                "须匹配 ^[A-Za-z_][A-Za-z0-9_]*$（字母或下划线开头，只含字母数字下划线）"
            )
        return v


class RuleIdsIn(BaseModel):
    """批量移入/移出的请求体。"""

    rule_ids: list[str] = Field(..., min_length=1, description="规则 ID 列表")
