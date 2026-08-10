# `/compare` 入参改造设计：`fields` 数组 → `field_a`/`field_b`/… 平铺键

日期：2026-08-10
状态：已确认，待实施

## 背景

`POST /compare` 当前用 `fields` 数组承载待比对字段，字段靠**下标**定位：

```json
{
  "rule_id": "cross_rule_01",
  "fields": [
    {"name": "总投资",     "value": "1.2亿"},
    {"name": "总投资金额", "value": "12000万元"}
  ]
}
```

下标语义贯穿全链路：`llm_rule.py` 按 `enumerate` 生成 `{field_0}` 占位符，43 条业务规则与 5 条 fixture 规则的 `prompt_template` 全部依赖它，UI 的规则编辑器与 Playground 也按数字下标渲染。

改造目标是把入参改成平铺的字母键：

```json
{
  "rule_id": "R001",
  "field_a": {"name": "项目代码", "value": "  2024-001 "},
  "field_b": {"name": "项目代码", "value": "2024-001"}
}
```

## 已确认的四项决策

| # | 决策 | 选择 |
|---|---|---|
| 1 | `rules.yaml` 里的 prompt 占位符 | **同步改成 `{field_a}` / `{field_a_name}`**，48 条规则全量替换 |
| 2 | 旧 `fields` 数组格式 | **不兼容**，传入直接 422 |
| 3 | 响应体回显字段 | **同步平铺**成 `field_a`/`field_b`/… |
| 4 | 键的校验规则 | **从 `field_a` 起必须连续**，上限 `field_z`（26 个） |

技术选型：Pydantic 模型**显式声明 26 个可选字段**（而非 `extra="allow"` 或 `create_model`），换取 Swagger 文档完整、IDE/mypy 可补全，且 `extra="forbid"` 白送「旧 `fields` 数组 → 422」。

## 设计

### 1. 数据模型 —— `app/schemas/compare.py`

```python
FIELD_LETTERS = string.ascii_lowercase          # a..z，下标 i ↔ 字母

class FieldInput(BaseModel):
    name: str | None = None
    value: str

class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")   # 旧 fields 数组在此自动 422
    rule_id: str
    field_a: FieldInput | None = None
    # ... 显式声明到 ...
    field_z: FieldInput | None = None

    @model_validator(mode="after")
    def _validate_contiguous(self): ...
    def ordered_fields(self) -> list[FieldInput]: ...
```

`_validate_contiguous` 的判定与报错文案：

| 请求 | 报错 |
|---|---|
| 一个 `field_*` 都不传 | `至少需要传入 field_a` |
| 只传 `field_b` | `field_ 键必须从 field_a 开始连续，缺少 field_a` |
| `field_a` + `field_c` | `field_ 键必须从 field_a 开始连续，缺少 field_b` |

实现要点：收集非 `None` 键的下标得到 `present`，与 `range(len(present))` 逐位比对，第一个不相等的位置即缺失字母。

`ordered_fields()` 按 a→z 返回已传字段列表；校验已保证连续，因此返回列表的下标 `i` 恒等于字母 `FIELD_LETTERS[i]`。

`CompareResponse` 同样显式声明 26 个可选字段。**`/compare` 路由必须加 `response_model_exclude_none=True`**，否则本次请求未使用的那些 `field_*` 键都会以 `null` 出现在响应里（两字段请求会多出 24 个 `"field_x": null`）。

### 2. prompt 渲染 —— `app/rules/llm_rule.py`

由于强制「从 a 起连续」，list 下标与字母构成双射，`compare()` 的签名 `list[tuple[str, str | None]]` **保持不变**，只改 `_render_user_prompt` 内的键名生成：

```python
for i, (value, name) in enumerate(fields):
    letter = FIELD_LETTERS[i]
    replacements[f"field_{letter}"] = value
    replacements[f"field_{letter}_name"] = name or ""
```

同时改写 `_FALLBACK_TEMPLATE`（`{field_0}`/`{field_1}` → `{field_a}`/`{field_b}`）与类/方法 docstring 中的占位符说明。

### 3. API 层 —— `app/api/compare.py`

- `req.fields` → `req.ordered_fields()`（`compare.py:43`、`:58`、`:79`、`:112`、`:126` 共 5 处）
- 422 文案：`fields[{i}].name` → `field_{letter}.name`
- 响应构造：`CompareResponse(rule_id=..., **{f"field_{L}": fi for L, fi in zip(FIELD_LETTERS, ordered)})`

**决策：历史记录内部的 `fields` 保持 list 结构。** 它只服务 UI 历史列表展示（`ui/app.js:565` 读 `record.fields`），不属于对外契约；`raw_request` 改存 `req.model_dump(exclude_none=True)`，自然就是新格式。这样 UI 历史模块零改动。

### 4. 配置批量替换

替换映射（作用于 `prompt_template` 与文件头部注释块）：

| 原 | 新 |
|---|---|
| `{field_0}` | `{field_a}` |
| `{field_0_name}` | `{field_a_name}` |
| `{field_1}` | `{field_b}` |
| `{field_1_name}` | `{field_b_name}` |
| `{field_2}` | `{field_c}` |

覆盖文件：`config/rules.yaml`（43 条规则）、`tests/fixtures/rules.yaml`（5 条规则）。现有规则实际最大索引为 2，即最多 3 个字段。

**前置步骤（重要）**：`config/rules.yaml` 当前有 387 行未提交改动。批量替换前必须先提交该文件或另存备份，否则替换出错时回滚会连带丢失这批改动。

**决策：`field_defs` 保持 `list[string]` 不变。** 它是规则配置而非接口入参，list 顺序天然映射 a/b/c；改成 dict 需要再动 22 条规则并重写 UI 渲染逻辑，收益不抵成本。在 yaml 注释与 `api.md` 中补充说明「下标 0 对应 `{field_a}`」。

### 5. UI —— `ui/app.js` + `ui/index.html`

| 位置 | 改动 |
|---|---|
| `app.js:266-274` | `removeFieldDef` 占位符重编号：数字减一 → 字母前移（删 b 后 `{field_c}` → `{field_b}`）。需 letter → index → -1 → letter 转换 |
| `app.js:284-290` | `renderChips` 插入 `{field_a}` / `{field_a_name}` |
| `app.js:370-374` | 模板反推正则 `/\{field_(\d+)\}/g` → `/\{field_([a-z])\}/g`，字段数由最大字母推导；默认标签 `fields[i]` → `field_a` |
| `app.js:390-403` | `runPlayground` 平铺构造 payload；错误提示改为 `field_a.value 不能为空` |
| `index.html:540` | 占位符说明文案 |
| `index.html:542` | textarea 的 placeholder 示例 |

`renderPgResult`（`app.js:426`）零改动——它只 `JSON.stringify` 整个响应，不读具体字段。

### 6. 文档

- `api.md`：请求参数表、请求示例、响应字段表、响应示例、3 条 curl 示例、错误码表中的 422 文案、规则配置的占位符说明段（约 12 处）
- `README.md:64`：curl 示例
- `app/api/admin_rules.py:57`：`prompt_template` 字段的 description
- `app/api/admin_rules.py:43-46`：`field_defs` 的 description，补充「下标 0 对应 `{field_a}`」

`api.md` 错误码表需新增一行：请求体键名不合法（旧 `fields` 数组 / 缺 `field_a` / 断号）→ 422。

### 7. 测试

**改写：**
- `tests/integration/test_compare_api.py`：全部 `/compare` 用例的请求体；`test_require_field_name_*` 的断言文案 `fields[0].name` → `field_a.name`；`test_single_field_compare` 与 `test_require_field_name_explicit_missing_returns_422` 内联规则的 `prompt_template`
- `tests/unit/test_llm_rule.py`：`_CFG` 及各用例内联的 6 处 `prompt_template`
- `tests/integration/test_admin_rules.py`：`:76`、`:96`、`:137` 的模板断言与 `:232` 的 `/compare` 请求体

**新增：**
- 传旧 `fields` 数组 → 422
- 只传 `field_b`（缺 `field_a`）→ 422，且 detail 含 `field_a`
- `field_a` + `field_c`（断号）→ 422，且 detail 含 `field_b`
- 一个 `field_*` 都不传 → 422
- 单字段 `field_a` 正常返回 200
- 三字段 `field_a`/`field_b`/`field_c` 正常返回 200
- 响应体含 `field_a`、`field_b`，且**不含** `field_c` 到 `field_z`（验证 `response_model_exclude_none`）

## 影响面清单

| 类别 | 文件 |
|---|---|
| 代码 | `app/schemas/compare.py`、`app/api/compare.py`、`app/rules/llm_rule.py`、`app/api/admin_rules.py` |
| 配置 | `config/rules.yaml`、`tests/fixtures/rules.yaml` |
| UI | `ui/app.js`、`ui/index.html` |
| 文档 | `api.md`、`README.md` |
| 测试 | `tests/integration/test_compare_api.py`、`tests/unit/test_llm_rule.py`、`tests/integration/test_admin_rules.py` |

`app/core/history.py` 无需改动（存储结构保持通用 dict）。

## 破坏性变更提示

这是一次**不向后兼容**的接口契约变更。上游调用方必须与本次发版同步切换到 `field_a`/`field_b` 格式，否则全部请求返回 422。发版前需确认所有调用方已就绪。
