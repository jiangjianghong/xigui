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
| `rule_id` | string | 是 | 规则 ID，对应库中规则的 `id`（见「5. 规则 CRUD」） |
| `<字段键>` | object | 是 | 每个待比对字段一个顶层键，值为 `{name?, value}`。键名由规则决定，见下 |
| `<字段键>.value` | string | 是 | 字段的文本值 |
| `<字段键>.name` | string | **看规则** | 字段名。**默认可省略**;若规则配了 `require_field_name: true`,缺则 422 |

**字段键怎么定：**

- 规则没配 `field_defs` → 键是 `field_a`、`field_b`、`field_c`…，个数按 `prompt_template` 里最大的字母占位符
- 规则配了 `field_defs: [keyan_name, zixun_name]` → 键就是这两个 ID，此时传 `field_a` 会返 422

请求键必须与规则的键集合**完全相等**：少传、多传、拼错都返 422，`detail` 里会指名缺哪个/多哪个。

请求示例：

```json
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
| `<字段键>` | object | 回显传入的字段，键名与请求一致。未传的键不出现 |

响应示例：

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

#### curl 示例

> 所有规则均走 LLM,判定边界由各条规则自己的 `prompt_template` 描述。
> prompt 里用 `{键}` 引用字段值、`{键_name}` 引用字段名,键名与请求体一致。

```bash
# 单字段规则
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"zhongxue_cross_wenjainming",
       "field_a":{"value":"DN500管网5公里，新建"}}'

# 默认字母键
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"cross_rule_01",
       "field_a":{"name":"总投资","value":"1.2亿"},
       "field_b":{"name":"总投资金额","value":"12000万元"}}'

# 规则自定义 ID
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"xiangmu_rule_01",
       "keyan_name_baogao":{"value":"XX供水工程"},
       "zixun_name_baogao":{"value":"XX供水项目"}}'
```

#### 错误码

| HTTP | 触发条件 | 响应体 |
| --- | --- | --- |
| 200 | 比对成功(无论 true / false) | `CompareResponse` |
| 200 | LLM 输出非合法 JSON | `result: false, reason: "LLM 输出非法 JSON"` |
| 422 | 字段值形状不对(缺 `value`、值不是对象、传了已废弃的 `fields` 数组) | FastAPI 默认 |
| 422 | 字段键不匹配:少传/多传/拼错 | `{"detail":"rule cross_rule_XX 缺少字段: field_b（本规则需要: field_a, field_b）"}` |
| 422 | 规则要求 name 必填但缺失 | `{"detail":"rule cross_rule_XX 要求 field_a.name 必填"}` |
| 422 | 写规则时 `field_defs` 元素非法 | `{"detail":"field_defs 元素非法: '建设单位A'..."}` |
| 404 | `rule_id` 不存在 | `{"detail":"rule_id 不存在: cross_rule_XX"}` |
| 500 | 规则配置错误(如 prompt 模板占位符与字段键不匹配) / 其他未捕获异常 | `{"detail":"..."}` |
| 502 | LLM 上游错误(鉴权/网络) | `{"detail":"LLM 上游错误: ..."}` |
| 503 | 数据库不可用 | `{"detail":"数据库不可用,请稍后重试"}` |
| 504 | LLM 调用超时 | `{"detail":"LLM 调用超时: ..."}` |

---

### 3. 重新加载规则（已降级）

- 方法：`POST`
- 路径：`/admin/reload-rules`
- 描述：**已降级为空操作。** 规则真源已是 MySQL，每次请求实时查库，不存在可 reload 的缓存。端点保留而非删除，是怕既有运维脚本还在调它——突然 404 比空操作更难排查。

响应（`rules_loaded` 为库中当前规则总数）：

```json
{"status": "ok", "rules_loaded": 54, "note": "规则已实时读库,无需 reload"}
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
| `GET` | `/admin/rules` | 列出全部规则,返回 `{count, rules: [...]}`。支持 `?project=<key>` 按项目筛选、`?project=__none__` 只看未分组 |
| `GET` | `/admin/rules/{id}` | 按 id 查询单条规则 |
| `POST` | `/admin/rules` | 新增。id 冲突返 422;`project` 引用不存在的项目返 422 |
| `PUT` | `/admin/rules/{id}` | 全量替换。支持 rename（body.id 可以与 URL 中的 id 不同，但新 id 不能与已有规则冲突） |
| `DELETE` | `/admin/rules/{id}` | 删除 |

规则存在 MySQL，写操作**立即生效**(下一次 `/compare` 就是新规则)。`rules_loaded` 字段保留，值为库中规则总数。

每条规则的响应都带嵌套 `project`，未分组时为 `null`：

```json
{
  "id": "cross_rule_01",
  "name": "总投资金额一致",
  "description": "...",
  "require_field_name": false,
  "system_prompt": "...",
  "prompt_template": "...",
  "project": { "key": "zhengfu_touzi", "name": "政府投资项目" }
}
```

#### 规则请求体字段说明

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 是 | 规则唯一编号（如 `cross_rule_01`），全局唯一 |
| `project` | string \| null | 否 | 所属项目的 `key`。`null` 或省略表示未分组。引用不存在的项目返 422 |
| `name` | string | 否 | 中文规则名（用于日志/展示） |
| `description` | string | 否 | 规则描述，仅文档用 |
| `field_defs` | list[string] | 否 | 自定义字段 ID 列表，元素即请求体里的字段键。不写/为空则用 `field_a`/`field_b`/…。元素须匹配 `^[A-Za-z_][A-Za-z0-9_]*$`、规则内不重复、不得为 `rule_id` |
| `require_field_name` | bool | 否 | 是否要求请求里每个 `field.name` 都必填。默认 `false` |
| `system_prompt` | string | 否 | LLM 系统提示。留空则不向模型发送 system 消息 |
| `prompt_template` | string | 是 | prompt 模板，占位符与字段键同名：`{field_a}` 或 `{keyan_name}`，加 `_name` 后缀引用字段名 |

请求示例：

```json
{
  "id": "cross_rule_12",
  "project": "zhengfu_touzi",
  "name": "新规则示例",
  "description": "示例规则描述",
  "field_defs": ["shenbao_jine", "pifu_jine"],
  "require_field_name": false,
  "system_prompt": "你是一个校验专家",
  "prompt_template": "请判断以下两个值是否一致：\nA: {shenbao_jine}\nB: {pifu_jine}\n严格按 JSON 输出: {{\"match\": true|false, \"reason\": \"<理由>\"}}"
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
        {"key": "field_a", "name": "总投资", "value": "1.2亿"},
        {"key": "field_b", "name": "总投资金额", "value": "12000万元"}
      ],
      "result": true,
      "reason": "1.2亿等于12000万元",
      "time_ms": 1520,
      "cost": {"prompt_tokens": 156, "completion_tokens": 12, "total_tokens": 168},
      "created_at": "2025-01-15T10:30:45.123456",
      "raw_request": {
        "rule_id": "cross_rule_01",
        "field_a": {"name": "总投资", "value": "1.2亿"},
        "field_b": {"name": "总投资金额", "value": "12000万元"}
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
- 内容:① 规则增删改查(每个字段含说明)② 项目管理与规则批量移入移出 ③ yaml 导入导出 ④ Playground 试跑(选规则 + 填字段值 + 看结果)⑤ 请求历史(查看所有比对请求记录)
- 用途:非开发者维护规则、调试 prompt 时尤其方便,改完一条规则点保存,效果立即可验。

---

### 8. 项目管理

规则可归属到 0 或 1 个项目,便于按项目类型分组。未分组是合法状态。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/admin/projects` | 列出全部项目,每项带 `rule_count` |
| `GET` | `/admin/projects/{key}` | 按 key 查询单个项目 |
| `POST` | `/admin/projects` | 新增。key 冲突返 422 |
| `PUT` | `/admin/projects/{key}` | 修改,**支持改 key**。目标 key 被占用返 422 |
| `DELETE` | `/admin/projects/{key}` | 删除。其下规则变为未分组,**规则本身不删** |
| `POST` | `/admin/projects/{key}/rules` | 批量把规则移入该项目 |
| `POST` | `/admin/rules/unassign` | 批量把规则移出项目(置为未分组) |

URL 用 `key` 而非自增 id,与规则接口用 `rule_id` 保持一致。

#### 项目请求体字段说明

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `key` | string | 是 | 项目稳定标识,导出的 yaml 引用它。须匹配 `^[A-Za-z_][A-Za-z0-9_]*$`(与 `field_defs` 同一套正则) |
| `name` | string | 是 | 项目中文名 |
| `description` | string | 否 | 项目描述,仅文档用 |

改 `key` **不影响规则归属**——规则通过内部 `project_id` 关联,`key` 只是对外标识。但导出的 yaml 引用的是 `key`,故改 key 后旧导出文件再导入会因项目未定义而使相关规则落到未分组(触发 warning,不中断)。

#### 删除项目

返回受影响的规则数,供 UI 提示:

```json
{"status": "ok", "key": "zhengfu_touzi", "unassigned_rules": 12}
```

#### 批量移入 / 移出

请求体都是 `{"rule_ids": ["a", "b"]}`,响应 `{"status":"ok","moved": 2}`。不存在的 rule_id 静默跳过,`moved` 是实际命中条数。空数组返 422。

移出不设计成 `DELETE /admin/projects/{key}/rules`,原因有二:带 body 的 DELETE 虽合法,但部分 HTTP 客户端与代理会静默丢弃其 body;且"移出"本质是把归属置空,不需要知道原项目是谁。

```bash
# 批量移入
curl -X POST http://localhost:6549/admin/projects/zhengfu_touzi/rules \
  -H "Content-Type: application/json" \
  -d '{"rule_ids":["cross_rule_01","cross_rule_02"]}'

# 批量移出
curl -X POST http://localhost:6549/admin/rules/unassign \
  -H "Content-Type: application/json" \
  -d '{"rule_ids":["cross_rule_01"]}'
```

#### 错误码

| 状态码 | 场景 |
| --- | --- |
| 404 | 项目不存在(查询/修改/删除/批量移入) |
| 422 | key 已存在 / key 非法 / 目标 key 被占用 / `rule_ids` 为空 |

---

### 9. 导入导出

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/admin/export` | 导出 yaml 附件。`?project=<key>` 只导该项目、`?project=__none__` 只导未分组、不传导全部 |
| `POST` | `/admin/import` | multipart 上传 yaml,按 id **合并 upsert** |

#### 导出

响应是 `application/x-yaml` 附件,文件名带日期:`rules-YYYYMMDD.yaml`,按项目导出时为 `rules-<key>-YYYYMMDD.yaml`,未分组为 `rules-unassigned-YYYYMMDD.yaml`。

文件顶层含 `projects:` 节点(项目定义)与 `rules:` 节点,每条规则带 `project: <key>`。未分组的规则不写 `project` 键。按项目导出时 `projects:` 只含该项目;导未分组时无 `projects:` 节点。

```bash
curl -OJ http://localhost:6549/admin/export
curl -OJ "http://localhost:6549/admin/export?project=zhengfu_touzi"
```

项目不存在返 404。

#### 导入

**语义是合并 upsert,不是替换**:项目按 `key`、规则按 `id` 比对——库里有则覆盖,没有则新增;**库里有而文件里没有的原封不动**。因此可以只导入含数条规则的片段文件。

已存在规则的 `sort_order` **不被覆盖**,否则导入片段会打乱现有顺序。

**全文件单事务**:任何一条规则校验不过则**整体回滚**返 422,不会留下"导入了一半"的中间状态——那种状态既不知道成功了哪几条,也不敢重试。

```bash
curl -X POST http://localhost:6549/admin/import \
  -F "file=@rules-20260824.yaml"
```

响应：

```json
{
  "status": "ok",
  "projects": { "created": 2, "updated": 1 },
  "rules":    { "created": 10, "updated": 5 },
  "warnings": ["规则 cross_rule_09 引用的项目 xxx 未在文件中定义,已置为未分组"]
}
```

#### 错误码

| 状态码 | 场景 | 说明 |
| --- | --- | --- |
| 422 | 扩展名不是 `.yaml` / `.yml` | 文件名校验 |
| 422 | 文件为空或非 UTF-8 编码 | — |
| 422 | YAML 语法错误 | detail 里带行号 |
| 422 | 规则/项目内容校验失败 | detail 指明第几条错在哪,**整体回滚** |
| 200 | 引用了未定义的项目 | 不中断,warning + 该规则置未分组 |

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
| `rules_config_path` | 规则**种子**文件路径，默认 `config/rules.yaml`。仅用于首次导入与默认导出文件名，直接编辑不生效 |

### 数据库 (config/config.yaml 的 `mysql:` 节)

| 字段 | 说明 |
| --- | --- |
| `mysql.host` | 主机，默认 `127.0.0.1` |
| `mysql.port` | 端口，默认 `3306` |
| `mysql.database` | 库名，默认 `ai_cross_check` |
| `mysql.username` | 用户名，默认 `root` |
| `mysql.password` | 密码 |
| `mysql.charset` | 字符集，默认 `utf8mb4`（规则里全是中文 prompt） |
| `mysql.pool_size` | 连接池大小，默认 5 |
| `mysql.max_overflow` | 池外最多再开几条，默认 10 |
| `mysql.echo` | `true` 时打印所有 SQL，排错用。默认 `false` |

`host` / `port` / `database` / `username` / `password` 五项支持同名大写环境变量覆盖(`MYSQL_HOST` 等)，优先级高于配置文件——生产不想把密码写进文件时用。其余四项只读配置文件。

启动时**自动建库建表**，需要应用账号有 `CREATE` 权限；无权限时把 `docs/schema.sql` 交给 DBA 手工执行。**不做字段自动迁移**,表结构变更需人工 `ALTER` 并同步更新 `docs/schema.sql` 与 `app/db/models.py`。

### 规则种子 / 导入导出格式 (config/rules.yaml)

> 规则真源是 MySQL。本文件只在两处参与:①首次启动且 `rules` 表为空时自动导入一次 ②导入导出的载体格式。**直接编辑它不会生效。**

顶层含可选的 `projects:` 与 `rules:` 两个节点。每条规则包含以下字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 规则唯一标识（如 `cross_rule_01`），全局唯一 |
| `project` | string | 所属项目的 `key`。省略即未分组。引用未定义的项目会告警并置为未分组，不中断导入 |
| `name` | string | 规则名称（仅用于日志/文档） |
| `description` | string | 规则描述（仅用于日志/文档） |
| `field_defs` | list[string] | 自定义字段 ID 列表，元素即请求体里的字段键。不写/为空则用 `field_a`/`field_b`/… |
| `require_field_name` | bool | 是否要求请求里每个 `field.name` 都必填 |
| `system_prompt` | string | LLM 系统提示 |
| `prompt_template` | string | Prompt 模板，占位符与字段键同名：`{field_a}` 或 `{keyan_name}`，加 `_name` 后缀引用字段名 |

`projects:` 节点每项含 `key` / `name` / `description`。规则引用 `key` 而非自增 id，因为自增 id 在开发库与生产库必然不同，跨环境导入会错乱。

**Prompt 占位符说明**：
- `{键}` 引用字段值，`{键_name}` 引用字段名
- 键名由 `field_defs` 决定；未配置时是 `field_a`、`field_b`、`field_c`…
- `match` 必须让模型输出 `true|false`，输出中文会被 `bool()` 判成 `True`
- JSON 示例中的大括号要写成双重 `{{ }}`，否则 `.format()` 会报错

**改规则的方式**：
- Web UI 的「规则管理」页增删改，**立即生效**（每次请求实时读库）
- 或用「导入」上传 yaml（按 id 合并 upsert，见「9. 导入导出」）
- `POST /admin/reload-rules` 已降级为空操作
