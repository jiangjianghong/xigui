# 部署文档总览

按目标环境选择对应文档：

| 场景 | 文档 |
| --- | --- |
| 有网络的服务器，首次部署或日常更新 | [docker_deployment.md](docker_deployment.md) |
| 目标服务器无外网，需要打包离线包 | [offline_deployment.md](offline_deployment.md) |
| 整机搬迁/换服务器，迁移已运行的服务 | [migration.md](migration.md) |

## 部署前置条件

- 目标服务器已安装 Docker（>= 20.10）、Docker Compose v2
- 已经准备好 `.env` 与 `config/config.yaml`（含真实的 `llm.api_key`）
- 已规划好挂载目录（代码、配置、日志、数据）的宿主机路径与权限
- 如使用本地 vLLM 后端，需确保宿主机能访问 vLLM 服务的 base_url
