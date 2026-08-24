#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "[sync-ntfy] Synchronisiere ntfy-Logins aus VP-Datenbank ..."

while IFS=$'\t' read -r username password topic; do
  if [[ -z "${username}" || -z "${password}" || -z "${topic}" ]]; then
    continue
  fi

  echo "[sync-ntfy] Aktualisiere ${username} ..."

  if ! docker compose exec -T -e "NTFY_PASSWORD=${password}" ntfy ntfy user add "${username}" >/dev/null 2>&1; then
    docker compose exec -T -e "NTFY_PASSWORD=${password}" ntfy ntfy user change-pass "${username}" >/dev/null
  fi

  # Alte Installationen legten teils eine konkurrierende read-only-Wildcard an.
  # Sie würde trotz exakter rw-Regel weiterhin jeden Publish mit 403 blockieren.
  docker compose exec -T ntfy ntfy access --reset "${username}" "${topic}*" >/dev/null 2>&1 || true
  docker compose exec -T ntfy ntfy access "${username}" "${topic}" rw >/dev/null
done < <(
  docker compose exec -T vp python - <<'PY'
from admin import store

for recipient in store().notification_recipients():
    user = recipient.user
    print(f"{user.ntfy_username}\t{user.ntfy_password}\t{user.ntfy_topic}")
PY
)

while IFS=$'\t' read -r publisher_username publisher_password; do
  if [[ -z "${publisher_username}" || -z "${publisher_password}" ]]; then
    continue
  fi
  echo "[sync-ntfy] Aktualisiere System-Publisher ${publisher_username} ..."
  if ! docker compose exec -T -e "NTFY_PASSWORD=${publisher_password}" ntfy ntfy user add "${publisher_username}" >/dev/null 2>&1; then
    docker compose exec -T -e "NTFY_PASSWORD=${publisher_password}" ntfy ntfy user change-pass "${publisher_username}" >/dev/null
  fi
  docker compose exec -T ntfy ntfy access "${publisher_username}" "*" wo >/dev/null
done < <(
  docker compose exec -T vp python - <<'PY'
from ntfy.service import resolve_ntfy_publisher_auth

username, password = resolve_ntfy_publisher_auth()
print(f"{username}\t{password}")
PY
)

echo "[sync-ntfy] Fertig."
