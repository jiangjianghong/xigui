# nerdctl 部署指南

适用于目标服务器**使用 nerdctl(containerd)而非 Docker** 的场景。
nerdctl 是 containerd 官方 CLI,在常用子命令上与 docker 高度兼容,
本项目的 `Dockerfile` / `docker-compose.yml` / Makefile / scripts 都已统一支持。

## 一、命令对照表

| docker | nerdctl | 说明 |
| --- | --- | --- |
| `docker build` | `nerdctl build` | 完全一致 |
| `docker run` | `nerdctl run` | 完全一致 |
| `docker ps` | `nerdctl ps` | 完全一致 |
| `docker logs -f` | `nerdctl logs -f` | 完全一致 |
| `docker exec` | `nerdctl exec` | 完全一致 |
| `docker save -o` | `nerdctl save -o` | 镜像导出 |
| `docker load -i` | `nerdctl load -i` | 镜像导入 |
| `docker compose up -d` | `nerdctl compose up -d` | compose 子命令通用 |
| `docker compose logs` | `nerdctl compose logs` | 同上 |

> nerdctl 默认 namespace 是 `default`。若与 k8s 共用 containerd,
> 可加 `--namespace k8s.io` 或 `nerdctl namespace list` 切换查看。

## 二、用本项目的 Makefile

Makefile 内置两个变量,共同控制构建/运行行为:

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `CONTAINER_CMD` | `docker` | 容器运行时,改成 `nerdctl` 即整套命令切到 nerdctl |
| `PYTHON_IMAGE` | (空,走 Dockerfile 默认 `python:3.12-slim`) | 基础镜像,国内可换 daocloud/阿里云镜像加速 |

**最常用的一条龙写法**(nerdctl + 国内基础镜像源):

```bash
make build \
  CONTAINER_CMD=nerdctl \
  PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim

make up  CONTAINER_CMD=nerdctl
make logs CONTAINER_CMD=nerdctl
```

或者用 `export` 把变量持久化到当前 shell,后续命令就不用每次带:

```bash
export CONTAINER_CMD=nerdctl
export PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim

make build
make up
make logs
make sh
make restart
```

`make help` 会显示当前生效的变量值,方便确认:

```bash
$ make help CONTAINER_CMD=nerdctl PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
Available targets (CONTAINER_CMD=nerdctl, PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim):
  ...
```

scripts/ 下的 `start.sh` / `stop.sh` / `restart.sh` / `package_offline.sh`
也都读这两个环境变量,行为一致。

## 三、首次部署(目标服务器有网)

### 3.1 把项目文件夹拷过去

```bash
scp -r wanzi_guizhe/ root@target:/opt/
# 或 rsync -avz wanzi_guizhe/ root@target:/opt/wanzi_guizhe/
```

### 3.2 准备配置

```bash
ssh root@target
cd /opt/wanzi_guizhe

cp .env.example .env
# 编辑 config/config.yaml:
#   - 填好 llm.api_key(或切换到 vllm 选项 B/C 注释段)
#   - 内网调用确认 llm.api_base 可达
```

### 3.3 构建并启动

```bash
# 一条龙写法(最推荐,国内 + nerdctl):
make build up \
  CONTAINER_CMD=nerdctl \
  PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim

make logs CONTAINER_CMD=nerdctl
```

或先 export 再跑:

```bash
export CONTAINER_CMD=nerdctl
export PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
make build
make up
make logs
```

若目标机能直连 Docker Hub,`PYTHON_IMAGE` 可以省略,走 Dockerfile 默认值。

### 3.4 验证

```bash
curl http://localhost:6549/health
# 期望返回 {"status":"ok","rules_loaded":5,...}

# 跑一次确定性比对(不消耗 token)
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"R001",
       "field_a":{"value":"  2024-001 "},
       "field_b":{"value":"2024-001"}}'
```

## 四、国内拉 `python:3.12-slim` 加速

`docker.io/library/python:3.12-slim` 在国内**经常超时或被限流**。
本项目的 `docker/Dockerfile` 已经把基础镜像参数化:

```dockerfile
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
```

**Makefile 已封装好**——用 `PYTHON_IMAGE` 变量传入即可:

```bash
# 经 Makefile(推荐,docker 也通用)
make build PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
make build PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim CONTAINER_CMD=nerdctl

# 直接调底层命令(等价)
nerdctl compose build --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
```

常用国内源:

| 源 | 镜像名 |
| --- | --- |
| DaoCloud 公开加速(推荐) | `docker.m.daocloud.io/library/python:3.12-slim` |
| 阿里云 | `registry.cn-hangzhou.aliyuncs.com/library/python:3.12-slim` |
| 腾讯云 | `ccr.ccs.tencentyun.com/library/python:3.12-slim` |

> 你也可以用公司内部 Harbor 仓库:`harbor.company.com/base/python:3.12-slim`。

## 五、日常运维

```bash
export CONTAINER_CMD=nerdctl

make logs            # 跟随日志
make sh              # 进容器
make restart         # 代码挂载更新后重启
make down            # 停止并移除
nerdctl ps           # 查看状态
nerdctl images       # 查看本地镜像
```

## 六、典型问题

### 6.1 `nerdctl compose` 找不到命令

需要 nerdctl ≥ 1.0(自带 compose 子命令)。检查:
```bash
nerdctl --version
nerdctl compose version
```

低版本需升级:`nerdctl-full` 二进制包含 compose 插件;或单独装 nerdctl-compose。

### 6.2 镜像 pull 全程很慢/超时

走 §四 的 `--build-arg PYTHON_IMAGE=<国内源>`,或在 containerd 层配 registry mirror(`/etc/containerd/config.toml` 加 `[plugins."io.containerd.grpc.v1.cri".registry.mirrors."docker.io"]`)。

### 6.3 端口 6549 已被占用

```bash
ss -tlnp | grep 6549
# 改 docker-compose.yml 的 ports 映射,如 "6550:6549"
```

### 6.4 nerdctl run 报 `snapshotter` 错误

```bash
# 通常因为 containerd 默认 snapshotter 与文件系统不兼容
nerdctl --snapshotter overlayfs ps
# 或写进 /etc/nerdctl/nerdctl.toml: snapshotter = "overlayfs"
```

### 6.5 我的服务器既有 docker 也有 nerdctl,会冲突吗?

不会。两者各自管理自己的镜像/容器存储。但**不要互相 import/export**——
docker save 出来的 tar 给 nerdctl load 大多数情况能用,反之亦然,
但跨工具时建议先在同一工具内验证。

## 七、文档关系

- 有网 + 用 docker:[`docker_deployment.md`](docker_deployment.md)
- 有网 + 用 nerdctl:**本文**
- 无网,需要离线包:[`offline_deployment.md`](offline_deployment.md)(`CONTAINER_CMD=nerdctl` 同样支持)
- 整机搬迁:[`migration.md`](migration.md)
