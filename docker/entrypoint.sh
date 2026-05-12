#!/usr/bin/env bash
set -euo pipefail

# Place runtime preparation here (wait for DB, run migrations, etc.).
# Keep it idempotent so restarting the container is always safe.

exec "$@"
