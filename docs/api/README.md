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
  "rules_loaded": 5,
  "rule_ids": ["R001", "R002", "R003", "R004", "R005"]
}
```

---

### 2. 规则比对（核心接口）

- 方法：`POST`
- 路径：`/compare`
- 描述：根据 `rule_id` 对两个字段做比对，返回布尔结果 + 中文理由 + 耗时 + token 成本。

请求体：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `rule_id` | string | 是 | 规则 ID，对应 `config/rules.yaml` 中的 `id` |
| `field_a` | object | 是 | 字段 A，含 `name`（可选）和 `value`（必填） |
| `field_b` | object | 是 | 字段 B，结构同上 |
| `field_x.value` | string | 是 | 字段的文本值 |
| `field_x.name` | string | **看规则** | 字段名。**默认可省略**;若规则配了 `require_field_name: true`,缺则 422 |

请求示例(name 可省略,等价于完整写法):

```json
{
  "rule_id": "R001",
  "field_a": {"value": "  2024-001 "},
  "field_b": {"value": "2024-001"}
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

响应示例：

```json
{
  "rule_id": "R001",
  "result": true,
  "reason": "两个项目代码一致",
  "time": 312,
  "cost": {"prompt_tokens": 156, "completion_tokens": 12, "total_tokens": 168}
}
```

#### 五条规则 curl 示例

> 所有规则均走 LLM,判定边界由 `config/rules.yaml` 中各自的 `prompt_template` 描述,详见 [`rules.md`](rules.md)。

```bash
# R001 项目代码完全一致
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"R001",
       "field_a":{"name":"项目代码","value":"  ABC-001 "},
       "field_b":{"name":"项目代码","value":"abc-001"}}'

# R002 总投资金额一致
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"R002",
       "field_a":{"name":"总投资","value":"1.2亿"},
       "field_b":{"name":"总投资金额","value":"12000万元"}}'

# R003 批复时间早于申报时间
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"R003",
       "field_a":{"name":"批复时间","value":"2024年1月10日"},
       "field_b":{"name":"申报时间","value":"2024-02-01"}}'

# R004 建设单位名称基本一致(必须传 name,因 prompt 用了 {a_name}/{b_name})
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"R004",
       "field_a":{"name":"建设单位","value":"贵州省XX建设有限公司"},
       "field_b":{"name":"建设单位","value":"XX建设公司"}}'

# R005 建设内容详情完全一致
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"R005",
       "field_a":{"name":"建设内容","value":"机房;光纤;UPS"},
       "field_b":{"name":"建设内容","value":"光纤、UPS、机房"}}'
```

#### 错误码

| HTTP | 触发条件 | 响应体 |
| --- | --- | --- |
| 200 | 比对成功(无论 true / false) | `CompareResponse` |
| 200 | LLM 输出非合法 JSON | `result: false, reason: "LLM 输出非法 JSON"` |
| 422 | 请求参数 schema 校验失败(如 value 缺失) | FastAPI 默认 |
| 422 | 规则要求 name 必填但缺失 | `{"detail":"rule R00X 要求 field_a.name 必填"}` |
| 404 | `rule_id` 不存在 | `{"detail":"rule_id 不存在: Rxxx"}` |
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
{"status": "ok", "rules_loaded": 5}
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
| `PUT` | `/admin/rules/{id}` | 全量替换。请求体里的 id 必须与 URL 一致 |
| `DELETE` | `/admin/rules/{id}` | 删除 |

写操作完成后自动 reload,**无需重启**且**保留 yaml 文件里的注释**。

### 6. Web 管理界面

- 路径:`/ui/`(根路径 `/` 自动 302 跳转过来)
- 内容:① 规则增删改查(每个字段含说明)② Playground 试跑(选规则 + 填字段值 + 看结果)
- 用途:非开发者维护规则、调试 prompt 时尤其方便,改完一条规则点保存,效果立即可验。
