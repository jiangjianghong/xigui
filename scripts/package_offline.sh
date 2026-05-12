#!/usr/bin/env bash
# 构建离线部署包：Docker 镜像 tar + Python wheels
# 在有外网的机器上执行本脚本，再把生成的 bundle 拷贝到目标主机
set -euo pipefail

cd "$(dirname "$0")/.."

OUT_DIR="docker/offline"
IMAGE_TAG="wanzi_guizhe:latest"
BUNDLE_NAME="wanzi_guizhe_offline_$(date +%Y%m%d_%H%M%S).tar.gz"

mkdir -p "$OUT_DIR/wheels"

echo "[1/4] Building image $IMAGE_TAG"
docker compose build

echo "[2/4] Saving image to tarball"
docker save -o "$OUT_DIR/image.tar" "$IMAGE_TAG"

echo "[3/4] Downloading Python wheels for offline pip install"
docker run --rm -v "$(pwd)/requirements:/req" -v "$(pwd)/$OUT_DIR/wheels:/wheels" \
  python:3.12-slim \
  pip download -r /req/prod.txt -d /wheels

echo "[4/4] Packaging bundle: $BUNDLE_NAME"
tar -czf "$BUNDLE_NAME" \
  app config docker docker-compose.yml requirements scripts \
  .env.example .dockerignore .gitignore \
  "$OUT_DIR/image.tar" "$OUT_DIR/wheels"

echo "Done. Ship $BUNDLE_NAME to the target server."
