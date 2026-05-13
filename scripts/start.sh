#!/usr/bin/env bash
# Bring the application stack up in detached mode.
# 注意:本脚本只负责"启动",不在 up 阶段重复构建。
# 镜像构建通过 `make build`(等价 `$CONTAINER_CMD compose build --build-arg ...`)单独完成,
# 那里才是接收 PYTHON_IMAGE / APT_MIRROR 等 build-arg 的地方。
# 这样写的原因:nerdctl 的 `compose up` 不识别 `--build-arg`,只有 `compose build` 识别。
set -euo pipefail

cd "$(dirname "$0")/.."

CONTAINER_CMD="${CONTAINER_CMD:-docker}"

if [ ! -f .env ]; then
  echo ".env not found. Copy .env.example to .env and edit it before starting."
  exit 1
fi

$CONTAINER_CMD compose up -d
$CONTAINER_CMD compose ps
