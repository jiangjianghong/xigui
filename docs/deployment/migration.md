# 服务器迁移指南

将一台已运行 `wanzi_guizhe` 的服务器搬迁/复制到另一台服务器。
适用场景：换硬件、换机房、灾备恢复等。

## 一、迁移内容清单

需要从原服务器带走的（按重要性）：

| 项目 | 路径 | 说明 |
| --- | --- | --- |
| 应用代码 | `app/` | 当前运行的源码 |
| 运行配置 | `config/config.yaml` | 含 LLM api_key（敏感） |
| 规则配置 | `config/rules.yaml` | 生产实际生效的规则 |
| 环境变量 | `.env` | 含敏感信息，加密传输 |
| 历史日志（可选） | `logs/` | 排查问题用 |
| Docker 镜像 | `docker save` 导出 | 见下文 |
| Compose / Dockerfile | `docker-compose.yml`, `docker/` | 与镜像同源 |

## 二、迁移步骤

### 2.1 在原服务器上打包

```bash
cd /path/to/wanzi_guizhe

# 1) 暂停服务保证数据一致性
docker compose stop app

# 2) 导出当前运行的镜像
docker save -o /tmp/wanzi_guizhe_image.tar wanzi_guizhe:latest

# 3) 打包整个项目目录（含 .env / logs）
tar -czf /tmp/wanzi_guizhe_migrate_$(date +%F).tar.gz \
  --exclude='.git' \
  --exclude='docker/offline' \
  -C /path/to .
```

### 2.2 传输

```bash
scp /tmp/wanzi_guizhe_migrate_*.tar.gz /tmp/wanzi_guizhe_image.tar user@new-host:/opt/
```

### 2.3 在新服务器上还原

> 前置：新服务器已装好 Docker 与 Compose v2

```bash
cd /opt
mkdir wanzi_guizhe && tar -xzf wanzi_guizhe_migrate_*.tar.gz -C wanzi_guizhe
cd wanzi_guizhe

# 加载镜像
docker load -i /opt/wanzi_guizhe_image.tar

# 启动
docker compose up -d
docker compose logs -f app
```

### 2.4 验证

```bash
docker compose ps                       # 容器是否 healthy
curl http://localhost:6549/health       # 服务自报状态 + 规则数

# 跑一次确定性规则比对（不消耗 token）
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"R001","field_a":{"name":"项目代码","value":"  X "},"field_b":{"name":"项目代码","value":"x"}}'
```

### 2.5 切流量 & 关停旧机

确认新机稳定运行一段时间后，DNS / 负载均衡切到新机，旧机执行 `docker compose down` 并保留备份至少一个迭代周期。

## 三、目标服务器无网络时

完全等价于离线部署流程，但镜像/代码/数据都来自原服务器而非构建机。具体见 [offline_deployment.md](offline_deployment.md)。

## 四、迁移检查清单

- [ ] `.env` 中的密钥已同步且权限 600
- [ ] `config/config.yaml` 中的 `llm.api_base` / `api_key` / `backend` 在新机可达
- [ ] `config/rules.yaml` 与原服务器一致（除非有意调整）
- [ ] 端口 6549 未被占用，防火墙已放行
- [ ] 时区与系统时间正确（影响日志）
- [ ] 新机磁盘空间预留足够（镜像 + 日志增长）
