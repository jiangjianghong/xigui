#!/usr/bin/env bash
# Bring the application stack up in detached mode.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo ".env not found. Copy .env.example to .env and edit it before starting."
  exit 1
fi

docker compose up -d --build
docker compose ps
