#!/usr/bin/env bash
# Restart the app container so a code-mount update takes effect without
# rebuilding the image.
set -euo pipefail

cd "$(dirname "$0")/.."

CONTAINER_CMD="${CONTAINER_CMD:-docker}"
$CONTAINER_CMD compose restart app
$CONTAINER_CMD compose ps
