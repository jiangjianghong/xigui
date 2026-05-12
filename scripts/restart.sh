#!/usr/bin/env bash
# Restart the app container so a code-mount update takes effect without
# rebuilding the image.
set -euo pipefail

cd "$(dirname "$0")/.."
docker compose restart app
docker compose ps
