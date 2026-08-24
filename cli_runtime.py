"""Hilfen für lokale CLI-Skripte im Docker/MariaDB-Setup."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent


def running_in_container() -> bool:
    return Path("/.dockerenv").exists()


def uses_internal_mariadb() -> bool:
    database_url = os.getenv("APP_DATABASE_URL", "").strip().lower()
    db_host = os.getenv("DB_HOST", "").strip().lower()
    return "@mariadb" in database_url or db_host == "mariadb"


def maybe_delegate_to_vp_container(script_name: str) -> None:
    """Leitet CLI-Aufrufe auf den VP-Container um, wenn lokal nicht passend."""

    if running_in_container():
        return
    if not uses_internal_mariadb():
        return
    if os.getenv("VP_CLI_NO_DELEGATE", "false").lower() in {"1", "true", "yes"}:
        return

    command = ["docker", "compose", "exec", "vp", "python", script_name, *sys.argv[1:]]
    try:
        result = subprocess.run(command, cwd=ROOT, check=False)
    except FileNotFoundError as error:
        raise RuntimeError(
            "Docker Compose wurde nicht gefunden. Nutze für Admin-Befehle entweder "
            "`./start-all.sh docker` oder eine Python-Umgebung mit allen Abhängigkeiten."
        ) from error
    raise SystemExit(result.returncode)
