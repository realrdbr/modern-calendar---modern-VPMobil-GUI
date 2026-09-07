#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-docker}"
CHILD_PID=""
SHUTTING_DOWN=0
STARTUP_COMPLETE=0

cd "$ROOT_DIR"

shutdown() {
  local exit_code=$?
  if (( SHUTTING_DOWN )); then
    return
  fi
  SHUTTING_DOWN=1
  trap - INT TERM EXIT
  echo
  if (( exit_code != 0 && STARTUP_COMPLETE == 0 )) && [[ "$MODE" == docker || "$MODE" == docker-proxy ]]; then
    echo "[start-all] Start fehlgeschlagen (Exit $exit_code). Container bleiben für die Diagnose erhalten."
    docker compose ps -a || true
    docker compose logs --no-color --tail=100 ntfy ntfy-provisioner vp || true
    local ntfy_id
    ntfy_id="$(docker compose ps -a -q ntfy 2>/dev/null)" || ntfy_id=""
    if [[ -n "$ntfy_id" ]]; then
      echo "[start-all] Letzte ntfy-Healthchecks:"
      docker inspect --format '{{if .State.Health}}{{range .State.Health.Log}}{{.End}} exit={{.ExitCode}} {{.Output}}{{end}}{{else}}Kein Healthcheck vorhanden.{{end}}' "$ntfy_id" || true
    fi
    echo "[start-all] Nach Behebung erneut starten. Manuell beenden: docker compose down"
    exit "$exit_code"
  fi
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
    STARTUP_COMPLETE=1
    docker compose logs -f
    ;;
  docker-proxy)
    echo "[start-all] Starte Stack inkl. Caddy-Proxy-Profil..."
    docker compose --profile proxy up -d --build
    bash ./sync-ntfy-users.sh
    STARTUP_COMPLETE=1
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
