# `/compare` 入参改造实施计划

> **已作废** —— 被 `2026-08-20-compare-custom-field-ids.md` 取代。
> 本计划的基线（43 条规则、`{field_0}` 占位符、`fields` 数组）已不复存在：
> `config/rules.yaml` 已替换为生产导出的 54 条并转换到 `{field_a}`。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `POST /compare` 的入参从 `fields` 数组改成 `field_a`/`field_b`/… 平铺键，占位符同步改为 `{field_a}`，不兼容旧格式。

**Architecture:** Pydantic 模型显式声明 26 个可选字段 `field_a`..`field_z` 配 `extra="forbid"`，由 `model_validator` 强制「从 `field_a` 起连续」。因为连续性被强制，列表下标与字母构成双射，`LLMRule.compare()` 签名保持不变，只在渲染层把键名从数字换成字母。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、pytest、ruamel.yaml、原生 JS（无构建）

**基线：** 实施前 `.venv/Scripts/python.exe -m pytest -q` 应为 `42 passed`。

**参考规格：** `docs/superpowers/specs/2026-08-10-compare-field-keys-design.md`

---

## 文件结构

| 文件 | 职责 | 本次角色 |
|---|---|---|
| `app/schemas/compare.py` | 请求/响应模型与键校验 | 重写，新增 `FIELD_LETTERS`、`ordered_fields()` |
| `app/rules/llm_rule.py` | prompt 渲染与 LLM 调用 | 渲染层键名改字母 |
| `app/api/compare.py` | 路由接线、错误映射、历史落库 | 5 处引用改接 `ordered_fields()` |
| `app/api/admin_rules.py` | 规则 CRUD | 仅改两处 description 文案 |
| `tests/fixtures/rules.yaml` | 测试规则 fixture | 占位符批量替换 |
| `config/rules.yaml` | 业务活配置 | 占位符批量替换 |
| `tests/unit/test_compare_schema.py` | **新建**，键校验单测 | 覆盖连续性/拒绝旧格式 |
| `ui/app.js` | 规则编辑器 + Playground | 4 处占位符逻辑改字母 |
| `ui/index.html` | 规则表单静态文案 | 2 处占位符说明 |
| `api.md` / `README.md` | 对外契约文档 | 示例与表格同步 |

**任务顺序说明：** Task 1 是一个完整纵切面（schema + 渲染 + 路由 + fixture + 全部测试）。它不能再拆——改动 `CompareRequest` 的瞬间 `app/api/compare.py` 就会 `AttributeError`，任何中间切分都会留下红测试的提交。Task 1 内部按 2-5 分钟一步细分，末尾全绿再提交。Task 2–4 相互独立。

---

## Task 0: 保护 `config/rules.yaml` 的未提交改动

`config/rules.yaml` 当前有 387 行未提交改动。Task 3 会对该文件做批量替换，若不先落盘，替换出错时回滚会连带丢失这批改动。

**Files:**
- Modify: 无（仅 git 操作）

- [ ] **Step 1: 确认待保护的改动范围**

```bash
git diff --stat config/rules.yaml
```

预期输出包含 `config/rules.yaml | 390 +++...`（387 增 3 删）。

- [ ] **Step 2: 提交这批规则扩充**

```bash
git add config/rules.yaml
git commit -m "feat(rules): 扩充业务规则至 43 条"
```

- [ ] **Step 3: 确认工作区干净**

```bash
git status --short
```

预期输出为空。若你不希望提交这批改动，改为执行 `cp config/rules.yaml config/rules.yaml.bak` 并在 Task 3 完成验证后删除备份——但后续所有 `git checkout` 类回滚操作都必须避开该文件。

---

## Task 1: 数据模型 + 渲染 + 路由接线（纵切面）

**Files:**
- Rewrite: `app/schemas/compare.py`
- Modify: `app/rules/llm_rule.py:20-28`（`_FALLBACK_TEMPLATE`）、`:54-55`、`:72`（docstring）、`:75-78`（渲染）
- Modify: `app/api/compare.py:21`（import）、`:34-44`、`:55-66`、`:79`、`:112`、`:117`、`:120-127`
- Modify: `tests/fixtures/rules.yaml`（占位符 + 头部注释）
- Create: `tests/unit/test_compare_schema.py`
- Modify: `tests/unit/test_llm_rule.py:20`、`:81`、`:108`、`:117`、`:138`
- Modify: `tests/integration/test_compare_api.py`（全部 `/compare` 用例）
- Modify: `tests/integration/test_admin_rules.py:76`、`:96`、`:137`、`:232`

- [ ] **Step 1: 写 schema 层失败测试**

创建 `tests/unit/test_compare_schema.py`：

```python
"""``CompareRequest`` 的键校验单元测试。

覆盖"从 field_a 起必须连续"这条规则的全部分支,以及旧版 fields 数组的拒绝。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.compare import FIELD_LETTERS, CompareRequest


def _fi(value: str, name: str | None = None) -> dict:
    """构造 FieldInput 的入参 dict。name 为 None 时不放该键。"""
    d: dict = {"value": value}
    if name is not None:
        d["name"] = name
    return d


def test_field_letters_covers_a_to_z():
    assert FIELD_LETTERS[0] == "a"
    assert FIELD_LETTERS[-1] == "z"
    assert len(FIELD_LETTERS) == 26


def test_single_field_a_ok():
    req = CompareRequest(rule_id="R001", field_a=_fi("v1"))
    assert [f.value for f in req.ordered_fields()] == ["v1"]


def test_two_contiguous_fields_ok():
    req = CompareRequest(rule_id="R001", field_a=_fi("v1"), field_b=_fi("v2"))
    assert [f.value for f in req.ordered_fields()] == ["v1", "v2"]


def test_three_contiguous_fields_ok():
    req = CompareRequest(
        rule_id="R001",
        field_a=_fi("v1"),
        field_b=_fi("v2"),
        field_c=_fi("v3"),
    )
    assert [f.value for f in req.ordered_fields()] == ["v1", "v2", "v3"]


def test_no_field_at_all_rejected():
    with pytest.raises(ValidationError) as exc:
        CompareRequest(rule_id="R001")
    assert "至少需要传入 field_a" in str(exc.value)


def test_missing_field_a_rejected():
    """只传 field_b:缺 field_a,报错要指名 field_a。"""
    with pytest.raises(ValidationError) as exc:
        CompareRequest(rule_id="R001", field_b=_fi("v2"))
    assert "缺少 field_a" in str(exc.value)


def test_gap_in_middle_rejected():
    """field_a + field_c 断号:报错要指名 field_b。"""
    with pytest.raises(ValidationError) as exc:
        CompareRequest(rule_id="R001", field_a=_fi("v1"), field_c=_fi("v3"))
    assert "缺少 field_b" in str(exc.value)


def test_legacy_fields_array_rejected():
    """旧版 fields 数组由 extra=forbid 拦下。"""
    with pytest.raises(ValidationError):
        CompareRequest(rule_id="R001", fields=[{"value": "v1"}, {"value": "v2"}])


def test_unknown_key_rejected():
    with pytest.raises(ValidationError):
        CompareRequest(rule_id="R001", field_a=_fi("v1"), field_zzz=_fi("v2"))


def test_ordered_fields_carries_name():
    req = CompareRequest(
        rule_id="R001",
        field_a=_fi("v1", "项目代码"),
        field_b=_fi("v2", "项目编号"),
    )
    assert [f.name for f in req.ordered_fields()] == ["项目代码", "项目编号"]


def test_ordered_fields_name_defaults_to_none():
    req = CompareRequest(rule_id="R001", field_a=_fi("v1"))
    assert req.ordered_fields()[0].name is None
```

- [ ] **Step 2: 跑新测试，确认失败**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_compare_schema.py -q
```

预期：`ImportError: cannot import name 'FIELD_LETTERS' from 'app.schemas.compare'`。

- [ ] **Step 3: 重写 `app/schemas/compare.py`**

全文替换为：

```python
"""``/compare`` 接口的请求 / 响应数据模型。"""

from __future__ import annotations

import string

from pydantic import BaseModel, ConfigDict, Field, model_validator

# 字段键后缀:field_a .. field_z。
# 请求校验强制"从 field_a 起连续",因此有序列表的下标 i 与 FIELD_LETTERS[i]
# 构成双射——下游(渲染层、响应构造)可以直接用下标换算字母,无需另传键名。
FIELD_LETTERS = string.ascii_lowercase


class FieldInput(BaseModel):
    """单个字段的输入:名称(可选)+ 值。

    ``name`` 默认是可选的,简化业务侧调用。是否强制要求由具体规则的
    ``require_field_name`` 决定(在 ``/compare`` 入口校验,缺则返 422)。
    """

    name: str | None = Field(
        default=None,
        description="字段名称，例如 '项目代码'、'总投资金额'。是否必填由规则配置决定。",
    )
    value: str = Field(..., description="字段的文本值，可以包含单位/日期/中文/空白")


class CompareRequest(BaseModel):
    """``POST /compare`` 请求体。

    待比对字段以 ``field_a`` / ``field_b`` / ... 平铺传入,必须从 ``field_a``
    开始连续,最多到 ``field_z``。``extra="forbid"`` 让任何未声明的键——包含
    已废弃的 ``fields`` 数组——直接返 422。
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(..., description="规则 ID，对应 config/rules.yaml 中的 id")

    field_a: FieldInput | None = None
    field_b: FieldInput | None = None
    field_c: FieldInput | None = None
    field_d: FieldInput | None = None
    field_e: FieldInput | None = None
    field_f: FieldInput | None = None
    field_g: FieldInput | None = None
    field_h: FieldInput | None = None
    field_i: FieldInput | None = None
    field_j: FieldInput | None = None
    field_k: FieldInput | None = None
    field_l: FieldInput | None = None
    field_m: FieldInput | None = None
    field_n: FieldInput | None = None
    field_o: FieldInput | None = None
    field_p: FieldInput | None = None
    field_q: FieldInput | None = None
    field_r: FieldInput | None = None
    field_s: FieldInput | None = None
    field_t: FieldInput | None = None
    field_u: FieldInput | None = None
    field_v: FieldInput | None = None
    field_w: FieldInput | None = None
    field_x: FieldInput | None = None
    field_y: FieldInput | None = None
    field_z: FieldInput | None = None

    @model_validator(mode="after")
    def _validate_contiguous(self) -> CompareRequest:
        """校验 field_ 键从 a 开始连续,不允许缺头或断号。"""
        present = [
            i
            for i, letter in enumerate(FIELD_LETTERS)
            if getattr(self, f"field_{letter}") is not None
        ]
        if not present:
            raise ValueError("至少需要传入 field_a")
        for expected, actual in enumerate(present):
            if expected != actual:
                raise ValueError(
                    f"field_ 键必须从 field_a 开始连续，缺少 "
                    f"field_{FIELD_LETTERS[expected]}"
                )
        return self

    def ordered_fields(self) -> list[FieldInput]:
        """按 a→z 返回已传字段。

        校验已保证连续,故返回列表的下标 ``i`` 恒对应
        ``field_{FIELD_LETTERS[i]}``,遇到第一个 None 即可停。
        """
        out: list[FieldInput] = []
        for letter in FIELD_LETTERS:
            fi = getattr(self, f"field_{letter}")
            if fi is None:
                break
            out.append(fi)
        return out


class Cost(BaseModel):
    """LLM token 用量。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class CompareResponse(BaseModel):
    """``POST /compare`` 响应体。

    回显字段与请求同构(``field_a`` / ``field_b`` / ...)。路由上配了
    ``response_model_exclude_none=True``,本次未使用的 ``field_*`` 键不会
    出现在响应 JSON 里。
    """

    rule_id: str
    result: bool = Field(..., description="比对结果。True 表示通过/一致")
    reason: str = Field(..., description="判断理由（中文）")
    time: int = Field(..., description="本次调用耗时，毫秒")
    cost: Cost = Field(default_factory=Cost)

    field_a: FieldInput | None = None
    field_b: FieldInput | None = None
    field_c: FieldInput | None = None
    field_d: FieldInput | None = None
    field_e: FieldInput | None = None
    field_f: FieldInput | None = None
    field_g: FieldInput | None = None
    field_h: FieldInput | None = None
    field_i: FieldInput | None = None
    field_j: FieldInput | None = None
    field_k: FieldInput | None = None
    field_l: FieldInput | None = None
    field_m: FieldInput | None = None
    field_n: FieldInput | None = None
    field_o: FieldInput | None = None
    field_p: FieldInput | None = None
    field_q: FieldInput | None = None
    field_r: FieldInput | None = None
    field_s: FieldInput | None = None
    field_t: FieldInput | None = None
    field_u: FieldInput | None = None
    field_v: FieldInput | None = None
    field_w: FieldInput | None = None
    field_x: FieldInput | None = None
    field_y: FieldInput | None = None
    field_z: FieldInput | None = None
```

- [ ] **Step 4: 跑 schema 单测，确认全过**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_compare_schema.py -q
```

预期：`11 passed`。

此时其余测试会大面积报红（`app/api/compare.py` 还在读 `req.fields`），这是预期的中间态，Step 12 才恢复全绿。

- [ ] **Step 5: 改 `app/rules/llm_rule.py` 的渲染键名**

在 `import re` 下方加入 `import string`，把 `_FALLBACK_TEMPLATE`（原 `:23-28`）改为：

```python
# 字段键后缀,与 app.schemas.compare.FIELD_LETTERS 同源。此处不 import schemas,
# 避免规则层反向依赖接口层。
_FIELD_LETTERS = string.ascii_lowercase

# 没有 prompt_template 时的最后兜底,避免崩溃
_FALLBACK_TEMPLATE = (
    "请判断以下字段在语义上是否一致。\n"
    "A: {field_a}\n"
    "B: {field_b}\n"
    '严格输出 JSON: {{"match": true|false, "reason": "<不超过30字>"}}'
)
```

把 `_render_user_prompt`（原 `:65-83`）改为：

```python
    def _render_user_prompt(
        self,
        fields: list[tuple[str, str | None]],
    ) -> str:
        """渲染 prompt_template。

        ``fields`` 为 ``[(value, name), ...]`` 列表,下标 i 对应请求里的
        ``field_{a+i}``(请求侧已强制"从 field_a 起连续")。构建
        ``{field_a}`` ``{field_a_name}`` ... 等占位符的替换字典,
        name 缺失时占位空串。
        """
        replacements: dict[str, str] = {}
        for i, (value, name) in enumerate(fields):
            if i >= len(_FIELD_LETTERS):
                log.warning("字段数超过 %d 个,截断 rule_id=%s", len(_FIELD_LETTERS), self.id)
                break
            letter = _FIELD_LETTERS[i]
            replacements[f"field_{letter}"] = value
            replacements[f"field_{letter}_name"] = name or ""
        try:
            return self.prompt_template.format(**replacements)
        except (KeyError, IndexError) as e:
            log.warning("prompt_template 渲染失败 rule_id=%s err=%s", self.id, e)
            return self.prompt_template
```

把类 docstring（原 `:54-55`）中的两行改为：

```python
    prompt_template 中使用 ``{field_a}`` ``{field_b}`` ... 按字母引用字段值,
    ``{field_a_name}`` ``{field_b_name}`` ... 引用字段名。
```

- [ ] **Step 6: 改 `tests/unit/test_llm_rule.py` 的模板**

5 处替换：

```python
# :20
_CFG = {
    "id": "T_LLM",
    "name": "test rule",
    "prompt_template": "A: {field_a}\nB: {field_b}\n输出 JSON。",
}

# :81
        "prompt_template": "判断字段 {field_a_name} 与 {field_b_name}: A={field_a} B={field_b}",

# :107-108
    """未配置时默认 False(不校验);即使 prompt_template 含 {field_a_name} 也不会自动推导。"""
    cfg = {"id": "X", "prompt_template": "判断 {field_a_name}: {field_a} vs {field_b}"}

# :117
        "prompt_template": "A:{field_a} B:{field_b}",

# :138
        "prompt_template": "A:{field_a} B:{field_b}",
```

- [ ] **Step 7: 跑 llm_rule 单测，确认全过**

```bash
.venv/Scripts/python.exe -m pytest tests/unit/ -q
```

预期：`22 passed`（11 条 schema + 11 条 llm_rule）。

- [ ] **Step 8: 改 `app/api/compare.py` 接线**

import 行（`:21`）改为：

```python
from app.schemas.compare import FIELD_LETTERS, CompareRequest, CompareResponse, Cost
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
    fields = req.ordered_fields()
    log.info(
        "收到比对请求 rule_id=%s fields=%d",
        req.rule_id, len(fields),
    )
```

name 必填校验（`:55-66`）中的列表推导改为：

```python
        missing = [
            f"field_{FIELD_LETTERS[i]}.name"
            for i, f in enumerate(fields)
            if not (f.name or "").strip()
        ]
```

规则调用（`:79`）改为：

```python
        res = rule.compare([(f.value, f.name) for f in fields])
```

历史落库（`:112`、`:117`）两行改为：

```python
        "fields": [f.model_dump() for f in fields],
```

```python
        "raw_request": req.model_dump(exclude_none=True),
```

响应构造（`:120-127`）改为：

```python
    return CompareResponse(
        rule_id=req.rule_id,
        result=res.match,
        reason=res.reason,
        time=elapsed_ms,
        cost=Cost(**(res.usage or {})),
        **{f"field_{FIELD_LETTERS[i]}": f for i, f in enumerate(fields)},
    )
```

- [ ] **Step 9: 替换 `tests/fixtures/rules.yaml` 占位符**

```bash
sed -i 's/{field_0_name}/{field_a_name}/g; s/{field_1_name}/{field_b_name}/g; s/{field_2_name}/{field_c_name}/g; s/{field_0}/{field_a}/g; s/{field_1}/{field_b}/g; s/{field_2}/{field_c}/g' tests/fixtures/rules.yaml
```

确认无残留：

```bash
grep -c "field_[0-9]" tests/fixtures/rules.yaml
```

预期输出 `0`。

再手工改两处自然语言注释——头部注释块的占位符说明段（原 `:14-17`）改为：

```yaml
#   prompt_template        Prompt 模板。所有规则的判定标准都由这里描述。
#                          支持占位符(按请求中 field_a / field_b / ... 的字母):
#                            {field_a} {field_b} {field_c} ...   字段值
#                            {field_a_name} {field_b_name} ...   字段名
```

以及 R003 的 description（原 `:71`）：

```yaml
    description: "判断 field_a 的日期是否严格早于 field_b"
```

- [ ] **Step 10: 改写 `tests/integration/test_compare_api.py` 的现有用例**

把全部 `/compare` 请求体从数组改为平铺键。逐处对照：

```python
# test_unknown_rule_id_returns_404 (:65-68)
    resp = client.post("/compare", json={
        "rule_id": "RXXX",
        "field_a": {"name": "x", "value": "1"},
        "field_b": {"name": "x", "value": "1"},
    })

# test_R001_returns_true_when_llm_says_match (:98-111)
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

# test_R001_returns_false_when_llm_says_no_match (:116-122)
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "field_a": {"name": "项目代码", "value": "2024-001"},
        "field_b": {"name": "项目代码", "value": "2024-002"},
    })

# test_R002_numeric_unit_conversion (:131-137)
    resp = client.post("/compare", json={
        "rule_id": "R002",
        "field_a": {"name": "总投资", "value": "1.2亿"},
        "field_b": {"name": "总投资金额", "value": "12000万元"},
    })

# test_R003_time_order_pass (:145-151)
    resp = client.post("/compare", json={
        "rule_id": "R003",
        "field_a": {"name": "批复时间", "value": "2024年1月10日"},
        "field_b": {"name": "申报时间", "value": "2024-02-01"},
    })

# test_R004_semantic_similar_pass (:157-163)
    resp = client.post("/compare", json={
        "rule_id": "R004",
        "field_a": {"name": "建设单位", "value": "贵州省XX建设有限公司"},
        "field_b": {"name": "建设单位", "value": "XX建设公司"},
    })

# test_R005_invalid_json_returns_false (:173-179)
    resp = client.post("/compare", json={
        "rule_id": "R005",
        "field_a": {"name": "建设内容", "value": "a; b; c"},
        "field_b": {"name": "建设内容", "value": "a; b; c; d"},
    })

# test_field_name_optional_for_normal_rule (:205-208)
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "field_a": {"value": "  2024-001 "},
        "field_b": {"value": "2024-001"},
    })

# test_single_field_compare (:213-231) —— 内联规则模板与请求体同改
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

# test_require_field_name_explicit_missing_returns_422 (:234-253)
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

# test_require_field_name_R004_missing_returns_422 (:256-266)
    resp = client.post("/compare", json={
        "rule_id": "R004",
        "field_a": {"value": "贵州省XX建设有限公司"},
        "field_b": {"value": "XX建设公司"},
    })
    assert resp.status_code == 422
    assert "field_a.name" in resp.json()["detail"]

# test_require_field_name_R004_with_name_passes (:281-287)
    resp = client.post("/compare", json={
        "rule_id": "R004",
        "field_a": {"name": "建设单位", "value": "贵州省XX建设有限公司"},
        "field_b": {"name": "建设单位", "value": "XX建设公司"},
    })
```

- [ ] **Step 11: 追加键校验与响应形状的集成用例**

在 `tests/integration/test_compare_api.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# field_ 键校验(平铺格式)
# ---------------------------------------------------------------------------

def test_legacy_fields_array_returns_422():
    """旧版 fields 数组已不再受理。"""
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "fields": [{"value": "2024-001"}, {"value": "2024-001"}],
    })
    assert resp.status_code == 422


def test_no_field_key_returns_422():
    resp = client.post("/compare", json={"rule_id": "R001"})
    assert resp.status_code == 422
    assert "field_a" in resp.text


def test_missing_field_a_returns_422():
    """只传 field_b:缺头,报错指名 field_a。"""
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "field_b": {"value": "2024-001"},
    })
    assert resp.status_code == 422
    assert "field_a" in resp.text


def test_gap_between_keys_returns_422():
    """field_a + field_c 断号,报错指名 field_b。"""
    resp = client.post("/compare", json={
        "rule_id": "R001",
        "field_a": {"value": "2024-001"},
        "field_c": {"value": "2024-001"},
    })
    assert resp.status_code == 422
    assert "field_b" in resp.text


def test_three_fields_compare(mock_llm, monkeypatch):
    """三字段场景:field_a/field_b/field_c 全部渲染进 prompt。"""
    from app.api import compare as compare_module

    triple_rule = {
        "id": "RTRIPLE",
        "name": "三方金额一致",
        "require_field_name": False,
        "prompt_template": "申报:{field_a} 批复:{field_b} 承诺:{field_c}",
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
        "field_a": {"value": "500万"},
        "field_b": {"value": "500万"},
        "field_c": {"value": "5000000元"},
    })
    assert resp.status_code == 200
    assert resp.json()["result"] is True
    user_msg = captured["messages"][-1]["content"]
    assert "申报:500万" in user_msg
    assert "批复:500万" in user_msg
    assert "承诺:5000000元" in user_msg


def test_response_omits_unused_field_keys(mock_llm):
    """response_model_exclude_none:未使用的 field_c..field_z 不出现在响应里。"""
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
```

- [ ] **Step 12: 改 `tests/integration/test_admin_rules.py` 的 4 处**

```python
# :76 —— fixture R004 的模板已换字母
    assert "{field_a_name}" in body["prompt_template"]

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

- [ ] **Step 13: 跑全量测试，确认全绿**

```bash
.venv/Scripts/python.exe -m pytest -q
```

预期：`59 passed`（原有 42 条 + 11 条 schema 单测 + 6 条新增集成用例）。

若有 failed，先看是否 `app/api/compare.py` 还有遗漏的 `req.fields` 引用：

```bash
grep -rn "req\.fields\|field_[0-9]" app/
```

预期输出为空。

- [ ] **Step 14: 跑 lint**

```bash
.venv/Scripts/python.exe -m ruff check app tests
.venv/Scripts/python.exe -m mypy app
```

预期：ruff 报 `All checks passed!`；mypy 若报 `CompareResponse` 的 `**` 展开类型问题，在该行末尾加 `# type: ignore[arg-type]`。

- [ ] **Step 15: 提交**

```bash
git add app/schemas/compare.py app/rules/llm_rule.py app/api/compare.py \
        tests/fixtures/rules.yaml tests/unit/test_compare_schema.py \
        tests/unit/test_llm_rule.py tests/integration/test_compare_api.py \
        tests/integration/test_admin_rules.py
git commit -m "feat!: /compare 入参改为 field_a/field_b 平铺键

BREAKING CHANGE: 不再受理 fields 数组,传入返 422。
占位符同步改为 {field_a}/{field_a_name};键必须从 field_a 起连续,上限 field_z。"
```

---

## Task 2: 业务规则活配置替换

**Files:**
- Modify: `config/rules.yaml`（43 条规则的 `prompt_template` + 头部注释块）

- [ ] **Step 1: 确认替换前的占位符分布**

```bash
grep -o "{field_[0-9]" config/rules.yaml | sort | uniq -c
```

预期：`field_0` 与 `field_1` 各 47 处、`field_2` 1 处。

- [ ] **Step 2: 批量替换**

```bash
sed -i 's/{field_0_name}/{field_a_name}/g; s/{field_1_name}/{field_b_name}/g; s/{field_2_name}/{field_c_name}/g; s/{field_0}/{field_a}/g; s/{field_1}/{field_b}/g; s/{field_2}/{field_c}/g' config/rules.yaml
```

- [ ] **Step 3: 确认无残留**

```bash
grep -c "field_[0-9]" config/rules.yaml
```

预期输出 `0`。

- [ ] **Step 4: 手工改头部注释块**

把 `config/rules.yaml:13-18` 改为：

```yaml
#   prompt_template        Prompt 模板。所有规则的判定标准都由这里描述。
#                          支持占位符(按请求中 field_a / field_b / ... 的字母):
#                            {field_a} {field_b} {field_c} ...   字段值
#                            {field_a_name} {field_b_name} ...   字段名
#                          field_defs 的下标 0 对应 {field_a},下标 1 对应 {field_b},依此类推。
#                          注意:JSON 示例里的大括号要写成双重 {{ }},否则 .format 会报错。
```

- [ ] **Step 5: 确认 yaml 仍可解析且规则数不变**

```bash
.venv/Scripts/python.exe -c "import yaml; d=yaml.safe_load(open('config/rules.yaml',encoding='utf-8')); print(len(d['rules']))"
```

预期输出 `43`。

- [ ] **Step 6: 提交**

```bash
git add config/rules.yaml
git commit -m "refactor(rules): 业务规则占位符改为 {field_a} 字母制"
```

---

## Task 3: UI 改造

**Files:**
- Modify: `ui/app.js:260-278`（`removeFieldDef`）、`:280-291`（`renderChips`）、`:364-382`（`onPgRuleChange`）、`:385-403`（`runPlayground`）
- Modify: `ui/index.html:540`、`:542`

- [ ] **Step 1: 改 `removeFieldDef` 的占位符重编号**

`ui/app.js:260-278` 整个函数替换为：

```javascript
function removeFieldDef(i) {
    const defs = getFieldDefs();
    if (defs.length <= 1) return;

    const ta = document.getElementById('f-prompt');
    const letter = String.fromCharCode(97 + i);   // 0 → 'a'
    // 被删字段仍被模板引用 → 提醒(占位符会悬空,需手动清理)
    const usedRe = new RegExp(`\\{field_${letter}(_name)?\\}`);
    if (usedRe.test(ta.value)) {
        if (!confirm(`模板仍引用 {field_${letter}}(或其 _name 变体),删除后该占位符将失效或指向其他字段,建议取消并先清理模板。确认删除吗?`)) return;
    }
    // 更靠后的字母自动前移一位:{field_d} → {field_c}(含 _name 变体)
    ta.value = ta.value.replace(/\{field_([a-z])(_name)?\}/g, (m, L, suffix) => {
        const n = L.charCodeAt(0) - 97;
        return n > i ? `{field_${String.fromCharCode(96 + n)}${suffix || ''}}` : m;
    });

    defs.splice(i, 1);
    renderFieldDefRows(defs);
}
```

- [ ] **Step 2: 改 `renderChips` 的占位符生成**

`ui/app.js:280-291` 整个函数替换为：

```javascript
function renderChips() {
    const defs = getFieldDefs();
    const requireName = document.getElementById('f-require').value === 'true';
    const box = document.getElementById('f-chips');
    box.innerHTML = defs.map((name, i) => {
        const letter = String.fromCharCode(97 + i);
        const label = escape(name.trim() || `字段${letter.toUpperCase()}`);
        const nameChip = requireName
            ? `<span class="chip" title="插入 {field_${letter}_name}" onclick="insertPlaceholder('{field_${letter}_name}')">${label}·名</span>`
            : '';
        return `<span class="chip" title="插入 {field_${letter}}" onclick="insertPlaceholder('{field_${letter}}')">${label}</span>${nameChip}`;
    }).join('') + `<span class="chip" onclick="insertPlaceholder('{{&quot;match&quot;: true|false, &quot;reason&quot;: &quot;<不超过30字>&quot;}}')">JSON 输出范式</span>`;
}
```

- [ ] **Step 3: 改 `onPgRuleChange` 的模板反推与标签**

`ui/app.js:364-382` 那一段替换为：

```javascript
    // 字段输入框:优先 field_defs(数量+业务名标签);旧规则回落模板反推
    const defs = Array.isArray(r.field_defs) && r.field_defs.length ? r.field_defs : null;
    let count;
    if (defs) {
        count = defs.length;
    } else {
        // 模板形如 {field_a},倒数第二个字符即字母
        const matches = r.prompt_template.match(/\{field_[a-z]\}/g);
        count = matches
            ? Math.max(...matches.map(m => m.charCodeAt(m.length - 2) - 97)) + 1
            : 2;
    }
    container.innerHTML = Array.from({length: count}, (_, i) => {
        const letter = String.fromCharCode(97 + i);
        const label = defs && defs[i] ? escape(defs[i]) : `field_${letter}`;
        const preName = defs && defs[i] ? escape(defs[i]) : '';
        return `
        <div class="card p-3">
            <label class="field-label"><span class="name">${label}</span>${r.require_field_name ? '<span class="required">*</span>' : ''}</label>
            ${r.require_field_name ? `<input class="input pg-field-name mb-2" data-idx="${i}" placeholder="字段名" value="${preName}">` : ''}
            <textarea class="input pg-field-value" data-idx="${i}" rows="2" placeholder="${label} 的值"></textarea>
        </div>`;
    }).join('');
```

- [ ] **Step 4: 改 `runPlayground` 的 payload 构造**

`ui/app.js:389-403` 那一段替换为：

```javascript
    const valueEls = document.querySelectorAll('.pg-field-value');
    const payload = { rule_id: r.id };
    for (const el of valueEls) {
        const idx = parseInt(el.dataset.idx);
        const letter = String.fromCharCode(97 + idx);
        const value = el.value.trim();
        if (!value) { toast(`field_${letter}.value 不能为空`, 'error'); return; }
        const entry = { value };
        if (r.require_field_name) {
            const nameEl = document.querySelector(`.pg-field-name[data-idx="${idx}"]`);
            entry.name = nameEl ? nameEl.value.trim() : '';
        }
        payload[`field_${letter}`] = entry;
    }
```

（原先在其后的 `const payload = { rule_id: r.id, fields };` 一行随之删除。）

- [ ] **Step 5: 改 `ui/index.html` 的两处文案**

`:540` 的 `<p class="field-help">` 内容改为：

```html
                    <p class="field-help">规则的核心,用自然语言描述判定标准。支持占位符 <code>{field_a}</code> <code>{field_b}</code> ... <code>{field_a_name}</code> ... 按字母引用。<b>JSON 示例里的大括号要写双重 {{ }}</b>,否则 .format 会报错。</p>
```

`:542` 的 textarea placeholder 改为：

```html
                    <textarea id="f-prompt" class="input" rows="10" placeholder="请判断以下字段是否一致...&#10;A: {field_a}&#10;B: {field_b}&#10;严格按 JSON 输出: {{&quot;match&quot;: true|false, &quot;reason&quot;: &quot;...&quot;}}"></textarea>
```

- [ ] **Step 6: 确认无残留数字占位符**

```bash
grep -n "field_[0-9]\|fields\[" ui/app.js ui/index.html
```

预期输出为空。

- [ ] **Step 7: 手工验证 UI**

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --port 6549
```

浏览器打开 `http://localhost:6549/ui/`，验证三点：

1. **Playground** 选中 R001 → 两个输入框标签为业务名或 `field_a`/`field_b` → 填值试跑 → 返回 200 且「原始响应」里是 `field_a`/`field_b` 平铺、无 `field_c`..`field_z`
2. **规则编辑** 打开任一规则 → 字段 chip 显示为 `{field_a}` / `{field_b}` → 点击能插入模板
3. **删字段** 在有 3 个 field_defs 的规则里删中间那个 → 模板中 `{field_c}` 自动变成 `{field_b}`

验证完 `Ctrl+C` 停服务。

- [ ] **Step 8: 提交**

```bash
git add ui/app.js ui/index.html
git commit -m "feat(ui): 规则编辑器与 Playground 适配 field_a 字母键"
```

---

## Task 4: 文档同步

**Files:**
- Modify: `api.md:45-51`、`:52-69`、`:73-98`、`:100-123`、`:125-136`、`:180-204`、`:333-355`
- Modify: `README.md:60-70`
- Modify: `app/api/admin_rules.py:43-46`、`:55-58`

- [ ] **Step 1: 改 `api.md` 的请求参数表（`:45-51`）**

```markdown
| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `rule_id` | string | 是 | 规则 ID，对应 `config/rules.yaml` 中的 `id` |
| `field_a` | object | 是 | 第 1 个待比对字段，含 `name`（可选）和 `value`（必填） |
| `field_b` … `field_z` | object | 否 | 第 2~26 个字段。**必须从 `field_a` 起连续**，不得缺头或断号 |
| `field_*.value` | string | 是 | 字段的文本值 |
| `field_*.name` | string | **看规则** | 字段名。**默认可省略**;若规则配了 `require_field_name: true`,缺则 422 |
```

- [ ] **Step 2: 改 `api.md` 的请求示例（`:52-69`）**

把 `请求示例：` 下方那个 json 代码块的**内容**整体换成下列文本（围栏的三反引号本身保持不动）：

    // 单字段校验
    {
      "rule_id": "cross_rule_08",
      "field_a": {"value": "DN500管网5公里，新建"}
    }

    // 两字段交叉
    {
      "rule_id": "cross_rule_01",
      "field_a": {"name": "总投资", "value": "1.2亿"},
      "field_b": {"name": "总投资金额", "value": "12000万元"}
    }

（上面为便于阅读做了 4 空格缩进，写进 `api.md` 时**去掉这层缩进**。）

- [ ] **Step 3: 改 `api.md` 的响应表与示例（`:73-98`）**

响应表最后一行 `| \`fields\` | array | 回显传入的字段列表 |` 改为：

```markdown
| `field_a` … | object | 回显传入的字段，键名与请求一致。未使用的键不出现 |
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

- [ ] **Step 4: 改 `api.md` 的 curl 示例（`:100-123`）**

```markdown
> 所有规则均走 LLM,判定边界由 `config/rules.yaml` 中各自的 `prompt_template` 描述。
> prompt 中使用 `{field_a}` `{field_b}` ... 按字母引用字段值,`{field_a_name}` ... 引用字段名。

# 单字段校验
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"cross_rule_08",
       "field_a":{"value":"DN500管网5公里，新建"}}'

# 两字段交叉
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"cross_rule_01",
       "field_a":{"name":"总投资","value":"1.2亿"},
       "field_b":{"name":"总投资金额","value":"12000万元"}}'

# 三字段交叉（示例）
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"some_rule",
       "field_a":{"name":"申报表","value":"500万"},
       "field_b":{"name":"批复","value":"500万"},
       "field_c":{"name":"承诺函","value":"500万"}}'
```

- [ ] **Step 5: 改 `api.md` 的错误码表（`:125-136`）**

把 422 那两行改为三行：

```markdown
| 422 | 请求参数 schema 校验失败(如 value 缺失) | FastAPI 默认 |
| 422 | 键名不合法：传了已废弃的 `fields` 数组 / 缺 `field_a` / 字母断号 | FastAPI 默认，detail 内含缺失的键名 |
| 422 | 规则要求 name 必填但缺失 | `{"detail":"rule cross_rule_XX 要求 field_a.name 必填"}` |
```

- [ ] **Step 6: 改 `api.md` 的规则字段说明（`:180-204`、`:333-355`）**

`:187` 的 `field_defs` 行改为：

```markdown
| `field_defs` | list[string] | 否 | 字段定义列表，下标 0 对应 `{field_a}`、下标 1 对应 `{field_b}`，元素为字段业务名（可为空串） |
```

`:190` 与 `:345` 的 `prompt_template` 行改为：

```markdown
| `prompt_template` | string | 是 | prompt 模板，支持占位符 `{field_a}`/`{field_b}`/.../`{field_a_name}`/... |
```

`:202` 的请求示例内 `prompt_template` 改为：

```json
  "prompt_template": "请判断以下两个值是否一致：\nA: {field_a}\nB: {field_b}\n严格按 JSON 输出: {{\"match\": true|false, \"reason\": \"<理由>\"}}"
```

`:342` 的 `field_defs` 行同 `:187` 处理。`:347-350` 的占位符说明段改为：

```markdown
**Prompt 占位符说明**：
- `{field_a}`, `{field_b}`, `{field_c}` ... 按字母引用字段值
- `{field_a_name}`, `{field_b_name}` ... 按字母引用字段名
- JSON 示例中的大括号要写成双重 `{{ }}`，否则 `.format()` 会报错
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

- [ ] **Step 8: 改 `app/api/admin_rules.py` 的两处 description**

`:43-46`：

```python
    field_defs: list[str] | None = Field(
        default=None,
        description="字段定义列表,下标 0 对应 {field_a}、下标 1 对应 {field_b},元素为字段业务名(可为空串)",
    )
```

`:55-58`：

```python
    prompt_template: str = Field(
        ..., min_length=1,
        description="prompt 模板,支持占位符 {field_a}/{field_b}/.../{field_a_name}/...",
    )
```

- [ ] **Step 9: 全库确认无遗漏**

```bash
grep -rn "field_[0-9]\|\"fields\"\|fields\[" --include="*.py" --include="*.md" --include="*.js" --include="*.html" --include="*.yaml" . | grep -v "^./.venv" | grep -v "^./docs/superpowers"
```

预期输出为空（`docs/superpowers/` 下的 spec 与本计划记录的是改造过程，保留原样）。

- [ ] **Step 10: 跑全量测试确认文档改动没碰坏代码**

```bash
.venv/Scripts/python.exe -m pytest -q
```

预期：**0 failed**。

- [ ] **Step 11: 提交**

```bash
git add api.md README.md app/api/admin_rules.py
git commit -m "docs: 接口文档与字段说明同步至 field_a 平铺格式"
```

---

## 验收清单

- [ ] `.venv/Scripts/python.exe -m pytest -q` → 0 failed
- [ ] `.venv/Scripts/python.exe -m ruff check app tests` → All checks passed
- [ ] `grep -rn "field_[0-9]" app/ config/ tests/ ui/` → 空
- [ ] `POST /compare` 传 `fields` 数组 → 422
- [ ] `POST /compare` 传 `field_a` + `field_c` → 422，detail 含 `field_b`
- [ ] 响应体含 `field_a`/`field_b`，不含 `field_c`..`field_z`
- [ ] `http://localhost:6549/docs` 的 Swagger 请求体列出 `field_a`..`field_z`
- [ ] UI Playground 试跑通过，规则编辑器 chip 显示字母占位符

## 上线提示

这是**不向后兼容**的接口变更。发版前须确认所有上游调用方已切换到 `field_a`/`field_b` 格式，否则其请求会全部返回 422。
