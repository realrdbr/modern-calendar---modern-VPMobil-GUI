#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# A single exec propagates database/provisioning failures to start-all.
# Credentials remain inside VP and go through the signed internal provisioner.
docker compose exec -T vp python -m ntfy.sync_users
