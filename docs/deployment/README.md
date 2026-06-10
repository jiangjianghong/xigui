# 部署文档总览

按目标环境选择对应文档：

| 场景 | 文档 |
| --- | --- |
| 有网络的服务器,首次部署或日常更新(docker) | [docker_deployment.md](docker_deployment.md) |
| 目标服务器使用 nerdctl/containerd 而非 docker | [nerdctl_deployment.md](nerdctl_deployment.md) |
| 整机搬迁/换服务器,迁移已运行的服务 | [migration.md](migration.md) |

> 项目的 Makefile 和 scripts 都已支持 `CONTAINER_CMD` 变量,docker 与 nerdctl
> 共用一套命令(`make up CONTAINER_CMD=nerdctl`)。Dockerfile 的基础镜像也已
> 参数化,可通过 `--build-arg PYTHON_IMAGE=<国内源>` 换源加速。

## 部署前置条件

- 目标服务器已安装 Docker(>= 20.10)+ Docker Compose v2,**或** nerdctl(>= 1.0)+ containerd
- 已经准备好 `.env` 与 `config/config.yaml`(含真实的 `llm.api_key` 或 vLLM 地址)
- 已规划好挂载目录(代码、配置、日志、数据)的宿主机路径与权限
- 如使用本地 vLLM 后端,需确保宿主机能访问 vLLM 服务的 base_url
