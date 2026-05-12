# 离线部署指南

适用于**目标服务器无外网**的场景。整体流程是：在一台**有网络**的机器上打包好镜像 + 依赖 + 代码，然后将整个压缩包搬到目标机器解压、加载、启动。

## 一、在有网络的机器上打包

```bash
# 在项目根目录
make package-offline
# 或 bash scripts/package_offline.sh
```

脚本会：
1. `docker compose build` 构建出 `wanzi_guizhe:latest`
2. `docker save` 将镜像导出到 `docker/offline/image.tar`
3. 在 `python:3.12-slim` 容器中 `pip download` 把所有依赖 wheel 落到 `docker/offline/wheels/`（便于在容器外重建/调试时也可离线 pip 安装）
4. 将下面这些一并打成 `wanzi_guizhe_offline_<timestamp>.tar.gz`：
   ```
   app/  config/  docker/  docker-compose.yml  requirements/
   scripts/  .env.example  .dockerignore  .gitignore
   docker/offline/image.tar
   docker/offline/wheels/
   ```

产物示例：`wanzi_guizhe_offline_20260512_143000.tar.gz`

## 二、传输到目标服务器

任意手段：`scp`、U 盘、内网文件服务器等。

```bash
scp wanzi_guizhe_offline_*.tar.gz user@target-host:/opt/
```

## 三、目标服务器上解压并加载

目标服务器**至少需要预先安装** Docker + Docker Compose v2。
若 Docker 也无法联网安装，请提前用 deb/rpm 离线包部署 Docker。

```bash
cd /opt
mkdir wanzi_guizhe && tar -xzf wanzi_guizhe_offline_*.tar.gz -C wanzi_guizhe
cd wanzi_guizhe

# 1) 加载离线 Docker 镜像
docker load -i docker/offline/image.tar
docker images | grep wanzi_guizhe

# 2) 准备运行配置
cp .env.example .env
# 编辑 config/config.yaml 填入 llm.api_key（如使用本地 vLLM 则改 backend / api_base）

# 3) 启动（此时 docker-compose 不会再去拉镜像，因为已加载到本地）
docker compose up -d
docker compose ps
docker compose logs -f app
```

## 四、之后的日常更新（无网环境）

代码挂载下，日常更新只是替换 `app/` 或 `config/rules.yaml` 等文件：

```bash
# 1) 把新版 app/ 通过 U 盘 / scp 拷过来，覆盖目标服务器对应目录
# 2) 重启或仅热加载规则：
docker compose restart app
# 或：仅更新规则（无需 restart）
curl -X POST http://localhost:6549/admin/reload-rules
```

只有依赖或 Dockerfile 改变时才需要重新打包镜像并 `docker load`。

## 五、常见问题

- **镜像加载后版本不对** —— `docker load` 只会按打包时的 tag 还原，如需替换请先 `docker rmi wanzi_guizhe:latest` 再重新 load。
- **挂载目录权限问题** —— 容器内默认以 `root` 运行，如目标系统启用了 user namespace，可在 `docker-compose.yml` 中加 `user: "1000:1000"`。
- **wheels 目录有什么用** —— 当需要在宿主机上（而非容器内）直接 pip 安装时使用：`pip install --no-index --find-links docker/offline/wheels -r requirements/prod.txt`
- **LLM 调不通** —— 检查 `config/config.yaml` 的 `llm.api_base` 在目标服务器是否可达；本地 vLLM 部署时把 `backend` 改成 `vllm` 并指向本地服务地址。
