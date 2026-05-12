#!/usr/bin/env bash
# Stop and remove the application containers (volumes are preserved).
set -euo pipefail

cd "$(dirname "$0")/.."
docker compose down
