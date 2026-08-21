# `/compare` 自定义字段 ID 设计

日期：2026-08-20
状态：已确认，待实施
取代：`2026-08-10-compare-field-keys-design.md`（那份只做到字母键，没有自定义 ID）

## 背景

改造启动时的判断是「把 `fields` 数组改成 `field_a`/`field_b` 平铺键」。实施前从生产服务器导出规则配置，发现两件事推翻了原有前提。

**其一，生产接口本来就是平铺键。** 导出文件的头部注释写着：

```
#   require_field_name     bool。是否要求请求里 field_a.name / field_b.name 必填。
```

生产环境那版接口的请求体一直是 `field_a` / `field_b` 平铺的。仓库里的 `fields` 数组是后来重构引入的，反而偏离了生产契约。所以本次改造不是「引入新格式」，而是**让代码回归生产已有的契约**。上游消费者无需改动。

**其二，规则配置里已经存在字段 ID 的雏形。** 被覆盖前的工作区版本中，22 条规则配了 `field_defs`，元素形如 `keyan_name_baogao` / `zixun_name_baogao` —— slug 形态的标识符，语义是「可研报告的项目名称」对「咨询评估报告的项目名称」。这些 ID 当时只存在于规则侧，请求侧仍靠数组下标定位，语义没有传递到调用方。

由此确定本次改造的真正目标：**让字段键既能是默认的 `field_a`/`field_b`，也能是规则自定义的语义化 ID。**

## 设计决策

### 决策 1：平铺键，不兼容 `fields` 数组

请求体形状：

```json
// 默认键
{
  "rule_id": "cross_rule_01",
  "field_a": {"name": "总投资",     "value": "1.2亿"},
  "field_b": {"name": "总投资金额", "value": "12000万元"}
}

// 规则自定义 ID
{
  "rule_id": "xiangmu_rule_01",
  "keyan_name_baogao": {"value": "XX供水工程"},
  "zixun_name_baogao": {"value": "XX供水项目"}
}
```

`fields` 数组不再受理，传入返 422。字段 ID 与 `rule_id` 同处顶层，`rule_id` 是保留字。

### 决策 2：请求侧与模板侧严格一致

规则决定本次请求该出现哪些键，请求必须一字不差对上，模板占位符用同一套键。不做「双键并存」（同一个值既能用 `{field_a}` 又能用 `{keyan_name_baogao}` 引用）——那会让模板读者无法确定字段来源。

代价：配了 `field_defs` 的规则，模板必须写自定义 ID，不能写 `{field_a}`。

### 决策 3：`field_defs` 升格为字段 ID

`field_defs` 此前身兼二职（UI 显示的业务名 + 事实上的 ID）。现明确它**只是字段 ID 列表**，写入时校验：

- 匹配 `^[A-Za-z_][A-Za-z0-9_]*$`
- 规则内不重复
- 不得为 `rule_id`

中文业务名改走请求体里已有的 `name` 字段，不重复承担。

**当前 54 条规则一律不配 `field_defs`**，全部使用默认字母键 —— 因为现有消费者用的就是 `field_a`/`field_b`。自定义 ID 是为将来新规则准备的能力。

> 这意味着自定义 ID 路径在生产配置里零使用。除测试覆盖外，实施后已用临时探针规则
> 对真实 LLM 端到端验证过一次（创建规则 → 用自定义 ID 调用 → 断言回显与 422 分支 →
> 删除规则），见本文末「实施验证记录」。

### 决策 4：键集合必须完全相等

配了 `field_defs: [a, b, c]` 的规则，请求传的键必须与之完全相等。少传、多传、拼错全部 422，错误信息指名缺哪个/多哪个。

不采用「允许少传、缺的渲染成空串」——空值会悄悄发给 LLM，比对结果莫名其妙，而拼写错误会被静默吞掉。

## 架构

### 键集合是唯一真相源

```
规则配置                          →  本规则的合法字段键（有序）
─────────────────────────────────────────────────────────────
field_defs 缺失 / 空数组          →  field_a, field_b, ...
                                     （个数按 prompt_template 里最大的字母占位符反推）
field_defs: [keyan_x, zixun_x]   →  keyan_x, zixun_x
```

三处由它派生，各自的职责边界：

| 派生处 | 用法 |
|---|---|
| 请求校验 | 传入键集合必须与之**完全相等** |
| prompt 渲染 | 每个键注入 `{键}` 与 `{键_name}` 两个占位符 |
| 响应回显 | 按同样的键平铺 |

### 校验时机：搬到路由层

键集合依赖 `rule_id` 查出来的规则配置，而 Pydantic 模型校验跑在路由函数体之前——那时还不知道是哪条规则。因此：

- `CompareRequest` 只静态声明 `rule_id`，配 `extra="allow"` 收下其余键
- 每个 extra 值先按 `FieldInput`（`{name?, value}`）解析，形状不对 → 422
- 路由查到规则、算出键集合后，再做完全一致性校验 → 422

**副作用**：Swagger 请求体列不出具体键名，只能标成「任意键 → `{name?, value}`」，靠 description 说明。这是决策 2 的必然代价，无法绕过。

### 单元边界

| 单元 | 职责 | 依赖 |
|---|---|---|
| `app/schemas/compare.py` | `FieldInput` / `CompareRequest` / `CompareResponse` 形状，extra 值解析 | 无 |
| `app/rules/field_keys.py`（新建） | 由规则配置推导有序键集合；模板占位符反推 | 无（纯函数，不 import schemas/api） |
| `app/rules/llm_rule.py` | 按键集合渲染 prompt | `field_keys` |
| `app/api/compare.py` | 键集合一致性校验、错误映射、历史落库 | `schemas` + `field_keys` |
| `app/api/admin_rules.py` | `field_defs` 写入校验 | `field_keys` |

把键推导单独拆成 `field_keys.py` 而非塞进 schemas，是因为渲染层（`llm_rule`）也要用它，而规则层不应反向依赖接口层。

## 错误矩阵

| 场景 | 码 | detail |
|---|---|---|
| `rule_id` 不存在 | 404 | `rule_id 不存在: X` |
| 传了 `fields` 数组 | 422 | 未知字段: `fields` |
| 缺字段 | 422 | `rule X 缺少字段: field_b` |
| 多传/拼错字段 | 422 | `rule X 不接受字段: field_bb` |
| 字段值形状不对 | 422 | FastAPI 默认（缺 `value` 等） |
| 规则要求 name 但缺失 | 422 | `rule X 要求 field_a.name 必填` |
| `field_defs` 元素非法（写规则时） | 422 | `field_defs 元素非法: 建设单位A` |
| LLM 超时 / 上游错误 | 504 / 502 | 沿用现状 |

## 测试策略

| 层 | 覆盖 |
|---|---|
| `tests/unit/test_field_keys.py`（新建） | 键推导：无 `field_defs` 按占位符反推、有 `field_defs` 直接用、单字段、空 `field_defs` 等价于缺失 |
| `tests/unit/test_compare_schema.py`（新建） | extra 值解析、`FieldInput` 形状校验 |
| `tests/unit/test_llm_rule.py` | 渲染用字母键与自定义 ID 两套模板 |
| `tests/integration/test_compare_api.py` | 完全一致性的四个分支（齐/缺/多/拼错）、`fields` 数组 422、自定义 ID 端到端、响应形状 |
| `tests/integration/test_admin_rules.py` | `field_defs` 校验的合法与非法输入 |

自定义 ID 路径没有生产数据兜底，集成测试必须包含一条注入的自定义 ID 规则跑通端到端。

## 配置迁移（已完成）

`config/rules.yaml` 已从生产导出版本转换到位：

| 项 | 处数 |
|---|---|
| `{a_value}` → `{field_a}` | 54 |
| `{b_value}` → `{field_b}` | 53 |
| 删 `expected_field_names` | 54 |
| 头部注释块重写 | 1 段 |
| `cross_rule_01` 的 `match` 由 `"一致"\|"不一致"` 改为 `true\|false` | 1 |

最后一项是修 bug：`llm_rule.py` 用 `bool(parsed.get("match"))` 判定，而 `bool("不一致")` 为 `True`，该规则此前恒返回 `result: true`。

54 条规则均已逐条模拟 `.format()` 渲染通过。原始文件备份于 `config/rules.yaml.orig.bak`，改造验证完毕后删除，不入库。

**当前系统处于不可用中间态**：配置已用 `{field_a}`，代码仍认 `{field_0}`，`.format()` 抛 `KeyError` 后被 `llm_rule.py:82` 吞掉并返回未渲染的模板原文——LLM 收到字面量 `{field_a}`，接口却照常返回 200。这是静默降级，只有一条 WARNING 日志。代码改造必须尽快跟上。

## 实施验证记录

2026-08-21，实施完成后对真实服务（`localhost:6549`，接真实 LLM）跑的端到端验证：

| 场景 | 结果 |
|---|---|
| 默认字母键 + 真实 LLM（`1.2亿` vs `12000万元`） | 200，`result=true`，`reason="均为120000000元"`；回显 `field_a`/`field_b`，无 `fields` 键、无 `field_c..z` |
| 自定义 ID 建规则 → 调用（`keyan_jine`/`zixun_jine`） | 201 → 200，`result=true`，`reason="500万元等于5000000元"`，按自定义 ID 回显 |
| 对自定义 ID 规则传 `field_a`/`field_b` | 422，detail 同时列出缺少的两个 ID 与不接受的两个字母键 |
| 缺字段 / 多字段 / 拼错键 | 全部 422，detail 指名到具体键 |
| 旧版 `fields` 数组 | 422 |
| 写入中文 `field_defs` | 422 |
| 单字段规则只传 `field_a` | 200 |

临时探针规则验证后已删除，`config/rules.yaml` 仍为 54 条、无 `field_defs`（唯一残留差异是文件末尾补了换行符，ruamel 写盘时规范化，内容零变化）。

## 上线提示

对**上游消费者**：生产接口本就是 `field_a`/`field_b` 平铺，本次改造后契约不变，无需改动。

对**仓库当前代码的调用方**（若有按 `fields` 数组对接的）：不向后兼容，请求会返 422。
