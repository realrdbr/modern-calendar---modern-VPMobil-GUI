"""VPrintfy-Webserver mit Anmeldung und persönlichem ntfy-Versand."""

from __future__ import annotations

from datetime import date, datetime
import json
import os
from pathlib import Path
from threading import Event, Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import time
from urllib.parse import parse_qs, urlparse
from urllib.parse import quote

from dotenv import load_dotenv

from account_page import render_login, render_subscriptions
from accounts import AccountStore, NotifySettings, Session
from cli_runtime import running_in_container, uses_internal_mariadb
from ntfy.service import NtfyService, resolve_ntfy_internal_url
from plan_page import get_selected_subject_cookie_name, get_week_plans_for_page, get_week_version, render_plan_page
from rooms_page import render_rooms_page
from subscriptions import SubscriptionNotifier, available_class_names_from_plans, subject_key, subject_options_from_plans
from teacher_page import render_teacher_page
from vp_data import ResourceNotFound, Unauthorized, fetch_plan, find_free_rooms_in_plan, get_plan_for_page, get_subject_catalog_plans, log
from web_utils import format_week_value, join_cookie_list, make_cookie, parse_cookie_header, parse_hour, parse_week, query_value, query_values, redirect, send_html, split_cookie_list


load_dotenv()
ROOT = Path(__file__).resolve().parent
SESSION_COOKIE = "vpmobil_session"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() in {"1", "true", "yes"}


def resolve_bind_host() -> str:
    requested = os.getenv("BIND_HOST", os.getenv("HOST", "127.0.0.1"))
    if Path("/.dockerenv").exists() and requested in {"127.0.0.1", "localhost"}:
        return "0.0.0.0"
    return requested


def build_store() -> AccountStore:
    database_url = os.getenv("APP_DATABASE_URL", "").strip()
    use_sqlite_fallback = uses_internal_mariadb() and not running_in_container()

    if not database_url and not use_sqlite_fallback and os.getenv("DB_HOST") and os.getenv("DB_USER") and os.getenv("DB_NAME"):
        password = quote(os.getenv("DB_PASSWORD", ""), safe="")
        user = quote(os.getenv("DB_USER", ""), safe="")
        host = os.getenv("DB_HOST", "")
        port = os.getenv("DB_PORT", "3306")
        name = quote(os.getenv("DB_NAME", ""), safe="")
        database_url = f"mariadb://{user}:{password}@{host}:{port}/{name}"

    if database_url and not use_sqlite_fallback:
        retries = max(1, int(os.getenv("DB_CONNECT_RETRIES", "20")))
        wait_seconds = max(1, int(os.getenv("DB_CONNECT_WAIT_SECONDS", "2")))
        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                return AccountStore(database_url, os.getenv("APP_ENCRYPTION_KEY", ""))
            except Exception as error:
                last_error = error
                if attempt == retries:
                    break
                log(f"Datenbank noch nicht bereit (Versuch {attempt}/{retries}): {error}. Warte {wait_seconds}s ...")
                time.sleep(wait_seconds)
        raise RuntimeError(f"Datenbankverbindung fehlgeschlagen: {last_error}") from last_error

    path = Path(os.getenv("APP_DATABASE", str(ROOT / "data" / "vpmobil.sqlite3")))
    if use_sqlite_fallback:
        log(
            "Docker-interne MariaDB-Konfiguration außerhalb des Containers erkannt. "
            f"Nutze stattdessen SQLite unter {path}."
        )
    return AccountStore(path, os.getenv("APP_ENCRYPTION_KEY", ""))


class NotificationWorker(Thread):
    def __init__(self, store: AccountStore, stop_event: Event):
        super().__init__(name="vpmobil-notifications", daemon=True)
        self.store = store
        self.stop_event = stop_event
        self.interval = max(15, int(os.getenv("NOTIFICATION_INTERVAL_SECONDS", "60")))
        self.notifier = SubscriptionNotifier(store, resolve_ntfy_internal_url())

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                sent = self.notifier.poll_once(fetch_plan(date.today()), datetime.now())
                if sent:
                    log(f"{sent} persönliche ntfy-Benachrichtigung(en) gesendet.")
            except Exception as error:
                log(f"Benachrichtigungs-Worker fehlgeschlagen: {error}")
            self.stop_event.wait(self.interval)


class AppRequestHandler(BaseHTTPRequestHandler):
    """Weboberfläche; Anmeldedaten und Fachauswahl liegen ausschließlich serverseitig."""

    store: AccountStore

    def _cookies(self) -> dict[str, str]:
        return parse_cookie_header(self.headers.get("Cookie"))

    def _session(self) -> Session | None:
        return self.store.get_session(self._cookies().get(SESSION_COOKIE))

    def _client_ip(self) -> str:
        # X-Forwarded-For wird bewusst nicht blind vertraut; davor gehört ein Reverse Proxy.
        return self.client_address[0]

    def _post_data(self) -> dict[str, list[str]]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length < 0 or length > 16_384:
            raise ValueError("Ungültige Formulargröße.")
        if not self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
            raise ValueError("Ungültiger Formulartyp.")
        return parse_qs(self.rfile.read(length).decode("utf-8", errors="strict"), keep_blank_values=True)

    @staticmethod
    def _field(data: dict[str, list[str]], name: str) -> str:
        return data.get(name, [""])[0]

    def _require_session(self) -> Session | None:
        session = self._session()
        if session is None:
            redirect(self, "/login")
        return session

    def _validate_csrf(self, session: Session, data: dict[str, list[str]]) -> bool:
        import secrets
        return secrets.compare_digest(self._field(data, "csrf_token"), session.csrf_token)

    @staticmethod
    def _split_time_list(raw_value: str) -> tuple[str, ...]:
        values = []
        for part in raw_value.replace("\n", ",").split(","):
            cleaned = part.strip()
            if cleaned:
                values.append(cleaned)
        return tuple(values)

    @staticmethod
    def _subject_field_name(class_name: str) -> str:
        return f"subject__{quote(class_name, safe='')}"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/login":
            if self._session():
                redirect(self, "/abos")
            else:
                send_html(self, render_login())
            return
        if parsed.path == "/abos":
            self.handle_subscriptions()
            return
        if parsed.path == "/api/plan-version":
            selected_date = parse_week(query_value(query, "woche"))
            self._send_json({"version": get_week_version(get_week_plans_for_page(selected_date))})
            return
        if parsed.path == "/api/room-version":
            from web_utils import parse_date
            plan = get_plan_for_page(parse_date(query_value(query, "datum")))
            self._send_json({"version": str(getattr(plan, "zeitstempel", "") or "")})
            return
        if parsed.path == "/raeume":
            self.handle_rooms_page(query)
            return
        if parsed.path == "/lehrer":
            self.handle_teacher_page(query)
            return
        self.handle_plan_page(query)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            data = self._post_data()
        except (UnicodeDecodeError, ValueError):
            self.send_error(400, "Ungültige Anfrage")
            return
        if path == "/login":
            user = self.store.authenticate(self._field(data, "username"), self._field(data, "pin"), self._client_ip())
            if user is None:
                send_html(self, render_login("Anmeldung nicht möglich. Prüfe Benutzername und PIN oder versuche es später erneut."))
                return
            token, _csrf = self.store.create_session(user.id)
            redirect(self, "/abos", [make_cookie(SESSION_COOKIE, token, max_age=14 * 86400, http_only=True, secure=COOKIE_SECURE)])
            return
        session = self._require_session()
        if session is None:
            return
        if not self._validate_csrf(session, data):
            self.send_error(403, "Ungültige Sicherheitsprüfung")
            return
        if path == "/abos/test":
            try:
                SubscriptionNotifier(self.store, resolve_ntfy_internal_url()).send_user_test(session.user)
                self.render_subscriptions(session, test_sent=True)
            except Exception:
                self.render_subscriptions(session, error="Die Testbenachrichtigung konnte nicht gesendet werden. Prüfe die ntfy-Verbindung.")
            return
        if path == "/abos":
            try:
                catalog_plans = get_subject_catalog_plans()
                available_classes = set(available_class_names_from_plans(catalog_plans))
                selected_classes = set(data.get("class_name", []))
                if not selected_classes:
                    raise ValueError("Bitte wähle mindestens eine Klasse aus.")
                if not selected_classes <= available_classes:
                    raise ValueError("Die Klassenauswahl passt nicht zum aktuellen Stundenplan.")
                subject_selections: dict[str, set[str]] = {}
                for class_name in selected_classes:
                    allowed = {option.key for option in subject_options_from_plans(catalog_plans, class_name)}
                    field_name = self._subject_field_name(class_name)
                    selected_subjects = set(data.get(field_name, []))
                    if not selected_subjects <= allowed:
                        raise ValueError(f"Die Fachauswahl für Klasse {class_name} passt nicht zum aktuellen Stundenplan.")
                    subject_selections[class_name] = selected_subjects
                lesson_times = tuple(
                    value.strip()
                    for value in data.get("lesson_notification_time", [])
                    if value.strip()
                )
                # Alte Clients/Formulare bleiben während eines rollenden Deployments kompatibel.
                if not lesson_times:
                    lesson_times = self._split_time_list(self._field(data, "lesson_notification_times"))
                if not lesson_times:
                    raise ValueError("Bitte hinterlege mindestens eine Uhrzeit für Stundenbenachrichtigungen.")
                event_type_options = self.store.get_calendar_event_types()
                allowed_event_types = {option.id for option in event_type_options}
                selected_event_types = tuple(dict.fromkeys(data.get("calendar_event_type", [])))
                if not set(selected_event_types) <= allowed_event_types:
                    raise ValueError("Die ausgewählten Kalender-Kategorien sind ungültig.")
                settings = NotifySettings(
                    lesson_notifications_enabled=self._field(data, "lesson_notifications_enabled") == "on",
                    lesson_notification_times=lesson_times,
                    calendar_notifications_enabled=self._field(data, "calendar_notifications_enabled") == "on",
                    calendar_notification_time=self._field(data, "calendar_notification_time").strip() or "16:00",
                    calendar_notification_days_before=int(self._field(data, "calendar_notification_days_before") or "1"),
                    calendar_notification_types=selected_event_types,
                )
                if settings.calendar_notifications_enabled and not settings.calendar_notification_types:
                    raise ValueError("Bitte wähle mindestens eine Kalender-Kategorie aus.")
                self.store.save_subscription_preferences(session.user.id, selected_classes, subject_selections, settings)
                self.render_subscriptions(session, saved=True)
            except Exception as error:
                self.render_subscriptions(session, error=str(error))
            return
        if path == "/logout":
            self.store.delete_session(self._cookies().get(SESSION_COOKIE))
            redirect(self, "/login", [make_cookie(SESSION_COOKIE, "", max_age=0, http_only=True, secure=COOKIE_SECURE)])
            return
        self.send_error(404)

    def _send_json(self, payload: dict[str, str]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    def render_subscriptions(
        self, session: Session, *, saved: bool = False, test_sent: bool = False, error: str | None = None,
    ) -> None:
        try:
            catalog_plans = get_subject_catalog_plans()
            class_options = available_class_names_from_plans(catalog_plans)
            subject_options_by_class = {
                class_name: subject_options_from_plans(catalog_plans, class_name)
                for class_name in class_options
            }
            selected_classes, _ = self.store.get_selected_classes(session.user.id, session.user.class_name)
            stored_subjects, has_subjects = self.store.get_subject_selections(session.user.id, session.user.class_name)
            if has_subjects:
                selected_subjects_by_class = stored_subjects
            else:
                calendar_courses = self.store.get_calendar_course_ids(session.user.username)
                selected_subjects_by_class = {}
                for class_name in selected_classes:
                    allowed_keys = {option.key for option in subject_options_by_class.get(class_name, [])}
                    defaults = {
                        key for course_id in calendar_courses
                        if (key := subject_key(course_id)) in allowed_keys
                    }
                    selected_subjects_by_class[class_name] = defaults
            settings, has_settings = self.store.load_notify_settings(session.user.id)
            event_type_options = self.store.get_calendar_event_types()
            if not has_settings and not settings.calendar_notification_types:
                settings = NotifySettings(
                    lesson_notifications_enabled=settings.lesson_notifications_enabled,
                    lesson_notification_times=settings.lesson_notification_times,
                    calendar_notifications_enabled=settings.calendar_notifications_enabled,
                    calendar_notification_time=settings.calendar_notification_time,
                    calendar_notification_days_before=settings.calendar_notification_days_before,
                    calendar_notification_types=tuple(option.id for option in event_type_options),
                )
        except Exception as exception:
            class_options, subject_options_by_class, selected_classes, selected_subjects_by_class = [], {}, (session.user.class_name,), {}
            settings, event_type_options = NotifySettings(), []
            error = error or f"Kursliste konnte nicht geladen werden: {exception}"
        send_html(
            self,
            render_subscriptions(
                session.user,
                class_options,
                selected_classes,
                subject_options_by_class,
                selected_subjects_by_class,
                settings,
                event_type_options,
                session.csrf_token,
                os.getenv("NTFY_PUBLIC_URL", "http://127.0.0.1:8090"),
                saved,
                error,
                test_sent,
            ),
        )

    def handle_subscriptions(self) -> None:
        session = self._require_session()
        if session:
            self.render_subscriptions(session)

    def handle_plan_page(self, query: dict[str, list[str]]) -> None:
        selected_date = parse_week(query_value(query, "woche"))
        cookies = self._cookies()
        selected_class = query_value(query, "klasse") or cookies.get("selected_class")
        selected_subjects: list[str] = []
        headers: list[str] = []
        if query_value(query, "klasse_clear") == "1":
            headers.append(make_cookie("selected_class", "", max_age=0))
            redirect(self, f"/?woche={format_week_value(selected_date)}", headers)
            return
        subject_cookie_name = get_selected_subject_cookie_name(selected_class) if selected_class else None
        if selected_class and subject_cookie_name:
            selected_subjects = query_values(query, "fach") or split_cookie_list(cookies.get(subject_cookie_name))
        if query_value(query, "fach_clear") == "1":
            selected_subjects = []
            if subject_cookie_name:
                headers.append(make_cookie(subject_cookie_name, "", max_age=0))
        if selected_class:
            headers.append(make_cookie("selected_class", selected_class))
        if selected_class and subject_cookie_name and "fach" in query:
            headers.append(make_cookie(subject_cookie_name, join_cookie_list(selected_subjects)))
        try:
            html = render_plan_page(selected_date, selected_class, selected_subjects)
        except ResourceNotFound:
            html = render_plan_page(selected_date, selected_class, selected_subjects, error_message="Für diese Woche wurden keine Vertretungsplandaten gefunden.")
        except Unauthorized:
            html = render_plan_page(selected_date, selected_class, selected_subjects, error_message="Die Zugangsdaten sind ungültig oder haben keinen Zugriff auf diese Daten.")
        except Exception as error:
            html = render_plan_page(selected_date, selected_class, selected_subjects, error_message=f"Beim Laden der Daten ist ein Fehler aufgetreten: {error}")
        send_html(self, html, headers)

    def handle_teacher_page(self, query: dict[str, list[str]]) -> None:
        selected_date = parse_week(query_value(query, "woche"))
        cookies = self._cookies()
        teacher = query_value(query, "lehrer") or cookies.get("selected_teacher")
        headers: list[str] = []

        if query_value(query, "lehrer_clear") == "1":
            teacher = None
            headers.append(make_cookie("selected_teacher", "", max_age=0))
            redirect(self, f"/lehrer?woche={format_week_value(selected_date)}", headers)
            return

        if teacher:
            headers.append(make_cookie("selected_teacher", teacher))
        try:
            send_html(self, render_teacher_page(selected_date, teacher), headers)
        except Exception as error:
            send_html(
                self,
                render_teacher_page(selected_date, teacher, error_message=f"Beim Laden der Daten ist ein Fehler aufgetreten: {error}"),
                headers,
            )

    def handle_rooms_page(self, query: dict[str, list[str]]) -> None:
        from web_utils import parse_date
        selected_date, selected_hour = parse_date(query_value(query, "datum")), parse_hour(query_value(query, "stunde"))
        try:
            plan = get_plan_for_page(selected_date)
            html = render_rooms_page(selected_date, selected_hour, find_free_rooms_in_plan(plan, selected_hour), plan_version=str(getattr(plan, "zeitstempel", "") or ""))
        except Exception as error:
            html = render_rooms_page(selected_date, selected_hour, None, error_message=f"Beim Laden der Daten ist ein Fehler aufgetreten: {error}")
        send_html(self, html)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> None:
    store = build_store()
    NtfyService(ROOT).ensure_running()
    AppRequestHandler.store = store
    host = resolve_bind_host()
    port = int(os.getenv("VP_PORT", os.getenv("PORT", "8000")))
    server = ThreadingHTTPServer((host, port), AppRequestHandler)
    stop_event = Event()
    worker = NotificationWorker(store, stop_event)
    worker.start()
    print(f"Webanwendung läuft unter http://{host}:{port}")
    print("Ankündigungen: /abos · Anmeldung: /login")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer wird beendet.")
    finally:
        stop_event.set()
        server.server_close()


if __name__ == "__main__":
    main()
