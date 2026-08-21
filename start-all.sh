#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-docker}"

cd "$ROOT_DIR"

case "$MODE" in
  docker)
    echo "[start-all] Starte Stack ohne Proxy (localhost-Testing)..."
    docker compose up -d --build
    bash ./sync-ntfy-users.sh
    docker compose logs -f
    ;;
  docker-proxy)
    echo "[start-all] Starte Stack inkl. Caddy-Proxy-Profil..."
    docker compose --profile proxy up -d --build
    bash ./sync-ntfy-users.sh
    docker compose --profile proxy logs -f
    ;;
  local)
    echo "[start-all] Starte lokal (Python VP + Node Kalender)..."
    npm run dev:all
    ;;
  *)
    echo "Nutzung: ./start-all.sh [docker|docker-proxy|local]"
    exit 1
    ;;
esac
