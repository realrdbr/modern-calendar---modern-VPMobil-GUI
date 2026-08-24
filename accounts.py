"""Persistente, sicherheitsrelevante Nutzerverwaltung für VpMobil."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
from typing import Any, Iterator, Mapping
from urllib.parse import unquote, urlparse

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

try:
    import pymysql
    from pymysql.cursors import DictCursor
except Exception:  # pragma: no cover
    pymysql = None
    DictCursor = None


LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_LOCK = timedelta(minutes=30)
LOGIN_MAX_FAILURES = 5
SESSION_LIFETIME = timedelta(days=14)
DEFAULT_LESSON_NOTIFICATION_TIMES = ("07:00", "09:10", "11:00", "13:15")
DEFAULT_CALENDAR_NOTIFICATION_TIME = "16:00"
DEFAULT_CALENDAR_NOTIFICATION_DAYS_BEFORE = 1


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_db_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def from_db_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def validate_username(username: str) -> str:
    username = username.strip()
    if not 3 <= len(username) <= 64:
        raise ValueError("Der Benutzername muss 3 bis 64 Zeichen lang sein.")
    if not all(char.isalnum() or char in "._-" for char in username):
        raise ValueError("Der Benutzername darf nur Buchstaben, Zahlen, Punkt, Unterstrich und Bindestrich enthalten.")
    return username


def validate_pin(pin: str) -> None:
    if len(pin) != 4 or not pin.isascii() or not pin.isdigit():
        raise ValueError("Die PIN muss genau vier Ziffern enthalten.")


def _legacy_pin_hash(pin: str) -> str:
    return hashlib.sha256(f"{pin}_cal11_salt_2026".encode("utf-8")).hexdigest()


def _hash_shared_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(pin.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=64)
    return f"scrypt${salt.hex()}${derived.hex()}"


def _verify_shared_pin(pin: str, stored_pin: str | None) -> bool:
    if not stored_pin:
        return False
    if stored_pin.startswith("scrypt$"):
        parts = stored_pin.split("$")
        if len(parts) != 3:
            return False
        try:
            salt = bytes.fromhex(parts[1])
            expected = bytes.fromhex(parts[2])
        except ValueError:
            return False
        actual = hashlib.scrypt(pin.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    if len(stored_pin) == 64 and all(char in "0123456789abcdefABCDEF" for char in stored_pin):
        return hmac.compare_digest(_legacy_pin_hash(pin), stored_pin)
    return hmac.compare_digest(pin, stored_pin)


@dataclass(frozen=True)
class User:
    id: int
    username: str
    class_name: str
    active: bool
    ntfy_topic: str
    ntfy_username: str
    ntfy_password: str


@dataclass(frozen=True)
class Session:
    user: User
    csrf_token: str


@dataclass(frozen=True)
class NotifySettings:
    lesson_notifications_enabled: bool = True
    lesson_notification_times: tuple[str, ...] = DEFAULT_LESSON_NOTIFICATION_TIMES
    calendar_notifications_enabled: bool = False
    calendar_notification_time: str = DEFAULT_CALENDAR_NOTIFICATION_TIME
    calendar_notification_days_before: int = DEFAULT_CALENDAR_NOTIFICATION_DAYS_BEFORE
    calendar_notification_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class CalendarEventTypeOption:
    id: str
    label: str


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    title: str
    date: str
    end_date: str | None
    start_time: str | None
    end_time: str | None
    course_id: str
    event_type: str
    description: str
    author: str


@dataclass(frozen=True)
class NotificationRecipient:
    user: User
    selected_classes: tuple[str, ...]
    subject_selections: dict[str, set[str]]
    notify_settings: NotifySettings
    calendar_courses: set[str]


class AccountStore:
    """Speicher mit Argon2id-PINs und verschlüsselten ntfy-Zugangsdaten."""

    def __init__(self, database: Path | str, encryption_key: str):
        try:
            self._fernet = Fernet(encryption_key.encode("ascii"))
        except (ValueError, TypeError) as error:
            raise RuntimeError(
                "APP_ENCRYPTION_KEY fehlt oder ist ungültig. Erzeuge ihn mit "
                "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"`."
            ) from error

        self._backend = "sqlite"
        self.database_path: Path | None = None
        self._mysql_config: dict[str, Any] | None = None

        database_value = str(database)
        if database_value.startswith(("mysql://", "mariadb://")):
            if pymysql is None:
                raise RuntimeError("Für MariaDB/MySQL wird das Paket `pymysql` benötigt.")
            self._backend = "mysql"
            self._mysql_config = self._parse_mysql_url(database_value)
        else:
            self.database_path = Path(database_value)
            self.database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                os.chmod(self.database_path.parent, 0o700)
            except OSError:
                pass

        self._hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
        self._initialize()
        if self.database_path is not None:
            try:
                os.chmod(self.database_path, 0o600)
            except OSError:
                pass

    @staticmethod
    def _parse_mysql_url(url: str) -> dict[str, Any]:
        parsed = urlparse(url)
        if parsed.scheme not in {"mysql", "mariadb"}:
            raise RuntimeError("APP_DATABASE_URL muss mit mysql:// oder mariadb:// beginnen.")
        if not parsed.hostname or not parsed.username:
            raise RuntimeError("APP_DATABASE_URL enthält keinen gültigen Host oder Benutzer.")
        db_name = parsed.path.lstrip("/")
        if not db_name:
            raise RuntimeError("APP_DATABASE_URL enthält keinen Datenbanknamen.")
        return {
            "host": parsed.hostname,
            "port": parsed.port or 3306,
            "user": unquote(parsed.username),
            "password": unquote(parsed.password or ""),
            "database": db_name,
            "charset": "utf8mb4",
            "cursorclass": DictCursor,
            "autocommit": False,
        }

    @property
    def _integrity_errors(self) -> tuple[type[BaseException], ...]:
        errors: tuple[type[BaseException], ...] = (sqlite3.IntegrityError,)
        if pymysql is not None:
            errors += (pymysql.err.IntegrityError,)
        return errors

    def _prepare_query(self, query: str) -> str:
        if self._backend == "mysql":
            return query.replace("?", "%s")
        return query

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        if self._backend == "sqlite":
            assert self.database_path is not None
            connection = sqlite3.connect(self.database_path)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA journal_mode = WAL")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            return

        assert self._mysql_config is not None
        connection = pymysql.connect(**self._mysql_config)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _execute(self, connection: Any, query: str, params: tuple[Any, ...] = ()) -> Any:
        prepared = self._prepare_query(query)
        if self._backend == "sqlite":
            return connection.execute(prepared, params)
        cursor = connection.cursor()
        cursor.execute(prepared, params)
        return cursor

    def _run(self, connection: Any, query: str, params: tuple[Any, ...] = ()) -> None:
        cursor = self._execute(connection, query, params)
        if self._backend == "mysql":
            cursor.close()

    def _executemany(self, connection: Any, query: str, params: list[tuple[Any, ...]]) -> None:
        prepared = self._prepare_query(query)
        if self._backend == "sqlite":
            connection.executemany(prepared, params)
            return
        cursor = connection.cursor()
        try:
            cursor.executemany(prepared, params)
        finally:
            cursor.close()

    def _fetchone(self, connection: Any, query: str, params: tuple[Any, ...] = ()) -> Any:
        cursor = self._execute(connection, query, params)
        try:
            row = cursor.fetchone()
        finally:
            if self._backend == "mysql":
                cursor.close()
        return row

    def _fetchall(self, connection: Any, query: str, params: tuple[Any, ...] = ()) -> list[Any]:
        cursor = self._execute(connection, query, params)
        try:
            rows = cursor.fetchall()
        finally:
            if self._backend == "mysql":
                cursor.close()
        return rows

    def _initialize(self) -> None:
        with self._connection() as connection:
            if self._backend == "sqlite":
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY,
                        username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                        pin_hash TEXT NOT NULL,
                        class_name TEXT NOT NULL,
                        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                        ntfy_topic TEXT NOT NULL UNIQUE,
                        ntfy_username TEXT NOT NULL UNIQUE,
                        ntfy_password_encrypted BLOB NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS user_subjects (
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        subject_key TEXT NOT NULL,
                        PRIMARY KEY (user_id, subject_key)
                    );
                    CREATE TABLE IF NOT EXISTS user_selected_classes (
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        class_name TEXT NOT NULL,
                        PRIMARY KEY (user_id, class_name)
                    );
                    CREATE TABLE IF NOT EXISTS user_subject_selections (
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        class_name TEXT NOT NULL,
                        subject_key TEXT NOT NULL,
                        PRIMARY KEY (user_id, class_name, subject_key)
                    );
                    CREATE TABLE IF NOT EXISTS user_notification_settings (
                        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                        settings_json TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS sessions (
                        token_hash TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        csrf_token TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS login_attempts (
                        id INTEGER PRIMARY KEY,
                        username TEXT NOT NULL,
                        ip_address TEXT NOT NULL,
                        attempted_at TEXT NOT NULL,
                        successful INTEGER NOT NULL CHECK (successful IN (0, 1))
                    );
                    CREATE INDEX IF NOT EXISTS idx_login_attempts_lookup
                        ON login_attempts(username, ip_address, attempted_at);
                    CREATE TABLE IF NOT EXISTS notification_deliveries (
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        event_key TEXT NOT NULL,
                        delivered_at TEXT NOT NULL,
                        PRIMARY KEY (user_id, event_key)
                    );
                    CREATE TABLE IF NOT EXISTS calendar_users (
                        username TEXT PRIMARY KEY COLLATE NOCASE,
                        courses TEXT NOT NULL,
                        pin TEXT DEFAULT NULL,
                        preferences TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'ACTIVE'
                    );
                    CREATE TABLE IF NOT EXISTS calendar_events (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        date TEXT NOT NULL,
                        end_date TEXT DEFAULT NULL,
                        start_time TEXT DEFAULT NULL,
                        end_time TEXT DEFAULT NULL,
                        course_id TEXT NOT NULL,
                        type TEXT NOT NULL,
                        description TEXT DEFAULT '',
                        author TEXT DEFAULT ''
                    );
                    CREATE TABLE IF NOT EXISTS calendar_event_categories (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        color TEXT NOT NULL,
                        sort_order INTEGER NOT NULL DEFAULT 0
                    );
                    """
                )
                return

            statements = [
                """
                CREATE TABLE IF NOT EXISTS vp_users (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(64) NOT NULL UNIQUE,
                    pin_hash VARCHAR(255) NOT NULL,
                    class_name VARCHAR(64) NOT NULL,
                    active TINYINT(1) NOT NULL DEFAULT 1,
                    ntfy_topic VARCHAR(255) NOT NULL UNIQUE,
                    ntfy_username VARCHAR(255) NOT NULL UNIQUE,
                    ntfy_password_encrypted LONGBLOB NOT NULL,
                    created_at VARCHAR(40) NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
                """
                CREATE TABLE IF NOT EXISTS vp_user_subjects (
                    user_id BIGINT NOT NULL,
                    subject_key VARCHAR(160) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
                    PRIMARY KEY (user_id, subject_key),
                    CONSTRAINT fk_vp_user_subjects_user
                        FOREIGN KEY (user_id) REFERENCES vp_users(id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
                """
                CREATE TABLE IF NOT EXISTS vp_user_selected_classes (
                    user_id BIGINT NOT NULL,
                    class_name VARCHAR(64) NOT NULL,
                    PRIMARY KEY (user_id, class_name),
                    CONSTRAINT fk_vp_user_selected_classes_user
                        FOREIGN KEY (user_id) REFERENCES vp_users(id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
                """
                CREATE TABLE IF NOT EXISTS vp_user_subject_selections (
                    user_id BIGINT NOT NULL,
                    class_name VARCHAR(64) NOT NULL,
                    subject_key VARCHAR(160) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
                    PRIMARY KEY (user_id, class_name, subject_key),
                    CONSTRAINT fk_vp_user_subject_selections_user
                        FOREIGN KEY (user_id) REFERENCES vp_users(id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
                """
                CREATE TABLE IF NOT EXISTS vp_user_notification_settings (
                    user_id BIGINT PRIMARY KEY,
                    settings_json LONGTEXT NOT NULL,
                    CONSTRAINT fk_vp_user_notification_settings_user
                        FOREIGN KEY (user_id) REFERENCES vp_users(id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
                """
                CREATE TABLE IF NOT EXISTS vp_sessions (
                    token_hash CHAR(64) PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    csrf_token VARCHAR(255) NOT NULL,
                    expires_at VARCHAR(40) NOT NULL,
                    created_at VARCHAR(40) NOT NULL,
                    CONSTRAINT fk_vp_sessions_user
                        FOREIGN KEY (user_id) REFERENCES vp_users(id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
                """
                CREATE TABLE IF NOT EXISTS vp_login_attempts (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(64) NOT NULL,
                    ip_address VARCHAR(64) NOT NULL,
                    attempted_at VARCHAR(40) NOT NULL,
                    successful TINYINT(1) NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_vp_login_attempts_lookup
                ON vp_login_attempts(username, ip_address, attempted_at)
                """,
                """
                CREATE TABLE IF NOT EXISTS vp_notification_deliveries (
                    user_id BIGINT NOT NULL,
                    event_key VARCHAR(255) NOT NULL,
                    delivered_at VARCHAR(40) NOT NULL,
                    PRIMARY KEY (user_id, event_key),
                    CONSTRAINT fk_vp_notification_user
                        FOREIGN KEY (user_id) REFERENCES vp_users(id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
            ]
            for statement in statements:
                self._run(connection, statement)
            try:
                self._run(connection, "ALTER TABLE vp_users ADD COLUMN pin_hash VARCHAR(255) NOT NULL DEFAULT ''")
            except Exception:
                pass
            try:
                self._run(
                    connection,
                    """
                    ALTER TABLE vp_user_subjects
                    MODIFY subject_key VARCHAR(160) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL
                    """,
                )
            except Exception:
                pass

    def _decrypt(self, value: bytes) -> str:
        try:
            return self._fernet.decrypt(value).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as error:
            raise RuntimeError("Die gespeicherten ntfy-Zugangsdaten können nicht entschlüsselt werden.") from error

    def _users_table(self) -> str:
        return "users" if self._backend == "sqlite" else "vp_users"

    def _legacy_subjects_table(self) -> str:
        return "user_subjects" if self._backend == "sqlite" else "vp_user_subjects"

    def _selected_classes_table(self) -> str:
        return "user_selected_classes" if self._backend == "sqlite" else "vp_user_selected_classes"

    def _subject_selections_table(self) -> str:
        return "user_subject_selections" if self._backend == "sqlite" else "vp_user_subject_selections"

    def _settings_table(self) -> str:
        return "user_notification_settings" if self._backend == "sqlite" else "vp_user_notification_settings"

    def _calendar_users_table(self) -> str:
        return "calendar_users" if self._backend == "sqlite" else "users"

    def _calendar_events_table(self) -> str:
        return "calendar_events" if self._backend == "sqlite" else "events"

    def _calendar_event_categories_table(self) -> str:
        return "calendar_event_categories" if self._backend == "sqlite" else "event_categories"

    def _get_user_row_by_id(self, connection: Any, user_id: int) -> Any:
        return self._fetchone(connection, f"SELECT * FROM {self._users_table()} WHERE id = ?", (user_id,))

    @staticmethod
    def _validate_class_names(class_names: set[str]) -> set[str]:
        normalized = {class_name.strip() for class_name in class_names if class_name.strip()}
        if not normalized:
            raise ValueError("Mindestens eine Klasse muss ausgewählt sein.")
        if any(len(class_name) > 64 for class_name in normalized):
            raise ValueError("Mindestens eine Klasse ist ungültig.")
        return normalized

    @staticmethod
    def _validate_subject_keys(subject_keys: set[str]) -> set[str]:
        if any(not key.startswith("subject:") or len(key) > 160 for key in subject_keys):
            raise ValueError("Ungültige Fachauswahl.")
        return set(subject_keys)

    @staticmethod
    def _normalize_notification_time(value: str) -> str:
        value = value.strip()
        try:
            return datetime.strptime(value, "%H:%M").strftime("%H:%M")
        except ValueError as error:
            raise ValueError(f"Ungültige Uhrzeit: {value}") from error

    def _settings_from_row(self, row: Any | None) -> NotifySettings:
        if row is None:
            return NotifySettings()
        raw = row["settings_json"]
        data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        lesson_times = tuple(
            dict.fromkeys(
                self._normalize_notification_time(value)
                for value in data.get("lesson_notification_times", DEFAULT_LESSON_NOTIFICATION_TIMES)
            )
        )
        if not lesson_times:
            lesson_times = DEFAULT_LESSON_NOTIFICATION_TIMES
        calendar_types = tuple(
            event_type.strip()
            for event_type in data.get("calendar_notification_types", ())
            if isinstance(event_type, str) and event_type.strip()
        )
        days_before = int(data.get("calendar_notification_days_before", DEFAULT_CALENDAR_NOTIFICATION_DAYS_BEFORE))
        if days_before < 0:
            raise ValueError("Kalender-Erinnerungen dürfen nicht in der Vergangenheit liegen.")
        return NotifySettings(
            lesson_notifications_enabled=bool(data.get("lesson_notifications_enabled", True)),
            lesson_notification_times=lesson_times,
            calendar_notifications_enabled=bool(data.get("calendar_notifications_enabled", False)),
            calendar_notification_time=self._normalize_notification_time(
                str(data.get("calendar_notification_time", DEFAULT_CALENDAR_NOTIFICATION_TIME))
            ),
            calendar_notification_days_before=days_before,
            calendar_notification_types=calendar_types,
        )

    def _user_from_row(self, row: Any) -> User:
        return User(
            id=int(row["id"]),
            username=row["username"],
            class_name=row["class_name"],
            active=bool(row["active"]),
            ntfy_topic=row["ntfy_topic"],
            ntfy_username=row["ntfy_username"],
            ntfy_password=self._decrypt(row["ntfy_password_encrypted"]),
        )

    def _ensure_shared_calendar_user(self, connection: Any, username: str, pin: str) -> None:
        if self._backend != "mysql":
            return
        self._run(
            connection,
            """
            INSERT INTO users (username, courses, pin, preferences, status)
            VALUES (?, ?, ?, ?, ?)
            ON DUPLICATE KEY UPDATE pin = VALUES(pin)
            """,
            (username.lower(), "[]", _hash_shared_pin(pin), json.dumps({}), "ACTIVE"),
        )

    def _bootstrap_vp_user_from_calendar(self, connection: Any, username: str, pin: str) -> Any | None:
        if self._backend != "mysql":
            return None
        calendar_row = self._fetchone(
            connection,
            "SELECT username, pin, status FROM users WHERE LOWER(username) = LOWER(?)",
            (username,),
        )
        if not calendar_row:
            return None
        if (calendar_row.get("status") or "ACTIVE") == "BLOCKED":
            return None
        if not _verify_shared_pin(pin, calendar_row.get("pin")):
            return None

        resolved_username = calendar_row["username"]
        class_name = (os.getenv("VP_DEFAULT_CLASS", "11") or "11").strip()
        if not class_name:
            class_name = "11"
        ntfy_topic = f"vp-{resolved_username.lower()}"
        ntfy_username = f"vp_{resolved_username.lower()}"
        ntfy_password = secrets.token_urlsafe(24)
        encrypted_password = self._fernet.encrypt(ntfy_password.encode("utf-8"))

        try:
            self._run(
                connection,
                """INSERT INTO vp_users
                (username, pin_hash, class_name, active, ntfy_topic, ntfy_username, ntfy_password_encrypted, created_at)
                VALUES (?, ?, ?, 1, ?, ?, ?, ?)""",
                (
                    resolved_username,
                    self._hasher.hash(pin),
                    class_name,
                    ntfy_topic,
                    ntfy_username,
                    encrypted_password,
                    to_db_time(utcnow()),
                ),
            )
        except self._integrity_errors:
            pass

        return self._fetchone(
            connection,
            """
            SELECT vp.*, u.pin AS calendar_pin, u.status AS calendar_status
            FROM vp_users vp
            LEFT JOIN users u ON LOWER(u.username) = LOWER(vp.username)
            WHERE LOWER(vp.username) = LOWER(?)
            """,
            (resolved_username,),
        )

    def create_user(
        self, username: str, pin: str, class_name: str, *, ntfy_topic: str,
        ntfy_username: str, ntfy_password: str,
    ) -> User:
        username = validate_username(username)
        validate_pin(pin)
        if not class_name.strip() or len(class_name) > 64:
            raise ValueError("Die Klasse ist ungültig.")
        encrypted_password = self._fernet.encrypt(ntfy_password.encode("utf-8"))
        with self._connection() as connection:
            try:
                if self._backend == "sqlite":
                    cursor = self._execute(
                        connection,
                        """INSERT INTO users
                        (username, pin_hash, class_name, ntfy_topic, ntfy_username, ntfy_password_encrypted, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (username, self._hasher.hash(pin), class_name.strip(), ntfy_topic,
                         ntfy_username, encrypted_password, to_db_time(utcnow())),
                    )
                    last_id = cursor.lastrowid
                    self._run(
                        connection,
                        """
                        INSERT INTO calendar_users(username, courses, pin, preferences, status)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(username) DO UPDATE SET pin = excluded.pin
                        """,
                        (username, "[]", _hash_shared_pin(pin), json.dumps({}), "ACTIVE"),
                    )
                    row = self._fetchone(connection, "SELECT * FROM users WHERE id = ?", (last_id,))
                    return self._user_from_row(row)

                self._ensure_shared_calendar_user(connection, username, pin)
                cursor = self._execute(
                    connection,
                    """INSERT INTO vp_users
                    (username, pin_hash, class_name, active, ntfy_topic, ntfy_username, ntfy_password_encrypted, created_at)
                    VALUES (?, ?, ?, 1, ?, ?, ?, ?)""",
                    (
                        username,
                        self._hasher.hash(pin),
                        class_name.strip(),
                        ntfy_topic,
                        ntfy_username,
                        encrypted_password,
                        to_db_time(utcnow()),
                    ),
                )
                last_id = cursor.lastrowid
                cursor.close()
                row = self._fetchone(connection, "SELECT * FROM vp_users WHERE id = ?", (last_id,))
                return self._user_from_row(row)
            except self._integrity_errors as error:
                raise ValueError("Der Benutzername existiert bereits.") from error

    def get_user(self, username: str) -> User:
        username = validate_username(username)
        with self._connection() as connection:
            if self._backend == "sqlite":
                row = self._fetchone(connection, "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,))
            else:
                row = self._fetchone(connection, "SELECT * FROM vp_users WHERE LOWER(username) = LOWER(?)", (username,))
        if row is None:
            raise ValueError("Benutzer nicht gefunden.")
        return self._user_from_row(row)

    def delete_user(self, username: str) -> None:
        username = validate_username(username)
        with self._connection() as connection:
            if self._backend == "sqlite":
                cursor = self._execute(connection, "DELETE FROM users WHERE username = ? COLLATE NOCASE", (username,))
                deleted = cursor.rowcount
                self._run(connection, "DELETE FROM calendar_users WHERE username = ? COLLATE NOCASE", (username,))
            else:
                cursor = self._execute(connection, "DELETE FROM vp_users WHERE LOWER(username) = LOWER(?)", (username,))
                deleted = cursor.rowcount
                self._run(connection, "DELETE FROM users WHERE LOWER(username) = LOWER(?)", (username,))
            if self._backend == "mysql":
                cursor.close()
            if deleted != 1:
                raise ValueError("Benutzer nicht gefunden.")

    def set_pin(self, username: str, pin: str) -> None:
        username = validate_username(username)
        validate_pin(pin)
        with self._connection() as connection:
            if self._backend == "sqlite":
                cursor = self._execute(
                    connection,
                    "UPDATE users SET pin_hash = ? WHERE username = ? COLLATE NOCASE",
                    (self._hasher.hash(pin), username),
                )
                changed = cursor.rowcount
                self._run(
                    connection,
                    """
                    INSERT INTO calendar_users(username, courses, pin, preferences, status)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(username) DO UPDATE SET pin = excluded.pin
                    """,
                    (username, "[]", _hash_shared_pin(pin), json.dumps({}), "ACTIVE"),
                )
            else:
                self._run(connection, "UPDATE vp_users SET pin_hash = ? WHERE LOWER(username) = LOWER(?)", (self._hasher.hash(pin), username))
                self._run(
                    connection,
                    """
                    INSERT INTO users (username, courses, pin, preferences, status)
                    VALUES (?, ?, ?, ?, ?)
                    ON DUPLICATE KEY UPDATE pin = VALUES(pin)
                    """,
                    (username.lower(), "[]", _hash_shared_pin(pin), json.dumps({}), "ACTIVE"),
                )
                cursor = self._execute(connection, "SELECT COUNT(*) AS c FROM vp_users WHERE LOWER(username) = LOWER(?)", (username,))
                changed = int(cursor.fetchone()["c"])
            if self._backend == "mysql":
                cursor.close()
            if changed != 1:
                raise ValueError("Benutzer nicht gefunden.")

    def set_active(self, username: str, active: bool) -> None:
        username = validate_username(username)
        with self._connection() as connection:
            if self._backend == "sqlite":
                cursor = self._execute(
                    connection, "UPDATE users SET active = ? WHERE username = ? COLLATE NOCASE", (int(active), username)
                )
                rowcount = cursor.rowcount
                if rowcount == 1 and not active:
                    self._run(connection, "DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE username = ? COLLATE NOCASE)", (username,))
            else:
                cursor = self._execute(connection, "UPDATE vp_users SET active = ? WHERE LOWER(username) = LOWER(?)", (int(active), username))
                rowcount = cursor.rowcount
                if rowcount == 1 and not active:
                    self._run(connection, "DELETE FROM vp_sessions WHERE user_id IN (SELECT id FROM vp_users WHERE LOWER(username) = LOWER(?))", (username,))
            if self._backend == "mysql":
                cursor.close()
            if rowcount != 1:
                raise ValueError("Benutzer nicht gefunden.")

    def _is_locked(self, connection: Any, username: str, ip_address: str) -> bool:
        threshold = to_db_time(utcnow() - LOGIN_WINDOW)
        table = "login_attempts" if self._backend == "sqlite" else "vp_login_attempts"
        failures = self._fetchall(
            connection,
            f"""SELECT attempted_at FROM {table}
                WHERE LOWER(username) = LOWER(?) AND ip_address = ? AND successful = 0 AND attempted_at >= ?
                ORDER BY attempted_at DESC LIMIT ?""",
            (username, ip_address, threshold, LOGIN_MAX_FAILURES),
        )
        if len(failures) < LOGIN_MAX_FAILURES:
            return False
        return from_db_time(failures[0]["attempted_at"]) + LOGIN_LOCK > utcnow()

    def authenticate(self, username: str, pin: str, ip_address: str) -> User | None:
        username = username.strip()
        now = to_db_time(utcnow())
        with self._connection() as connection:
            if self._is_locked(connection, username, ip_address):
                return None

            valid = False
            user_row = None
            if self._backend == "sqlite":
                user_row = self._fetchone(connection, "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,))
                if user_row is not None and bool(user_row["active"]):
                    try:
                        valid = self._hasher.verify(user_row["pin_hash"], pin)
                        if valid and self._hasher.check_needs_rehash(user_row["pin_hash"]):
                            self._run(connection, "UPDATE users SET pin_hash = ? WHERE id = ?", (self._hasher.hash(pin), user_row["id"]))
                    except (VerificationError, InvalidHashError):
                        valid = False
                self._run(
                    connection,
                    "INSERT INTO login_attempts(username, ip_address, attempted_at, successful) VALUES (?, ?, ?, ?)",
                    (username, ip_address, now, int(valid)),
                )
            else:
                user_row = self._fetchone(
                    connection,
                    """
                    SELECT vp.*, u.pin AS calendar_pin, u.status AS calendar_status
                    FROM vp_users vp
                    LEFT JOIN users u ON LOWER(u.username) = LOWER(vp.username)
                    WHERE LOWER(vp.username) = LOWER(?)
                    """,
                    (username,),
                )
                if user_row is None:
                    user_row = self._bootstrap_vp_user_from_calendar(connection, username, pin)
                if user_row is not None and bool(user_row["active"]) and (user_row.get("calendar_status") or "ACTIVE") != "BLOCKED":
                    calendar_valid = _verify_shared_pin(pin, user_row.get("calendar_pin"))
                    vp_valid = False
                    try:
                        vp_valid = self._hasher.verify(user_row.get("pin_hash", ""), pin)
                    except (VerificationError, InvalidHashError):
                        vp_valid = False
                    valid = calendar_valid or vp_valid
                    if valid and not calendar_valid:
                        self._ensure_shared_calendar_user(connection, username, pin)
                self._run(
                    connection,
                    "INSERT INTO vp_login_attempts(username, ip_address, attempted_at, successful) VALUES (?, ?, ?, ?)",
                    (username, ip_address, now, int(valid)),
                )

            if valid and user_row is not None:
                table = "login_attempts" if self._backend == "sqlite" else "vp_login_attempts"
                self._run(connection, f"DELETE FROM {table} WHERE attempted_at < ?", (to_db_time(utcnow() - timedelta(days=2)),))
                return self._user_from_row(user_row)
        return None

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def create_session(self, user_id: int) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        table = "sessions" if self._backend == "sqlite" else "vp_sessions"
        with self._connection() as connection:
            self._run(
                connection,
                f"INSERT INTO {table}(token_hash, user_id, csrf_token, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                (self._token_hash(token), user_id, csrf_token, to_db_time(utcnow() + SESSION_LIFETIME), to_db_time(utcnow())),
            )
        return token, csrf_token

    def get_session(self, token: str | None) -> Session | None:
        if not token or len(token) > 256:
            return None
        with self._connection() as connection:
            if self._backend == "sqlite":
                row = self._fetchone(
                    connection,
                    """SELECT users.*, sessions.csrf_token FROM sessions JOIN users ON users.id = sessions.user_id
                    WHERE sessions.token_hash = ? AND sessions.expires_at > ? AND users.active = 1""",
                    (self._token_hash(token), to_db_time(utcnow())),
                )
                self._run(connection, "DELETE FROM sessions WHERE expires_at <= ?", (to_db_time(utcnow()),))
            else:
                row = self._fetchone(
                    connection,
                    """SELECT vp_users.*, vp_sessions.csrf_token
                    FROM vp_sessions
                    JOIN vp_users ON vp_users.id = vp_sessions.user_id
                    WHERE vp_sessions.token_hash = ? AND vp_sessions.expires_at > ? AND vp_users.active = 1""",
                    (self._token_hash(token), to_db_time(utcnow())),
                )
                self._run(connection, "DELETE FROM vp_sessions WHERE expires_at <= ?", (to_db_time(utcnow()),))
        return Session(self._user_from_row(row), row["csrf_token"]) if row else None

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        table = "sessions" if self._backend == "sqlite" else "vp_sessions"
        with self._connection() as connection:
            self._run(connection, f"DELETE FROM {table} WHERE token_hash = ?", (self._token_hash(token),))

    def load_notify_settings(self, user_id: int) -> tuple[NotifySettings, bool]:
        with self._connection() as connection:
            row = self._fetchone(connection, f"SELECT settings_json FROM {self._settings_table()} WHERE user_id = ?", (user_id,))
        return self._settings_from_row(row), row is not None

    def _normalize_notify_settings(self, settings: NotifySettings) -> tuple[NotifySettings, str]:
        normalized = NotifySettings(
            lesson_notifications_enabled=settings.lesson_notifications_enabled,
            lesson_notification_times=tuple(
                dict.fromkeys(self._normalize_notification_time(value) for value in settings.lesson_notification_times)
            ) or DEFAULT_LESSON_NOTIFICATION_TIMES,
            calendar_notifications_enabled=settings.calendar_notifications_enabled,
            calendar_notification_time=self._normalize_notification_time(settings.calendar_notification_time),
            calendar_notification_days_before=max(0, int(settings.calendar_notification_days_before)),
            calendar_notification_types=tuple(
                dict.fromkeys(event_type.strip() for event_type in settings.calendar_notification_types if event_type.strip())
            ),
        )
        payload = json.dumps(
            {
                "lesson_notifications_enabled": normalized.lesson_notifications_enabled,
                "lesson_notification_times": list(normalized.lesson_notification_times),
                "calendar_notifications_enabled": normalized.calendar_notifications_enabled,
                "calendar_notification_time": normalized.calendar_notification_time,
                "calendar_notification_days_before": normalized.calendar_notification_days_before,
                "calendar_notification_types": list(normalized.calendar_notification_types),
            },
            sort_keys=True,
        )
        return normalized, payload

    def _save_notify_settings_with_connection(self, connection: Any, user_id: int, payload: str) -> None:
        if self._backend == "sqlite":
            self._run(
                connection,
                f"""
                INSERT INTO {self._settings_table()}(user_id, settings_json)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET settings_json = excluded.settings_json
                """,
                (user_id, payload),
            )
        else:
            self._run(
                connection,
                f"""
                INSERT INTO {self._settings_table()}(user_id, settings_json)
                VALUES (?, ?)
                ON DUPLICATE KEY UPDATE settings_json = VALUES(settings_json)
                """,
                (user_id, payload),
            )

    def save_notify_settings(self, user_id: int, settings: NotifySettings) -> None:
        _normalized, payload = self._normalize_notify_settings(settings)
        with self._connection() as connection:
            self._save_notify_settings_with_connection(connection, user_id, payload)

    def save_subscription_preferences(
        self,
        user_id: int,
        class_names: set[str],
        subject_selections: Mapping[str, set[str]],
        settings: NotifySettings,
    ) -> None:
        """Speichert Klassen, Fächer und Zeiten atomar in einer Transaktion."""
        normalized_classes = self._validate_class_names(class_names)
        normalized_selections = {
            class_name.strip(): self._validate_subject_keys(set(keys))
            for class_name, keys in subject_selections.items()
            if class_name.strip()
        }
        if set(normalized_selections) != normalized_classes:
            raise ValueError("Die Fachauswahl passt nicht zu den ausgewählten Klassen.")
        _normalized_settings, settings_payload = self._normalize_notify_settings(settings)
        with self._connection() as connection:
            self._run(connection, f"DELETE FROM {self._selected_classes_table()} WHERE user_id = ?", (user_id,))
            self._executemany(
                connection,
                f"INSERT INTO {self._selected_classes_table()}(user_id, class_name) VALUES (?, ?)",
                [(user_id, class_name) for class_name in sorted(normalized_classes)],
            )
            self._run(connection, f"DELETE FROM {self._legacy_subjects_table()} WHERE user_id = ?", (user_id,))
            self._run(connection, f"DELETE FROM {self._subject_selections_table()} WHERE user_id = ?", (user_id,))
            selection_rows = [
                (user_id, class_name, subject_key)
                for class_name in sorted(normalized_selections)
                for subject_key in sorted(normalized_selections[class_name])
            ]
            if selection_rows:
                self._executemany(
                    connection,
                    f"INSERT INTO {self._subject_selections_table()}(user_id, class_name, subject_key) VALUES (?, ?, ?)",
                    selection_rows,
                )
            legacy_class = min(normalized_selections)
            legacy_rows = [(user_id, key) for key in sorted(normalized_selections[legacy_class])]
            if legacy_rows:
                self._executemany(
                    connection,
                    f"INSERT INTO {self._legacy_subjects_table()}(user_id, subject_key) VALUES (?, ?)",
                    legacy_rows,
                )
            self._save_notify_settings_with_connection(connection, user_id, settings_payload)

    def get_selected_classes(self, user_id: int, fallback_class_name: str | None = None) -> tuple[tuple[str, ...], bool]:
        with self._connection() as connection:
            rows = self._fetchall(
                connection,
                f"SELECT class_name FROM {self._selected_classes_table()} WHERE user_id = ? ORDER BY class_name ASC",
                (user_id,),
            )
            if rows:
                return tuple(row["class_name"] for row in rows), True
            user_row = self._get_user_row_by_id(connection, user_id) if fallback_class_name is None else None
        resolved_fallback = fallback_class_name or (user_row["class_name"] if user_row is not None else None)
        if not resolved_fallback:
            return tuple(), False
        return (resolved_fallback,), False

    def replace_selected_classes(self, user_id: int, class_names: set[str]) -> None:
        normalized = sorted(self._validate_class_names(class_names))
        with self._connection() as connection:
            self._run(connection, f"DELETE FROM {self._selected_classes_table()} WHERE user_id = ?", (user_id,))
            self._executemany(
                connection,
                f"INSERT INTO {self._selected_classes_table()}(user_id, class_name) VALUES (?, ?)",
                [(user_id, class_name) for class_name in normalized],
            )

    def get_subject_selections(
        self,
        user_id: int,
        fallback_class_name: str | None = None,
    ) -> tuple[dict[str, set[str]], bool]:
        with self._connection() as connection:
            rows = self._fetchall(
                connection,
                f"""
                SELECT class_name, subject_key
                FROM {self._subject_selections_table()}
                WHERE user_id = ?
                ORDER BY class_name ASC, subject_key ASC
                """,
                (user_id,),
            )
            if rows:
                selections: dict[str, set[str]] = {}
                for row in rows:
                    selections.setdefault(row["class_name"], set()).add(row["subject_key"])
                return selections, True
            legacy_rows = self._fetchall(
                connection,
                f"SELECT subject_key FROM {self._legacy_subjects_table()} WHERE user_id = ? ORDER BY subject_key ASC",
                (user_id,),
            )
            if not legacy_rows:
                return {}, False
            user_row = self._get_user_row_by_id(connection, user_id) if fallback_class_name is None else None
        resolved_fallback = fallback_class_name or (user_row["class_name"] if user_row is not None else None)
        if not resolved_fallback:
            return {}, False
        return ({resolved_fallback: {row["subject_key"] for row in legacy_rows}}, False)

    def get_subjects(self, user_id: int) -> set[str]:
        selections, _ = self.get_subject_selections(user_id)
        return {subject_key for class_subjects in selections.values() for subject_key in class_subjects}

    def replace_subjects(self, user_id: int, subject_keys: set[str] | Mapping[str, set[str]]) -> None:
        if isinstance(subject_keys, Mapping):
            normalized_classes = {class_name.strip() for class_name in subject_keys if class_name.strip()}
            selections = {
                class_name.strip(): self._validate_subject_keys(set(keys))
                for class_name, keys in subject_keys.items()
                if class_name.strip()
            }
            if not normalized_classes:
                selections = {}
        else:
            with self._connection() as connection:
                user_row = self._get_user_row_by_id(connection, user_id)
            if user_row is None:
                raise ValueError("Benutzer nicht gefunden.")
            selections = {user_row["class_name"]: self._validate_subject_keys(set(subject_keys))}
        with self._connection() as connection:
            self._run(connection, f"DELETE FROM {self._legacy_subjects_table()} WHERE user_id = ?", (user_id,))
            self._run(connection, f"DELETE FROM {self._subject_selections_table()} WHERE user_id = ?", (user_id,))
            selection_rows = [
                (user_id, class_name, subject_key)
                for class_name in sorted(selections)
                for subject_key in sorted(selections[class_name])
            ]
            if selection_rows:
                self._executemany(
                    connection,
                    f"INSERT INTO {self._subject_selections_table()}(user_id, class_name, subject_key) VALUES (?, ?, ?)",
                    selection_rows,
                )
            legacy_class = min(selections) if selections else None
            legacy_rows = [(user_id, key) for key in sorted(selections.get(legacy_class, set()))] if legacy_class else []
            if legacy_rows:
                self._executemany(
                    connection,
                    f"INSERT INTO {self._legacy_subjects_table()}(user_id, subject_key) VALUES (?, ?)",
                    legacy_rows,
                )

    def get_calendar_course_ids(self, username: str) -> set[str]:
        with self._connection() as connection:
            row = self._fetchone(
                connection,
                f"SELECT courses FROM {self._calendar_users_table()} WHERE LOWER(username) = LOWER(?)",
                (username,),
            )
        if row is None:
            return set()
        raw = row["courses"]
        courses = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        return {
            str(course_id).strip()
            for course_id in courses
            if isinstance(course_id, str) and str(course_id).strip()
        }

    def get_calendar_event_types(self) -> list[CalendarEventTypeOption]:
        with self._connection() as connection:
            category_rows = self._fetchall(
                connection,
                f"SELECT id, name FROM {self._calendar_event_categories_table()} ORDER BY sort_order ASC, name ASC",
            )
            if category_rows:
                return [CalendarEventTypeOption(id=row["id"], label=row["name"]) for row in category_rows]
            event_rows = self._fetchall(
                connection,
                f"SELECT DISTINCT type FROM {self._calendar_events_table()} ORDER BY type ASC",
            )
        return [CalendarEventTypeOption(id=row["type"], label=row["type"]) for row in event_rows if row["type"]]

    def get_calendar_events(self) -> list[CalendarEvent]:
        with self._connection() as connection:
            rows = self._fetchall(
                connection,
                f"""
                SELECT id, title, date, end_date, start_time, end_time, course_id, type, description, author
                FROM {self._calendar_events_table()}
                ORDER BY date ASC, COALESCE(start_time, ''), title ASC
                """,
            )
        return [
            CalendarEvent(
                id=row["id"],
                title=row["title"],
                date=row["date"],
                end_date=row["end_date"],
                start_time=row["start_time"],
                end_time=row["end_time"],
                course_id=row["course_id"],
                event_type=row["type"],
                description=row["description"] or "",
                author=row["author"] or "",
            )
            for row in rows
        ]

    def subscribed_users(self) -> list[tuple[User, set[str]]]:
        recipients = self.notification_recipients()
        return [
            (
                recipient.user,
                {subject_key for class_subjects in recipient.subject_selections.values() for subject_key in class_subjects},
            )
            for recipient in recipients
        ]

    def notification_recipients(self) -> list[NotificationRecipient]:
        with self._connection() as connection:
            rows = self._fetchall(connection, f"SELECT * FROM {self._users_table()} WHERE active = 1")
            selected_class_rows = self._fetchall(
                connection,
                f"SELECT user_id, class_name FROM {self._selected_classes_table()} ORDER BY user_id ASC, class_name ASC",
            )
            selection_rows = self._fetchall(
                connection,
                f"""
                SELECT user_id, class_name, subject_key
                FROM {self._subject_selections_table()}
                ORDER BY user_id ASC, class_name ASC, subject_key ASC
                """,
            )
            legacy_rows = self._fetchall(
                connection,
                f"SELECT user_id, subject_key FROM {self._legacy_subjects_table()} ORDER BY user_id ASC, subject_key ASC",
            )
            settings_rows = self._fetchall(
                connection,
                f"SELECT user_id, settings_json FROM {self._settings_table()} ORDER BY user_id ASC",
            )
        selected_classes_by_user: dict[int, list[str]] = {}
        for row in selected_class_rows:
            selected_classes_by_user.setdefault(int(row["user_id"]), []).append(row["class_name"])
        selections_by_user: dict[int, dict[str, set[str]]] = {}
        for row in selection_rows:
            selections_by_user.setdefault(int(row["user_id"]), {}).setdefault(row["class_name"], set()).add(row["subject_key"])
        legacy_by_user: dict[int, set[str]] = {}
        for row in legacy_rows:
            legacy_by_user.setdefault(int(row["user_id"]), set()).add(row["subject_key"])
        settings_by_user = {
            int(row["user_id"]): self._settings_from_row(row)
            for row in settings_rows
        }
        recipients: list[NotificationRecipient] = []
        for row in rows:
            user = self._user_from_row(row)
            selected_classes = tuple(selected_classes_by_user.get(user.id) or [user.class_name])
            subject_selections = selections_by_user.get(user.id)
            if subject_selections is None and legacy_by_user.get(user.id):
                subject_selections = {user.class_name: set(legacy_by_user[user.id])}
            recipients.append(
                NotificationRecipient(
                    user=user,
                    selected_classes=selected_classes,
                    subject_selections={class_name: set(keys) for class_name, keys in (subject_selections or {}).items()},
                    notify_settings=settings_by_user.get(user.id, NotifySettings()),
                    calendar_courses=self.get_calendar_course_ids(user.username),
                )
            )
        return recipients

    def mark_delivery_once(self, user_id: int, event_key: str) -> bool:
        table = "notification_deliveries" if self._backend == "sqlite" else "vp_notification_deliveries"
        try:
            with self._connection() as connection:
                self._run(
                    connection,
                    f"INSERT INTO {table}(user_id, event_key, delivered_at) VALUES (?, ?, ?)",
                    (user_id, event_key, to_db_time(utcnow())),
                )
        except self._integrity_errors:
            return False
        return True

    def forget_delivery(self, user_id: int, event_key: str) -> None:
        table = "notification_deliveries" if self._backend == "sqlite" else "vp_notification_deliveries"
        with self._connection() as connection:
            self._run(connection, f"DELETE FROM {table} WHERE user_id = ? AND event_key = ?", (user_id, event_key))
