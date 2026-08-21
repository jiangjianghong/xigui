# `/compare` 自定义字段 ID 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `/compare` 的字段键既能是默认的 `field_a`/`field_b`，也能是规则通过 `field_defs` 自定义的语义化 ID，请求键与规则键集合必须完全相等。

**Architecture:** 新增纯函数模块 `app/rules/field_keys.py`，由规则配置推导「有序合法字段键」，作为请求校验、prompt 渲染、响应回显三处的唯一真相源。因为键集合依赖 `rule_id` 查出的规则配置，而 Pydantic 校验跑在路由函数体之前，`CompareRequest` 改为 `extra="allow"`，一致性校验搬到路由层。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、pytest、ruamel.yaml、原生 JS（无构建）

**参考规格：** `docs/superpowers/specs/2026-08-20-compare-custom-field-ids-design.md`

**基线：** 实施前 `.venv/Scripts/python.exe -m pytest -q` 应为 `42 passed`。

> **注意：系统当前处于不可用中间态。** `config/rules.yaml` 已转换为 `{field_a}`，而代码仍认 `{field_0}`，`.format()` 抛 `KeyError` 后被 `llm_rule.py:82` 吞掉，返回未渲染的模板原文，接口照常返回 200。测试是绿的，因为它们读 `tests/fixtures/rules.yaml`，不碰 `config/rules.yaml`。Task 2 完成后中间态才消除。

---

## 文件结构

| 文件 | 职责 | 本次角色 |
|---|---|---|
| `app/rules/field_keys.py` | **新建**。由规则配置推导有序字段键；持有 fallback 模板 | 纯函数，零项目内依赖 |
| `tests/unit/test_field_keys.py` | **新建**。键推导单测 | Task 1 |
| `app/schemas/compare.py` | 请求/响应模型 | 重写，`extra="allow"` |
| `app/rules/llm_rule.py` | prompt 渲染与 LLM 调用 | 渲染层改按键名，fallback 模板改 import |
| `app/api/compare.py` | 路由接线、键一致性校验、错误映射、历史落库 | 重写字段处理部分 |
| `app/api/admin_rules.py` | 规则 CRUD | 新增 `field_defs` 校验 |
| `tests/fixtures/rules.yaml` | 测试规则 fixture | R001/R002/R003/R005 改字母键；R004 改自定义 ID |
| `tests/unit/test_compare_schema.py` | **新建**。请求模型单测 | Task 2 |
| `tests/unit/test_llm_rule.py` | 渲染单测 | 5 处模板替换 |
| `tests/integration/test_compare_api.py` | `/compare` 集成 | 11 处请求体改写 + 7 条新用例 |
| `tests/integration/test_admin_rules.py` | 规则 CRUD 集成 | 4 处模板/断言 + `field_defs` 校验用例 |
| `ui/app.js` | 规则编辑器 + Playground + 历史 | 7 处 |
| `ui/index.html` | 表单静态文案 | 3 处 |
| `api.md` / `README.md` | 对外契约文档 | 示例与表格同步 |

**任务顺序说明：** Task 1 是纯新增，提交后测试仍全绿。Task 2 是纵切面，不能再拆——改动 `CompareRequest` 的瞬间 `app/api/compare.py` 就会 `AttributeError`。Task 3–5 相互独立。

---

## Task 1: 字段键推导模块

**Files:**
- Create: `app/rules/field_keys.py`
- Create: `tests/unit/test_field_keys.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_field_keys.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_field_keys.py -q
```

预期：`ModuleNotFoundError: No module named 'app.rules.field_keys'`。

- [ ] **Step 3: 创建 `app/rules/field_keys.py`**

```python
"""字段键推导:由规则配置算出本规则的「有序合法字段键」。

这个列表是三处的唯一真相源:
- ``app/api/compare.py``   请求键必须与它完全相等
- ``app/rules/llm_rule.py`` 每个键注入 ``{键}`` 与 ``{键_name}`` 两个占位符
- ``app/api/compare.py``   响应按同样的键平铺回显

本模块是纯函数,不 import schemas / api,以免规则层反向依赖接口层。
"""

from __future__ import annotations

import re
import string
from typing import Any

# 默认字段键的字母后缀:field_a .. field_z
FIELD_LETTERS = string.ascii_lowercase

# 没有 prompt_template 时的最后兜底,避免崩溃。
# 放在本模块而非 llm_rule,是因为键推导也要读它(缺模板时按它反推出 a/b)。
FALLBACK_TEMPLATE = (
    "请判断以下字段在语义上是否一致。\n"
    "A: {field_a}\n"
    "B: {field_b}\n"
    '严格输出 JSON: {{"match": true|false, "reason": "<不超过30字>"}}'
)

# 匹配 {field_a} / {field_a_name},只认小写单字母
_LETTER_PLACEHOLDER_RE = re.compile(r"\{field_([a-z])(?:_name)?\}")


def default_key(index: int) -> str:
    """下标 → 默认字段键。``0`` → ``field_a``。"""
    return f"field_{FIELD_LETTERS[index]}"


def field_keys_for(rule_cfg: dict[str, Any]) -> list[str]:
    """返回这条规则的有序合法字段键。

    - 配了非空 ``field_defs`` → 直接用它,顺序即请求/渲染顺序
    - 否则 → 从 ``prompt_template`` 反推:取出现过的最大字母,
      返回 ``field_a`` 到该字母的**连续**列表(模板跳号也补齐,
      因为键集合必须连续才能与下标一一对应)
    - 模板不含任何字母占位符 → 返回空列表(该规则不接受字段)
    """
    defs = rule_cfg.get("field_defs") or []
    if defs:
        return [str(d) for d in defs]

    template = rule_cfg.get("prompt_template") or FALLBACK_TEMPLATE
    letters = _LETTER_PLACEHOLDER_RE.findall(template)
    if not letters:
        return []
    max_index = max(FIELD_LETTERS.index(c) for c in letters)
    return [default_key(i) for i in range(max_index + 1)]
```

- [ ] **Step 4: 跑测试确认全过**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_field_keys.py -q
```

预期：`13 passed`。

- [ ] **Step 5: 跑全量确认没碰坏别的**

```bash
.venv/Scripts/python.exe -m pytest -q
```

预期：`55 passed`（原 42 + 新 13）。

- [ ] **Step 6: 提交**

```bash
git add app/rules/field_keys.py tests/unit/test_field_keys.py
git commit -m "feat(rules): 新增字段键推导模块 field_keys"
```

---

## Task 2: 数据模型 + 渲染 + 路由（纵切面）

**Files:**
- Rewrite: `app/schemas/compare.py`
- Modify: `app/rules/llm_rule.py`（`_FALLBACK_TEMPLATE`、类 docstring、`_render_user_prompt`、`compare` docstring）
- Rewrite: `app/api/compare.py` 的字段处理部分
- Modify: `tests/fixtures/rules.yaml`
- Create: `tests/unit/test_compare_schema.py`
- Modify: `tests/unit/test_llm_rule.py`
- Modify: `tests/integration/test_compare_api.py`
- Modify: `tests/integration/test_admin_rules.py`

- [ ] **Step 1: 写 schema 层失败测试**

创建 `tests/unit/test_compare_schema.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_compare_schema.py -q
```

预期：`ImportError: cannot import name 'FieldInput'` 之后的 `AttributeError: 'CompareRequest' object has no attribute 'field_inputs'`——总之是 FAIL。

- [ ] **Step 3: 重写 `app/schemas/compare.py`**

全文替换为：

```python
"""``/compare`` 接口的请求 / 响应数据模型。

字段以平铺键承载,键名由规则决定(``field_a``/``field_b``... 或规则
``field_defs`` 里的自定义 ID)。因为键集合依赖 ``rule_id`` 查出的规则配置,
而 Pydantic 校验跑在路由函数体之前,这里只能 ``extra="allow"`` 收下所有键,
一致性校验由 ``app/api/compare.py`` 在查到规则后完成。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


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

        解析结果缓存在 ``__pydantic_extra__`` 里(原地替换 dict 为模型实例),
        供 ``field_inputs()`` 读取。
        """
        extra = self.__pydantic_extra__ or {}
        for key, raw in list(extra.items()):
            if isinstance(raw, FieldInput):
                continue
            if not isinstance(raw, dict):
                raise ValueError(
                    f"字段 {key} 的值必须是对象 {{name?, value}},收到 {type(raw).__name__}"
                )
            extra[key] = FieldInput(**raw)
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
    ``fields`` 本身不出现在输出里。
    """

    model_config = ConfigDict(extra="allow")

    rule_id: str
    result: bool = Field(..., description="比对结果。True 表示通过/一致")
    reason: str = Field(..., description="判断理由（中文）")
    time: int = Field(..., description="本次调用耗时，毫秒")
    cost: Cost = Field(default_factory=Cost)

    def __init__(self, **data):
        fields: dict[str, FieldInput] = data.pop("fields", None) or {}
        super().__init__(**data, **fields)
```

- [ ] **Step 4: 跑 schema 单测确认全过**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_compare_schema.py -q
```

预期：`12 passed`。

此时其余测试会大面积报红（`app/api/compare.py` 还在读 `req.fields`），这是预期的中间态，Step 13 才恢复全绿。

- [ ] **Step 5: 改 `app/rules/llm_rule.py`**

把顶部的 `_FALLBACK_TEMPLATE` 定义（`:22-28`）整段替换为从 `field_keys` 引入：

```python
from app.core.llm_client import chat_json
from app.rules.base import CompareResult
from app.rules.field_keys import FALLBACK_TEMPLATE
```

（删掉原来的 `_FALLBACK_TEMPLATE = (...)` 五行，并把 `:63` 的 `cfg.get("prompt_template") or _FALLBACK_TEMPLATE` 改为 `cfg.get("prompt_template") or FALLBACK_TEMPLATE`。）

把类 docstring 里的两行（`:54-55`）改为：

```python
    prompt_template 中用 ``{键}`` 引用字段值、``{键_name}`` 引用字段名,
    键名由规则的 field_defs 决定,未配则是 ``field_a`` ``field_b`` ...
```

把 `_render_user_prompt`（`:65-83`）替换为：

```python
    def _render_user_prompt(self, fields: list[tuple[str, str, str | None]]) -> str:
        """渲染 prompt_template。

        ``fields`` 为 ``[(key, value, name), ...]``,key 即请求里的字段键。
        每个字段注入 ``{key}`` 与 ``{key_name}`` 两个占位符,name 缺失时占位空串。
        """
        replacements: dict[str, str] = {}
        for key, value, name in fields:
            replacements[key] = value
            replacements[f"{key}_name"] = name or ""
        try:
            return self.prompt_template.format(**replacements)
        except (KeyError, IndexError) as e:
            log.warning("prompt_template 渲染失败 rule_id=%s err=%s", self.id, e)
            return self.prompt_template
```

把 `compare` 的签名与 docstring（`:85-93`）改为：

```python
    def compare(self, fields: list[tuple[str, str, str | None]]) -> CompareResult:
        """执行比对。

        ``fields`` 为 ``[(key, value, name), ...]``,与请求里的字段键一一对应。
        """
```

- [ ] **Step 6: 改 `tests/unit/test_llm_rule.py`**

5 处模板 + 全部 `compare(...)` / `_render_user_prompt(...)` 调用都要带上 key。逐处替换：

```python
# :20 —— 模块级 _CFG
_CFG = {
    "id": "T_LLM",
    "name": "test rule",
    "prompt_template": "A: {field_a}\nB: {field_b}\n输出 JSON。",
}

# :81 —— 引用字段名的模板
        "prompt_template": "判断字段 {field_a_name} 与 {field_b_name}: A={field_a} B={field_b}",

# :107-108
    """未配置时默认 False(不校验);即使 prompt_template 含 {field_a_name} 也不会自动推导。"""
    cfg = {"id": "X", "prompt_template": "判断 {field_a_name}: {field_a} vs {field_b}"}

# :117
        "prompt_template": "A:{field_a} B:{field_b}",

# :138
        "prompt_template": "A:{field_a} B:{field_b}",
```

然后把该文件里全部 8 处 `compare(...)` 调用改为三元组（行号为改前位置，逐处对照）：

```python
# :39
    res = rule.compare([("field_a", "贵州省XX建设有限公司", None), ("field_b", "XX建设公司", None)])

# :47
    res = build_rule(_CFG).compare([("field_a", "A公司", None), ("field_b", "B集团", None)])

# :54
    res = build_rule(_CFG).compare([("field_a", "A", None), ("field_b", "B", None)])

# :64
    res = build_rule(_CFG).compare([("field_a", "A", None), ("field_b", "A", None)])

# :75
        build_rule(_CFG).compare([("field_a", "A", None), ("field_b", "B", None)])

# :90
    build_rule(cfg).compare([("field_a", "v1", "项目代码"), ("field_b", "v2", "编号")])

# :126
    build_rule(cfg).compare([("field_a", "a", None), ("field_b", "b", None)])

# :147
    build_rule(cfg).compare([("field_a", "a", None), ("field_b", "b", None)])
```

改完确认没有遗漏的二元组调用：

```bash
grep -n "compare(\[(\"" tests/unit/test_llm_rule.py | grep -v "field_"
```

预期输出为空。

- [ ] **Step 7: 追加自定义 ID 的渲染单测**

在 `tests/unit/test_llm_rule.py` 末尾追加：

```python
def test_render_with_custom_id_keys():
    """自定义 ID 键:模板用 {keyan_name} 而非 {field_a}。"""
    from app.rules.llm_rule import LLMRule

    rule = LLMRule({
        "id": "CUSTOM",
        "prompt_template": "可研:{keyan_name} 咨评:{zixun_name}",
    })
    out = rule._render_user_prompt([
        ("keyan_name", "XX供水工程", None),
        ("zixun_name", "XX供水项目", None),
    ])
    assert out == "可研:XX供水工程 咨评:XX供水项目"


def test_render_custom_id_name_placeholder():
    from app.rules.llm_rule import LLMRule

    rule = LLMRule({
        "id": "CUSTOM",
        "prompt_template": "{keyan_name_name}={keyan_name}",
    })
    out = rule._render_user_prompt([("keyan_name", "值", "可研项目名称")])
    assert out == "可研项目名称=值"
```

- [ ] **Step 8: 跑单测确认全过**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/ -q
```

预期：`38 passed`（13 field_keys + 12 schema + 13 llm_rule）。

- [ ] **Step 9: 重写 `app/api/compare.py` 的字段处理**

import 行（`:21`）改为：

```python
from app.rules.field_keys import field_keys_for
from app.schemas.compare import CompareRequest, CompareResponse, Cost
```

在 `_find_rule_cfg` 下方新增校验函数：

```python
def _validate_field_keys(rule_id: str, expected: list[str], got: set[str]) -> None:
    """校验请求键与规则键集合完全相等。不等则抛 422。"""
    missing = [k for k in expected if k not in got]
    unexpected = sorted(got - set(expected))
    if not missing and not unexpected:
        return
    parts = []
    if missing:
        parts.append(f"缺少字段: {', '.join(missing)}")
    if unexpected:
        parts.append(f"不接受字段: {', '.join(unexpected)}")
    detail = f"rule {rule_id} " + "；".join(parts)
    if expected:
        detail += f"（本规则需要: {', '.join(expected)}）"
    log.warning("字段键不匹配 rule_id=%s %s", rule_id, detail)
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)
```

路由装饰器与函数开头（`:34-44`）改为：

```python
@router.post(
    "/compare",
    response_model=CompareResponse,
    response_model_exclude_none=True,
    summary="按指定规则比对若干字段",
)
def compare(req: CompareRequest) -> CompareResponse:
    t0 = time.perf_counter()
    inputs = req.field_inputs()
    log.info("收到比对请求 rule_id=%s fields=%d", req.rule_id, len(inputs))
```

在 `rule_cfg is None` 的 404 分支之后、`should_require_field_name` 之前，插入键一致性校验并算出有序字段：

```python
    expected_keys = field_keys_for(rule_cfg)
    _validate_field_keys(req.rule_id, expected_keys, set(inputs))
    # 校验已保证两者相等,按规则顺序排列
    ordered = [(k, inputs[k]) for k in expected_keys]
```

name 必填校验（`:55-66`）中的列表推导改为：

```python
        missing = [
            f"{key}.name" for key, f in ordered if not (f.name or "").strip()
        ]
```

规则调用（`:79`）改为：

```python
        res = rule.compare([(k, f.value, f.name) for k, f in ordered])
```

历史落库（`:112`、`:117`）两行改为：

```python
        "fields": [{"key": k, **f.model_dump()} for k, f in ordered],
```

```python
        "raw_request": req.model_dump(),
```

响应构造（`:120-127`）改为：

```python
    return CompareResponse(
        rule_id=req.rule_id,
        result=res.match,
        reason=res.reason,
        time=elapsed_ms,
        cost=Cost(**(res.usage or {})),
        fields=dict(ordered),
    )
```

- [ ] **Step 10: 改 `tests/fixtures/rules.yaml`**

头部注释块的占位符说明段（`:13-17`）改为：

```yaml
#   field_defs             可选。自定义字段 ID 列表,决定本规则请求体里的字段键。
#                          不写/为空则用默认的 field_a / field_b / ...
#   prompt_template        Prompt 模板。所有规则的判定标准都由这里描述。
#                          占位符与请求里的字段键一一对应:
#                            {field_a} {field_b} ...          字段值(默认键)
#                            {field_a_name} {field_b_name}    字段名
#                          配了 field_defs 时改用自定义 ID,如 {danwei_shenbao}
#                          注意:JSON 示例里的大括号要写成双重 {{ }},否则 .format 会报错。
```

R001/R002/R003/R005 做机械替换：`{field_0}` → `{field_a}`，`{field_1}` → `{field_b}`。

```bash
sed -i 's/{field_0}/{field_a}/g; s/{field_1}/{field_b}/g' tests/fixtures/rules.yaml
```

R003 的 description（`:65`）改为：

```yaml
    description: "判断 field_a 的日期是否严格早于 field_b"
```

**R004 改造成自定义 ID 的测试载体**——把 `:80-100` 那一段整体替换为：

```yaml
  # --- R004: 建设单位名称基本一致 ---
  # 本规则是「自定义字段 ID」的测试载体:field_defs 声明了两个 slug,
  # 请求体必须用 danwei_shenbao / danwei_pifu 作键,传 field_a 会返 422。
  # prompt_template 同时引用 {danwei_shenbao_name},因此 require_field_name=true。
  - id: "R004"
    name: "建设单位名称基本一致"
    description: "容忍简称/全称/前后缀差异;把字段名注入 prompt,让 LLM 知道在判什么"
    field_defs: ["danwei_shenbao", "danwei_pifu"]
    require_field_name: true
    system_prompt: "你是熟悉企业法人主体识别的合规审核员,能区分简称、全称、行政区划前缀、母公司与分支机构,输出严格遵守约定格式。"
    prompt_template: |
      请判断字段"{danwei_shenbao_name}"与字段"{danwei_pifu_name}"的两个值是否指向同一个法人主体。

      允许差异:简称与全称、有/无行政区划前缀、"有限公司"与"有限责任公司"互换、
      集团/分公司层级差异(如"XX集团" 与 "XX集团北京分公司"视为不同主体)。
      不允许差异:名称中关键字明显不同(如"中铁"与"中建")。

      A ({danwei_shenbao_name}): {danwei_shenbao}
      B ({danwei_pifu_name}): {danwei_pifu}

      严格按 JSON 输出,不要任何额外说明:
      {{"match": true|false, "reason": "<不超过30字的中文理由>"}}
```

确认无残留：

```bash
grep -c "field_[0-9]" tests/fixtures/rules.yaml
```

预期输出 `0`。

- [ ] **Step 11: 改写 `tests/integration/test_compare_api.py` 的 11 处请求体**

逐处对照（行号为改前的位置）：

```python
# :65-68  test_unknown_rule_id_returns_404
    resp = client.post("/compare", json={
        "rule_id": "RXXX",
        "field_a": {"name": "x", "value": "1"},
        "field_b": {"name": "x", "value": "1"},
    })

# :98-111  test_R001_returns_true_when_llm_says_match
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "field_a": {"name": "项目代码", "value": "  2024-001 "},
        "field_b": {"name": "项目代码", "value": "2024-001"},
    })
    body = resp.json()
    assert resp.status_code == 200
    assert body["rule_id"] == "R001"
    assert body["result"] is True
    assert body["cost"]["total_tokens"] == 108
    assert body["time"] >= 0
    assert body["field_a"]["value"] == "  2024-001 "
    assert body["field_b"]["value"] == "2024-001"

# :116-122  test_R001_returns_false_when_llm_says_no_match
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "field_a": {"name": "项目代码", "value": "2024-001"},
        "field_b": {"name": "项目代码", "value": "2024-002"},
    })

# :131-137  test_R002_numeric_unit_conversion
    resp = client.post("/compare", json={
        "rule_id": "R002",
        "field_a": {"name": "总投资", "value": "1.2亿"},
        "field_b": {"name": "总投资金额", "value": "12000万元"},
    })

# :145-151  test_R003_time_order_pass
    resp = client.post("/compare", json={
        "rule_id": "R003",
        "field_a": {"name": "批复时间", "value": "2024年1月10日"},
        "field_b": {"name": "申报时间", "value": "2024-02-01"},
    })

# :157-163  test_R004_semantic_similar_pass —— R004 已改自定义 ID
    resp = client.post("/compare", json={
        "rule_id": "R004",
        "danwei_shenbao": {"name": "建设单位", "value": "贵州省XX建设有限公司"},
        "danwei_pifu": {"name": "建设单位", "value": "XX建设公司"},
    })

# :173-179  test_R005_invalid_json_returns_false
    resp = client.post("/compare", json={
        "rule_id": "R005",
        "field_a": {"name": "建设内容", "value": "a; b; c"},
        "field_b": {"name": "建设内容", "value": "a; b; c; d"},
    })

# :205-208  test_field_name_optional_for_normal_rule
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "field_a": {"value": "  2024-001 "},
        "field_b": {"value": "2024-001"},
    })

# :213-231  test_single_field_compare —— 内联规则模板与请求体同改
def test_single_field_compare(mock_llm, monkeypatch):
    """单字段校验场景:注入一条只引用 {field_a} 的规则,只传 field_a。"""
    from app.api import compare as compare_module

    single_rule = {
        "id": "RSINGLE",
        "name": "单字段规模数量校验",
        "require_field_name": False,
        "prompt_template": "请判断以下建设规模描述是否含明确数量:{field_a}",
    }
    monkeypatch.setattr(compare_module, "get_rules", lambda: [single_rule])

    mock_llm["content"] = '{"match": true, "reason": "含明确规模数量"}'
    resp = client.post("/compare", json={
        "rule_id": "RSINGLE",
        "field_a": {"value": "DN500管网5公里，新建"},
    })
    assert resp.status_code == 200
    assert resp.json()["result"] is True

# :234-253  test_require_field_name_explicit_missing_returns_422
def test_require_field_name_explicit_missing_returns_422(monkeypatch):
    """yaml 显式 require_field_name=true 时,缺 name 必须 422。"""
    from app.api import compare as compare_module

    fake_rule = {
        "id": "RTEST",
        "name": "test",
        "require_field_name": True,
        "prompt_template": "A: {field_a} B: {field_b}",
    }
    monkeypatch.setattr(compare_module, "get_rules", lambda: [fake_rule])

    resp = client.post("/compare", json={
        "rule_id": "RTEST",
        "field_a": {"value": "x"},
        "field_b": {"value": "x"},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "field_a.name" in detail
    assert "field_b.name" in detail

# :258-266  test_require_field_name_R004_missing_returns_422
    resp = client.post("/compare", json={
        "rule_id": "R004",
        "danwei_shenbao": {"value": "贵州省XX建设有限公司"},
        "danwei_pifu": {"value": "XX建设公司"},
    })
    assert resp.status_code == 422
    assert "danwei_shenbao.name" in resp.json()["detail"]

# :281-287  test_require_field_name_R004_with_name_passes
    resp = client.post("/compare", json={
        "rule_id": "R004",
        "danwei_shenbao": {"name": "建设单位", "value": "贵州省XX建设有限公司"},
        "danwei_pifu": {"name": "建设单位", "value": "XX建设公司"},
    })
```

- [ ] **Step 12: 追加键校验与响应形状的集成用例**

在 `tests/integration/test_compare_api.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# 字段键一致性校验
# ---------------------------------------------------------------------------

def test_legacy_fields_array_returns_422():
    """旧版 fields 数组已不再受理:值不是 {name?, value} 对象。"""
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "fields": [{"value": "2024-001"}, {"value": "2024-001"}],
    })
    assert resp.status_code == 422


def test_no_field_key_returns_422():
    resp = client.post("/compare", json={"rule_id": "R001"})
    assert resp.status_code == 422
    assert "field_a" in resp.text


def test_missing_one_key_returns_422():
    """只传 field_a,缺 field_b。"""
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "field_a": {"value": "2024-001"},
    })
    assert resp.status_code == 422
    assert "缺少字段" in resp.json()["detail"]
    assert "field_b" in resp.json()["detail"]


def test_extra_key_returns_422():
    """多传一个未知键。"""
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "field_a": {"value": "1"},
        "field_b": {"value": "1"},
        "field_c": {"value": "1"},
    })
    assert resp.status_code == 422
    assert "不接受字段" in resp.json()["detail"]
    assert "field_c" in resp.json()["detail"]


def test_typo_key_returns_422():
    """拼错键名:同时报缺少与不接受,不会静默当成空串发给 LLM。"""
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "field_a": {"value": "1"},
        "field_bb": {"value": "1"},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "field_b" in detail and "field_bb" in detail


def test_letter_keys_rejected_for_custom_id_rule():
    """R004 配了 field_defs,传 field_a/field_b 必须 422。"""
    resp = client.post("/compare", json={
        "rule_id": "R004",
        "field_a": {"name": "建设单位", "value": "甲"},
        "field_b": {"name": "建设单位", "value": "乙"},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "danwei_shenbao" in detail
    assert "field_a" in detail


def test_custom_id_renders_into_prompt(mock_llm, monkeypatch):
    """自定义 ID 端到端:键名进请求、占位符进 prompt、键名回显。"""
    from app.api import compare as compare_module
    from app.rules import llm_rule

    triple_rule = {
        "id": "RTRIPLE",
        "name": "三方金额一致",
        "field_defs": ["shenbao_amount", "pifu_amount", "hetong_amount"],
        "require_field_name": False,
        "prompt_template": "申报:{shenbao_amount} 批复:{pifu_amount} 合同:{hetong_amount}",
    }
    monkeypatch.setattr(compare_module, "get_rules", lambda: [triple_rule])

    captured: dict = {}

    def _capture(messages, **kwargs):
        captured["messages"] = messages
        return '{"match": true, "reason": "三方一致"}', {
            "prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7,
        }

    monkeypatch.setattr(llm_rule, "chat_json", _capture)

    resp = client.post("/compare", json={
        "rule_id": "RTRIPLE",
        "shenbao_amount": {"value": "500万"},
        "pifu_amount": {"value": "500万"},
        "hetong_amount": {"value": "5000000元"},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"] is True
    assert body["shenbao_amount"]["value"] == "500万"
    assert body["hetong_amount"]["value"] == "5000000元"

    user_msg = captured["messages"][-1]["content"]
    assert "申报:500万" in user_msg
    assert "批复:500万" in user_msg
    assert "合同:5000000元" in user_msg


def test_response_omits_unused_field_keys(mock_llm):
    """response_model_exclude_none:未使用的键不出现在响应里。"""
    mock_llm["content"] = '{"match": true, "reason": "项目代码一致"}'
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "field_a": {"name": "项目代码", "value": "  2024-001 "},
        "field_b": {"name": "项目代码", "value": "2024-001"},
    })
    body = resp.json()
    assert resp.status_code == 200
    assert body["field_a"] == {"name": "项目代码", "value": "  2024-001 "}
    assert body["field_b"] == {"name": "项目代码", "value": "2024-001"}
    for letter in "cdefghijklmnopqrstuvwxyz":
        assert f"field_{letter}" not in body
    assert "fields" not in body


def test_history_records_field_key(mock_llm):
    """历史落库的 fields 每项带 key,供 UI 显示键名。"""
    from app.core.history import get_history_store

    mock_llm["content"] = '{"match": true, "reason": "一致"}'
    client.post("/compare", json={
        "rule_id": "R001",
        "field_a": {"value": "2024-001"},
        "field_b": {"value": "2024-001"},
    })
    items = get_history_store().list(rule_id="R001", limit=1)["items"]
    assert items, "历史里应有刚写入的记录"
    fields = items[0]["fields"]
    assert [f["key"] for f in fields] == ["field_a", "field_b"]
    assert fields[0]["value"] == "2024-001"
```

- [ ] **Step 13: 改 `tests/integration/test_admin_rules.py` 的 4 处**

```python
# :76 —— fixture R004 的模板已换自定义 ID
    assert "{danwei_shenbao_name}" in body["prompt_template"]

# :78 —— field_defs 断言
    assert body["field_defs"] == ["danwei_shenbao", "danwei_pifu"]

# :96 —— NEW_RULE
    "prompt_template": "A:{field_a} B:{field_b} JSON:{{\"match\":true,\"reason\":\"\"}}",

# :137
                   prompt_template="新模板 A:{field_a} B:{field_b}")

# :230-233 —— 端到端 /compare
    resp = client.post("/compare", json={
        "rule_id": "R999",
        "field_a": {"value": "v1"},
        "field_b": {"value": "v2"},
    })
```

`:249`、`:275`、`:279` 那几处 `field_defs=["申报金额", ...]` / `["甲", "乙"]` 是中文，Task 3 加了校验后会 422。**本步先把它们改成 slug**：

```python
# :249
    payload = dict(NEW_RULE, field_defs=["shenbao_amount", "pifu_amount", "hetong_amount"])
# :254 与 :260 的断言同步
    assert body["field_defs"] == ["shenbao_amount", "pifu_amount", "hetong_amount"]
    assert saved["field_defs"] == ["shenbao_amount", "pifu_amount", "hetong_amount"]

# :275
        "/admin/rules", json=dict(NEW_RULE, field_defs=["jia", "yi"])
# :279
    payload = dict(NEW_RULE, field_defs=["jia", "yi", "bing"])
# :284
    assert after["field_defs"] == ["jia", "yi", "bing"]
```

> 注意：`NEW_RULE` 的模板是 `{field_a}/{field_b}`，而 `field_defs` 给了 3 个 slug，二者不一致。这些用例只测 `field_defs` 的读写往返，不发 `/compare`，所以不冲突。若 Task 3 加了「模板占位符必须覆盖 field_defs」的校验，这几条会失败——Task 3 明确**不加**该校验，原因见那里。

- [ ] **Step 14: 跑全量测试确认全绿**

```bash
.venv/Scripts/python.exe -m pytest -q
```

预期：`0 failed`。

若有 failed，先确认没有遗漏的旧引用：

```bash
grep -rn "req\.fields\|field_[0-9]" app/ tests/
```

预期输出为空。

- [ ] **Step 15: 验证真实业务配置能渲染**

这是消除中间态的关键验证——测试用 fixture，不覆盖 `config/rules.yaml`：

```bash
.venv/Scripts/python.exe -c "
import sys,yaml,re,logging; sys.stdout.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.WARNING)
from app.rules.llm_rule import LLMRule
from app.rules.field_keys import field_keys_for
rules=yaml.safe_load(open('config/rules.yaml',encoding='utf-8'))['rules']
# 未替换的占位符 = 渲染后仍存在 {标识符} 形态。
# 不能简单查 '{'：模板里 JSON 示例的 {{ }} 经 .format() 后本就变成单层 { }。
LEFT=re.compile(r'\{[A-Za-z_][A-Za-z0-9_]*\}')
bad=[]
for r in rules:
    keys=field_keys_for(r)
    if not keys:
        bad.append((r['id'],'无字段键')); continue
    out=LLMRule(r)._render_user_prompt([(k,'V_'+k,'N_'+k) for k in keys])
    left=LEFT.findall(out)
    if left: bad.append((r['id'],left))
print('规则数:',len(rules))
print('字段键个数分布:',{len(field_keys_for(r)) for r in rules})
print('渲染异常:',bad)
"
```

预期：`规则数: 54` / `字段键个数分布: {1, 2}` / `渲染异常: []`。

- [ ] **Step 16: 跑 lint**

```bash
.venv/Scripts/python.exe -m ruff check app tests
.venv/Scripts/python.exe -m mypy app
```

**既有 lint 债（改造前 HEAD 就存在，不在本次范围内，别顺手改）：**

- ruff `Found 3 errors`，全是 F401 未使用 import：`app/core/rules_io.py:77` 的 `CommentedSeq`、`app/main.py:15` 的 `Path`、`tests/integration/test_admin_rules.py:21` 的 `rules_io`
- mypy `Found 1 error`：`app/core/config.py:24` 缺 PyYAML 的 type stubs

判断本次改动是否干净，用范围限定的命令：

```bash
.venv/Scripts/python.exe -m ruff check app/schemas/compare.py app/api/compare.py app/rules/ tests/unit/ tests/integration/test_compare_api.py
```

预期：`All checks passed!`。mypy 除上述既有 error 外不应新增。`CompareResponse.__init__` 需标注为 `def __init__(self, **data: Any) -> None:`（并 `from typing import Any`），否则 mypy 会提 `annotation-unchecked` note。

- [ ] **Step 17: 提交**

```bash
git add app/schemas/compare.py app/rules/llm_rule.py app/api/compare.py \
        tests/fixtures/rules.yaml tests/unit/test_compare_schema.py \
        tests/unit/test_llm_rule.py tests/integration/test_compare_api.py \
        tests/integration/test_admin_rules.py
git commit -m "feat!: /compare 字段键支持规则自定义 ID

BREAKING CHANGE: 不再受理 fields 数组。字段以平铺键传入,键名由规则的
field_defs 决定,未配则为 field_a/field_b/...;请求键必须与规则键集合完全相等。"
```

---

## Task 3: `field_defs` 写入校验

规则通过 `POST/PUT /admin/rules` 写入时校验 `field_defs`，防止中文、空串、重复、保留字进入配置——这些值会直接成为请求体的 JSON 键。

**不校验「模板占位符是否覆盖 field_defs」**：`field_defs` 与模板由两个独立表单区维护，编辑过程中天然存在中间态（先加字段再写模板），后端强校验会让保存流程卡死。该一致性由 UI 在保存前做非阻断提醒（Task 4 Step 4），后端只守住「键本身是否合法」。

**Files:**
- Modify: `app/api/admin_rules.py`（`RuleIn.field_defs` 的 description + 新增 validator）
- Modify: `tests/integration/test_admin_rules.py`（追加用例）

- [ ] **Step 1: 写失败测试**

在 `tests/integration/test_admin_rules.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# field_defs 合法性校验
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_defs, why", [
    (["建设单位A", "建设单位B"], "中文不能当 JSON 键"),
    (["", ""], "空串"),
    (["has space", "b"], "含空格"),
    (["1abc", "b"], "数字开头"),
    (["a-b", "c"], "含连字符"),
    (["same", "same"], "规则内重复"),
    (["rule_id", "b"], "占用顶层保留字"),
])
def test_invalid_field_defs_rejected(client, isolated_rules, bad_defs, why):
    resp = client.post("/admin/rules", json=dict(NEW_RULE, field_defs=bad_defs))
    assert resp.status_code == 422, f"{why} 应被拒绝: {bad_defs}"


@pytest.mark.parametrize("good_defs", [
    ["keyan_name", "zixun_name"],
    ["_leading_underscore", "b"],
    ["a1", "b2"],
    ["ONE", "two"],
])
def test_valid_field_defs_accepted(client, isolated_rules, good_defs):
    resp = client.post("/admin/rules", json=dict(NEW_RULE, field_defs=good_defs))
    assert resp.status_code in (200, 201), resp.text


def test_empty_field_defs_accepted(client, isolated_rules):
    """空列表 = 不启用自定义 ID,合法。"""
    resp = client.post("/admin/rules", json=dict(NEW_RULE, field_defs=[]))
    assert resp.status_code in (200, 201), resp.text
```

确认文件顶部已 `import pytest`；若没有，加上。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/Scripts/python.exe -m pytest tests/integration/test_admin_rules.py -q -k field_defs
```

预期：`test_invalid_field_defs_rejected` 的 7 个参数化用例全部 FAIL（当前无校验，返回 200）。

- [ ] **Step 3: 加校验**

在 `app/api/admin_rules.py` 顶部 import 区加：

```python
import re

from pydantic import field_validator
```

（`field_validator` 加进已有的 `from pydantic import ...` 行。）

在 `log = logging.getLogger("aicheck.admin.rules")` 下方加模块级常量：

```python
# 字段 ID 的合法形态。放模块级而非 RuleIn 类体内——Pydantic v2 会把类体里
# 以下划线开头的属性包成 ModelPrivateAttr，validator 里再取就不是 Pattern 了
# （报 "'ModelPrivateAttr' object has no attribute 'match'"）。
_FIELD_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
```

在 `RuleIn` 类里，把 `field_defs` 的定义改为：

```python
    field_defs: list[str] | None = Field(
        default=None,
        description=(
            "自定义字段 ID 列表,决定本规则请求体里的字段键。"
            "不写/为空则用默认的 field_a/field_b/...。"
            "元素须匹配 ^[A-Za-z_][A-Za-z0-9_]*$、规则内不重复、不得为 rule_id"
        ),
    )
```

顺带把 `prompt_template` 的 description 改为：

```python
    prompt_template: str = Field(
        ..., min_length=1,
        description="prompt 模板,占位符与字段键同名:{field_a} 或 {keyan_name},加 _name 后缀引用字段名",
    )
```

并在 `RuleIn` 类体内追加 validator：

```python
    @field_validator("field_defs")
    @classmethod
    def _check_field_defs(cls, v: list[str] | None) -> list[str] | None:
        """字段 ID 会直接成为请求体的 JSON 键,必须是合法标识符且不撞保留字。"""
        if not v:
            return v
        seen: set[str] = set()
        for item in v:
            if not _FIELD_ID_RE.match(item or ""):
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
```

- [ ] **Step 4: 跑测试确认全过**

```bash
.venv/Scripts/python.exe -m pytest tests/integration/test_admin_rules.py -q
```

预期：`0 failed`。

- [ ] **Step 5: 跑全量**

```bash
.venv/Scripts/python.exe -m pytest -q
```

预期：`0 failed`。

- [ ] **Step 6: 提交**

```bash
git add app/api/admin_rules.py tests/integration/test_admin_rules.py
git commit -m "feat(admin): field_defs 写入校验（合法标识符/不重复/不撞保留字）"
```

---

## Task 4: UI 改造

`field_defs` 的语义从「字段业务名」变成「字段 ID」，规则编辑器那一栏的输入含义随之改变：**填 ID，留空则用该行的默认字母键**。

**Files:**
- Modify: `ui/app.js`（`openModal` 字段行推导、`renderFieldDefRows`、`removeFieldDef`、`renderChips`、`saveRule` 校验、`onPgRuleChange`、`runPlayground`、历史详情）
- Modify: `ui/index.html:530`、`:540`、`:542`

- [ ] **Step 1: 加一个键推导辅助函数**

在 `ui/app.js` 的 `getFieldDefs()`（`:234`）上方插入：

```javascript
// 与后端 app/rules/field_keys.py 同源:由 field_defs + 模板推出有序字段键。
// defs[i] 非空则用它,否则用 field_a/field_b/...
function keyOf(defs, i) {
    const id = (defs[i] || '').trim();
    return id || `field_${String.fromCharCode(97 + i)}`;
}

// 从模板反推需要几个默认字母键(与后端 field_keys_for 的反推分支一致)
function inferKeyCount(template) {
    const m = (template || '').match(/\{field_([a-z])(?:_name)?\}/g);
    if (!m) return 0;
    const maxIdx = Math.max(...m.map(s => s.match(/\{field_([a-z])/)[1].charCodeAt(0) - 97));
    return maxIdx + 1;
}
```

- [ ] **Step 2: 改 `openModal` 的字段行推导（`:147-157`）**

那一段替换为：

```javascript
    // 字段行:优先 field_defs(自定义 ID);没配则按模板反推个数,ID 留空表示用字母键。
    const inferred = inferKeyCount(r.prompt_template);
    let defs = Array.isArray(r.field_defs) && r.field_defs.length ? [...r.field_defs] : [];
    if (!defs.length) {
        defs = Array.from({length: inferred || 2}, () => '');
    } else {
        while (defs.length < inferred) defs.push('');
    }
    renderFieldDefRows(defs);
```

- [ ] **Step 3: 改 `renderFieldDefRows`（`:239-253`）**

整个函数替换为：

```javascript
function renderFieldDefRows(defs) {
    const box = document.getElementById('f-fielddefs');
    const single = defs.length <= 1;  // 仅剩 1 行时禁用删除
    box.innerHTML = defs.map((id, i) => `
        <div class="flex items-center gap-2">
            <code class="fd-key font-mono text-xs text-surface-500 w-36 shrink-0">{${escape(keyOf(defs, i))}}</code>
            <input class="input fd-name" data-idx="${i}" value="${escape(id)}"
                   placeholder="字段 ID,留空则用 field_${String.fromCharCode(97 + i)}"
                   oninput="onFieldIdInput(${i})">
            <button type="button" class="btn-ghost text-red-600 shrink-0" title="删除该字段"
                    onclick="removeFieldDef(${i})" ${single ? 'disabled' : ''}>
                <i class="ri-close-line"></i>
            </button>
        </div>
    `).join('');
    renderChips();
}

// 只更新受影响的那一行 code 标签 + chips,避免整块 re-render 丢焦点
function onFieldIdInput(i) {
    const defs = getFieldDefs();
    const codeEls = document.querySelectorAll('#f-fielddefs .fd-key');
    if (codeEls[i]) codeEls[i].textContent = `{${keyOf(defs, i)}}`;
    renderChips();
}
```

- [ ] **Step 4: 改 `removeFieldDef`（`:260-278`）**

整个函数替换为：

```javascript
function removeFieldDef(i) {
    const defs = getFieldDefs();
    if (defs.length <= 1) return;

    const ta = document.getElementById('f-prompt');
    const key = keyOf(defs, i);
    const usesCustomId = !!(defs[i] || '').trim();

    // 被删字段仍被模板引用 → 提醒(占位符会悬空)
    const usedRe = new RegExp(`\\{${key}(_name)?\\}`);
    if (usedRe.test(ta.value)) {
        if (!confirm(`模板仍引用 {${key}}(或其 _name 变体),删除后该占位符将失效,建议取消并先清理模板。确认删除吗?`)) return;
    }

    // 只有默认字母键需要重编号:{field_d} → {field_c}。
    // 自定义 ID 是名字不是位置,删掉后其余 ID 不动。
    if (!usesCustomId) {
        ta.value = ta.value.replace(/\{field_([a-z])(_name)?\}/g, (m, L, suffix) => {
            const n = L.charCodeAt(0) - 97;
            return n > i ? `{field_${String.fromCharCode(96 + n)}${suffix || ''}}` : m;
        });
    }

    defs.splice(i, 1);
    renderFieldDefRows(defs);
}
```

- [ ] **Step 5: 改 `renderChips`（`:280-291`）**

整个函数替换为：

```javascript
function renderChips() {
    const defs = getFieldDefs();
    const requireName = document.getElementById('f-require').value === 'true';
    const box = document.getElementById('f-chips');
    box.innerHTML = defs.map((_, i) => {
        const key = keyOf(defs, i);
        const nameChip = requireName
            ? `<span class="chip" title="插入 {${key}_name}" onclick="insertPlaceholder('{${key}_name}')">${escape(key)}·名</span>`
            : '';
        return `<span class="chip" title="插入 {${key}}" onclick="insertPlaceholder('{${key}}')">${escape(key)}</span>${nameChip}`;
    }).join('') + `<span class="chip" onclick="insertPlaceholder('{{&quot;match&quot;: true|false, &quot;reason&quot;: &quot;<不超过30字>&quot;}}')">JSON 输出范式</span>`;
}
```

- [ ] **Step 6: 改 `saveRule` 的字段校验（`:181-204`）**

那一段替换为：

```javascript
    // 字段定义校验
    const rawDefs = payload.field_defs;
    const ID_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;
    const filled = rawDefs.filter(s => s);
    // 要么全填(自定义 ID),要么全空(用字母键),不允许一半一半
    if (filled.length && filled.length !== rawDefs.length) {
        toast('字段 ID 要么全部填写,要么全部留空(留空则用 field_a/field_b/...)', 'error');
        return;
    }
    for (const id of filled) {
        if (!ID_RE.test(id)) {
            toast(`字段 ID "${id}" 非法:须字母或下划线开头,只含字母数字下划线`, 'error');
            return;
        }
        if (id === 'rule_id') { toast('字段 ID 不能叫 rule_id,它是请求体顶层保留字', 'error'); return; }
    }
    if (new Set(filled).size !== filled.length) {
        toast('字段 ID 不能重复', 'error');
        return;
    }
    // 全空则不写 field_defs,让后端走字母键分支
    payload.field_defs = filled.length ? filled : undefined;

    // 模板引用的键 vs 定义的键
    const keys = rawDefs.map((_, i) => keyOf(rawDefs, i));
    const usedKeys = new Set(
        (payload.prompt_template.match(/\{([A-Za-z_][A-Za-z0-9_]*?)(?:_name)?\}/g) || [])
            .map(s => s.slice(1, -1).replace(/_name$/, ''))
    );
    const undefinedRefs = [...usedKeys].filter(k => !keys.includes(k) && /^field_[a-z]$|^[A-Za-z_]/.test(k));
    const unused = keys.filter(k => !usedKeys.has(k));
    if (unused.length && !confirm(`以下字段已定义但模板未引用:${unused.join('、')}。确认继续保存吗?`)) return;

    // 友好提示:模板用了 {键_name} 但没勾 require_field_name
    if (/\{[A-Za-z_][A-Za-z0-9_]*_name\}/.test(payload.prompt_template) && !payload.require_field_name) {
        if (!confirm('prompt_template 引用了 {键_name},但 require_field_name 是 false,LLM 将拿到空字符串。确认继续吗?')) return;
    }
```

> `undefinedRefs` 只做计算不阻断——模板里可能有 `{{...}}` 转义的 JSON 示例，正则难以完全排除，误报会挡住正常保存。真正的守门在后端渲染时的 WARNING 日志。

- [ ] **Step 7: 改 `onPgRuleChange` 的字段输入框（`:364-382`）**

那一段替换为：

```javascript
    // 字段输入框:键名即 field_defs 的 ID,没配则 field_a/field_b/...
    const defs = Array.isArray(r.field_defs) && r.field_defs.length ? r.field_defs : null;
    const keys = defs
        ? [...defs]
        : Array.from({length: inferKeyCount(r.prompt_template) || 2}, (_, i) => `field_${String.fromCharCode(97 + i)}`);
    container.innerHTML = keys.map((key, i) => `
        <div class="card p-3">
            <label class="field-label"><span class="name">${escape(key)}</span>${r.require_field_name ? '<span class="required">*</span>' : ''}</label>
            ${r.require_field_name ? `<input class="input pg-field-name mb-2" data-key="${escape(key)}" placeholder="字段名(中文业务名)">` : ''}
            <textarea class="input pg-field-value" data-key="${escape(key)}" rows="2" placeholder="${escape(key)} 的值"></textarea>
        </div>`).join('');
```

- [ ] **Step 8: 改 `runPlayground` 的 payload 构造（`:389-403`）**

那一段替换为：

```javascript
    const valueEls = document.querySelectorAll('.pg-field-value');
    const payload = { rule_id: r.id };
    for (const el of valueEls) {
        const key = el.dataset.key;
        const value = el.value.trim();
        if (!value) { toast(`${key}.value 不能为空`, 'error'); return; }
        const entry = { value };
        if (r.require_field_name) {
            const nameEl = document.querySelector(`.pg-field-name[data-key="${key}"]`);
            entry.name = nameEl ? nameEl.value.trim() : '';
        }
        payload[key] = entry;
    }
```

（原先其后的 `const payload = { rule_id: r.id, fields };` 一行随之删除。）

- [ ] **Step 9: 改历史详情的字段显示（`:565-570`）**

`${escape(f.name || \`字段${i}\`)}` 改为回落到 `key`：

```javascript
                        ${(record.fields || []).map((f, i) => `
                            <div class="text-sm">
                                <span class="text-surface-500">${escape(f.name || f.key || `字段${i}`)}:</span>
                                <span class="text-surface-700 ml-1">${escape(f.value || '-')}</span>
                            </div>
                        `).join('')}
```

改造前落库的旧记录没有 `key`，会回落到 `字段${i}`，不报错。

- [ ] **Step 10: 改 `ui/index.html` 三处文案**

`:530`：

```html
                    <p class="field-help">本规则要比对的字段。<b>填字段 ID</b>(如 <code>keyan_name</code>),它就是请求体里的键名;<b>留空则用默认的 <code>field_a</code>/<code>field_b</code></b>。下方模板区会生成对应 chip,点击即插入占位符。</p>
```

`:540`：

```html
                    <p class="field-help">规则的核心,用自然语言描述判定标准。占位符与字段 ID 同名:<code>{field_a}</code> <code>{field_b}</code> ... 或自定义的 <code>{keyan_name}</code>;加 <code>_name</code> 后缀引用字段名。<b>JSON 示例里的大括号要写双重 {{ }}</b>,否则 .format 会报错。</p>
```

`:542`：

```html
                    <textarea id="f-prompt" class="input" rows="10" placeholder="请判断以下字段是否一致...&#10;A: {field_a}&#10;B: {field_b}&#10;严格按 JSON 输出: {{&quot;match&quot;: true|false, &quot;reason&quot;: &quot;...&quot;}}"></textarea>
```

`:513` 里的 `{field_N_name}` 也改为 `{键_name}`：

```html
                    <p class="field-help">是否要求请求里每个 <code>field.name</code> 都必填。<b>若 prompt_template 引用了 {键_name},务必勾选 true</b>;否则 LLM 拿到空字符串,判定不准。</p>
```

- [ ] **Step 11: 确认无残留**

```bash
grep -n "field_\[0-9\]\|field_N\|fields\[\|data-idx" ui/app.js ui/index.html
```

预期：只剩 `f-fielddefs` 相关的 `data-idx`（字段定义行仍按下标索引，这是对的），没有 `field_0`/`field_N`/`fields[` 残留。

- [ ] **Step 12: 手工验证 UI**

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --port 6549
```

浏览器打开 `http://localhost:6549/ui/`，验证五点：

1. **Playground 字母键**：选 `cross_rule_01` → 两个输入框标签为 `field_a`/`field_b` → 填值试跑 → 200，「原始响应」里是 `field_a`/`field_b` 平铺
2. **Playground 自定义 ID**：新建一条规则，字段 ID 填 `keyan_name`/`zixun_name`，模板写 `{keyan_name}` vs `{zixun_name}` → 保存 → Playground 选中它 → 输入框标签是这两个 ID → 试跑返回 200，响应里是这两个键
3. **chip 联动**：在字段 ID 框里打字，左侧 `{...}` 标签与下方 chip 实时跟着变，且输入框不丢焦点
4. **ID 校验**：字段 ID 填中文 → 保存时 toast 报错；填一个空一个 → 报「要么全填要么全空」
5. **删字段**：3 行全空（字母键）时删中间那行 → 模板里 `{field_c}` 自动变 `{field_b}`；3 行都填了 ID 时删中间那行 → 其余 ID 不变

验证完 `Ctrl+C` 停服务。

- [ ] **Step 13: 提交**

```bash
git add ui/app.js ui/index.html
git commit -m "feat(ui): 规则编辑器与 Playground 适配自定义字段 ID"
```

---

## Task 5: 文档同步与收尾

**Files:**
- Modify: `api.md`
- Modify: `README.md:60-70`
- Delete: `config/rules.yaml.orig.bak`

- [ ] **Step 1: 改 `api.md` 的请求参数表**

定位：`grep -n "rule_id.*string.*是" api.md`。把请求参数表替换为：

```markdown
| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `rule_id` | string | 是 | 规则 ID，对应 `config/rules.yaml` 中的 `id` |
| `<字段键>` | object | 是 | 每个待比对字段一个顶层键，值为 `{name?, value}`。键名由规则决定，见下 |
| `<字段键>.value` | string | 是 | 字段的文本值 |
| `<字段键>.name` | string | **看规则** | 字段名。默认可省略；若规则配了 `require_field_name: true`，缺则 422 |

**字段键怎么定：**

- 规则没配 `field_defs` → 键是 `field_a`、`field_b`、`field_c`…，个数按 `prompt_template` 里最大的字母占位符
- 规则配了 `field_defs: [keyan_name, zixun_name]` → 键就是这两个 ID，此时传 `field_a` 会返 422

请求键必须与规则的键集合**完全相等**：少传、多传、拼错都返 422，`detail` 里会指名缺哪个/多哪个。
```

- [ ] **Step 2: 改 `api.md` 的请求示例**

把 `请求示例：` 下方 json 代码块的内容整体换成（围栏三反引号不动，写入时去掉此处的 4 空格缩进）：

    // 默认字母键
    {
      "rule_id": "cross_rule_01",
      "field_a": {"name": "总投资", "value": "1.2亿"},
      "field_b": {"name": "总投资金额", "value": "12000万元"}
    }

    // 规则自定义 ID（该规则 field_defs: [keyan_name_baogao, zixun_name_baogao]）
    {
      "rule_id": "xiangmu_rule_01",
      "keyan_name_baogao": {"value": "XX供水工程"},
      "zixun_name_baogao": {"value": "XX供水项目"}
    }

    // 单字段规则
    {
      "rule_id": "zhongxue_cross_wenjainming",
      "field_a": {"value": "DN500管网5公里，新建"}
    }

- [ ] **Step 3: 改 `api.md` 的响应表与示例**

响应表里 `fields` 那一行改为：

```markdown
| `<字段键>` | object | 回显传入的字段，键名与请求一致。未传的键不出现 |
```

响应示例改为：

```json
{
  "rule_id": "cross_rule_01",
  "result": true,
  "reason": "1.2亿等于12000万元",
  "time": 1520,
  "cost": {"prompt_tokens": 156, "completion_tokens": 12, "total_tokens": 168},
  "field_a": {"name": "总投资", "value": "1.2亿"},
  "field_b": {"name": "总投资金额", "value": "12000万元"}
}
```

- [ ] **Step 4: 改 `api.md` 的 curl 示例**

```markdown
> 所有规则均走 LLM，判定边界由 `config/rules.yaml` 中各自的 `prompt_template` 描述。
> prompt 里用 `{键}` 引用字段值、`{键_name}` 引用字段名，键名与请求体一致。

# 默认字母键
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"cross_rule_01",
       "field_a":{"name":"总投资","value":"1.2亿"},
       "field_b":{"name":"总投资金额","value":"12000万元"}}'

# 自定义 ID
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"xiangmu_rule_01",
       "keyan_name_baogao":{"value":"XX供水工程"},
       "zixun_name_baogao":{"value":"XX供水项目"}}'
```

- [ ] **Step 5: 改 `api.md` 的错误码表**

把 422 相关行改为：

```markdown
| 422 | 字段值形状不对（缺 `value`、值不是对象） | FastAPI 默认 |
| 422 | 字段键不匹配：少传/多传/拼错，或传了已废弃的 `fields` 数组 | `{"detail":"rule X 缺少字段: field_b（本规则需要: field_a, field_b）"}` |
| 422 | 规则要求 name 必填但缺失 | `{"detail":"rule X 要求 field_a.name 必填"}` |
| 422 | 写规则时 `field_defs` 元素非法 | `{"detail":"field_defs 元素非法: '建设单位A'..."}` |
```

- [ ] **Step 6: 改 `api.md` 的规则字段说明（两处 `field_defs` 与 `prompt_template` 行）**

```markdown
| `field_defs` | list[string] | 否 | 自定义字段 ID 列表，元素即请求体里的字段键。不写/为空则用 `field_a`/`field_b`/…。元素须匹配 `^[A-Za-z_][A-Za-z0-9_]*$`、规则内不重复、不得为 `rule_id` |
| `prompt_template` | string | 是 | prompt 模板，占位符与字段键同名：`{field_a}` 或 `{keyan_name}`，加 `_name` 后缀引用字段名 |
```

占位符说明段改为：

```markdown
**Prompt 占位符说明**：
- `{键}` 引用字段值，`{键_name}` 引用字段名
- 键名由 `field_defs` 决定；未配置时是 `field_a`、`field_b`、`field_c`…
- JSON 示例中的大括号要写成双重 `{{ }}`，否则 `.format()` 会报错
- `match` 必须让模型输出 `true|false`，输出中文会被 `bool()` 判成 `True`
```

- [ ] **Step 7: 改 `README.md:60-70` 的 curl 示例**

```bash
curl -X POST http://localhost:6549/compare \
  -H "Content-Type: application/json" \
  -d '{"rule_id":"R001",
       "field_a":{"name":"项目代码","value":"  2024-001 "},
       "field_b":{"name":"项目代码","value":"2024-001"}}'
# {"rule_id":"R001","result":true,"reason":"两个项目代码一致","time":312,
#  "cost":{"prompt_tokens":156,"completion_tokens":12,"total_tokens":168},
#  "field_a":{"name":"项目代码","value":"  2024-001 "},
#  "field_b":{"name":"项目代码","value":"2024-001"}}
```

- [ ] **Step 8: 全库确认无遗漏**

```bash
grep -rn "field_[0-9]\|a_value\|b_value\|\"fields\":\s*\[" \
  --include="*.py" --include="*.md" --include="*.js" --include="*.html" --include="*.yaml" . \
  | grep -v "^./.venv" | grep -v "^./docs/superpowers"
```

预期输出为空（`docs/superpowers/` 下的 spec 与计划记录的是改造过程，保留原样）。

- [ ] **Step 9: 跑全量测试**

```bash
.venv/Scripts/python.exe -m pytest -q
```

预期：`0 failed`。

- [ ] **Step 10: 删掉配置备份**

确认 Task 2 Step 15 的 54 条渲染验证已通过后：

```bash
rm config/rules.yaml.orig.bak
```

- [ ] **Step 11: 提交**

```bash
git add api.md README.md
git rm --cached config/rules.yaml.orig.bak 2>/dev/null || true
git commit -m "docs: 接口文档同步至自定义字段 ID 格式"
```

---

## 验收清单

- [ ] `.venv/Scripts/python.exe -m pytest -q` → 0 failed
- [ ] `.venv/Scripts/python.exe -m ruff check app tests` → All checks passed
- [ ] `grep -rn "field_[0-9]\|a_value\|b_value" app/ config/ tests/ ui/` → 空
- [ ] Task 2 Step 15 的 54 条业务规则渲染验证 → 未替换占位符列表为空
- [ ] `POST /compare` 传 `fields` 数组 → 422
- [ ] `POST /compare` 对 R001 只传 `field_a` → 422，detail 含「缺少字段」与 `field_b`
- [ ] `POST /compare` 对 R001 多传 `field_c` → 422，detail 含「不接受字段」
- [ ] `POST /compare` 对 R004（配了 `field_defs`）传 `field_a` → 422
- [ ] `POST /compare` 对 R004 传 `danwei_shenbao`/`danwei_pifu` → 200，响应用同样的键回显
- [ ] `POST /admin/rules` 传 `field_defs: ["中文"]` → 422
- [ ] UI Playground 两种键都能试跑；字段 ID 输入框实时联动且不丢焦点
- [ ] `config/rules.yaml.orig.bak` 已删除

## 上线提示

**对生产上游消费者：** 生产接口本就是 `field_a`/`field_b` 平铺，本次改造后契约不变，无需改动。

**对按仓库 `fields` 数组对接的调用方（若有）：** 不向后兼容，请求会返 422。

**自定义 ID 路径零生产验证：** 当前 54 条规则全部使用默认字母键，`field_defs` 一条都没配。自定义 ID 只有测试覆盖。第一条真正启用它的规则上线前，建议先在 Playground 手工跑通。
