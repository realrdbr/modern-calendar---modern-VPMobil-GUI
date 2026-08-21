#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "[sync-ntfy] Synchronisiere ntfy-Logins aus VP-Datenbank ..."

while IFS=$'\t' read -r username password topic; do
  if [[ -z "${username}" || -z "${password}" || -z "${topic}" ]]; then
    continue
  fi

  if ! docker compose exec -T -e "NTFY_PASSWORD=${password}" ntfy ntfy user add "${username}" >/dev/null 2>&1; then
    docker compose exec -T -e "NTFY_PASSWORD=${password}" ntfy ntfy user change-pass "${username}" >/dev/null
  fi

  docker compose exec -T ntfy ntfy access "${username}" "${topic}" read-only >/dev/null
  docker compose exec -T ntfy ntfy access "${username}" "${topic}*" read-only >/dev/null
done < <(
  docker compose exec -T vp python - <<'PY'
from admin import store

for user, _ in store().subscribed_users():
    print(f"{user.ntfy_username}\t{user.ntfy_password}\t{user.ntfy_topic}")
PY
)

echo "[sync-ntfy] Fertig."
