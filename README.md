# AI交叉校验（ai_cross_check）

投资项目文件合规审核系统**第二阶段：规则比对引擎**。

## 这个服务做什么

后端在第一阶段用多模态模型从原始文件(可研批复、初设批复、项目纳入实施方案说明、削绩情况说明、地方财政资金承诺函等)抽取字段,存入数据库后,调用本服务的 `POST /compare` 接口:

- 输入:规则 ID + 平铺的字段键 `{rule_id, field_a: {name?, value}, field_b: {...}}`。键名由规则的 `field_defs` 决定,未配则是 `field_a`/`field_b`/...
- 处理:按规则配的 `prompt_template` 调 LLM 做比对
- 输出:`{rule_id, result, reason, time, cost}`

**所有规则都走 LLM**,判定边界由规则的 `prompt_template` 用自然语言描述。规则存在 **MySQL** 里,通过 Web UI 或接口增删改,**改完立即生效**(每次请求实时读库,没有缓存)。`config/rules.yaml` 已降级为**导入导出的载体格式**——直接编辑它不会生效。

规则可归属到**项目**,便于按项目类型分组管理;未分组也是合法状态。

LLM 支持两套后端:阿里云百炼(`dashscope`)与本地 vLLM 部署的 Qwen3(`vllm`),切换只改 `config/config.yaml` 里一个字段。

## 目录结构

```
.
├── app/
│   ├── api/             # FastAPI 路由:/compare /health /admin/*
│   ├── core/            # 配置加载、LLM 客户端、日志、导入导出编排
│   ├── db/              # ORM 模型、引擎会话、建库建表、三个仓储
│   ├── rules/           # 规则引擎:base + registry + LLMRule
│   └── schemas/         # Pydantic 请求/响应模型
├── config/
│   ├── config.yaml      # 主配置(app + llm + mysql + rules_config_path)
│   └── rules.yaml       # 规则种子 / 导入导出模板(直接编辑不生效)
├── docker/              # Dockerfile + entrypoint
├── docs/                # 接口/部署/开发文档 + schema.sql
├── scripts/             # start/stop/restart + import_rules.py
├── tests/               # unit + integration
├── docker-compose.yml
└── Makefile
```

## 快速开始

```bash
cp .env.example .env
cp config/config.example.yaml config/config.yaml  # 首次部署；升级时保留已有配置
# 编辑 config/config.yaml：填入真实 llm.api_key，以及 mysql: 节的连接信息
uv sync
uv run python scripts/set_admin_password.py  # 初始化管理员；没有默认密码

make build && make up && make logs
```

### 数据库

规则、项目、比对历史都存在 MySQL。启动时会**自动建库建表**(`CREATE DATABASE IF NOT EXISTS` + 建表),因此:

- 应用账号需要 `CREATE` 权限;若不便授权,把 `docs/schema.sql` 交给 DBA 手工执行即可
- **不做字段自动迁移**——后续表结构变更需人工执行 `ALTER`,并同步更新 `docs/schema.sql` 与 `app/db/models.py`

首次启动且 `rules` 表为空时,会自动从 `config/rules.yaml` 导入一次(日志里能看到"首次启动,从 ... 导入 N 条规则")。限定"表为空"才导,是为了避免删光规则后重启又全部长回来。也可手工重跑:

```bash
uv run python scripts/import_rules.py                    # 导入 config/rules.yaml
uv run python scripts/import_rules.py path/to/other.yaml
```

连接信息支持环境变量覆盖(`MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_DATABASE` / `MYSQL_USERNAME` / `MYSQL_PASSWORD`),生产不想把密码写进文件时用。

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
  -H "Authorization: Bearer $XIGUI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"rule_id":"R001",
       "field_a":{"name":"项目代码","value":"  2024-001 "},
       "field_b":{"name":"项目代码","value":"2024-001"}}'
# {"rule_id":"R001","result":true,"reason":"两个项目代码一致","time":312,
#  "cost":{"prompt_tokens":156,"completion_tokens":12,"total_tokens":168},
#  "field_a":{"name":"项目代码","value":"  2024-001 "},
#  "field_b":{"name":"项目代码","value":"2024-001"}}
```

更多示例见 [docs/api/README.md](docs/api/README.md)。

## 鉴权

管理员先登录后台；在「接口访问控制」为程序创建 API Key 或配置 IP 白名单。登录会话、有效 Key 和命中白名单的 IP 权限相同，均可调用、查询和管理全部接口及访问文档。所有调用方共享数据。

账号使用 Argon2id 哈希，默认固定 30 分钟会话。首次初始化或重置密码执行 `uv run python scripts/set_admin_password.py`。部署使用单 worker，手动 Uvicorn 启动加 `--no-proxy-headers`。

完整权限、代理与配置说明见 [鉴权文档](docs/authentication.md)。

## 规则配置

所有规则统一为 LLM 规则,字段:`id` / `name` / `description` / `prompt_template` / `system_prompt` / `require_field_name` / `field_defs` / `project` 等。规则真源是 **MySQL**,新增规则**不需改代码**:

- **Web UI**(推荐):打开 `http://localhost:6549/ui/`,在"规则管理"页点"新增规则",填好字段保存即刻生效。每个字段下方都有用法说明。
- **导入 yaml**:UI 右上角「导入」上传 yaml,按 `id` **合并 upsert**——库里有则覆盖、没有则新增,库里有而文件没有的原封不动。因此可以只导入几条规则的片段文件。任何一条校验不过则**整体回滚**,不会留下导入一半的状态。
- **导出 yaml**:「导出」按当前项目筛选导出,可用于备份或跨环境迁移。导出→导入可完整还原(含项目归属)。

> 直接编辑 `config/rules.yaml` **不再自动生效**,必须走导入。`POST /admin/reload-rules` 已降级为空操作(仅返回当前规则条数),端点保留是为了不让既有运维脚本突然 404。

### 项目

规则可归属到 0 或 1 个项目。项目支持增删改查、规则批量移入移出,查询规则时返回所属项目。**删项目只解除归属,绝不级联删规则**——其下规则变为"未分组"。

详见 [api.md](api.md)。

## 文档导航

- [接口文档](docs/api/README.md) · [规则配置详解](docs/api/rules.md)
- [部署总览](docs/deployment/README.md) · [Docker 部署](docs/deployment/docker_deployment.md) · [nerdctl 部署](docs/deployment/nerdctl_deployment.md) · [离线部署](docs/deployment/offline_deployment.md) · [服务器迁移](docs/deployment/migration.md)
- [开发指南](docs/development/README.md)
