#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-docker}"
CHILD_PID=""
SHUTTING_DOWN=0

cd "$ROOT_DIR"

shutdown() {
  local exit_code=$?
  if (( SHUTTING_DOWN )); then
    return
  fi
  SHUTTING_DOWN=1
  trap - INT TERM EXIT
  echo
  echo "[start-all] Fahre alle Dienste herunter..."

  if [[ -n "$CHILD_PID" ]] && kill -0 "$CHILD_PID" 2>/dev/null; then
    kill -TERM -- "-$CHILD_PID" 2>/dev/null || kill -TERM "$CHILD_PID" 2>/dev/null || true
    wait "$CHILD_PID" 2>/dev/null || true
  fi

  case "$MODE" in
    docker)
      docker compose down || true
      ;;
    docker-proxy)
      docker compose --profile proxy down || true
      ;;
  esac

  echo "[start-all] Alle Dienste wurden beendet."
  exit "$exit_code"
}

trap shutdown INT TERM EXIT

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
    setsid npm run dev:all &
    CHILD_PID=$!
    wait "$CHILD_PID"
    ;;
  *)
    echo "Nutzung: ./start-all.sh [docker|docker-proxy|local]"
    exit 1
    ;;
esac
