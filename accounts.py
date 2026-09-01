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
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

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
MAX_CALENDAR_NOTIFICATION_DAYS_BEFORE = 30


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


def _verify_account_pin(hasher: PasswordHasher, stored_pin: str | None, pin: str) -> tuple[bool, bool]:
    """Prüft PINs aus allen unterstützten DB-Versionen.

    Rückgabe: (gültig, sollte_auf_argon2_aktualisiert_werden).
    """
    if not stored_pin:
        return False, False
    try:
        valid = hasher.verify(stored_pin, pin)
        return valid, bool(valid and hasher.check_needs_rehash(stored_pin))
    except (VerificationError, InvalidHashError):
        valid = _verify_shared_pin(pin, stored_pin)
        return valid, valid


@dataclass(frozen=True)
class User:
    id: int
    username: str
    class_name: str
    active: bool
    ntfy_topic: str
    ntfy_username: str
    ntfy_password: str
    vp_only: bool = False
    must_change_pin: bool = False


@dataclass(frozen=True)
class Session:
    user: User
    csrf_token: str


@dataclass(frozen=True)
class NotifySettings:
    lesson_notifications_enabled: bool = True
    lesson_notification_times: tuple[str, ...] = DEFAULT_LESSON_NOTIFICATION_TIMES
    daily_summary_day_before: bool = False
    calendar_notifications_enabled: bool = False
    calendar_notification_time: str = DEFAULT_CALENDAR_NOTIFICATION_TIME
    calendar_notification_times: dict[str, str] | None = None
    calendar_notification_days_before: int = DEFAULT_CALENDAR_NOTIFICATION_DAYS_BEFORE
    calendar_notification_days_before_by_type: dict[str, int] | None = None
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

    @staticmethod
    def _sqlite_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
        return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table_name})")}

    def _sqlite_add_column_if_missing(self, connection: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
        if column_name not in self._sqlite_columns(connection, table_name):
            self._run(connection, f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

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
                    CREATE TABLE IF NOT EXISTS vp_only_users (
                        username TEXT PRIMARY KEY COLLATE NOCASE,
                        user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                        pin_hash TEXT NOT NULL,
                        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                        must_change_pin INTEGER NOT NULL DEFAULT 1 CHECK (must_change_pin IN (0, 1)),
                        created_by TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS vp_only_sessions (
                        token_hash TEXT PRIMARY KEY,
                        username TEXT NOT NULL COLLATE NOCASE REFERENCES vp_only_users(username) ON DELETE CASCADE,
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
                        deleted_at TEXT DEFAULT NULL,
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
                    CREATE TABLE IF NOT EXISTS courses (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        teacher TEXT NOT NULL DEFAULT '',
                        type TEXT NOT NULL DEFAULT 'GK',
                        sort_order INTEGER NOT NULL DEFAULT 999
                    );
                    """
                )
                self._sqlite_add_column_if_missing(connection, "users", "pin_hash", "TEXT NOT NULL DEFAULT ''")
                self._sqlite_add_column_if_missing(connection, "users", "active", "INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))")
                self._sqlite_add_column_if_missing(connection, "calendar_users", "pin", "TEXT DEFAULT NULL")
                self._sqlite_add_column_if_missing(connection, "calendar_users", "preferences", "TEXT NOT NULL DEFAULT '{}'")
                self._sqlite_add_column_if_missing(connection, "calendar_users", "status", "TEXT NOT NULL DEFAULT 'ACTIVE'")
                self._sqlite_add_column_if_missing(connection, "calendar_events", "end_date", "TEXT DEFAULT NULL")
                self._sqlite_add_column_if_missing(connection, "calendar_events", "start_time", "TEXT DEFAULT NULL")
                self._sqlite_add_column_if_missing(connection, "calendar_events", "end_time", "TEXT DEFAULT NULL")
                self._sqlite_add_column_if_missing(connection, "calendar_events", "description", "TEXT DEFAULT ''")
                self._sqlite_add_column_if_missing(connection, "calendar_events", "author", "TEXT DEFAULT ''")
                self._sqlite_add_column_if_missing(connection, "vp_only_users", "must_change_pin", "INTEGER NOT NULL DEFAULT 1 CHECK (must_change_pin IN (0, 1))")
                self._sqlite_add_column_if_missing(connection, "notification_deliveries", "deleted_at", "TEXT DEFAULT NULL")
                legacy_subjects_exists = self._fetchone(
                    connection,
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'user_subjects'",
                )
                if legacy_subjects_exists:
                    self._run(
                        connection,
                        """INSERT OR IGNORE INTO user_subject_selections(user_id, class_name, subject_key)
                        SELECT legacy.user_id, users.class_name, legacy.subject_key
                        FROM user_subjects legacy
                        JOIN users ON users.id = legacy.user_id""",
                    )
                    self._run(connection, "DROP TABLE user_subjects")
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
                CREATE TABLE IF NOT EXISTS app_sessions (
                    token_hash CHAR(64) PRIMARY KEY,
                    username VARCHAR(64) NOT NULL,
                    csrf_token VARCHAR(255) NOT NULL,
                    expires_at DATETIME(3) NOT NULL,
                    created_at DATETIME(3) NOT NULL,
                    INDEX idx_app_sessions_user (username),
                    INDEX idx_app_sessions_expiry (expires_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
                """
                CREATE TABLE IF NOT EXISTS vp_only_users (
                    username VARCHAR(64) PRIMARY KEY,
                    user_id BIGINT NOT NULL UNIQUE,
                    pin_hash VARCHAR(255) NOT NULL,
                    active TINYINT(1) NOT NULL DEFAULT 1,
                    must_change_pin TINYINT(1) NOT NULL DEFAULT 1,
                    created_by VARCHAR(64) NOT NULL,
                    created_at VARCHAR(40) NOT NULL,
                    INDEX idx_vp_only_users_user_id (user_id),
                    CONSTRAINT fk_vp_only_users_profile
                        FOREIGN KEY (user_id) REFERENCES vp_users(id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """,
                """
                CREATE TABLE IF NOT EXISTS vp_only_sessions (
                    token_hash CHAR(64) PRIMARY KEY,
                    username VARCHAR(64) NOT NULL,
                    csrf_token VARCHAR(255) NOT NULL,
                    expires_at DATETIME(3) NOT NULL,
                    created_at DATETIME(3) NOT NULL,
                    INDEX idx_vp_only_sessions_user (username),
                    INDEX idx_vp_only_sessions_expiry (expires_at),
                    CONSTRAINT fk_vp_only_sessions_user
                        FOREIGN KEY (username) REFERENCES vp_only_users(username)
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
                CREATE TABLE IF NOT EXISTS vp_notification_deliveries (
                    user_id BIGINT NOT NULL,
                    event_key VARCHAR(255) NOT NULL,
                    delivered_at VARCHAR(40) NOT NULL,
                    deleted_at VARCHAR(40) DEFAULT NULL,
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
                self._run(connection, "CREATE INDEX idx_vp_login_attempts_lookup ON vp_login_attempts(username, ip_address, attempted_at)")
            except Exception:
                pass
            try:
                self._run(connection, "ALTER TABLE vp_users ADD COLUMN pin_hash VARCHAR(255) NOT NULL DEFAULT ''")
            except Exception:
                pass
            # The shared calendar service owns `users`, but VP may start first
            # during a rolling deployment. Add the class column here as well so
            # a first VP login cannot fail before the calendar service performs
            # its own migration.
            try:
                self._run(connection, "ALTER TABLE users ADD COLUMN class_name VARCHAR(64) NOT NULL DEFAULT '11'")
            except Exception:
                pass
            try:
                self._run(connection, "ALTER TABLE vp_only_users ADD COLUMN must_change_pin TINYINT(1) NOT NULL DEFAULT 1")
            except Exception:
                pass
            try:
                self._run(connection, "ALTER TABLE vp_notification_deliveries ADD COLUMN deleted_at VARCHAR(40) DEFAULT NULL")
            except Exception:
                pass
            try:
                self._run(
                    connection,
                    """INSERT IGNORE INTO vp_only_users(username, user_id, pin_hash, active, must_change_pin, created_by, created_at)
                    SELECT LOWER(vp.username), vp.id, vp.pin_hash, vp.active, 0, 'migration', vp.created_at
                    FROM vp_users vp
                    LEFT JOIN users u ON LOWER(u.username) = LOWER(vp.username)
                    WHERE u.username IS NULL AND vp.pin_hash <> ''""",
                )
            except Exception:
                pass
            # `users.pin` is the sole credential source for shared accounts.  The
            # legacy column remains in place so older schemas/binaries still run
            # and VP-only accounts can be migrated on their next successful login.
            try:
                self._run(
                    connection,
                    """UPDATE vp_users vp
                    INNER JOIN users u ON LOWER(u.username) = LOWER(vp.username)
                    SET vp.pin_hash = ''
                    WHERE vp.pin_hash <> ''""",
                )
            except Exception:
                # The calendar container may create `users` moments later.
                # Authentication still performs the same migration lazily.
                pass
            legacy_subjects_exists = self._fetchone(connection, "SHOW TABLES LIKE 'vp_user_subjects'")
            if legacy_subjects_exists:
                self._run(
                    connection,
                    """INSERT IGNORE INTO vp_user_subject_selections(user_id, class_name, subject_key)
                    SELECT legacy.user_id, users.class_name, legacy.subject_key
                    FROM vp_user_subjects legacy
                    JOIN vp_users users ON users.id = legacy.user_id""",
                )
                self._run(connection, "DROP TABLE vp_user_subjects")

            legacy_sessions_exists = self._fetchone(connection, "SHOW TABLES LIKE 'vp_sessions'")
            if legacy_sessions_exists:
                legacy_sessions = self._fetchall(
                    connection,
                    """SELECT sessions.token_hash, users.username, sessions.csrf_token,
                              sessions.expires_at, sessions.created_at
                    FROM vp_sessions sessions
                    JOIN vp_users users ON users.id = sessions.user_id""",
                )
                for session in legacy_sessions:
                    self._run(
                        connection,
                        """INSERT IGNORE INTO app_sessions
                        (token_hash, username, csrf_token, expires_at, created_at)
                        VALUES (?, ?, ?, ?, ?)""",
                        (
                            session["token_hash"], session["username"].lower(), session["csrf_token"],
                            from_db_time(session["expires_at"]).replace(tzinfo=None),
                            from_db_time(session["created_at"]).replace(tzinfo=None),
                        ),
                    )
                self._run(connection, "DROP TABLE vp_sessions")

    def _decrypt(self, value: bytes) -> str:
        try:
            return self._fernet.decrypt(value).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as error:
            raise RuntimeError("Die gespeicherten ntfy-Zugangsdaten können nicht entschlüsselt werden.") from error

    def _users_table(self) -> str:
        return "users" if self._backend == "sqlite" else "vp_users"

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
        days_before = min(
            MAX_CALENDAR_NOTIFICATION_DAYS_BEFORE,
            int(data.get("calendar_notification_days_before", DEFAULT_CALENDAR_NOTIFICATION_DAYS_BEFORE)),
        )
        if days_before < 0:
            raise ValueError("Kalender-Erinnerungen dürfen nicht in der Vergangenheit liegen.")
        raw_category_times = data.get("calendar_notification_times", {})
        category_times = {
            str(event_type).strip(): self._normalize_notification_time(str(value))
            for event_type, value in raw_category_times.items()
            if str(event_type).strip()
        } if isinstance(raw_category_times, dict) else {}
        raw_category_days = data.get("calendar_notification_days_before_by_type", {})
        category_days = {
            str(event_type).strip(): max(0, min(MAX_CALENDAR_NOTIFICATION_DAYS_BEFORE, int(value)))
            for event_type, value in raw_category_days.items()
            if str(event_type).strip()
        } if isinstance(raw_category_days, dict) else {}
        return NotifySettings(
            lesson_notifications_enabled=bool(data.get("lesson_notifications_enabled", True)),
            lesson_notification_times=lesson_times,
            daily_summary_day_before=bool(data.get("daily_summary_day_before", False)),
            calendar_notifications_enabled=bool(data.get("calendar_notifications_enabled", False)),
            calendar_notification_time=self._normalize_notification_time(
                str(data.get("calendar_notification_time", DEFAULT_CALENDAR_NOTIFICATION_TIME))
            ),
            calendar_notification_times=category_times,
            calendar_notification_days_before=days_before,
            calendar_notification_days_before_by_type=category_days,
            calendar_notification_types=calendar_types,
        )

    def _user_from_row(self, row: Any) -> User:
        keys = set(row.keys())
        return User(
            id=int(row["id"]),
            username=row["username"],
            class_name=row["class_name"],
            active=bool(row["active"]),
            ntfy_topic=row["ntfy_topic"],
            ntfy_username=row["ntfy_username"],
            ntfy_password=self._decrypt(row["ntfy_password_encrypted"]),
            vp_only=bool(row["vp_only"]) if "vp_only" in keys else False,
            must_change_pin=bool(row["must_change_pin"]) if "must_change_pin" in keys else False,
        )

    def _is_vp_only_user_id(self, connection: Any, user_id: int) -> bool:
        row = self._fetchone(connection, "SELECT 1 FROM vp_only_users WHERE user_id = ?", (user_id,))
        return row is not None

    def is_admin(self, username: str) -> bool:
        username = validate_username(username)
        with self._connection() as connection:
            if self._backend == "sqlite":
                row = self._fetchone(
                    connection,
                    "SELECT status FROM calendar_users WHERE username = ? COLLATE NOCASE",
                    (username,),
                )
            else:
                row = self._fetchone(connection, "SELECT status FROM users WHERE LOWER(username) = LOWER(?)", (username,))
        return row is not None and row["status"] == "ADMIN"

    def username_exists(self, username: str) -> bool:
        username = validate_username(username)
        with self._connection() as connection:
            if self._backend == "sqlite":
                row = self._fetchone(
                    connection,
                    """SELECT 1 FROM users WHERE username = ? COLLATE NOCASE
                    UNION
                    SELECT 1 FROM calendar_users WHERE username = ? COLLATE NOCASE""",
                    (username, username),
                )
            else:
                row = self._fetchone(
                    connection,
                    """SELECT 1 FROM users WHERE LOWER(username) = LOWER(?)
                    UNION
                    SELECT 1 FROM vp_users WHERE LOWER(username) = LOWER(?)""",
                    (username, username),
                )
        return row is not None

    def _ensure_shared_calendar_user(self, connection: Any, username: str, pin: str | None) -> None:
        if self._backend != "mysql":
            return
        self._run(
            connection,
            """
            INSERT INTO users (username, courses, pin, preferences, status)
            VALUES (?, ?, ?, ?, ?)
            ON DUPLICATE KEY UPDATE pin = VALUES(pin)
            """,
            (username.lower(), "[]", _hash_shared_pin(pin) if pin else None, json.dumps({}), "ACTIVE"),
        )

    def _bootstrap_vp_user_from_calendar(
        self, connection: Any, username: str, pin: str | None, *, trusted_session: bool = False,
    ) -> Any | None:
        if self._backend != "mysql":
            return None
        try:
            calendar_row = self._fetchone(
                connection,
                "SELECT username, pin, status, class_name FROM users WHERE LOWER(username) = LOWER(?)",
                (username,),
            )
        except Exception:
            # Compatibility with a calendar service that has not yet run the
            # migration. It still authenticates safely; only the documented
            # default class is available until that service is updated.
            calendar_row = self._fetchone(
                connection,
                "SELECT username, pin, status FROM users WHERE LOWER(username) = LOWER(?)",
                (username,),
            )
        if not calendar_row:
            return None
        if (calendar_row.get("status") or "ACTIVE") == "BLOCKED":
            return None
        calendar_pin = calendar_row.get("pin")
        if not trusted_session and calendar_pin and (pin is None or not _verify_shared_pin(pin, calendar_pin)):
            return None

        resolved_username = calendar_row["username"]
        class_name = (calendar_row.get("class_name") or os.getenv("VP_DEFAULT_CLASS", "11") or "11").strip()
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
                    "",
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
            SELECT vp.*, u.pin AS calendar_pin, u.status AS calendar_status,
                   u.username AS calendar_username
            FROM vp_users vp
            LEFT JOIN users u ON LOWER(u.username) = LOWER(vp.username)
            WHERE LOWER(vp.username) = LOWER(?)
            """,
            (resolved_username,),
        )

    def get_login_identity(self, username: str) -> tuple[str, bool]:
        """Löst ein gemeinsames Kalender-/VP-Konto auf und meldet, ob eine PIN nötig ist."""
        username = validate_username(username)
        with self._connection() as connection:
            if self._backend == "sqlite":
                row = self._fetchone(
                    connection,
                    """SELECT users.username,
                              COALESCE(only_users.pin_hash, users.pin_hash) AS resolved_pin_hash,
                              users.active AS user_active,
                              only_users.active AS vp_only_active
                    FROM users
                    LEFT JOIN vp_only_users only_users ON only_users.user_id = users.id
                    WHERE users.username = ? COLLATE NOCASE""",
                    (username,),
                )
                if row is None or not bool(row["user_active"]) or row["vp_only_active"] == 0:
                    raise ValueError("Benutzer nicht gefunden.")
                return row["username"], bool(row["resolved_pin_hash"])

            row = self._fetchone(
                connection,
                """SELECT u.username, u.pin, u.status, vp.active, vp.pin_hash
                FROM users u
                LEFT JOIN vp_users vp ON LOWER(vp.username) = LOWER(u.username)
                WHERE LOWER(u.username) = LOWER(?)""",
                (username,),
            )
            if row is not None:
                if (row.get("status") or "ACTIVE") == "BLOCKED" or row.get("active") == 0:
                    raise ValueError("Benutzer nicht gefunden.")
                return row["username"], bool(row.get("pin"))

            vp_row = self._fetchone(
                connection,
                """SELECT vp.username, only_users.pin_hash, only_users.active
                FROM vp_only_users only_users
                JOIN vp_users vp ON vp.id = only_users.user_id
                WHERE LOWER(only_users.username) = LOWER(?)""",
                (username,),
            )
            if vp_row is None or not bool(vp_row["active"]):
                raise ValueError("Benutzer nicht gefunden.")
            return vp_row["username"], bool(vp_row.get("pin_hash"))

    def create_vp_only_user(
        self, username: str, pin: str, class_name: str, *, created_by: str,
        ntfy_topic: str, ntfy_username: str, ntfy_password: str,
    ) -> User:
        username = validate_username(username)
        created_by = validate_username(created_by)
        validate_pin(pin)
        if not class_name.strip() or len(class_name) > 64:
            raise ValueError("Die Klasse ist ungültig.")
        encrypted_password = self._fernet.encrypt(ntfy_password.encode("utf-8"))
        now = to_db_time(utcnow())
        with self._connection() as connection:
            if self._backend == "sqlite":
                if self._fetchone(connection, "SELECT 1 FROM calendar_users WHERE username = ? COLLATE NOCASE", (username,)):
                    raise ValueError("Name bereits vergeben.")
                try:
                    cursor = self._execute(
                        connection,
                        """INSERT INTO users
                        (username, pin_hash, class_name, ntfy_topic, ntfy_username, ntfy_password_encrypted, created_at)
                        VALUES (?, '', ?, ?, ?, ?, ?)""",
                        (username, class_name.strip(), ntfy_topic, ntfy_username, encrypted_password, now),
                    )
                    user_id = cursor.lastrowid
                    self._run(
                        connection,
                        """INSERT INTO vp_only_users(username, user_id, pin_hash, active, must_change_pin, created_by, created_at)
                        VALUES (?, ?, ?, 1, 1, ?, ?)""",
                        (username, user_id, self._hasher.hash(pin), created_by, now),
                    )
                except self._integrity_errors as error:
                    raise ValueError("Name bereits vergeben.") from error
                row = self._fetchone(connection, "SELECT users.*, 1 AS vp_only, 1 AS must_change_pin FROM users WHERE id = ?", (user_id,))
                return self._user_from_row(row)

            if self._fetchone(connection, "SELECT 1 FROM users WHERE LOWER(username) = LOWER(?)", (username,)):
                raise ValueError("Name bereits vergeben.")
            try:
                cursor = self._execute(
                    connection,
                    """INSERT INTO vp_users
                    (username, pin_hash, class_name, active, ntfy_topic, ntfy_username, ntfy_password_encrypted, created_at)
                    VALUES (?, '', ?, 1, ?, ?, ?, ?)""",
                    (username, class_name.strip(), ntfy_topic, ntfy_username, encrypted_password, now),
                )
                user_id = cursor.lastrowid
                cursor.close()
                self._run(
                    connection,
                    """INSERT INTO vp_only_users(username, user_id, pin_hash, active, must_change_pin, created_by, created_at)
                    VALUES (?, ?, ?, 1, 1, ?, ?)""",
                    (username.lower(), user_id, self._hasher.hash(pin), created_by.lower(), now),
                )
            except self._integrity_errors as error:
                raise ValueError("Name bereits vergeben.") from error
            row = self._fetchone(connection, "SELECT vp_users.*, 1 AS vp_only, 1 AS must_change_pin FROM vp_users WHERE id = ?", (user_id,))
            return self._user_from_row(row)

    def create_user(
        self, username: str, pin: str, class_name: str, *, ntfy_topic: str,
        ntfy_username: str, ntfy_password: str,
    ) -> User:
        username = validate_username(username)
        if pin:
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
                        (username, self._hasher.hash(pin) if pin else "", class_name.strip(), ntfy_topic,
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
                        (username, "[]", _hash_shared_pin(pin) if pin else None, json.dumps({}), "ACTIVE"),
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
                        "",
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
                row = self._fetchone(
                    connection,
                    """SELECT users.*, CASE WHEN only_users.username IS NULL THEN 0 ELSE 1 END AS vp_only,
                              COALESCE(only_users.must_change_pin, 0) AS must_change_pin
                    FROM users
                    LEFT JOIN vp_only_users only_users ON only_users.user_id = users.id
                    WHERE users.username = ? COLLATE NOCASE""",
                    (username,),
                )
            else:
                row = self._fetchone(
                    connection,
                    """SELECT vp_users.*, IF(only_users.username IS NULL, 0, 1) AS vp_only,
                              COALESCE(only_users.must_change_pin, 0) AS must_change_pin
                    FROM vp_users
                    LEFT JOIN vp_only_users only_users ON only_users.user_id = vp_users.id
                    WHERE LOWER(vp_users.username) = LOWER(?)""",
                    (username,),
                )
        if row is None:
            raise ValueError("Benutzer nicht gefunden.")
        return self._user_from_row(row)

    def list_vp_only_users(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = self._fetchall(
                connection,
                f"""SELECT only_users.username, vp.class_name, only_users.active,
                          only_users.must_change_pin, only_users.created_by, only_users.created_at
                FROM vp_only_users only_users
                JOIN {self._users_table()} vp ON vp.id = only_users.user_id
                ORDER BY only_users.created_at DESC, only_users.username ASC""",
            )
        return [
            {
                "username": row["username"],
                "class_name": row["class_name"],
                "active": bool(row["active"]),
                "must_change_pin": bool(row["must_change_pin"]),
                "created_by": row["created_by"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def list_admin_panel_users(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            if self._backend == "sqlite":
                rows = self._fetchall(
                    connection,
                    """SELECT * FROM (
                    SELECT users.username, users.class_name, users.active,
                              CASE WHEN only_users.username IS NULL THEN 0 ELSE 1 END AS vp_only,
                              COALESCE(calendar_users.status, 'ACTIVE') AS calendar_status
                    FROM users
                    LEFT JOIN vp_only_users only_users ON only_users.user_id = users.id
                    LEFT JOIN calendar_users ON calendar_users.username = users.username COLLATE NOCASE
                    UNION
                    SELECT calendar_users.username, '', 1,
                              0 AS vp_only, COALESCE(calendar_users.status, 'ACTIVE') AS calendar_status
                    FROM calendar_users
                    LEFT JOIN users ON users.username = calendar_users.username COLLATE NOCASE
                    WHERE users.id IS NULL
                    ) listed_users
                    ORDER BY LOWER(username) ASC""",
                )
            else:
                rows = self._fetchall(
                    connection,
                    """SELECT vp.username, vp.class_name, vp.active,
                              IF(only_users.username IS NULL, 0, 1) AS vp_only,
                              COALESCE(u.status, 'ACTIVE') AS calendar_status
                    FROM vp_users vp
                    LEFT JOIN vp_only_users only_users ON only_users.user_id = vp.id
                    LEFT JOIN users u ON LOWER(u.username) = LOWER(vp.username)
                    UNION
                    SELECT u.username, COALESCE(vp.class_name, ''), 1,
                              0 AS vp_only, COALESCE(u.status, 'ACTIVE') AS calendar_status
                    FROM users u
                    LEFT JOIN vp_users vp ON LOWER(vp.username) = LOWER(u.username)
                    WHERE vp.id IS NULL
                    ORDER BY username ASC""",
                )
        users = []
        for row in rows:
            vp_only = bool(row["vp_only"])
            calendar_status = row["calendar_status"] or "ACTIVE"
            users.append({
                "username": row["username"],
                "class_name": row["class_name"] or "",
                "kind": "VP-only" if vp_only else "Kalender + VP",
                "status": "BLOCKED" if vp_only and not bool(row["active"]) else calendar_status,
                "vp_only": vp_only,
                "can_set_calendar_status": not vp_only,
                "is_admin": (not vp_only) and calendar_status == "ADMIN",
            })
        return users

    def set_calendar_status(self, username: str, status: str) -> None:
        username = validate_username(username)
        if status not in {"ACTIVE", "READ_ONLY", "BLOCKED"}:
            raise ValueError("Ungültiger Status.")
        with self._connection() as connection:
            if self._backend == "sqlite":
                cursor = self._execute(
                    connection,
                    "UPDATE calendar_users SET status = ? WHERE username = ? COLLATE NOCASE AND status <> 'ADMIN'",
                    (status, username),
                )
            else:
                cursor = self._execute(
                    connection,
                    "UPDATE users SET status = ? WHERE LOWER(username) = LOWER(?) AND status <> 'ADMIN'",
                    (status, username),
                )
            changed = cursor.rowcount
            if self._backend == "mysql":
                cursor.close()
            if changed != 1:
                raise ValueError("Kalendernutzer nicht gefunden oder nicht änderbar.")
            if status == "BLOCKED":
                self.delete_user_sessions(username)

    def get_global_calendar_categories(self) -> list[dict[str, Any]]:
        table = "calendar_event_categories" if self._backend == "sqlite" else "event_categories"
        with self._connection() as connection:
            rows = self._fetchall(connection, f"SELECT id, name, color, sort_order FROM {table} ORDER BY sort_order ASC, name ASC")
        return [{"id": row["id"], "name": row["name"], "color": row["color"], "sort_order": int(row["sort_order"])} for row in rows]

    def save_global_calendar_category(self, category_id: str, name: str, color: str) -> None:
        category_id = category_id.strip().upper().replace(" ", "_")
        name = name.strip()
        color = color.strip()
        if not category_id or len(category_id) > 64 or not name or len(name) > 64:
            raise ValueError("Ungültige Kategorie.")
        if not (len(color) == 7 and color.startswith("#") and all(char in "0123456789abcdefABCDEF" for char in color[1:])):
            raise ValueError("Ungültige Farbe.")
        table = "calendar_event_categories" if self._backend == "sqlite" else "event_categories"
        with self._connection() as connection:
            order_row = self._fetchone(connection, f"SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM {table}")
            sort_order = int(order_row["next_order"] or 0)
            if self._backend == "sqlite":
                self._run(
                    connection,
                    f"""INSERT INTO {table}(id, name, color, sort_order) VALUES (?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET name = excluded.name, color = excluded.color""",
                    (category_id, name, color, sort_order),
                )
            else:
                self._run(
                    connection,
                    f"""INSERT INTO {table}(id, name, color, sort_order) VALUES (?, ?, ?, ?)
                    ON DUPLICATE KEY UPDATE name = VALUES(name), color = VALUES(color)""",
                    (category_id, name, color, sort_order),
                )

    def delete_global_calendar_category(self, category_id: str) -> None:
        category_id = category_id.strip()
        if not category_id or len(category_id) > 64:
            raise ValueError("Ungültige Kategorie.")
        table = "calendar_event_categories" if self._backend == "sqlite" else "event_categories"
        with self._connection() as connection:
            self._run(connection, f"DELETE FROM {table} WHERE id = ?", (category_id,))

    def get_global_courses(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = self._fetchall(connection, "SELECT id, name, teacher, type, sort_order FROM courses ORDER BY sort_order ASC, name ASC")
        return [{"id": row["id"], "name": row["name"], "teacher": row["teacher"], "type": row["type"], "sort_order": int(row["sort_order"])} for row in rows]

    def save_global_course(self, course_id: str, name: str, teacher: str, course_type: str) -> None:
        course_id = course_id.strip() or name.strip()
        name = name.strip() or course_id
        teacher = teacher.strip()
        if not course_id or len(course_id) > 64 or not name or len(name) > 64 or len(teacher) > 64:
            raise ValueError("Ungültiger Kurs.")
        if course_type not in {"LK", "GK", "AG"}:
            raise ValueError("Ungültiger Kurstyp.")
        with self._connection() as connection:
            order_row = self._fetchone(connection, "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM courses")
            sort_order = int(order_row["next_order"] or 0)
            if self._backend == "sqlite":
                self._run(
                    connection,
                    """INSERT INTO courses(id, name, teacher, type, sort_order) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET name = excluded.name, teacher = excluded.teacher, type = excluded.type""",
                    (course_id, name, teacher, course_type, sort_order),
                )
            else:
                self._run(
                    connection,
                    """INSERT INTO courses(id, name, teacher, type, sort_order) VALUES (?, ?, ?, ?, ?)
                    ON DUPLICATE KEY UPDATE name = VALUES(name), teacher = VALUES(teacher), type = VALUES(type)""",
                    (course_id, name, teacher, course_type, sort_order),
                )

    def delete_global_course(self, course_id: str) -> None:
        course_id = course_id.strip()
        if not course_id or len(course_id) > 64:
            raise ValueError("Ungültiger Kurs.")
        with self._connection() as connection:
            self._run(connection, "DELETE FROM courses WHERE id = ?", (course_id,))

    def reorder_global_courses(self, course_ids: list[str], course_types: list[str]) -> None:
        if len(course_ids) != len(course_types) or len(course_ids) > 500:
            raise ValueError("Ungültige Kurs-Reihenfolge.")
        normalized: list[tuple[str, str]] = []
        seen: set[str] = set()
        for course_id, course_type in zip(course_ids, course_types):
            course_id = str(course_id).strip()
            course_type = str(course_type).strip().upper()
            if not course_id or len(course_id) > 64 or course_type not in {"LK", "GK", "AG"}:
                raise ValueError("Ungültige Kurs-Reihenfolge.")
            lowered = course_id.lower()
            if lowered in seen:
                raise ValueError("Doppelte Kurse in der Reihenfolge.")
            seen.add(lowered)
            normalized.append((course_id, course_type))
        if not normalized:
            return
        with self._connection() as connection:
            existing_rows = self._fetchall(
                connection,
                f"SELECT id FROM courses WHERE LOWER(id) IN ({','.join('?' for _ in normalized)})",
                tuple(course_id.lower() for course_id, _course_type in normalized),
            )
            existing = {str(row["id"]).lower() for row in existing_rows}
            if existing != {course_id.lower() for course_id, _course_type in normalized}:
                raise ValueError("Mindestens ein Kurs existiert nicht.")
            for sort_order, (course_id, course_type) in enumerate(normalized):
                self._run(
                    connection,
                    "UPDATE courses SET sort_order = ?, type = ? WHERE LOWER(id) = LOWER(?)",
                    (sort_order, course_type, course_id),
                )

    def change_vp_only_pin(self, username: str, new_pin: str) -> None:
        username = validate_username(username)
        validate_pin(new_pin)
        with self._connection() as connection:
            cursor = self._execute(
                connection,
                "UPDATE vp_only_users SET pin_hash = ?, must_change_pin = 0 WHERE LOWER(username) = LOWER(?) AND active = 1",
                (self._hasher.hash(new_pin), username),
            )
            changed = cursor.rowcount
            if self._backend == "mysql":
                cursor.close()
            if changed != 1:
                raise ValueError("PIN kann nur für aktive VP-only-Nutzer geändert werden.")

    def admin_set_user_pin(self, username: str, new_pin: str) -> None:
        username = validate_username(username)
        validate_pin(new_pin)
        with self._connection() as connection:
            if self._backend == "sqlite":
                vp_only_row = self._fetchone(
                    connection,
                    """SELECT only_users.user_id
                    FROM vp_only_users only_users
                    JOIN users ON users.id = only_users.user_id
                    WHERE users.username = ? COLLATE NOCASE AND users.active = 1 AND only_users.active = 1""",
                    (username,),
                )
                if vp_only_row:
                    self._run(
                        connection,
                        "UPDATE vp_only_users SET pin_hash = ?, must_change_pin = 1 WHERE user_id = ?",
                        (self._hasher.hash(new_pin), vp_only_row["user_id"]),
                    )
                    self._run(connection, "DELETE FROM sessions WHERE user_id = ?", (vp_only_row["user_id"],))
                    self._run(connection, "DELETE FROM vp_only_sessions WHERE username = ? COLLATE NOCASE", (username,))
                    return
                current = self._fetchone(
                    connection,
                    "SELECT preferences FROM calendar_users WHERE username = ? COLLATE NOCASE",
                    (username,),
                )
                if not current:
                    raise ValueError("Benutzer nicht gefunden.")
                try:
                    preferences = json.loads(current["preferences"] or "{}")
                except json.JSONDecodeError:
                    preferences = {}
                preferences["forcePinChange"] = True
                self._run(
                    connection,
                    "UPDATE users SET pin_hash = ? WHERE username = ? COLLATE NOCASE",
                    (self._hasher.hash(new_pin), username),
                )
                self._run(
                    connection,
                    "UPDATE calendar_users SET pin = ?, preferences = ? WHERE username = ? COLLATE NOCASE",
                    (_hash_shared_pin(new_pin), json.dumps(preferences), username),
                )
                self._run(
                    connection,
                    "DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE username = ? COLLATE NOCASE)",
                    (username,),
                )
                self._run(connection, "DELETE FROM vp_only_sessions WHERE username = ? COLLATE NOCASE", (username,))
            else:
                vp_only_row = self._fetchone(
                    connection,
                    """SELECT only_users.user_id
                    FROM vp_only_users only_users
                    JOIN vp_users vp ON vp.id = only_users.user_id
                    LEFT JOIN users u ON LOWER(u.username) = LOWER(vp.username)
                    WHERE LOWER(vp.username) = LOWER(?) AND u.username IS NULL AND vp.active = 1 AND only_users.active = 1""",
                    (username,),
                )
                if vp_only_row:
                    self._run(
                        connection,
                        "UPDATE vp_only_users SET pin_hash = ?, must_change_pin = 1 WHERE user_id = ?",
                        (self._hasher.hash(new_pin), vp_only_row["user_id"]),
                    )
                    self._run(connection, "DELETE FROM vp_only_sessions WHERE LOWER(username) = LOWER(?)", (username,))
                    return
                current = self._fetchone(connection, "SELECT preferences FROM users WHERE LOWER(username) = LOWER(?)", (username,))
                if not current:
                    raise ValueError("Benutzer nicht gefunden.")
                try:
                    preferences = json.loads(current["preferences"] or "{}")
                except json.JSONDecodeError:
                    preferences = {}
                preferences["forcePinChange"] = True
                self._run(
                    connection,
                    "UPDATE users SET pin = ?, preferences = ? WHERE LOWER(username) = LOWER(?)",
                    (_hash_shared_pin(new_pin), json.dumps(preferences), username),
                )
                self._run(connection, "UPDATE vp_users SET pin_hash = '' WHERE LOWER(username) = LOWER(?)", (username,))
                self._run(connection, "DELETE FROM app_sessions WHERE LOWER(username) = LOWER(?)", (username,))
                self._run(connection, "DELETE FROM vp_only_sessions WHERE LOWER(username) = LOWER(?)", (username,))

    def delete_user(self, username: str) -> None:
        username = validate_username(username)
        with self._connection() as connection:
            if self._backend == "sqlite":
                user_id_row = self._fetchone(
                    connection,
                    "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                    (username,),
                )
                cursor = self._execute(connection, "DELETE FROM users WHERE username = ? COLLATE NOCASE", (username,))
                deleted = max(cursor.rowcount, 0)
                cursor = self._execute(connection, "DELETE FROM calendar_users WHERE username = ? COLLATE NOCASE", (username,))
                deleted += max(cursor.rowcount, 0)
                cursor = self._execute(connection, "DELETE FROM vp_only_sessions WHERE username = ? COLLATE NOCASE", (username,))
                deleted += max(cursor.rowcount, 0)
                if user_id_row:
                    cursor = self._execute(connection, "DELETE FROM vp_only_users WHERE user_id = ?", (user_id_row["id"],))
                    deleted += max(cursor.rowcount, 0)
            else:
                cursor = self._execute(connection, "DELETE FROM vp_users WHERE LOWER(username) = LOWER(?)", (username,))
                deleted = max(cursor.rowcount, 0)
                if self._backend == "mysql":
                    cursor.close()
                cursor = self._execute(connection, "DELETE FROM users WHERE LOWER(username) = LOWER(?)", (username,))
                deleted += max(cursor.rowcount, 0)
                if self._backend == "mysql":
                    cursor.close()
                cursor = self._execute(connection, "DELETE FROM vp_only_sessions WHERE LOWER(username) = LOWER(?)", (username,))
                deleted += max(cursor.rowcount, 0)
                if self._backend == "mysql":
                    cursor.close()
                cursor = self._execute(
                    connection,
                    "DELETE FROM app_sessions WHERE LOWER(username) = LOWER(?)",
                    (username,),
                )
                deleted += max(cursor.rowcount, 0)
            if self._backend == "mysql":
                cursor.close()
            if deleted < 1:
                return

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
                self._run(
                    connection,
                    """
                    INSERT INTO users (username, courses, pin, preferences, status)
                    VALUES (?, ?, ?, ?, ?)
                    ON DUPLICATE KEY UPDATE pin = VALUES(pin)
                    """,
                    (username.lower(), "[]", _hash_shared_pin(pin), json.dumps({}), "ACTIVE"),
                )
                self._run(connection, "UPDATE vp_users SET pin_hash = '' WHERE LOWER(username) = LOWER(?)", (username,))
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
                    self._run(connection, "DELETE FROM vp_only_sessions WHERE username = ? COLLATE NOCASE", (username,))
            else:
                cursor = self._execute(connection, "UPDATE vp_users SET active = ? WHERE LOWER(username) = LOWER(?)", (int(active), username))
                rowcount = cursor.rowcount
                if rowcount == 1 and not active:
                    self._run(connection, "DELETE FROM app_sessions WHERE LOWER(username) = LOWER(?)", (username,))
                    self._run(connection, "DELETE FROM vp_only_sessions WHERE LOWER(username) = LOWER(?)", (username,))
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
                user_row = self._fetchone(
                    connection,
                    """SELECT users.*, only_users.pin_hash AS vp_only_pin_hash,
                              only_users.active AS vp_only_active,
                              COALESCE(only_users.must_change_pin, 0) AS must_change_pin,
                              CASE WHEN only_users.username IS NULL THEN 0 ELSE 1 END AS vp_only
                    FROM users
                    LEFT JOIN vp_only_users only_users ON only_users.user_id = users.id
                    WHERE users.username = ? COLLATE NOCASE""",
                    (username,),
                )
                if user_row is not None and bool(user_row["active"]):
                    stored_pin = user_row["vp_only_pin_hash"] if bool(user_row["vp_only"]) else user_row["pin_hash"]
                    valid, needs_rehash = _verify_account_pin(self._hasher, stored_pin, pin)
                    valid = (not bool(user_row["vp_only"]) or bool(user_row["vp_only_active"])) and valid
                    if valid and needs_rehash:
                        if bool(user_row["vp_only"]):
                            self._run(connection, "UPDATE vp_only_users SET pin_hash = ? WHERE user_id = ?", (self._hasher.hash(pin), user_row["id"]))
                        else:
                            self._run(connection, "UPDATE users SET pin_hash = ? WHERE id = ?", (self._hasher.hash(pin), user_row["id"]))
                self._run(
                    connection,
                    "INSERT INTO login_attempts(username, ip_address, attempted_at, successful) VALUES (?, ?, ?, ?)",
                    (username, ip_address, now, int(valid)),
                )
            else:
                user_row = self._fetchone(
                    connection,
                    """
                    SELECT vp.*, u.pin AS calendar_pin, u.status AS calendar_status,
                        u.username AS calendar_username, only_users.pin_hash AS vp_only_pin_hash,
                        only_users.active AS vp_only_active, only_users.must_change_pin,
                        IF(only_users.username IS NULL, 0, 1) AS vp_only
                    FROM vp_users vp
                    LEFT JOIN users u ON LOWER(u.username) = LOWER(vp.username)
                    LEFT JOIN vp_only_users only_users ON only_users.user_id = vp.id
                    WHERE LOWER(vp.username) = LOWER(?)
                    """,
                    (username,),
                )
                if user_row is None:
                    user_row = self._bootstrap_vp_user_from_calendar(connection, username, pin)
                if user_row is not None and bool(user_row["active"]) and (user_row.get("calendar_status") or "ACTIVE") != "BLOCKED":
                    if user_row.get("calendar_username") is not None:
                        calendar_pin = user_row.get("calendar_pin")
                        valid = not calendar_pin or _verify_shared_pin(pin, calendar_pin)
                    elif user_row.get("vp_only_pin_hash") and bool(user_row.get("vp_only_active")):
                        valid, needs_rehash = _verify_account_pin(self._hasher, user_row.get("vp_only_pin_hash", ""), pin)
                        if valid and needs_rehash:
                            self._run(connection, "UPDATE vp_only_users SET pin_hash = ? WHERE user_id = ?", (self._hasher.hash(pin), user_row["id"]))
                    if valid and user_row.get("calendar_username") is None:
                        self._run(
                            connection,
                            "UPDATE vp_users SET pin_hash = '' WHERE id = ?",
                            (user_row["id"],),
                        )
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
        with self._connection() as connection:
            is_vp_only = self._is_vp_only_user_id(connection, user_id)
            if self._backend == "sqlite" and not is_vp_only:
                self._run(
                    connection,
                    "INSERT INTO sessions(token_hash, user_id, csrf_token, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                    (self._token_hash(token), user_id, csrf_token, to_db_time(utcnow() + SESSION_LIFETIME), to_db_time(utcnow())),
                )
            elif self._backend == "sqlite":
                user_row = self._get_user_row_by_id(connection, user_id)
                self._run(
                    connection,
                    "INSERT INTO vp_only_sessions(token_hash, username, csrf_token, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                    (self._token_hash(token), user_row["username"].lower(), csrf_token,
                     to_db_time(utcnow() + SESSION_LIFETIME), to_db_time(utcnow())),
                )
            elif is_vp_only:
                user_row = self._get_user_row_by_id(connection, user_id)
                self._run(
                    connection,
                    "INSERT INTO vp_only_sessions(token_hash, username, csrf_token, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                    (self._token_hash(token), user_row["username"].lower(), csrf_token,
                     (utcnow() + SESSION_LIFETIME).replace(tzinfo=None), utcnow().replace(tzinfo=None)),
                )
            else:
                user_row = self._get_user_row_by_id(connection, user_id)
                self._run(
                    connection,
                    "INSERT INTO app_sessions(token_hash, username, csrf_token, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                    (self._token_hash(token), user_row["username"].lower(), csrf_token,
                     (utcnow() + SESSION_LIFETIME).replace(tzinfo=None), utcnow().replace(tzinfo=None)),
                )
        return token, csrf_token

    def get_session(self, token: str | None) -> Session | None:
        if not token or len(token) > 256:
            return None
        with self._connection() as connection:
            if self._backend == "sqlite":
                row = self._fetchone(
                    connection,
                    """SELECT users.*, sessions.csrf_token, 0 AS vp_only, 0 AS must_change_pin
                    FROM sessions JOIN users ON users.id = sessions.user_id
                    WHERE sessions.token_hash = ? AND sessions.expires_at > ? AND users.active = 1""",
                    (self._token_hash(token), to_db_time(utcnow())),
                )
                if row is None:
                    row = self._fetchone(
                        connection,
                        """SELECT users.*, vp_only_sessions.csrf_token, 1 AS vp_only,
                                  only_users.must_change_pin AS must_change_pin
                        FROM vp_only_sessions
                        JOIN vp_only_users only_users ON only_users.username = vp_only_sessions.username COLLATE NOCASE
                        JOIN users ON users.id = only_users.user_id
                        WHERE vp_only_sessions.token_hash = ? AND vp_only_sessions.expires_at > ?
                          AND users.active = 1 AND only_users.active = 1""",
                        (self._token_hash(token), to_db_time(utcnow())),
                    )
                self._run(connection, "DELETE FROM sessions WHERE expires_at <= ?", (to_db_time(utcnow()),))
                self._run(connection, "DELETE FROM vp_only_sessions WHERE expires_at <= ?", (to_db_time(utcnow()),))
            else:
                shared_session = self._fetchone(
                    connection,
                    """SELECT app_sessions.username, app_sessions.csrf_token, users.status
                    FROM app_sessions
                    JOIN users ON LOWER(users.username) = LOWER(app_sessions.username)
                    WHERE app_sessions.token_hash = ? AND app_sessions.expires_at > ?""",
                    (self._token_hash(token), utcnow().replace(tzinfo=None)),
                )
                row = None
                if shared_session is not None and (shared_session.get("status") or "ACTIVE") != "BLOCKED":
                    row = self._fetchone(
                        connection,
                        "SELECT * FROM vp_users WHERE LOWER(username) = LOWER(?) AND active = 1",
                        (shared_session["username"],),
                    )
                    if row is None:
                        row = self._bootstrap_vp_user_from_calendar(
                            connection, shared_session["username"], None, trusted_session=True,
                        )
                    if row is not None and bool(row["active"]):
                        row["csrf_token"] = shared_session["csrf_token"]
                        row["vp_only"] = 0
                        row["must_change_pin"] = 0
                if row is None:
                    row = self._fetchone(
                        connection,
                        """SELECT vp.*, vp_only_sessions.csrf_token, 1 AS vp_only,
                                  only_users.must_change_pin AS must_change_pin
                        FROM vp_only_sessions
                        JOIN vp_only_users only_users ON LOWER(only_users.username) = LOWER(vp_only_sessions.username)
                        JOIN vp_users vp ON vp.id = only_users.user_id
                        WHERE vp_only_sessions.token_hash = ? AND vp_only_sessions.expires_at > ?
                          AND vp.active = 1 AND only_users.active = 1""",
                        (self._token_hash(token), utcnow().replace(tzinfo=None)),
                    )
                self._run(connection, "DELETE FROM app_sessions WHERE expires_at <= ?", (utcnow().replace(tzinfo=None),))
                self._run(connection, "DELETE FROM vp_only_sessions WHERE expires_at <= ?", (utcnow().replace(tzinfo=None),))
        return Session(self._user_from_row(row), row["csrf_token"]) if row else None

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        with self._connection() as connection:
            if self._backend == "sqlite":
                self._run(connection, "DELETE FROM sessions WHERE token_hash = ?", (self._token_hash(token),))
                self._run(connection, "DELETE FROM vp_only_sessions WHERE token_hash = ?", (self._token_hash(token),))
            else:
                self._run(connection, "DELETE FROM app_sessions WHERE token_hash = ?", (self._token_hash(token),))
                self._run(connection, "DELETE FROM vp_only_sessions WHERE token_hash = ?", (self._token_hash(token),))

    def delete_user_sessions(self, username: str) -> None:
        username = validate_username(username)
        with self._connection() as connection:
            if self._backend == "sqlite":
                self._run(
                    connection,
                    "DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE username = ? COLLATE NOCASE)",
                    (username,),
                )
                self._run(connection, "DELETE FROM vp_only_sessions WHERE username = ? COLLATE NOCASE", (username,))
            else:
                self._run(connection, "DELETE FROM app_sessions WHERE LOWER(username) = LOWER(?)", (username,))
                self._run(connection, "DELETE FROM vp_only_sessions WHERE LOWER(username) = LOWER(?)", (username,))

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
            daily_summary_day_before=bool(settings.daily_summary_day_before),
            calendar_notifications_enabled=settings.calendar_notifications_enabled,
            calendar_notification_time=self._normalize_notification_time(settings.calendar_notification_time),
            calendar_notification_times={
                event_type.strip(): self._normalize_notification_time(value)
                for event_type, value in (settings.calendar_notification_times or {}).items()
                if event_type.strip()
            },
            calendar_notification_days_before=max(
                0,
                min(MAX_CALENDAR_NOTIFICATION_DAYS_BEFORE, int(settings.calendar_notification_days_before)),
            ),
            calendar_notification_days_before_by_type={
                event_type.strip(): max(0, min(MAX_CALENDAR_NOTIFICATION_DAYS_BEFORE, int(value)))
                for event_type, value in (settings.calendar_notification_days_before_by_type or {}).items()
                if event_type.strip()
            },
            calendar_notification_types=tuple(
                dict.fromkeys(event_type.strip() for event_type in settings.calendar_notification_types if event_type.strip())
            ),
        )
        payload = json.dumps(
            {
                "lesson_notifications_enabled": normalized.lesson_notifications_enabled,
                "lesson_notification_times": list(normalized.lesson_notification_times),
                "daily_summary_day_before": normalized.daily_summary_day_before,
                "calendar_notifications_enabled": normalized.calendar_notifications_enabled,
                "calendar_notification_time": normalized.calendar_notification_time,
                "calendar_notification_times": normalized.calendar_notification_times,
                "calendar_notification_days_before": normalized.calendar_notification_days_before,
                "calendar_notification_days_before_by_type": normalized.calendar_notification_days_before_by_type,
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
        return {}, False

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

    def replace_calendar_course_ids(self, username: str, course_ids: set[str]) -> None:
        """Spiegelt eine VP-Fachauswahl in die Kursauswahl des Kalenders."""
        normalized = sorted({value.strip() for value in course_ids if value.strip()})
        with self._connection() as connection:
            self._run(
                connection,
                f"UPDATE {self._calendar_users_table()} SET courses = ? WHERE LOWER(username) = LOWER(?)",
                (json.dumps(normalized), username),
            )

    def _private_calendar(self, username: str) -> dict[str, list[dict[str, Any]]]:
        if self._backend != "mysql":
            return {"categories": [], "events": []}
        secret = os.getenv("CALENDAR_PRIVATE_DATA_KEY") or os.getenv("APP_ENCRYPTION_KEY", "")
        if not secret:
            return {"categories": [], "events": []}
        with self._connection() as connection:
            row = self._fetchone(connection, "SELECT nonce, ciphertext, auth_tag FROM user_private_calendar_data WHERE LOWER(username)=LOWER(?)", (username,))
        if row is None:
            return {"categories": [], "events": []}
        try:
            key = hashlib.sha256(secret.encode("utf-8")).digest()
            plaintext = AESGCM(key).decrypt(bytes(row["nonce"]), bytes(row["ciphertext"]) + bytes(row["auth_tag"]), username.lower().encode("utf-8"))
            value = json.loads(plaintext.decode("utf-8"))
            return {"categories": value.get("categories", []) if isinstance(value, dict) else [], "events": value.get("events", []) if isinstance(value, dict) else []}
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return {"categories": [], "events": []}

    def get_calendar_event_types(self, username: str | None = None) -> list[CalendarEventTypeOption]:
        with self._connection() as connection:
            category_rows = self._fetchall(
                connection,
                f"SELECT id, name FROM {self._calendar_event_categories_table()} ORDER BY sort_order ASC, name ASC",
            )
            options = [CalendarEventTypeOption(id=row["id"], label=row["name"]) for row in category_rows]
            if username:
                private = self._private_calendar(username).get("categories", [])
                options.extend(CalendarEventTypeOption(id=str(row["id"]), label=str(row["name"])) for row in private if row.get("id") and row.get("name"))
            if options:
                return options
            event_rows = self._fetchall(
                connection,
                f"SELECT DISTINCT type FROM {self._calendar_events_table()} ORDER BY type ASC",
            )
        options = [CalendarEventTypeOption(id=row["type"], label=row["type"]) for row in event_rows if row["type"]]
        if username:
            private = self._private_calendar(username).get("categories", [])
            options.extend(CalendarEventTypeOption(id=str(row["id"]), label=str(row["name"])) for row in private if row.get("id") and row.get("name"))
        return options

    def get_calendar_events(self, username: str | None = None) -> list[CalendarEvent]:
        with self._connection() as connection:
            rows = self._fetchall(
                connection,
                f"""
                SELECT id, title, date, end_date, start_time, end_time, course_id, type, description, author
                FROM {self._calendar_events_table()}
                ORDER BY date ASC, COALESCE(start_time, ''), title ASC
                """,
            )
        events = [
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
        if username:
            private = self._private_calendar(username).get("events", [])
            events.extend(CalendarEvent(id=str(event.get("id")), title=str(event.get("title", "")), date=str(event.get("date")), end_date=event.get("endDate"), start_time=event.get("startTime"), end_time=event.get("endTime"), course_id="ALLGEMEIN", event_type=str(event.get("type", "")), description=str(event.get("description", "")), author=username) for event in private if event.get("id") and event.get("date") and event.get("type"))
        return events

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
            users_table = self._users_table()
            rows = self._fetchall(
                connection,
                f"""
                SELECT profile.*,
                       CASE WHEN only_users.username IS NULL THEN 0 ELSE 1 END AS vp_only,
                       COALESCE(only_users.must_change_pin, 0) AS must_change_pin
                FROM {users_table} profile
                LEFT JOIN vp_only_users only_users ON only_users.user_id = profile.id
                WHERE profile.active = 1
                  AND (only_users.username IS NULL OR only_users.active = 1)
                """,
            )
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
        settings_by_user = {
            int(row["user_id"]): self._settings_from_row(row)
            for row in settings_rows
        }
        recipients: list[NotificationRecipient] = []
        for row in rows:
            user = self._user_from_row(row)
            selected_classes = tuple(selected_classes_by_user.get(user.id) or [user.class_name])
            subject_selections = selections_by_user.get(user.id)
            recipients.append(
                NotificationRecipient(
                    user=user,
                    selected_classes=selected_classes,
                    subject_selections={class_name: set(keys) for class_name, keys in (subject_selections or {}).items()},
                    notify_settings=settings_by_user.get(user.id, NotifySettings()),
                    calendar_courses=set() if user.vp_only else self.get_calendar_course_ids(user.username),
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

    def delivery_deletion_candidates(self, cutoff: datetime, limit: int = 200) -> list[tuple[User, str]]:
        cutoff_text = to_db_time(cutoff)
        with self._connection() as connection:
            if self._backend == "sqlite":
                rows = self._fetchall(
                    connection,
                    """SELECT users.*, deliveries.event_key,
                              CASE WHEN only_users.username IS NULL THEN 0 ELSE 1 END AS vp_only,
                              COALESCE(only_users.must_change_pin, 0) AS must_change_pin
                    FROM notification_deliveries deliveries
                    JOIN users ON users.id = deliveries.user_id
                    LEFT JOIN vp_only_users only_users ON only_users.user_id = users.id
                    WHERE deliveries.deleted_at IS NULL AND deliveries.delivered_at <= ?
                    ORDER BY deliveries.delivered_at ASC
                    LIMIT ?""",
                    (cutoff_text, limit),
                )
            else:
                rows = self._fetchall(
                    connection,
                    """SELECT vp_users.*, deliveries.event_key,
                              IF(only_users.username IS NULL, 0, 1) AS vp_only,
                              COALESCE(only_users.must_change_pin, 0) AS must_change_pin
                    FROM vp_notification_deliveries deliveries
                    JOIN vp_users ON vp_users.id = deliveries.user_id
                    LEFT JOIN vp_only_users only_users ON only_users.user_id = vp_users.id
                    WHERE deliveries.deleted_at IS NULL AND deliveries.delivered_at <= ?
                    ORDER BY deliveries.delivered_at ASC
                    LIMIT ?""",
                    (cutoff_text, limit),
                )
        return [(self._user_from_row(row), str(row["event_key"])) for row in rows]

    def mark_delivery_deleted(self, user_id: int, event_key: str) -> None:
        table = "notification_deliveries" if self._backend == "sqlite" else "vp_notification_deliveries"
        with self._connection() as connection:
            self._run(
                connection,
                f"UPDATE {table} SET deleted_at = ? WHERE user_id = ? AND event_key = ?",
                (to_db_time(utcnow()), user_id, event_key),
            )
