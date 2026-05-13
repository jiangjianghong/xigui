# 皖资规约（wanzi_guizhe）

投资项目文件合规审核系统**第二阶段：规则比对引擎**。

## 这个服务做什么

后端在第一阶段用多模态模型从原始文件(可研批复、初设批复、项目纳入实施方案说明、削绩情况说明、地方财政资金承诺函等)抽取字段,存入数据库后,调用本服务的 `POST /compare` 接口:

- 输入:规则 ID + 两个字段(name + value)
- 处理:按规则配的 `prompt_template` 调 LLM 做比对
- 输出:`{rule_id, result, reason, time, cost}`

**所有规则都走 LLM**,判定边界由 `config/rules.yaml` 的 `prompt_template` 用自然语言描述,**修改后无需重启**——下一次请求自动 reload,也可手动调 `POST /admin/reload-rules`。

LLM 支持两套后端:阿里云百炼(`dashscope`)与本地 vLLM 部署的 Qwen3(`vllm`),切换只改 `config/config.yaml` 里一个字段。

## 目录结构

```
.
├── app/
│   ├── api/             # FastAPI 路由:/compare /health /admin/*
│   ├── core/            # 配置加载、LLM 客户端、日志
│   ├── rules/           # 规则引擎:base + registry + LLMRule
│   └── schemas/         # Pydantic 请求/响应模型
├── config/
│   ├── config.yaml      # 主配置(app + llm + rules_config_path)
│   └── rules.yaml       # 规则活配置(修改即生效)
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

健康检查:

```bash
curl http://localhost:6549/health
```

**Web 管理界面**(规则 CRUD + 试跑)直接打开:

```
http://localhost:6549/        # 自动跳转 /ui/
```

示例:项目代码一致性比对

```bash
curl -X POST http://localhost:6549/compare \
  -H "Content-Type: application/json" \
  -d '{"rule_id":"R001",
       "field_a":{"name":"项目代码","value":"  2024-001 "},
       "field_b":{"name":"项目代码","value":"2024-001"}}'
# {"rule_id":"R001","result":true,"reason":"两个项目代码一致","time":312,
#  "cost":{"prompt_tokens":156,"completion_tokens":12,"total_tokens":168}}
```

更多示例见 [docs/api/README.md](docs/api/README.md)。

## 规则配置

所有规则统一为 LLM 规则,字段:`id` / `name` / `description` / `prompt_template` / `system_prompt` / `require_field_name` 等。
新增规则**不需改代码**:两种方式任选——
- **Web UI**:打开 `http://localhost:6549/ui/`,在"规则管理"页点"新增规则",填好字段保存即可。每个字段下方都有用法说明。
- **直接编辑 yaml**:复制 `config/rules.yaml` 中一条已有规则,改 id 和 prompt,下次请求自动生效;也可手动调 `POST /admin/reload-rules` 立刻刷新。

详见 [docs/api/rules.md](docs/api/rules.md)。

## 文档导航

- [接口文档](docs/api/README.md) · [规则配置详解](docs/api/rules.md)
- [部署总览](docs/deployment/README.md) · [Docker 部署](docs/deployment/docker_deployment.md) · [nerdctl 部署](docs/deployment/nerdctl_deployment.md) · [离线部署](docs/deployment/offline_deployment.md) · [服务器迁移](docs/deployment/migration.md)
- [开发指南](docs/development/README.md)
