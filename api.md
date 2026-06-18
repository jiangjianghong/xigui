# 接口文档

> FastAPI 自动生成的交互式文档地址：`http://<host>:6549/docs`（Swagger）或 `/redoc`。
> 本文件用于固化对外契约。

## 通用约定

- 基础路径：`/`（无前缀）
- 编码：UTF-8
- 数据格式：JSON
- 鉴权：当前未启用（内网调用）。生产环境如需鉴权，建议在反向代理层加 mTLS / token。

## 接口列表

### 1. 健康检查

- 方法：`GET`
- 路径：`/health`
- 描述：返回服务运行状态、已加载规则数,用于探活与排障。

请求参数：无

响应示例：

```json
{
  "status": "ok",
  "service": "ai_cross_check",
  "version": "0.1.0",
  "rules_loaded": 11,
  "rule_ids": ["cross_rule_01", "cross_rule_02", "cross_rule_03", "cross_rule_04", "cross_rule_05", "cross_rule_06", "cross_rule_07", "cross_rule_08", "cross_rule_09", "cross_rule_10", "cross_rule_11"]
}
```

---

### 2. 规则比对（核心接口）

- 方法：`POST`
- 路径：`/compare`
- 描述：根据 `rule_id` 对一个或多个字段做比对，返回布尔结果 + 中文理由 + 耗时 + token 成本。

请求体：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `rule_id` | string | 是 | 规则 ID，对应 `config/rules.yaml` 中的 `id` |
| `fields` | array | 是 | 字段列表（1~N 个），每个元素含 `name`（可选）和 `value`（必填） |
| `fields[].value` | string | 是 | 字段的文本值 |
| `fields[].name` | string | **看规则** | 字段名。**默认可省略**;若规则配了 `require_field_name: true`,缺则 422 |

请求示例：

```json
// 单字段校验
{
  "rule_id": "cross_rule_08",
  "fields": [{"value": "DN500管网5公里，新建"}]
}

// 两字段交叉
{
  "rule_id": "cross_rule_01",
  "fields": [
    {"name": "总投资", "value": "1.2亿"},
    {"name": "总投资金额", "value": "12000万元"}
  ]
}
```

响应：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `rule_id` | string | 回显传入的规则 ID |
| `result` | bool | `true` 表示比对通过(具体判定标准由该规则的 `prompt_template` 决定) |
| `reason` | string | 中文理由(由 LLM 给出) |
| `time` | int | 本次调用耗时，单位毫秒 |
| `cost.prompt_tokens` | int | LLM 输入 token |
| `cost.completion_tokens` | int | LLM 输出 token |
| `cost.total_tokens` | int | 总 token |
| `fields` | array | 回显传入的字段列表 |

响应示例：

```json
{
  "rule_id": "cross_rule_01",
  "result": true,
  "reason": "1.2亿等于12000万元",
  "time": 1520,
  "cost": {"prompt_tokens": 156, "completion_tokens": 12, "total_tokens": 168},
  "fields": [
    {"name": "总投资", "value": "1.2亿"},
    {"name": "总投资金额", "value": "12000万元"}
  ]
}
```

#### curl 示例

> 所有规则均走 LLM,判定边界由 `config/rules.yaml` 中各自的 `prompt_template` 描述。
> prompt 中使用 `{field_0}` `{field_1}` ... 按索引引用字段值,`{field_0_name}` ... 引用字段名。

```bash
# 单字段校验
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"cross_rule_08",
       "fields":[{"value":"DN500管网5公里，新建"}]}'

# 两字段交叉
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"cross_rule_01",
       "fields":[{"name":"总投资","value":"1.2亿"},
                 {"name":"总投资金额","value":"12000万元"}]}'

# 三字段交叉（示例）
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"some_rule",
       "fields":[{"name":"申报表","value":"500万"},
                 {"name":"批复","value":"500万"},
                 {"name":"承诺函","value":"500万"}]}'
```

#### 错误码

| HTTP | 触发条件 | 响应体 |
| --- | --- | --- |
| 200 | 比对成功(无论 true / false) | `CompareResponse` |
| 200 | LLM 输出非合法 JSON | `result: false, reason: "LLM 输出非法 JSON"` |
| 422 | 请求参数 schema 校验失败(如 value 缺失) | FastAPI 默认 |
| 422 | 规则要求 name 必填但缺失 | `{"detail":"rule cross_rule_XX 要求 fields[0].name 必填"}` |
| 404 | `rule_id` 不存在 | `{"detail":"rule_id 不存在: cross_rule_XX"}` |
| 500 | rules.yaml 配置错误 / 其他未捕获异常 | `{"detail":"..."}` |
| 502 | LLM 上游错误(鉴权/网络) | `{"detail":"LLM 上游错误: ..."}` |
| 504 | LLM 调用超时 | `{"detail":"LLM 调用超时: ..."}` |

---

### 3. 重新加载规则

- 方法：`POST`
- 路径：`/admin/reload-rules`
- 描述：强制丢弃规则缓存并从磁盘重读 `rules.yaml`。通常用不上——修改 yaml 后下一次 `/compare` 会基于 mtime 自动 reload；网络挂载等场景下 mtime 不可靠时手动触发。

响应：

```json
{"status": "ok", "rules_loaded": 11}
```

### 4. 重新加载主配置

- 方法：`POST`
- 路径：`/admin/reload-config`
- 描述：强制重读 `config/config.yaml`（含 LLM 配置）。注意：FastAPI 进程内已建立的 OpenAI Client 不会立即更新，新连接才生效。

响应：

```json
{"status": "ok", "llm_model": "qwen-plus", "llm_backend": "dashscope"}
```

---

### 5. 规则 CRUD(配合 Web UI)

> 用 Web UI(`http://<host>:6549/ui/`)操作即可,以下是底层 HTTP 契约,便于脚本化批量导入/导出。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/admin/rules` | 列出全部规则,返回 `{count, rules: [...]}` |
| `GET` | `/admin/rules/{id}` | 按 id 查询单条规则 |
| `POST` | `/admin/rules` | 新增。请求体字段同 `rules.yaml` 中一条规则。id 冲突返 422 |
| `PUT` | `/admin/rules/{id}` | 全量替换。支持 rename（body.id 可以与 URL 中的 id 不同，但新 id 不能与已有规则冲突） |
| `DELETE` | `/admin/rules/{id}` | 删除 |

写操作完成后自动 reload,**无需重启**且**保留 yaml 文件里的注释**。

#### 规则请求体字段说明

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 是 | 规则唯一编号（如 `cross_rule_01`），建议使用 `cross_rule_XX` 格式 |
| `name` | string | 否 | 中文规则名（用于日志/展示） |
| `description` | string | 否 | 规则描述，仅文档用 |
| `field_defs` | list[string] | 否 | 字段定义列表，下标即 `{field_N}` 占位符下标，元素为字段业务名（可为空串） |
| `require_field_name` | bool | 否 | 是否要求请求里每个 `field.name` 都必填。默认 `false` |
| `system_prompt` | string | 否 | LLM 系统提示。留空则不向模型发送 system 消息 |
| `prompt_template` | string | 是 | prompt 模板，支持占位符 `{field_0}`/`{field_1}`/.../`{field_0_name}`/... |

请求示例：

```json
{
  "id": "cross_rule_12",
  "name": "新规则示例",
  "description": "示例规则描述",
  "field_defs": ["字段A", "字段B"],
  "require_field_name": false,
  "system_prompt": "你是一个校验专家",
  "prompt_template": "请判断以下两个值是否一致：\nA: {field_0}\nB: {field_1}\n严格按 JSON 输出: {{\"match\": true|false, \"reason\": \"<理由>\"}}"
}
```

### 6. 请求历史记录

> 每次 `/compare` 请求完成后自动记录，支持按规则 ID 和结果过滤。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/admin/history` | 查询历史记录列表 |
| `GET` | `/admin/history/{record_id}` | 获取单条记录详情 |
| `DELETE` | `/admin/history` | 清空所有历史记录 |

#### 查询历史列表

- 方法：`GET`
- 路径：`/admin/history`

请求参数（Query）：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `rule_id` | string | 否 | 按规则 ID 过滤 |
| `result` | bool | 否 | 按比对结果过滤（`true`=一致，`false`=不一致） |
| `limit` | int | 否 | 每页数量，默认 50，最大 200 |
| `offset` | int | 否 | 偏移量，默认 0 |

响应示例：

```json
{
  "total": 128,
  "items": [
    {
      "id": "a1b2c3d4",
      "rule_id": "cross_rule_01",
      "rule_name": "总投资金额一致",
      "fields": [
        {"name": "总投资", "value": "1.2亿"},
        {"name": "总投资金额", "value": "12000万元"}
      ],
      "result": true,
      "reason": "1.2亿等于12000万元",
      "time_ms": 1520,
      "cost": {"prompt_tokens": 156, "completion_tokens": 12, "total_tokens": 168},
      "created_at": "2025-01-15T10:30:45.123456",
      "raw_request": {
        "rule_id": "cross_rule_01",
        "fields": [
          {"name": "总投资", "value": "1.2亿"},
          {"name": "总投资金额", "value": "12000万元"}
        ]
      }
    }
  ]
}
```

#### 获取单条记录详情

- 方法：`GET`
- 路径：`/admin/history/{record_id}`

响应：同列表中单条记录格式。

#### 清空历史记录

- 方法：`DELETE`
- 路径：`/admin/history`

响应示例：

```json
{"message": "已清空 128 条记录"}
```

#### curl 示例

```bash
# 查询全部历史
curl http://localhost:6549/admin/history

# 按规则过滤
curl "http://localhost:6549/admin/history?rule_id=cross_rule_01"

# 按结果过滤（只看不一致的）
curl "http://localhost:6549/admin/history?result=false"

# 分页查询
curl "http://localhost:6549/admin/history?limit=20&offset=40"

# 获取单条详情
curl http://localhost:6549/admin/history/a1b2c3d4

# 清空历史
curl -X DELETE http://localhost:6549/admin/history
```

---

### 7. Web 管理界面

- 路径:`/ui/`(根路径 `/` 自动 302 跳转过来)
- 内容:① 规则增删改查(每个字段含说明)② Playground 试跑(选规则 + 填字段值 + 看结果)③ 请求历史(查看所有比对请求记录)
- 用途:非开发者维护规则、调试 prompt 时尤其方便,改完一条规则点保存,效果立即可验。

---

## 配置说明

### 主配置 (config/config.yaml)

| 字段 | 说明 |
| --- | --- |
| `app.host` | 监听地址，默认 `0.0.0.0` |
| `app.port` | 监听端口，默认 `6549` |
| `app.log_level` | 日志级别，默认 `info` |
| `app.log_file_path` | 日志文件路径（相对项目根） |
| `app.log_max_bytes` | 单个日志文件最大字节数，默认 10MB |
| `app.log_backup_count` | 日志备份数量，默认 7 |
| `llm.model` | LLM 模型名称 |
| `llm.api_base` | LLM API 地址 |
| `llm.api_key` | LLM API 密钥 |
| `llm.max_tokens` | 最大输出 token 数，默认 8192 |
| `llm.temperature` | 温度参数，默认 0.1 |
| `llm.timeout` | 调用超时（秒），默认 300 |
| `llm.enable_thinking` | 是否启用思考模式，默认 `false` |
| `llm.backend` | 后端类型：`dashscope`（阿里云百炼）或 `vllm`（本地部署） |
| `rules_config_path` | 规则配置文件路径，默认 `config/rules.yaml` |

### 规则配置 (config/rules.yaml)

每条规则包含以下字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 规则唯一标识（如 `cross_rule_01`） |
| `name` | string | 规则名称（仅用于日志/文档） |
| `description` | string | 规则描述（仅用于日志/文档） |
| `field_defs` | list[string] | 字段定义列表，下标即 `{field_N}` 占位符下标 |
| `require_field_name` | bool | 是否要求请求里每个 `field.name` 都必填 |
| `system_prompt` | string | LLM 系统提示 |
| `prompt_template` | string | Prompt 模板，支持 `{field_N}` 和 `{field_N_name}` 占位符 |

**Prompt 占位符说明**：
- `{field_0}`, `{field_1}`, `{field_2}` ... 按索引引用字段值
- `{field_0_name}`, `{field_1_name}` ... 按索引引用字段名
- JSON 示例中的大括号要写成双重 `{{ }}`，否则 `.format()` 会报错

**热加载机制**：
- 自动：修改 yaml 后下一次请求时基于 mtime 自动 reload
- 手动：`curl -X POST http://localhost:6549/admin/reload-rules`
