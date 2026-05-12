#!/usr/bin/env bash
# Bring the application stack up in detached mode.
# 环境变量:
#   CONTAINER_CMD=docker|nerdctl    (默认 docker)
#   PYTHON_IMAGE=<基础镜像>          (默认走 Dockerfile 默认值;国内换源时设此项)
set -euo pipefail

cd "$(dirname "$0")/.."

CONTAINER_CMD="${CONTAINER_CMD:-docker}"
BUILD_ARGS=()
if [ -n "${PYTHON_IMAGE:-}" ]; then
  BUILD_ARGS+=(--build-arg "PYTHON_IMAGE=$PYTHON_IMAGE")
fi

if [ ! -f .env ]; then
  echo ".env not found. Copy .env.example to .env and edit it before starting."
  exit 1
fi

$CONTAINER_CMD compose up -d --build "${BUILD_ARGS[@]}"
$CONTAINER_CMD compose ps
