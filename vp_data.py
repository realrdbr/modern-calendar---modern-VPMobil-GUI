import json
import os
import pickle
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock, Thread
from time import monotonic

from dotenv import load_dotenv

from vpmobil import ResourceNotFound, Standardpfade, Unauthorized, VertretungsplanZugang
from vpmobil.weekly import fetch_weekly_plans

load_dotenv()

# Zugangsdaten für den Vertretungsplan.
SCHULNUMMER = os.getenv("SCHULNUMMER")
BENUTZERNAME = os.getenv("BENUTZERNAME")
PASSWORT = os.getenv("PASSWORT")


# Der Cache wird standardmäßig lokal im Projektordner gespeichert. Die mobile
# App setzt VPMOBIL_CACHE_DIR auf ihren privaten, plattformgerechten Ordner.
CACHE_DIR = Path(os.getenv("VPMOBIL_CACHE_DIR", ".vp_cache"))
CACHE_DAYS = 7
PAGE_REFRESH_INTERVAL_SECONDS = 30
WEEKLY_REFRESH_INTERVAL_SECONDS = 300

# Die Seitenansichten verwenden zusätzlich zum Dateicache einen kurzen
# Arbeitsspeicher-Cache. Dadurch kann die Oberfläche sofort den bekannten
# Plan zeigen, während die Aktualisierung im Hintergrund läuft.
_page_plan_cache: dict[date, object | None] = {}
_page_refreshing: set[date] = set()
_page_last_refresh: dict[date, float] = {}
_page_cache_lock = Lock()
_weekly_plan_cache: dict[date, dict[date, object]] = {}
_weekly_refreshing: set[date] = set()
_weekly_last_refresh: dict[date, float] = {}
_weekly_cache_lock = Lock()


# Feste Liste aller Räume, die grundsätzlich als verfügbar betrachtet werden können.
ALL_ROOMS = json.loads(os.getenv("ALL_ROOMS", "[]"))


ALPHABET = "abcdefghijklmnopqrstuvwxyz"


def natural_sort_key(item):
    # Spaltet den String in Zahlen (als int) und Textabschnitte auf
    return [
        int(text) if text.isdigit() else text for text in re.split(r"(\d+)", item)
    ]


def log(message: str) -> None:
    """Gibt eine einheitlich formatierte Konsolenmeldung aus."""

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")


def get_cache_path(selected_date: date) -> Path:
    """Gibt den Dateipfad für den Cache eines bestimmten Datums zurück."""

    return CACHE_DIR / f"{selected_date.isoformat()}.pickle"


def cleanup_cache() -> None:
    """Löscht Cache-Dateien, deren Datum nicht mehr in den letzten sieben Tagen liegt."""

    if not CACHE_DIR.exists():
        return

    oldest_allowed_date = date.today() - timedelta(days=CACHE_DAYS - 1)

    for cache_file in CACHE_DIR.glob("*.pickle"):
        try:
            cached_date = date.fromisoformat(cache_file.stem)
        except ValueError:
            log(f"Ungültige Cache-Datei entfernt: {cache_file}")
            cache_file.unlink(missing_ok=True)
            continue

        if cached_date < oldest_allowed_date:
            log(f"Alter Cache entfernt: {cache_file}")
            cache_file.unlink(missing_ok=True)


def load_plan_from_cache(selected_date: date):
    """Lädt einen Vertretungsplan aus dem lokalen Cache."""

    cache_path = get_cache_path(selected_date)

    if not cache_path.exists():
        log(f"Kein Cache für {selected_date.isoformat()} gefunden.")
        return None

    try:
        with cache_path.open("rb") as file:
            plan = pickle.load(file)

        plan_timestamp = get_plan_timestamp(plan)
        timestamp_text = plan_timestamp.strftime("%d.%m.%Y %H:%M") if plan_timestamp else "unbekannt"

        log(f"Cache für {selected_date.isoformat()} geladen. Planstand: {timestamp_text}")
        return plan
    except (OSError, pickle.PickleError, EOFError):
        log(f"Cache für {selected_date.isoformat()} ist beschädigt und wird gelöscht.")
        cache_path.unlink(missing_ok=True)
        return None


def save_plan_to_cache(selected_date: date, plan) -> None:
    """Speichert einen Vertretungsplan im lokalen Cache."""

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    cache_path = get_cache_path(selected_date)

    with cache_path.open("wb") as file:
        pickle.dump(plan, file)

    plan_timestamp = get_plan_timestamp(plan)
    timestamp_text = plan_timestamp.strftime("%d.%m.%Y %H:%M") if plan_timestamp else "unbekannt"

    log(f"Plan für {selected_date.isoformat()} im Cache gespeichert. Planstand: {timestamp_text}")


def get_plan_timestamp(plan) -> datetime | None:
    """Liest den Veröffentlichungszeitpunkt eines Plans aus."""

    return getattr(plan, "zeitstempel", None)


def is_remote_plan_newer(cached_plan, remote_plan) -> bool:
    """Prüft, ob der frisch geladene Plan neuer als der gecachte Plan ist."""

    cached_timestamp = get_plan_timestamp(cached_plan)
    remote_timestamp = get_plan_timestamp(remote_plan)

    # Wenn es bisher keinen Cache gibt, ist der geladene Plan automatisch relevant.
    if cached_plan is None:
        return True

    # Wenn der neue Plan keinen Zeitstempel hat, überschreiben wir den Cache nicht.
    # So bleibt ein bereits bekannter Plan erhalten.
    if remote_timestamp is None:
        return False

    # Wenn der alte Plan keinen Zeitstempel hat, ist ein neuer Plan mit Zeitstempel besser.
    if cached_timestamp is None:
        return True

    return remote_timestamp > cached_timestamp


def fetch_plan_from_vpmobil(selected_date: date):
    """Lädt den Vertretungsplan für das angegebene Datum direkt von VpMobil."""

    log(f"Prüfe VpMobil auf aktuellen Plan für {selected_date.isoformat()}.")

    access = VertretungsplanZugang(SCHULNUMMER, BENUTZERNAME, PASSWORT)
    plan = access.fetch(selected_date)

    plan_timestamp = get_plan_timestamp(plan)
    timestamp_text = plan_timestamp.strftime("%d.%m.%Y %H:%M") if plan_timestamp else "unbekannt"

    log(f"Plan von VpMobil geladen. Planstand: {timestamp_text}")

    return plan


def fetch_plan(selected_date: date):
    """Lädt den Plan, aktualisiert den Cache bei neueren Daten und nutzt sonst den alten Cache."""

    cleanup_cache()

    cached_plan = load_plan_from_cache(selected_date)

    try:
        remote_plan = fetch_plan_from_vpmobil(selected_date)
    except ResourceNotFound:
        if cached_plan is not None:
            log(f"VpMobil hat keinen Plan für {selected_date.isoformat()} geliefert. Nutze vorhandenen Cache.")
            return cached_plan

        log(f"VpMobil hat keinen Plan für {selected_date.isoformat()} geliefert und es gibt keinen Cache.")
        raise
    except Unauthorized:
        log("VpMobil-Zugriff verweigert. Zugangsdaten prüfen.")
        raise
    except Exception as error:
        if cached_plan is not None:
            log(f"VpMobil konnte nicht erreicht werden ({error}). Nutze vorhandenen Cache.")
            return cached_plan

        log(f"VpMobil konnte nicht erreicht werden ({error}) und es gibt keinen Cache.")
        raise

    if is_remote_plan_newer(cached_plan, remote_plan):
        log(f"Neuerer Plan für {selected_date.isoformat()} gefunden. Cache wird aktualisiert.")
        save_plan_to_cache(selected_date, remote_plan)
        return remote_plan

    log(f"Kein neuerer Plan für {selected_date.isoformat()} gefunden. Nutze vorhandenen Cache.")
    return cached_plan


"""def check_new_plan(selected_date: date):
    #Lädt den Plan, aktualisiert den Cache bei neueren Daten und nutzt sonst den alten Cache.

    cleanup_cache()

    cached_plan = load_plan_from_cache(selected_date)

    try:
        remote_plan = fetch_plan_from_vpmobil(selected_date)
    except ResourceNotFound:
        if cached_plan is not None:
            log(f"VpMobil hat keinen Plan für {selected_date.isoformat()} geliefert. Nutze vorhandenen Cache.")
            return 0

        log(f"VpMobil hat keinen Plan für {selected_date.isoformat()} geliefert und es gibt keinen Cache.")
        raise
    except Unauthorized:
        log("VpMobil-Zugriff verweigert. Zugangsdaten prüfen.")
        raise
    except Exception as error:
        if cached_plan is not None:
            log(f"VpMobil konnte nicht erreicht werden ({error}). Nutze vorhandenen Cache.")
            return "error"

        log(f"VpMobil konnte nicht erreicht werden ({error}) und es gibt keinen Cache.")
        raise

    if is_remote_plan_newer(cached_plan, remote_plan):
        log(f"Neuerer Plan für {selected_date.isoformat()} gefunden. Cache wird aktualisiert.")
        save_plan_to_cache(selected_date, remote_plan)
        return 1

    log(f"Kein neuerer Plan für {selected_date.isoformat()} gefunden. Nutze vorhandenen Cache.")
    return 0"""


def get_school_week_dates(selected_date: date) -> list[date]:
    """Gibt Montag bis Freitag der Woche zurück, in der das ausgewählte Datum liegt."""

    monday = selected_date - timedelta(days=selected_date.weekday())

    return [
        monday + timedelta(days=offset)
        for offset in range(5)
    ]


def get_future_week_dates(start_date: date | None = None) -> list[date]:
    """Gibt die zehn Werktage der nächsten zwei vollständigen Schulwochen zurück."""

    start_date = start_date or date.today()
    first_monday = start_date + timedelta(days=7 - start_date.weekday())
    return [
        first_monday + timedelta(days=offset)
        for offset in range(14)
        if (first_monday + timedelta(days=offset)).weekday() < 5
    ]


def get_future_week_plans() -> dict[date, object | None]:
    """Lädt Vertretungsplandaten für die nächsten zwei vollständigen Schulwochen."""

    plans: dict[date, object | None] = {}
    for plan_date in get_future_week_dates():
        try:
            plans[plan_date] = get_plan_for_page(plan_date)
        except ResourceNotFound:
            log(f"Kein Plan für den zukünftigen Tag {plan_date.isoformat()} verfügbar.")
            plans[plan_date] = None
        except Exception as error:
            log(f"Zukunftsplan für {plan_date.isoformat()} konnte nicht geladen werden: {error}")
            plans[plan_date] = None
    return plans


def get_subject_catalog_plans() -> list[object]:
    """Lädt den Klassen-Kurskatalog und ergänzt ihn um zwei Zukunftswochen."""

    plans: list[object] = []
    try:
        access = VertretungsplanZugang(SCHULNUMMER, BENUTZERNAME, PASSWORT)
        plans.append(access.get(date.today(), datei=Standardpfade.Klassen))
    except Exception as error:
        log(f"Klassen-Kurskatalog konnte nicht geladen werden: {error}")
    for week_start in (date.today(), date.today() + timedelta(days=7)):
        try:
            plans.extend(get_official_weekly_plans_for_page(week_start).values())
        except Exception as error:
            log(f"Originaler Wochenplan konnte nicht geladen werden: {error}")
    return plans


def fetch_official_weekly_plans(start_date: date | None = None) -> dict[date, object]:
    """Fetch the original Stundenplan24 weekly plan, including A/B weeks."""

    access = VertretungsplanZugang(SCHULNUMMER, BENUTZERNAME, PASSWORT)
    return fetch_weekly_plans(access, start_date)


def _week_monday(selected_date: date) -> date:
    return selected_date - timedelta(days=selected_date.weekday())


def _weekly_cache_path(monday: date) -> Path:
    return CACHE_DIR / "weekly" / f"{monday.isoformat()}.pickle"


def _load_weekly_cache(monday: date) -> dict[date, object] | None:
    path = _weekly_cache_path(monday)
    if not path.exists():
        return None
    try:
        with path.open("rb") as file:
            plans = pickle.load(file)
        if not isinstance(plans, dict):
            raise ValueError("Ungültiges Wochenplan-Cacheformat")
        return plans
    except (OSError, pickle.PickleError, EOFError, ValueError, TypeError):
        path.unlink(missing_ok=True)
        return None


def _save_weekly_cache(monday: date, plans: dict[date, object]) -> None:
    path = _weekly_cache_path(monday)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    with temporary_path.open("wb") as file:
        pickle.dump(plans, file)
    temporary_path.replace(path)


def refresh_official_weekly_plans_in_background(monday: date) -> None:
    now = monotonic()
    with _weekly_cache_lock:
        if (
            monday in _weekly_refreshing
            or now - _weekly_last_refresh.get(monday, 0) < WEEKLY_REFRESH_INTERVAL_SECONDS
        ):
            return
        _weekly_refreshing.add(monday)

    def refresh() -> None:
        try:
            plans = fetch_official_weekly_plans(monday)
            with _weekly_cache_lock:
                _weekly_plan_cache[monday] = plans
            _save_weekly_cache(monday, plans)
        except Exception as error:
            log(f"Hintergrund-Refresh des Wochenplans für {monday.isoformat()} fehlgeschlagen: {error}")
        finally:
            with _weekly_cache_lock:
                _weekly_refreshing.discard(monday)
                _weekly_last_refresh[monday] = monotonic()

    Thread(target=refresh, daemon=True, name=f"weekly-refresh-{monday.isoformat()}").start()


def get_official_weekly_plans_for_page(selected_date: date) -> dict[date, object]:
    """Returns a cached weekly plan immediately and refreshes it in background."""

    monday = _week_monday(selected_date)
    with _weekly_cache_lock:
        cached = _weekly_plan_cache.get(monday)
    if cached is None:
        cached = _load_weekly_cache(monday)
        if cached is not None:
            with _weekly_cache_lock:
                _weekly_plan_cache[monday] = cached
    if cached is not None:
        refresh_official_weekly_plans_in_background(monday)
        return cached

    plans = fetch_official_weekly_plans(monday)
    with _weekly_cache_lock:
        _weekly_plan_cache[monday] = plans
        _weekly_last_refresh[monday] = monotonic()
    _save_weekly_cache(monday, plans)
    return plans


def fetch_week_plans(selected_date: date) -> dict[date, object | None]:
    """Lädt die verfügbaren Pläne von Montag bis Freitag einer Woche.

    Nicht verfügbare Tage werden als None gespeichert. So kann die Wochenansicht
    trotzdem angezeigt werden, auch wenn einzelne Tagespläne fehlen.
    """

    week_plans = {}

    for plan_date in get_school_week_dates(selected_date):
        try:
            week_plans[plan_date] = fetch_plan(plan_date)
        except ResourceNotFound:
            log(f"Kein Plan für {plan_date.isoformat()} verfügbar.")
            week_plans[plan_date] = None

    return week_plans


def refresh_plan_in_background(selected_date: date) -> None:
    """Aktualisiert einen Tagesplan im Hintergrund, höchstens alle 30 Sekunden."""

    now = monotonic()

    with _page_cache_lock:
        last_refresh = _page_last_refresh.get(selected_date, 0)

        if (
            selected_date in _page_refreshing
            or now - last_refresh < PAGE_REFRESH_INTERVAL_SECONDS
        ):
            return

        _page_refreshing.add(selected_date)

    def refresh() -> None:
        try:
            refreshed_plan = fetch_plan(selected_date)

            with _page_cache_lock:
                _page_plan_cache[selected_date] = refreshed_plan
        except Exception as error:
            log(f"Hintergrund-Refresh für {selected_date.isoformat()} fehlgeschlagen: {error}")
        finally:
            with _page_cache_lock:
                _page_refreshing.discard(selected_date)
                _page_last_refresh[selected_date] = monotonic()

    Thread(target=refresh, daemon=True).start()


def get_plan_for_page(selected_date: date):
    """Lädt einen Tagesplan zuerst aus dem Cache und aktualisiert ihn danach."""

    with _page_cache_lock:
        cached_plan = _page_plan_cache.get(selected_date)

    if cached_plan is not None:
        refresh_plan_in_background(selected_date)
        return cached_plan

    cached_plan = load_plan_from_cache(selected_date)

    if cached_plan is not None:
        with _page_cache_lock:
            _page_plan_cache[selected_date] = cached_plan

        refresh_plan_in_background(selected_date)
        return cached_plan

    # Ohne lokalen Cache muss der erste Aufruf einmalig synchron laden.
    fresh_plan = fetch_plan(selected_date)

    with _page_cache_lock:
        _page_plan_cache[selected_date] = fresh_plan
        _page_last_refresh[selected_date] = monotonic()

    return fresh_plan


def get_week_plans_for_page(selected_date: date) -> dict[date, object | None]:
    """Lädt eine Woche sofort aus vorhandenem Cache und aktualisiert danach."""

    week_plans = {}
    has_cached_data = False

    for plan_date in get_school_week_dates(selected_date):
        with _page_cache_lock:
            cached_plan = _page_plan_cache.get(plan_date)

        if cached_plan is None and get_cache_path(plan_date).exists():
            cached_plan = load_plan_from_cache(plan_date)

            if cached_plan is not None:
                with _page_cache_lock:
                    _page_plan_cache[plan_date] = cached_plan

        if cached_plan is not None:
            has_cached_data = True

        week_plans[plan_date] = cached_plan

    if has_cached_data:
        # Auch fehlende Tage werden im Hintergrund geprüft. Die Seite muss dafür
        # nicht auf mehrere Netzwerkaufrufe warten.
        for plan_date in week_plans:
            refresh_plan_in_background(plan_date)

        return week_plans

    # Beim allerersten Aufruf ohne irgendeinen Wochen-Cache laden wir einmalig
    # synchron und zeigen fehlende Tage weiterhin als leer an.
    for plan_date in week_plans:
        try:
            week_plans[plan_date] = get_plan_for_page(plan_date)
        except ResourceNotFound:
            log(f"Kein Plan für {plan_date.isoformat()} verfügbar.")
            week_plans[plan_date] = None

    return week_plans


def collect_relevant_classes(plan) -> list:
    """Sammelt alle Klassen, die für die Raumbelegung ausgewertet werden sollen."""

    classes = []

    for grade in range(1, 13):
        main_class_found = False

        for letter in ALPHABET:
            class_name = f"{grade}{letter}"

            if class_name in plan.klassen:
                classes.append(plan.klassen[class_name])
                continue

            # Wenn es keine getrennten Klassen wie 11a oder 11b gibt,
            # wird einmalig die Hauptklasse wie "11" übernommen.
            if not main_class_found and str(grade) in plan.klassen:
                classes.append(plan.klassen[str(grade)])
                main_class_found = True
    return classes


def extract_room_number(room_value: object) -> int | None:
    """Extrahiert aus einem Raumwert die numerische Raumnummer."""

    digits = "".join(filter(str.isdigit, str(room_value)))

    if not digits:
        return None

    try:
        return int(digits)
    except ValueError:
        return None


def find_occupied_rooms(classes: list, selected_hour: int) -> set[int]:
    """Ermittelt alle Räume, die in der angegebenen Stunde belegt sind."""

    occupied_rooms = set()

    for class_item in classes:
        for period, lessons in class_item.stunden.items():
            if int(period) != selected_hour:
                continue

            for lesson in lessons:
                for room in lesson.räume:
                    room_number = extract_room_number(room)

                    if room_number is not None:
                        occupied_rooms.add(room_number)

    return occupied_rooms


def find_free_rooms(selected_date: date, selected_hour: int) -> list[int]:
    """Gibt alle freien Räume für ein Datum und eine Unterrichtsstunde zurück."""

    plan = fetch_plan(selected_date)
    return find_free_rooms_in_plan(plan, selected_hour)


def find_free_rooms_in_plan(plan, selected_hour: int) -> list[int]:
    """Gibt freie Räume für einen bereits geladenen Tagesplan zurück."""

    classes = collect_relevant_classes(plan)
    occupied_rooms = find_occupied_rooms(classes, selected_hour)

    return [room for room in ALL_ROOMS if room not in occupied_rooms]


__all__ = [
    "ResourceNotFound",
    "Unauthorized",
    "fetch_plan",
    "fetch_week_plans",
    "get_future_week_dates",
    "get_future_week_plans",
    "get_subject_catalog_plans",
    "fetch_official_weekly_plans",
    "get_official_weekly_plans_for_page",
    "get_plan_for_page",
    "get_week_plans_for_page",
    "find_free_rooms",
    "find_free_rooms_in_plan",
]
