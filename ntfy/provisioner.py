"""Minimaler interner Dienst für die offiziell unterstützte ntfy-CLI-Verwaltung."""

from __future__ import annotations

import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import subprocess
from threading import Lock
import time


AUTH_FILE = os.getenv("NTFY_AUTH_FILE", "/var/lib/ntfy/auth.db")
SHARED_SECRET = os.getenv("NTFY_PROVISIONER_SECRET", "").encode("utf-8")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,255}$")
TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{3,255}$")
CLI_LOCK = Lock()


def run_ntfy(*arguments: str, password: str | None = None) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["NTFY_AUTH_FILE"] = AUTH_FILE
    if password is not None:
        environment["NTFY_PASSWORD"] = password
    return subprocess.run(
        ["ntfy", *arguments], env=environment, text=True, capture_output=True,
        check=False, timeout=30,
    )


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self._reply(200 if self.path == "/health" else 404, {"ok": self.path == "/health"})

    def do_POST(self) -> None:
        if not SHARED_SECRET:
            self._reply(503, {"error": "Provisionierungs-Secret fehlt."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 2048:
            self._reply(400, {"error": "Ungültige Anfragegröße."})
            return
        body = self.rfile.read(length)
        expected = hmac.new(SHARED_SECRET, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(self.headers.get("X-Provisioner-Signature", ""), expected):
            self._reply(401, {"error": "Ungültige Signatur."})
            return
        try:
            payload = json.loads(body)
            timestamp = int(payload["timestamp"])
            username = str(payload["username"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._reply(400, {"error": "Ungültige Anfrage."})
            return
        if abs(int(time.time()) - timestamp) > 30 or not USERNAME_RE.fullmatch(username):
            self._reply(400, {"error": "Ungültige oder abgelaufene Anfrage."})
            return

        try:
            with CLI_LOCK:
                if self.path == "/ensure":
                    self._ensure(payload, username)
                elif self.path == "/ensure-publisher":
                    self._ensure_publisher(payload, username)
                elif self.path == "/delete":
                    result = run_ntfy("user", "del", username)
                    if result.returncode != 0 and "does not exist" not in (result.stderr + result.stdout).lower():
                        raise RuntimeError("ntfy-Benutzer konnte nicht gelöscht werden.")
                else:
                    self._reply(404, {"error": "Unbekannte Operation."})
                    return
        except (KeyError, TypeError, ValueError) as error:
            self._reply(400, {"error": str(error)})
            return
        except (RuntimeError, subprocess.TimeoutExpired):
            self._reply(502, {"error": "ntfy-Provisionierung fehlgeschlagen."})
            return
        self._reply(200, {"ok": True})

    @staticmethod
    def _ensure(payload: dict[str, object], username: str) -> None:
        topic = str(payload["topic"])
        password = str(payload["password"])
        if not TOPIC_RE.fullmatch(topic) or not 16 <= len(password) <= 128:
            raise ValueError("Ungültige ntfy-Zugangsdaten.")
        added = run_ntfy("user", "add", username, password=password)
        if added.returncode != 0:
            changed = run_ntfy("user", "change-pass", username, password=password)
            if changed.returncode != 0:
                raise RuntimeError("ntfy-Benutzer konnte nicht angelegt werden.")
        run_ntfy("access", "--reset", username, f"{topic}*")
        access = run_ntfy("access", username, topic, "read-write")
        if access.returncode != 0:
            raise RuntimeError("ntfy-Zugriff konnte nicht gesetzt werden.")

    @staticmethod
    def _ensure_publisher(payload: dict[str, object], username: str) -> None:
        password = str(payload["password"])
        if not 16 <= len(password) <= 128:
            raise ValueError("Ungültige ntfy-Zugangsdaten.")
        added = run_ntfy("user", "add", username, password=password)
        if added.returncode != 0:
            changed = run_ntfy("user", "change-pass", username, password=password)
            if changed.returncode != 0:
                raise RuntimeError("ntfy-Publisher konnte nicht angelegt werden.")
        # Alte, widersprüchliche Wildcard-ACLs entfernen. Der Publisher darf
        # ausschließlich veröffentlichen, niemals fremde Topics lesen.
        run_ntfy("access", "--reset", username, "*")
        access = run_ntfy("access", username, "*", "write-only")
        if access.returncode != 0:
            raise RuntimeError("ntfy-Publisher konnte nicht autorisiert werden.")

    def _reply(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    if not SHARED_SECRET:
        raise SystemExit("NTFY_PROVISIONER_SECRET fehlt.")
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
