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

Makefile 内置三个变量,共同控制构建/运行行为:

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `CONTAINER_CMD` | `docker` | 容器运行时,改成 `nerdctl` 即整套命令切到 nerdctl |
| `PYTHON_IMAGE` | (空,走 Dockerfile 默认 `python:3.12-slim`) | 基础镜像,国内可换 daocloud/阿里云镜像加速 |
| `APT_MIRROR` | (空,走 `deb.debian.org`) | 镜像内的 apt 源,国内**强烈建议**换成 `mirrors.aliyun.com` 等,否则 `apt-get update` 经常卡 20+ 分钟 |

**最常用的一条龙写法**(nerdctl + 国内基础镜像源 + 国内 apt 源):

```bash
make build \
  CONTAINER_CMD=nerdctl \
  PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim \
  APT_MIRROR=mirrors.aliyun.com

make up  CONTAINER_CMD=nerdctl
make logs CONTAINER_CMD=nerdctl
```

或者用 `export` 把变量持久化到当前 shell,后续命令就不用每次带:

```bash
export CONTAINER_CMD=nerdctl
export PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
export APT_MIRROR=mirrors.aliyun.com

make build
make up
make logs
make sh
make restart
```

`make help` 会显示当前生效的变量值,方便确认:

```bash
$ make help CONTAINER_CMD=nerdctl PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim APT_MIRROR=mirrors.aliyun.com
Available targets (CONTAINER_CMD=nerdctl, PYTHON_IMAGE=..., APT_MIRROR=mirrors.aliyun.com):
  ...
```

scripts/ 下的 `start.sh` / `stop.sh` / `restart.sh` / `package_offline.sh`
也都读 `CONTAINER_CMD`,行为一致。

> ⚠️ **构建 vs 启动**:`make build` 调用 `compose build`,接收 `PYTHON_IMAGE`/`APT_MIRROR` 等 build-arg;
> `make up` 调用 `compose up -d`,**不会**重复构建,只用现成镜像启动。
> nerdctl 的 `compose up` 不认 `--build-arg`,所以必须先 `make build` 再 `make up`(docker 也用同样流程,行为一致)。

## 三、首次部署(目标服务器有网)

> 假设目标机**能拉到容器基础镜像**(直连 Docker Hub 或走 §四 的国内/内部 Harbor 镜像源),但**连不上 GitLab**。
> 所以源码要从开发机打包搬过去,镜像构建在目标机本地完成。
> 如果目标机连容器镜像都拉不到,改走 [`offline_deployment.md`](offline_deployment.md)(连镜像也一起打包)。

### 3.1 在开发机把项目打成压缩包

在**项目根目录的上一级**执行,这样压缩包解压后是一个干净的 `ai_cross_check/` 目录。

```bash
cd /home/yaocy           # 进入项目所在父目录(根据实际路径调整)

# 打包前清掉本地缓存,避免把 .venv / __pycache__ / 历史日志一起带过去
find ai_cross_check -type d -name '__pycache__' -prune -exec rm -rf {} +
find ai_cross_check -type d -name '.pytest_cache' -prune -exec rm -rf {} +
rm -f ai_cross_check/logs/*.log* 2>/dev/null

# 打 tar.gz(带时间戳便于版本追溯),排除无关大文件
TS=$(date +%Y%m%d_%H%M%S)
tar --exclude='ai_cross_check/.git' \
    --exclude='ai_cross_check/.venv' \
    --exclude='ai_cross_check/data/*' \
    --exclude='ai_cross_check/logs/*' \
    --exclude='ai_cross_check/docker/offline' \
    --exclude='*.pyc' \
    -czvf ai_cross_check_${TS}.tar.gz ai_cross_check/

ls -lh ai_cross_check_${TS}.tar.gz
# 期望:几百 KB ~ 几 MB(纯源码 + 配置 + 文档,不含 .git 历史)
```

**说明**:

| 排除项 | 为什么 |
| --- | --- |
| `.git` | 历史动辄几十 MB,部署用不到 |
| `.venv` | 宿主机虚拟环境,容器内会重装 |
| `data/*` `logs/*` | 运行时产物,目标机重新生成 |
| `docker/offline` | 离线镜像包(有的话),本流程不用 |

如果一定要保留 `.git`(便于目标机查 commit 信息),把对应 `--exclude` 行去掉即可。

### 3.2 传输到目标服务器

```bash
# 任选一种,看现场可达性
scp ai_cross_check_${TS}.tar.gz root@target:/tmp/
# 或 rsync(断点续传更稳)
rsync -avzP ai_cross_check_${TS}.tar.gz root@target:/tmp/
# 或经跳板机
scp ai_cross_check_${TS}.tar.gz user@jumphost:/tmp/ && \
  ssh user@jumphost "scp /tmp/ai_cross_check_${TS}.tar.gz root@target:/tmp/"
```

**完全无 ssh 通道时**(只能 U 盘/移动硬盘):

```bash
# 开发机
cp ai_cross_check_${TS}.tar.gz /media/usb/

# 目标机
cp /media/usb/ai_cross_check_*.tar.gz /tmp/
```

建议同时算个 sha256 校验,防止传输过程出错:

```bash
# 开发机
sha256sum ai_cross_check_${TS}.tar.gz
# 目标机校验(对比上一步输出)
sha256sum /tmp/ai_cross_check_${TS}.tar.gz
```

### 3.3 目标机解压到 /opt/

```bash
ssh root@target

# 创建目标目录(/opt 是常见的第三方应用安装路径)
sudo mkdir -p /opt
cd /opt

# 解压(tar 会自动创建 ai_cross_check/ 子目录)
sudo tar -xzvf /tmp/ai_cross_check_*.tar.gz -C /opt/

# 检查解压结果
ls -la /opt/ai_cross_check/
# 期望看到 app/ config/ docker/ ui/ Makefile docker-compose.yml 等

# 调整属主(若用非 root 运行)
sudo chown -R $(whoami):$(whoami) /opt/ai_cross_check/

# 删除压缩包,释放磁盘
rm /tmp/ai_cross_check_*.tar.gz
```

**升级时**(已经部署过、要替换为新版本):

```bash
cd /opt
sudo mv ai_cross_check ai_cross_check_$(date +%Y%m%d_%H%M%S)_bak    # 旧版备份
sudo tar -xzvf /tmp/ai_cross_check_*.tar.gz -C /opt/
# 把旧的 .env / config 拷回来(如果配置已根据现场调过)
cp ai_cross_check_*_bak/.env ai_cross_check/.env
cp ai_cross_check_*_bak/config/config.yaml ai_cross_check/config/config.yaml
cp ai_cross_check_*_bak/config/rules.yaml ai_cross_check/config/rules.yaml
# 重启容器
cd ai_cross_check && make restart CONTAINER_CMD=nerdctl
```

> ⚠️ 升级时**一定要先备份 `config/rules.yaml`**——这是现场维护人员通过 UI 改动过的活配置,
> 直接覆盖会丢失所有人工编辑。压缩包里带的是开发版示例规则。

### 3.4 准备配置

```bash
cd /opt/ai_cross_check

cp .env.example .env
# 编辑 config/config.yaml:
#   - 填好 llm.api_key(或切换到 vllm 选项 B/C 注释段)
#   - 内网调用确认 llm.api_base 可达
```

### 3.5 构建并启动

```bash
# 一条龙写法(最推荐,国内 + nerdctl,apt 源也换国内,否则容易卡 20 分钟):
make build \
  CONTAINER_CMD=nerdctl \
  PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim \
  APT_MIRROR=mirrors.aliyun.com

make up   CONTAINER_CMD=nerdctl
make logs CONTAINER_CMD=nerdctl
```

或先 export 再跑:

```bash
export CONTAINER_CMD=nerdctl
export PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
export APT_MIRROR=mirrors.aliyun.com
make build
make up
make logs
```

若目标机能直连 Docker Hub,`PYTHON_IMAGE` 可以省略;若不在国内或 deb.debian.org 可达,`APT_MIRROR` 也可省。

### 3.6 验证

```bash
curl http://localhost:6549/health
# 期望返回 {"status":"ok","rules_loaded":5,...}

# 跑一次比对(LLM 在线,会消耗少量 token)
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"R001",
       "field_a":{"value":"  2024-001 "},
       "field_b":{"value":"2024-001"}}'

# 浏览器打开 Web 管理界面
# http://<target-ip>:6549/ui/
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

## 四点五、镜像内 apt 源国内加速

`python:3.12-slim` 是 Debian 基础镜像,默认 apt 源是 `deb.debian.org`,
国内访问**经常卡 20+ 分钟**(实测案例:`apt-get install build-essential curl` 跑了 1388 秒)。
Dockerfile 已把 apt 源参数化:

```dockerfile
ARG APT_MIRROR=""
RUN if [ -n "$APT_MIRROR" ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}|g" \
            /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
    fi \
    && apt-get update && apt-get install ...
```

用法:

```bash
make build APT_MIRROR=mirrors.aliyun.com
make build APT_MIRROR=mirrors.aliyun.com CONTAINER_CMD=nerdctl

# 也可与 PYTHON_IMAGE 叠加(实际部署强烈建议两者一起)
make build \
  PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim \
  APT_MIRROR=mirrors.aliyun.com \
  CONTAINER_CMD=nerdctl
```

常用国内 apt 源(只写域名,前缀 `http://` 由 sed 替换时保留):

| 源 | 值 |
| --- | --- |
| 阿里云(推荐) | `mirrors.aliyun.com` |
| 清华 TUNA | `mirrors.tuna.tsinghua.edu.cn` |
| 中科大 USTC | `mirrors.ustc.edu.cn` |
| 网易 | `mirrors.163.com` |

换源后 `apt-get update + install` 通常 30 秒内完成。

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

### 6.0 `unsupported restart policy "unless-stopped"`

部分 nerdctl 版本只识别 `always`,不识别 docker-compose 标准的 `unless-stopped`。
本项目 `docker-compose.yml` 的 `restart:` 字段已经统一改成 `always`(两者在
本服务场景下行为等价:容器异常退出/宿主重启都会自动拉起)。

如果你看到的是其它 compose 文件报这个错,直接把 `unless-stopped` → `always` 即可。

### 6.0.1 `unknown flag: --build-arg`(出现在 `make up` 时)

nerdctl 的 `compose up` 不支持 `--build-arg`(只有 `compose build` 支持)。
本项目 `scripts/start.sh` 已分离职责:`make build` 走 `compose build`(吃 build-arg),
`make up` 走 `compose up -d`(不重复构建)。
若你的 start.sh 还是旧版含 `--build --build-arg ...`,请同步新版。

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
