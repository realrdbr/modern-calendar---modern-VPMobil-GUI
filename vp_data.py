import json
import os
import pickle
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock, Thread
from time import monotonic
from types import SimpleNamespace

from dotenv import load_dotenv
from lesson_status import is_cancelled

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
_subject_catalog_cache: list[object] | None = None
_subject_catalog_last_refresh = 0.0
_subject_catalog_refreshing = False
_subject_catalog_lock = Lock()


def _json_env_list(name: str) -> list:
    """Liest JSON-Listen auch aus Compose-Werten mit erhaltenen Shell-Quotes."""
    raw = os.getenv(name, "[]").strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
        raw = raw[1:-1]
    value = json.loads(raw or "[]")
    if not isinstance(value, list):
        raise ValueError(f"{name} muss eine JSON-Liste sein.")
    return value


# Feste Liste aller Räume, die grundsätzlich als verfügbar betrachtet werden können.
ALL_ROOMS = _json_env_list("ALL_ROOMS")


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


def _refresh_subject_catalog() -> list[object]:
    global _subject_catalog_cache, _subject_catalog_last_refresh, _subject_catalog_refreshing

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
    with _subject_catalog_lock:
        _subject_catalog_cache = plans
        _subject_catalog_last_refresh = monotonic()
        _subject_catalog_refreshing = False
    return plans


def get_subject_catalog_plans() -> list[object]:
    """Liefert den Kurskatalog cache-first; Aktualisierung läuft im Hintergrund."""
    global _subject_catalog_refreshing
    with _subject_catalog_lock:
        cached = list(_subject_catalog_cache or [])
        stale = monotonic() - _subject_catalog_last_refresh >= WEEKLY_REFRESH_INTERVAL_SECONDS
        if cached and stale and not _subject_catalog_refreshing:
            _subject_catalog_refreshing = True
            Thread(target=_refresh_subject_catalog, daemon=True, name="subject-catalog-refresh").start()
    if cached:
        return cached
    return _refresh_subject_catalog()


def get_subject_catalog_plans_for_page() -> list[object]:
    """Liefert den Katalog ohne einen Seitenrequest durch Netzwerkzugriffe zu blockieren.

    Die Verwaltungs- und Benachrichtigungsabläufe dürfen weiterhin den
    vollständigen, synchronen Katalog anfordern. Die Planansicht zeigt dagegen
    stets den vorhandenen Cache sofort an und aktualisiert ihn im Hintergrund –
    genau wie die Tages- und Wochenplan-Caches.
    """

    global _subject_catalog_refreshing
    with _subject_catalog_lock:
        cached = list(_subject_catalog_cache or [])
        stale = monotonic() - _subject_catalog_last_refresh >= WEEKLY_REFRESH_INTERVAL_SECONDS
        if (not cached or stale) and not _subject_catalog_refreshing:
            _subject_catalog_refreshing = True
            Thread(target=_refresh_subject_catalog, daemon=True, name="subject-catalog-refresh").start()
    return cached


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
    cached = get_cached_official_weekly_plans_for_page(selected_date)
    if cached is not None:
        return cached

    plans = fetch_official_weekly_plans(monday)
    with _weekly_cache_lock:
        _weekly_plan_cache[monday] = plans
        _weekly_last_refresh[monday] = monotonic()
    _save_weekly_cache(monday, plans)
    return plans


def get_cached_official_weekly_plans_for_page(selected_date: date) -> dict[date, object] | None:
    """Liest ausschließlich den normalen Wochenplan-Cache, ohne Netzwerkzugriff."""

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
    return None


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

    # Ein normaler Stundenplan ist der korrekte Ersatz für Tage ohne
    # veröffentlichten Vertretungsplan. Der Wochen-Cache wird vor jedem
    # potenziell langsamen Tagesabruf geprüft – wichtig für Freie Räume.
    official_week = get_cached_official_weekly_plans_for_page(selected_date)
    official_plan = official_week.get(selected_date) if official_week else None
    if official_plan is not None:
        refresh_plan_in_background(selected_date)
        return official_plan

    # Ohne lokalen Cache muss der erste Aufruf einmalig den veröffentlichten
    # Tagesplan prüfen. Liefert VpMobil keinen Plan, laden wir zuverlässig den
    # normalen Stundenplan derselben Woche.
    try:
        fresh_plan = fetch_plan(selected_date)
    except ResourceNotFound:
        official_week = get_official_weekly_plans_for_page(selected_date)
        official_plan = official_week.get(selected_date)
        if official_plan is not None:
            return official_plan
        raise

    with _page_cache_lock:
        _page_plan_cache[selected_date] = fresh_plan
        _page_last_refresh[selected_date] = monotonic()

    return fresh_plan


def get_cached_plan_for_page(selected_date: date):
    """Liefert ausschließlich bereits bekannte Plandaten und blockiert nie.

    Diese Variante ist für interaktive Seiten gedacht, die sofort mit dem
    Server-Cache antworten sollen. Fehlt ein Eintrag, laufen sowohl die
    Vertretungsplan- als auch die normale Wochenplan-Aktualisierung parallel.
    """

    with _page_cache_lock:
        cached_plan = _page_plan_cache.get(selected_date)
    if cached_plan is None:
        cached_plan = load_plan_from_cache(selected_date)
        if cached_plan is not None:
            with _page_cache_lock:
                _page_plan_cache[selected_date] = cached_plan
    if cached_plan is not None:
        refresh_plan_in_background(selected_date)
        return cached_plan

    official_week = get_cached_official_weekly_plans_for_page(selected_date)
    official_plan = official_week.get(selected_date) if official_week else None
    if official_plan is not None:
        refresh_plan_in_background(selected_date)
        return official_plan

    # Kein Seitenrequest wartet auf diese Abrufe. Sobald einer fertig ist,
    # liefert die Versionsabfrage den Cache an den Browser aus.
    refresh_plan_in_background(selected_date)
    refresh_official_weekly_plans_in_background(_week_monday(selected_date))
    return None


def warm_page_caches_from_disk(anchor_date: date | None = None) -> int:
    """Wärmt Plan-Caches beim Start vor, ohne den Serverstart zu blockieren."""

    anchor_date = anchor_date or date.today()
    if anchor_date.weekday() >= 5:
        anchor_date += timedelta(days=7 - anchor_date.weekday())
    warmed = 0
    for week_anchor in (anchor_date, anchor_date + timedelta(days=7)):
        # Der normale Wochenplan ist der schnelle Fallback für Klassen-,
        # Lehrer- und Freie-Räume-Seite. Vorhandene Dateicaches werden sofort
        # übernommen; fehlt er, beginnt der Netzabruf rein im Hintergrund.
        if get_cached_official_weekly_plans_for_page(week_anchor) is None:
            refresh_official_weekly_plans_in_background(_week_monday(week_anchor))
        for plan_date in get_school_week_dates(week_anchor):
            with _page_cache_lock:
                if plan_date in _page_plan_cache:
                    continue
            cached_plan = load_plan_from_cache(plan_date)
            if cached_plan is None:
                continue
            with _page_cache_lock:
                _page_plan_cache[plan_date] = cached_plan
            warmed += 1
    if warmed:
        log(f"{warmed} Plan-Cache(s) für Vertretungsplan und Freie Räume vorgewärmt.")
    return warmed


def get_week_plans_for_page(selected_date: date) -> dict[date, object | None]:
    """Lädt Tagespläne cache-first und ergänzt fehlende Tage mit dem Wochenplan."""

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
        # Liegen Tagesdaten vor, wird die Seite strikt ohne Netzwerkwartezeit
        # gerendert. Ein vorhandener Wochen-Cache ergänzt fehlende Tage sofort;
        # fehlt er noch, startet sein Abruf ausschließlich im Hintergrund.
        official_week = get_cached_official_weekly_plans_for_page(selected_date)
        if any(plan is None for plan in week_plans.values()) and official_week is None:
            refresh_official_weekly_plans_in_background(_week_monday(selected_date))
        if official_week:
            for plan_date, official_plan in official_week.items():
                if week_plans.get(plan_date) is None:
                    week_plans[plan_date] = official_plan
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

    from vpmobil.utils import natural_sort_key
    return [item for _, item in sorted(plan.klassen.items(), key=lambda pair: natural_sort_key(pair[0]))]


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
                if is_cancelled(lesson):
                    continue
                for room in lesson.räume:
                    # Mehrfachräume dürfen nicht zu einer Nummer wie 101102 werden.
                    room_text = re.sub(r"(\d)\s*([–—-])\s*(?=\d)", r"\1\2", str(room))
                    for value in re.split(r"[,;/+&\s]+", room_text):
                        interval = re.fullmatch(r"(\d+)[–—-](\d+)", value)
                        if interval:
                            first, last = sorted(map(int, interval.groups()))
                            occupied_rooms.update(room for room in ALL_ROOMS if first <= room <= last)
                        else:
                            room_number = extract_room_number(value)
                            if room_number is not None:
                                occupied_rooms.add(room_number)

    return occupied_rooms


def find_occupied_rooms_in_plan(plan, selected_hour: int) -> set[int]:
    """Berücksichtigt auch Unterricht ohne Klassenzuordnung, etwa in Lehrerplänen."""
    if hasattr(plan, "stunden"):
        lessons = [lesson for lesson in plan.stunden if int(lesson.periode) == selected_hour]
        return find_occupied_rooms([SimpleNamespace(stunden={selected_hour: lessons})], selected_hour)
    return find_occupied_rooms(collect_relevant_classes(plan), selected_hour)


def find_free_rooms(selected_date: date, selected_hour: int) -> list[int]:
    """Gibt alle freien Räume für ein Datum und eine Unterrichtsstunde zurück."""

    plan = fetch_room_plan(selected_date)
    return find_free_rooms_in_plan(plan, selected_hour)


def find_free_rooms_in_plan(plan, selected_hour: int) -> list[int]:
    """Gibt freie Räume für einen bereits geladenen Tagesplan zurück."""

    occupied_rooms = find_occupied_rooms_in_plan(plan, selected_hour)

    return [room for room in ALL_ROOMS if room not in occupied_rooms]


__all__ = [
    "ResourceNotFound",
    "Unauthorized",
    "fetch_plan",
    "fetch_week_plans",
    "get_future_week_dates",
    "get_future_week_plans",
    "get_subject_catalog_plans",
    "get_subject_catalog_plans_for_page",
    "fetch_official_weekly_plans",
    "get_official_weekly_plans_for_page",
    "get_cached_official_weekly_plans_for_page",
    "get_cached_plan_for_page",
    "get_plan_for_page",
    "warm_page_caches_from_disk",
    "get_week_plans_for_page",
    "find_free_rooms",
    "find_free_rooms_in_plan",
]


_room_plan_cache = {}
_room_plan_lock = Lock()
_room_plan_refreshing = set()


def fetch_room_plan(selected_date: date):
    """Lädt aktuelle Raumplandaten; nur ein fehlender Tagesplan erlaubt den Normalplan."""
    try:
        plan = fetch_plan_from_vpmobil(selected_date)
    except ResourceNotFound:
        plan = fetch_official_weekly_plans(selected_date).get(selected_date)
    if plan is None:
        raise ResourceNotFound("Keine Plandaten für die Raumbelegung vorhanden.")
    if getattr(plan, "datum", selected_date) not in (None, selected_date):
        raise ValueError("Das gelieferte Plandatum stimmt nicht mit dem angefragten Datum überein.")
    return plan


def refresh_room_plan_in_background(selected_date: date) -> None:
    """Ein Abruf pro Datum; Netzwerkzugriffe halten keine Cache-Sperre."""
    with _room_plan_lock:
        if selected_date in _room_plan_refreshing:
            return
        _room_plan_refreshing.add(selected_date)

    def refresh():
        try:
            plan = fetch_room_plan(selected_date)
            with _room_plan_lock:
                if len(_room_plan_cache) > 30:
                    _room_plan_cache.clear()
                _room_plan_cache[selected_date] = (monotonic(), plan)
        except Exception as error:
            log(f"Raumplan-Refresh für {selected_date.isoformat()} fehlgeschlagen: {error}")
        finally:
            with _room_plan_lock:
                _room_plan_refreshing.discard(selected_date)

    Thread(target=refresh, daemon=True, name=f"room-refresh-{selected_date.isoformat()}").start()


def get_room_plan_for_page(selected_date: date, *, refresh: bool = True):
    """Liefert sofort den Cache; nur Seitenaufrufe starten einen Hintergrundabruf."""
    with _room_plan_lock:
        cached = _room_plan_cache.get(selected_date)
    plan = cached[1] if cached is not None else None
    if plan is None:
        with _page_cache_lock:
            plan = _page_plan_cache.get(selected_date)
        if plan is None:
            plan = load_plan_from_cache(selected_date)
        if plan is None:
            monday = _week_monday(selected_date)
            with _weekly_cache_lock:
                week = _weekly_plan_cache.get(monday)
            if week is None:
                week = _load_weekly_cache(monday)
            plan = week.get(selected_date) if week else None
        if plan is not None:
            with _room_plan_lock:
                # Ein zwischenzeitlich abgeschlossener Refresh hat Vorrang.
                cached = _room_plan_cache.setdefault(selected_date, (0, plan))
                plan = cached[1]
    if refresh:
        refresh_room_plan_in_background(selected_date)
    return plan
