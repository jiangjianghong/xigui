# 规则配置字段定义(field_defs)设计

日期:2026-06-10
状态:已确认(方案 A)

## 背景与目标

当前配置规则时,字段数量靠 `prompt_template` 里的 `{field_N}` 占位符隐式决定:

- 配置弹窗的占位符 chip 写死 `{field_0}` `{field_1}` `{field_2}`,要更多字段只能手敲;
- 字段没有业务名,试跑页只能显示 `fields[0]` 这种无意义标签;
- 模板写错下标(如引用了 `{field_2}` 但实际只传 2 个字段)要到运行时才暴露
  (`app/rules/llm_rule.py:79-83` 渲染 KeyError 后把模板原样发给 LLM)。

目标:配置时**先用"+"添加字段(填字段名),再点字段 chip 插入占位符**,
字段定义落盘到规则配置,配置/校验/试跑三处联动。

## 数据模型

规则新增**可选**字段 `field_defs: list[str]`,下标即占位符下标:

```yaml
- id: R007
  name: 三方金额一致
  field_defs:
    - 申报金额
    - 批复金额
    - 合同金额
  require_field_name: false
  prompt_template: |
    申报金额: {field_0}
    批复金额: {field_1}
    合同金额: {field_2}
    严格按 JSON 输出: {{"match": true|false, "reason": "<不超过30字>"}}
```

- `app/api/admin_rules.py` 的 `RuleIn` 加 `field_defs: list[str] | None = None`;
- `app/core/rules_io.py` 的 `normalize_rule` 字段顺序表加 `field_defs`(放 `description` 之后);
- `/compare` 运行时**不读取**该字段,渲染逻辑不变,对现有 API 调用方零影响;
- 旧规则没有 `field_defs` 完全合法(向后兼容)。

## 配置弹窗(ui/index.html + ui/app.js)

在 `prompt_template` 区块上方新增**字段定义区**:

```
字段定义
┌──────────────────────────────────┐
│ {field_0}  [申报金额        ]  ✕ │
│ {field_1}  [批复金额        ]  ✕ │
│ {field_2}  [合同金额        ]  ✕ │
└──────────────────────────────────┘
[+ 添加字段]
```

行为:

- 新增规则默认 2 行空字段;至少保留 1 行(仅剩 1 行时该行的 ✕ 禁用);
- 占位符 chip 由字段列表**动态生成**:chip 显示字段名(空则显示 `字段N`),
  点击插入 `{field_N}`;`require_field_name=true` 时每个 chip 旁多一个小按钮插入
  `{field_N_name}`;"JSON 输出范式" chip 保留;
- `require_field_name` 下拉切换时,`_name` 小按钮显隐联动;
- **删除中间字段 i**:
  1. 若模板引用 `{field_i}` / `{field_i_name}` → confirm 提醒"该占位符将失效,需手动清理";
  2. 模板中更大下标 j>i 的 `{field_j}` / `{field_j_name}` 自动减一(正则文本替换;
     JSON 示例的 `{{ }}` 双大括号不匹配 `{field_N}` 正则,不受影响);
- 编辑旧规则(无 `field_defs`):从模板反推字段个数(`{field_N}` 最大下标 + 1,
  无占位符默认 2),字段名留空待补。

## 保存校验(saveRule)

| 校验 | 行为 |
|---|---|
| 模板引用的最大下标 ≥ 字段数 | **阻止保存**(toast 报错) |
| 定义了字段但模板未引用 | confirm 非阻断提醒 |
| `require_field_name=true` 且有字段名为空 | **阻止保存** |
| 既有校验(id/prompt_template 非空、`{field_N_name}` vs require_field_name、rename 提醒) | 保留 |

payload 在现有字段基础上增加 `field_defs`(trim 后的字段名数组)。

## 试跑页联动(onPgRuleChange / runPlayground)

- 输入框数量与标签**优先取 `rule.field_defs`**(标签显示字段名,空名回落 `fields[i]`);
  无 `field_defs` 时回落现有模板反推逻辑;
- `require_field_name=true` 时,字段名输入框**预填**定义里的名字(可编辑)。

## 错误处理

- 后端对 `field_defs` 仅靠 pydantic `list[str] | None` 类型约束,不做业务校验
  (业务校验在前端保存时完成;API 直调方传错只影响 UI 展示,不影响 `/compare`);
- 前端删除字段的模板自动调整失败风险低(纯文本正则),confirm 已兜底。

## 测试

- 后端集成测试(`tests/integration/test_admin_rules.py`):
  - 创建带 `field_defs` 的规则 → GET 返回一致;
  - 不带 `field_defs` 创建/更新仍正常(向后兼容);
  - PUT 更新 `field_defs` 后读取一致。
- 前端无测试框架:`node --check ui/app.js` + 手动验收清单:
  添加/删除字段、chip 插入、删除中间字段时下标自动调整、保存校验、试跑页标签与预填。

## 不做的事(YAGNI)

- 字段定义不带类型/描述/示例值等元数据;
- 后端不基于 `field_defs` 校验 `/compare` 请求的字段数;
- 不做模板可视化构建器。
