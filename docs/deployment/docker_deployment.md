# Docker 部署（在线环境）

## 目录约定

容器内统一使用 `/app` 作为工作目录，以下宿主机目录会挂载进容器：

| 宿主机 | 容器内 | 用途 |
| --- | --- | --- |
| `./app` | `/app/app` | 应用代码,更新只需替换文件后 restart |
| `./config` | `/app/config` | 运行配置(含 rules.yaml,修改即生效) |
| `./ui` | `/app/ui` | Web 管理界面静态资源(规则 CRUD + Playground) |
| `./logs` | `/app/logs` | 日志输出 |
| `./data` | `/app/data` | 业务数据(当前阶段未使用,预留) |

## 首次部署

```bash
git clone <repo> wanzi_guizhe
cd wanzi_guizhe

cp .env.example .env
# 编辑 config/config.yaml 填入 llm.api_key（或部署本地 vLLM 时切 backend=vllm）

make build
make up
make logs
```

## 日常更新（代码挂载模式）

```bash
# 1) 拉取或替换 app/ 下的最新代码
git pull            # 或者直接 scp/rsync 覆盖 app/ 目录

# 2) 重启容器，挂载的代码立即生效
make restart
```

> 仅当依赖（`requirements/*.txt`）或 `Dockerfile` 改变时才需要 `make build`，
> 否则 restart 即可。

## 规则热更新（无需 restart）

修改 `config/rules.yaml` 保存后，下一次 `/compare` 请求会基于文件 mtime 自动 reload。
若挂载文件系统不上报 mtime（如部分 NFS / 内网网盘），可手动触发：

```bash
curl -X POST http://localhost:6549/admin/reload-rules
```

## 常用命令

```bash
make logs            # 跟随日志（含 docker stdout 与文件日志）
make sh              # 进入容器交互
make down            # 停止并移除容器
make test            # 在容器内跑 pytest
docker compose ps    # 查看状态
```

## 验证

```bash
# 健康检查
curl http://localhost:6549/health
# {"status":"ok","service":"wanzi_guizhe","version":"0.1.0","rules_loaded":5,
#  "rule_ids":["R001","R002","R003","R004","R005"]}

# Web 管理界面(浏览器直接打开)
# http://localhost:6549/        ← 自动跳到 /ui/
# http://localhost:6549/ui/     ← 规则增删改查 + Playground 试跑

# 一次实际比对(会调 LLM,需要 config.yaml 里填了真实 api_key)
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"R001",
       "field_a":{"value":"  2024-001 "},
       "field_b":{"value":"2024-001"}}'

# 看日志
tail -f logs/wanzi_guizhe.log
```
