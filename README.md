# 皖资规约（wanzi_guizhe）

投资项目文件合规审核系统**第二阶段：规则比对引擎**。

## 这个服务做什么

后端在第一阶段用多模态模型从原始文件（可研批复、初设批复、项目纳入实施方案说明、削绩情况说明、地方财政资金承诺函等）抽取字段，存入数据库后，调用本服务的 `POST /compare` 接口：

- 输入：规则 ID + 两个字段（name + value）
- 处理：按规则做比对，确定性规则走 Python 直算（不调 LLM、cost 全 0），语义规则调大模型
- 输出：`{rule_id, result, reason, time, cost}`

规则在 `config/rules.yaml` 里活配置，**修改后无需重启**——下一次请求自动 reload，也可手动调 `POST /admin/reload-rules`。

LLM 支持两套后端：阿里云百炼（`dashscope`）与本地 vLLM 部署的 Qwen3（`vllm`），切换只改 `config/config.yaml` 里一个字段。

## 目录结构

```
.
├── app/
│   ├── api/             # FastAPI 路由：/compare /health /admin/*
│   ├── core/            # 配置加载、LLM 客户端、日志
│   ├── rules/           # 规则引擎：base + registry + 5 种类型实现
│   ├── schemas/         # Pydantic 请求/响应模型
│   └── utils/           # normalize_text / extract_number / parse_date
├── config/
│   ├── config.yaml      # 主配置（app + llm + rules_config_path）
│   └── rules.yaml       # 规则活配置（修改即生效）
├── docker/              # Dockerfile + entrypoint
├── docs/                # 接口/部署/开发文档
├── requirements/        # base/dev/prod 三档依赖
├── scripts/             # start/stop/restart/package_offline
├── tests/               # unit + integration
├── docker-compose.yml
└── Makefile
```

## 快速开始

```bash
cp .env.example .env
# 编辑 config/config.yaml 填入真实 llm.api_key

make build && make up && make logs
```

健康检查：

```bash
curl http://localhost:6549/health
```

示例：项目代码严格相等（容忍空白/大小写）

```bash
curl -X POST http://localhost:6549/compare \
  -H "Content-Type: application/json" \
  -d '{"rule_id":"R001",
       "field_a":{"name":"项目代码","value":"  2024-001 "},
       "field_b":{"name":"项目代码","value":"2024-001"}}'
# {"rule_id":"R001","result":true,"reason":"两值归一化后完全一致","time":0,
#  "cost":{"prompt_tokens":0,"completion_tokens":0,"total_tokens":0}}
```

更多示例见 [docs/api/README.md](docs/api/README.md)。

## 已支持的规则类型

| type | 是否调 LLM | 用途 |
|---|---|---|
| `exact_match` | ❌ | 严格相等（带归一化） |
| `numeric_equal` | ❌ | 数值相等（带单位换算，如 "1.2亿" ≡ "12000万元"） |
| `time_order` | ❌ | 日期先后比较（lt/le/gt/ge/eq） |
| `semantic_similar` | ✅ | 语义"基本一致"（容忍简称/全称、行政前缀差异等） |
| `semantic_exact` | ✅ | 语义"严格一致"（允许排序/格式差异，不允许内容增减） |

新增规则的方法见 [docs/development/README.md](docs/development/README.md#如何添加一条规则)。

## 文档导航

- [接口文档](docs/api/README.md)
- [部署总览](docs/deployment/README.md) · [Docker 部署](docs/deployment/docker_deployment.md) · [离线部署](docs/deployment/offline_deployment.md) · [服务器迁移](docs/deployment/migration.md)
- [开发指南](docs/development/README.md)
