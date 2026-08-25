"""Startet und verwaltet die private ntfy-Docker-Instanz."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from urllib.parse import urlparse

import requests


def resolve_ntfy_internal_url() -> str:
    requested = os.getenv("NTFY_INTERNAL_URL", os.getenv("NTFY_PUBLIC_URL", "http://127.0.0.1:8090")).rstrip("/")
    parsed = urlparse(requested)
    in_container = Path("/.dockerenv").exists()
    if in_container and parsed.hostname in {"127.0.0.1", "localhost"}:
        return "http://ntfy"
    if not in_container and parsed.hostname == "ntfy":
        # `http://ntfy` ist nur im Compose-Netz auflösbar. Bei lokal gestarteter
        # VP-App ist derselbe Container über den gebundenen Loopback-Port erreichbar.
        return f"http://127.0.0.1:{int(os.getenv('NTFY_PORT', '8090'))}"
    return requested


def resolve_ntfy_publisher_auth() -> tuple[str, str]:
    username = os.getenv("NTFY_SERVER_USERNAME", "vpmobil_server").strip() or "vpmobil_server"
    password = os.getenv("NTFY_SERVER_PASSWORD", "").strip()
    if password:
        return username, password
    seed = os.getenv("APP_ENCRYPTION_KEY", "").strip()
    if not seed:
        return username, "vpmobil-server-local-only"
    derived = hashlib.sha256(seed.encode("utf-8")).digest() + hashlib.sha256((seed + ":ntfy").encode("utf-8")).digest()[:16]
    return username, base64.urlsafe_b64encode(derived).decode("ascii").rstrip("=")


class NtfyService:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        compose_name = os.getenv("NTFY_COMPOSE_FILE", "ntfy-compose.yml")
        self.compose_file = project_root / compose_name
        if not self.compose_file.exists():
            fallback = project_root / "docker-compose.yml"
            if fallback.exists():
                self.compose_file = fallback
        self.base_url = os.getenv("NTFY_PUBLIC_URL", "http://127.0.0.1:8090").rstrip("/")
        self.internal_url = resolve_ntfy_internal_url()
        self.provisioner_url = os.getenv("NTFY_PROVISIONER_URL", "").rstrip("/")
        self.provisioner_secret = os.getenv("NTFY_PROVISIONER_SECRET", os.getenv("APP_ENCRYPTION_KEY", "")).encode("utf-8")

    def _provision(self, operation: str, **values: str) -> None:
        if not self.provisioner_url:
            raise RuntimeError("Der interne ntfy-Provisionierungsdienst ist nicht konfiguriert.")
        if not self.provisioner_secret:
            raise RuntimeError("Das ntfy-Provisionierungs-Secret fehlt.")
        payload = {**values, "timestamp": int(time.time())}
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        signature = hmac.new(self.provisioner_secret, body, hashlib.sha256).hexdigest()
        response = requests.post(
            f"{self.provisioner_url}/{operation}", data=body,
            headers={"Content-Type": "application/json", "X-Provisioner-Signature": signature},
            timeout=35,
        )
        if not response.ok:
            raise RuntimeError(f"ntfy-Provisionierung wurde abgelehnt (HTTP {response.status_code}).")

    def _compose(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        command = ["docker", "compose", "-f", str(self.compose_file), *args]
        child_env = os.environ.copy()
        if env:
            child_env.update(env)
        return subprocess.run(
            command, cwd=self.project_root, env=child_env, text=True,
            capture_output=True, check=False, timeout=45,
        )

    def ensure_running(self) -> None:
        if os.getenv("NTFY_AUTOSTART", "true").lower() not in {"1", "true", "yes"}:
            return
        result = self._compose("up", "-d", "ntfy")
        if result.returncode != 0:
            raise RuntimeError(
                "ntfy konnte nicht per Docker Compose gestartet werden: "
                + (result.stderr.strip() or result.stdout.strip())
            )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                response = requests.get(f"{self.internal_url}/v1/health", timeout=2)
                if response.ok:
                    self.ensure_server_publisher()
                    return
            except requests.RequestException:
                pass
            time.sleep(0.5)
        raise RuntimeError(f"ntfy ist unter {self.internal_url} nach dem Start nicht erreichbar.")

    def create_reader(self) -> tuple[str, str, str]:
        """Erzeugt einen ntfy-Account mit Lese- und Schreibrecht auf genau ein Topic."""
        username = "u_" + secrets.token_hex(12)
        password = secrets.token_urlsafe(32)
        topic = "vpmobil-" + secrets.token_urlsafe(32).replace("_", "a").replace("-", "b")
        add = self._compose(
            "exec", "-T", "-e", f"NTFY_PASSWORD={password}", "ntfy", "ntfy", "user", "add", username
        )
        if add.returncode != 0:
            raise RuntimeError("ntfy-Nutzer konnte nicht angelegt werden: " + (add.stderr.strip() or add.stdout.strip()))
        access = self._compose("exec", "-T", "ntfy", "ntfy", "access", username, topic, "read-write")
        if access.returncode != 0:
            self._compose("exec", "-T", "ntfy", "ntfy", "user", "del", username)
            raise RuntimeError("ntfy-Zugriffsrecht konnte nicht gesetzt werden: " + (access.stderr.strip() or access.stdout.strip()))
        return topic, username, password

    def ensure_reader_credentials(self, topic: str, username: str, password: str) -> None:
        if self.provisioner_url:
            self._provision("ensure", topic=topic, username=username, password=password)
            return
        add = self._compose(
            "exec", "-T", "-e", f"NTFY_PASSWORD={password}", "ntfy", "ntfy", "user", "add", username
        )
        if add.returncode != 0:
            change = self._compose(
                "exec", "-T", "-e", f"NTFY_PASSWORD={password}", "ntfy", "ntfy", "user", "change-pass", username
            )
            if change.returncode != 0:
                raise RuntimeError(
                    "ntfy-Nutzer konnte nicht angelegt/aktualisiert werden: "
                    + (change.stderr.strip() or change.stdout.strip() or add.stderr.strip() or add.stdout.strip())
                )

        # Entfernt eine ACL aus älteren Installationen, die Schreibzugriffe auf
        # das exakte Topic trotz neuer Regel weiterhin blockieren kann.
        self._compose("exec", "-T", "ntfy", "ntfy", "access", "--reset", username, f"{topic}*")
        access = self._compose("exec", "-T", "ntfy", "ntfy", "access", username, topic, "read-write")
        if access.returncode != 0:
            raise RuntimeError(
                "ntfy-Zugriffsrecht konnte nicht gesetzt werden: "
                + (access.stderr.strip() or access.stdout.strip())
            )

    def ensure_server_publisher(self) -> None:
        username, password = resolve_ntfy_publisher_auth()
        add = self._compose(
            "exec", "-T", "-e", f"NTFY_PASSWORD={password}", "ntfy", "ntfy", "user", "add", username
        )
        if add.returncode != 0:
            change = self._compose(
                "exec", "-T", "-e", f"NTFY_PASSWORD={password}", "ntfy", "ntfy", "user", "change-pass", username
            )
            if change.returncode != 0:
                raise RuntimeError(
                    "ntfy-Servernutzer konnte nicht angelegt/aktualisiert werden: "
                    + (change.stderr.strip() or change.stdout.strip() or add.stderr.strip() or add.stdout.strip())
                )
        access = self._compose("exec", "-T", "ntfy", "ntfy", "access", username, "*", "write-only")
        if access.returncode != 0:
            raise RuntimeError(
                "ntfy-Servernutzer konnte nicht autorisiert werden: "
                + (access.stderr.strip() or access.stdout.strip())
            )

    def delete_reader(self, username: str) -> None:
        if self.provisioner_url:
            self._provision("delete", username=username)
            return
        result = self._compose("exec", "-T", "ntfy", "ntfy", "user", "del", username)
        if result.returncode != 0:
            raise RuntimeError("ntfy-Nutzer konnte nicht gelöscht werden: " + (result.stderr.strip() or result.stdout.strip()))

    def send_test_notification(self, topic: str, message: str) -> None:
        response = requests.post(
            f"{self.internal_url}/{topic}",
            data=message.encode("utf-8"),
            headers={"Title": "VPrintfy-Test", "Priority": "high", "Tags": "white_check_mark"},
            timeout=10,
        )
        response.raise_for_status()
