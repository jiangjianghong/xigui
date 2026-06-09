#!/usr/bin/env bash
# 构建离线部署包：容器镜像 tar + Python wheels
# 在有外网的机器上执行本脚本，再把生成的 bundle 拷贝到目标主机
# 容器运行时:默认 docker,可设 CONTAINER_CMD=nerdctl 切换
set -euo pipefail

cd "$(dirname "$0")/.."

CONTAINER_CMD="${CONTAINER_CMD:-docker}"
OUT_DIR="docker/offline"
IMAGE_TAG="ai_cross_check:latest"
BUNDLE_NAME="ai_cross_check_offline_$(date +%Y%m%d_%H%M%S).tar.gz"
# 可通过 PYTHON_IMAGE 切换基础镜像源(国内加速)
PYTHON_IMAGE="${PYTHON_IMAGE:-python:3.12-slim}"

mkdir -p "$OUT_DIR/wheels"

echo "[1/4] Building image $IMAGE_TAG (base=$PYTHON_IMAGE)"
$CONTAINER_CMD compose build --build-arg PYTHON_IMAGE="$PYTHON_IMAGE"

echo "[2/4] Saving image to tarball"
$CONTAINER_CMD save -o "$OUT_DIR/image.tar" "$IMAGE_TAG"

echo "[3/4] Downloading Python wheels for offline pip install"
$CONTAINER_CMD run --rm -v "$(pwd)/requirements:/req" -v "$(pwd)/$OUT_DIR/wheels:/wheels" \
  "$PYTHON_IMAGE" \
  pip download -r /req/prod.txt -d /wheels

echo "[4/4] Packaging bundle: $BUNDLE_NAME"
tar -czf "$BUNDLE_NAME" \
  app config docker docker-compose.yml requirements scripts \
  .env.example .dockerignore .gitignore \
  "$OUT_DIR/image.tar" "$OUT_DIR/wheels"

echo "Done. Ship $BUNDLE_NAME to the target server."
