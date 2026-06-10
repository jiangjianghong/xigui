.PHONY: help build up down restart logs sh test lint package-offline dev sync

# 容器运行时命令。默认 docker,可在调用时覆盖:
#   make up                            # 用 docker
#   make up CONTAINER_CMD=nerdctl      # 用 nerdctl(containerd)
# 也可通过环境变量 export CONTAINER_CMD=nerdctl 持久化。
CONTAINER_CMD ?= docker
export CONTAINER_CMD

# 构建时的基础镜像。空则用 Dockerfile 默认值 (python:3.12-slim)。
# 国内加速例:
#   make build PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
PYTHON_IMAGE ?=

# 构建时镜像内的 apt 源。空则走 deb.debian.org(国内常超时)。
# 国内加速例:
#   make build APT_MIRROR=mirrors.aliyun.com
#   make build APT_MIRROR=mirrors.tuna.tsinghua.edu.cn
APT_MIRROR ?=

BUILD_ARGS = $(if $(PYTHON_IMAGE),--build-arg PYTHON_IMAGE=$(PYTHON_IMAGE)) \
             $(if $(APT_MIRROR),--build-arg APT_MIRROR=$(APT_MIRROR))

help:
	@echo "Available targets (CONTAINER_CMD=$(CONTAINER_CMD), PYTHON_IMAGE=$(PYTHON_IMAGE), APT_MIRROR=$(APT_MIRROR)):"
	@echo "  dev              Install all dependencies (including dev) via uv"
	@echo "  sync             Install production dependencies via uv"
	@echo "  build            Build the container image"
	@echo "  up               Start the stack (detached)"
	@echo "  down             Stop and remove containers"
	@echo "  restart          Restart the app container (picks up code-mount changes)"
	@echo "  logs             Tail application logs"
	@echo "  sh               Open a shell inside the running container"
	@echo "  test             Run the test suite inside the container"
	@echo "  lint             Run ruff/mypy"
	@echo "  package-offline  Build an offline deployment bundle"
	@echo ""
	@echo "Override runtime: make <target> CONTAINER_CMD=nerdctl"
	@echo "Override base image: make build PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim"
	@echo "Override apt mirror: make build APT_MIRROR=mirrors.aliyun.com"

dev:
	uv sync --dev

sync:
	uv sync

build:
	$(CONTAINER_CMD) compose build $(BUILD_ARGS)

up:
	bash scripts/start.sh

down:
	bash scripts/stop.sh

restart:
	bash scripts/restart.sh

logs:
	$(CONTAINER_CMD) compose logs -f app

sh:
	$(CONTAINER_CMD) compose exec app /bin/bash

test:
	$(CONTAINER_CMD) compose exec app pytest

lint:
	$(CONTAINER_CMD) compose exec app ruff check .
	$(CONTAINER_CMD) compose exec app mypy app

package-offline:
	bash scripts/package_offline.sh
