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
- 描述：返回服务运行状态、已加载规则数与已注册规则类型，用于探活与排障。

请求参数：无

响应示例：

```json
{
  "status": "ok",
  "service": "wanzi_guizhe",
  "version": "0.1.0",
  "rules_loaded": 5,
  "rule_ids": ["R001", "R002", "R003", "R004", "R005"],
  "registered_types": ["exact_match", "numeric_equal", "semantic_exact", "semantic_similar", "time_order"]
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
| `field_a` | object | 是 | 字段 A，含 `name`（字段名）和 `value`（文本值） |
| `field_b` | object | 是 | 字段 B，结构同上 |

请求示例：

```json
{
  "rule_id": "R001",
  "field_a": {"name": "项目代码", "value": "  2024-001 "},
  "field_b": {"name": "项目代码", "value": "2024-001"}
}
```

响应：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `rule_id` | string | 回显传入的规则 ID |
| `result` | bool | `true` 表示比对通过（"基本一致"也算 true，由规则类型决定严格度） |
| `reason` | string | 中文理由 |
| `time` | int | 本次调用耗时，单位毫秒 |
| `cost.prompt_tokens` | int | LLM 输入 token，确定性规则为 0 |
| `cost.completion_tokens` | int | LLM 输出 token，确定性规则为 0 |
| `cost.total_tokens` | int | 总 token，确定性规则为 0 |

响应示例：

```json
{
  "rule_id": "R001",
  "result": true,
  "reason": "两值归一化后完全一致",
  "time": 0,
  "cost": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

#### 五种规则类型 curl 示例

```bash
# R001 exact_match（容忍空白/全半角/大小写）
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"R001",
       "field_a":{"name":"项目代码","value":"  ABC-001 "},
       "field_b":{"name":"项目代码","value":"abc-001"}}'
# result: true, total_tokens: 0

# R002 numeric_equal（"1.2亿" ≡ "12000万元"）
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"R002",
       "field_a":{"name":"总投资","value":"1.2亿"},
       "field_b":{"name":"总投资金额","value":"12000万元"}}'
# result: true, total_tokens: 0

# R003 time_order（lt：批复 < 申报）
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"R003",
       "field_a":{"name":"批复时间","value":"2024年1月10日"},
       "field_b":{"name":"申报时间","value":"2024-02-01"}}'
# result: true, total_tokens: 0

# R004 semantic_similar（调 LLM）
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"R004",
       "field_a":{"name":"建设单位","value":"贵州省XX建设有限公司"},
       "field_b":{"name":"建设单位","value":"XX建设公司"}}'
# result: true, total_tokens > 0

# R005 semantic_exact（调 LLM）
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"R005",
       "field_a":{"name":"建设内容","value":"A; B; C"},
       "field_b":{"name":"建设内容","value":"a, b, c"}}'
```

#### 错误码

| HTTP | 触发条件 | 响应体 |
| --- | --- | --- |
| 200 | 比对成功（无论 true / false） | `CompareResponse` |
| 200 | 数值/日期解析失败 | `result: false, reason: "无法解析..."` |
| 200 | LLM 输出非合法 JSON | `result: false, reason: "LLM 输出非法 JSON"` |
| 422 | 请求参数 schema 校验失败 | FastAPI 默认 |
| 404 | `rule_id` 不存在 | `{"detail":"rule_id 不存在: Rxxx"}` |
| 500 | rules.yaml 写了未知 type / 其他未捕获异常 | `{"detail":"..."}` |
| 502 | LLM 上游错误（鉴权/网络） | `{"detail":"LLM 上游错误: ..."}` |
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
