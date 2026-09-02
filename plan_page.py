import json
import re
from datetime import date, timedelta
from html import escape
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlencode, urlparse

from vp_data import (
    ResourceNotFound,
    Unauthorized,
    get_subject_catalog_plans_for_page,
    get_week_plans_for_page,
)
from subscriptions import lesson_display_label, subject_options_from_plans
from web_utils import (
    CALENDAR_PUBLIC_URL,
    COMMON_CSS,
    format_week_value,
    get_ab_week_label,
    join_cookie_list,
    make_cookie,
    parse_cookie_header,
    parse_week,
    query_value,
    query_values,
    redirect,
    send_html,
    split_cookie_list,
    start_server, DEFAULT_PORT,
    render_theme_script,
    render_today_marker_script,
    render_vp_navigation,
    render_vp_user_identity,
)

DAY_NAMES = {
    0: "Mo",
    1: "Di",
    2: "Mi",
    3: "Do",
    4: "Fr",
}


def natural_sort_key(item):
    # Spaltet den String in Zahlen (als int) und Textabschnitte auf
    return [
        int(text) if text.isdigit() else text for text in re.split(r"(\d+)", item)
    ]


def get_week_version(week_plans: dict[date, object | None]) -> str:
    """Erzeugt eine kurze Version aus den Plan-Zeitstempeln der Woche."""

    return "|".join(
        f"{plan_date.isoformat()}:{'vorhanden' if plan is not None else 'fehlend'}:{getattr(plan, 'zeitstempel', '') or ''}"
        for plan_date, plan in week_plans.items()
    )


def format_time(value) -> str:
    """Formatiert eine Uhrzeit für die Ausgabe."""

    if value is None:
        return ""

    return value.strftime("%H:%M")


def format_tuple(values: tuple[str, ...]) -> str:
    """Formatiert mehrere Werte als kurzen Text."""

    if not values:
        return "-"

    return ", ".join(str(value) for value in values)


def get_lesson_status_text(lesson) -> str:
    """Gibt den Status einer Stunde zurück."""

    if lesson.ausfall:
        return "Ausfall"

    if lesson.änderung:
        return "Vertretung"

    return "Regulär"


def get_lesson_subject_label(lesson, class_item=None) -> str:
    """Erzeugt eine Fach-Lehrer-Bezeichnung für den Filter."""

    if class_item is not None:
        return lesson_display_label(class_item, lesson)
    subject = lesson.fach or "Unbekannt"
    teacher = format_tuple(lesson.lehrer)
    return f"{subject} ({teacher})"


def get_selected_subject_cookie_name(class_name: str) -> str:
    """Erzeugt einen Cookie-Namen für die Fachauswahl einer Klasse."""

    safe_class_name = "".join(
        char
        for char in class_name
        if char.isalnum() or char in ("-", "_")
    )

    return f"selected_subjects_{safe_class_name}"


def get_selected_class_cookie_name(username: str) -> str:
    """Erzeugt einen benutzergebundenen Cookie-Namen für die VP-Klasse."""

    safe_username = "".join(
        char for char in username.casefold()
        if char.isalnum() or char in ("-", "_")
    )
    return f"selected_class_{safe_username or 'user'}"


def get_available_classes(week_plans: dict[date, object | None]) -> list[str]:
    """Sammelt alle Klassen, die in mindestens einem Wochenplan vorkommen."""

    classes = set()

    for plan in week_plans.values():
        if plan is None:
            continue

        classes.update(plan.klassen.keys())

    return sorted(classes, key=natural_sort_key)


def resolve_initial_class(account_class: str | None, available_classes: list[str]) -> str | None:
    """Ordnet die beim Konto hinterlegte Klasse einer verfügbaren Plan-Klasse zu."""

    if not available_classes:
        return None

    preferred = (account_class or "").strip()
    by_normalized_name = {class_name.casefold(): class_name for class_name in available_classes}
    if preferred.casefold() in by_normalized_name:
        return by_normalized_name[preferred.casefold()]

    match = re.match(r"\s*(\d+)", preferred)
    if match:
        grade = match.group(1)
        grade_classes = [
            class_name for class_name in available_classes
            if re.match(rf"^{re.escape(grade)}(?:\D|$)", class_name, re.IGNORECASE)
        ]
        if grade_classes:
            preferred_main = by_normalized_name.get(f"{grade}a")
            return preferred_main or grade_classes[0]

    return available_classes[0]


def get_class_subject_options(
    week_plans: dict[date, object | None],
    class_name: str,
    catalog_plans: list[object] | None = None,
) -> list[str]:
    """Sammelt nur Fächer, die in der ausgewählten Klasse in dieser Woche vorkommen."""
    source_plans = catalog_plans if catalog_plans is not None else week_plans.values()
    return [option.label for option in subject_options_from_plans(source_plans, class_name)]


def lesson_matches_subject_filter(lesson, selected_subjects: list[str], class_item=None) -> bool:
    """Prüft, ob eine Stunde zum Fachfilter passt."""

    if not selected_subjects:
        return True

    return get_lesson_subject_label(lesson, class_item) in selected_subjects


def collect_week_lessons(
    week_plans: dict[date, object | None],
    class_name: str,
    selected_subjects: list[str],
) -> dict[int, dict[date, list]]:
    """Sammelt Stunden einer Klasse für Montag bis Freitag."""

    week_lessons: dict[int, dict[date, list]] = {}

    for plan_date, plan in week_plans.items():
        if plan is None or class_name not in plan.klassen:
            continue

        class_item = plan.klassen[class_name]

        for period, lesson_items in class_item.stunden.items():
            for lesson in lesson_items:
                if not lesson_matches_subject_filter(lesson, selected_subjects, class_item):
                    continue

                week_lessons.setdefault(int(period), {}).setdefault(plan_date, []).append(lesson)

    return week_lessons


def render_class_selection(week_plans: dict[date, object | None], selected_date: date) -> str:
    """Rendert die Klassenauswahl."""

    class_links = []

    for class_name in get_available_classes(week_plans):
        query = urlencode({
            "woche": format_week_value(selected_date),
            "klasse": class_name,
        })

        class_links.append(
            f'<a class="choice-card" href="/?{query}">{escape(class_name)}</a>'
        )

    if not class_links:
        return '<p class="empty">Es wurden keine Klassen gefunden.</p>'

    return f"""
        <section class="message">
            <h2>Klasse auswählen</h2>
        </section>

        <section class="choice-grid">
            {"".join(class_links)}
        </section>
    """


def render_subject_filter(
    week_plans: dict[date, object | None],
    selected_date: date,
    selected_class: str,
    selected_subjects: list[str],
    catalog_plans: list[object] | None = None,
    filters_active: bool = True,
    block_mode: bool = False,
) -> str:
    """Rendert die Fachauswahl für die ausgewählte Klasse."""

    subject_options = []

    for subject_label in get_class_subject_options(week_plans, selected_class, catalog_plans):
        checked = "checked" if subject_label in selected_subjects else ""

        subject_options.append(f"""
            <label class="subject-option">
                <input type="checkbox" name="fach" value="{escape(subject_label)}" {checked}>
                <span>{escape(subject_label)}</span>
            </label>
        """)

    if not subject_options:
        return ""

    return f"""
        <section class="filter-card">
            <div class="filter-card-header">
              <details>
                <summary class="filter-summary">Fächer filtern</summary>

                <form method="get" action="/" class="subject-form">
                    <input type="hidden" name="woche" value="{selected_date.isoformat()}">
                    <input type="hidden" name="klasse" value="{escape(selected_class)}">

                    <div class="subject-grid">
                        {"".join(subject_options)}
                    </div>

                    <div class="filter-actions">
                        <button type="submit">Filter speichern</button>
                        <a class="button button-secondary" href="/?woche={selected_date.isoformat()}&klasse={escape(selected_class)}&fach_clear=1">
                            Filter löschen
                        </a>
                    </div>
                </form>
              </details>
              <a class="filter-toggle-button" href="/?{urlencode({'woche': format_week_value(selected_date), 'klasse': selected_class, **({'show_all': '1'} if filters_active else {}), **({'block': '1'} if block_mode else {})})}"
                 aria-label="{'Ganzen Vertretungsplan anzeigen' if filters_active else 'Gespeicherte Filter wieder aktivieren'}"
                 title="{'Ganzen Vertretungsplan anzeigen' if filters_active else 'Gespeicherte Filter wieder aktivieren'}">{'+' if filters_active else '-'}</a>
            </div>
        </section>
    """


def render_lesson_details(lesson) -> str:
    """Erzeugt den Detailbereich einer Stunde."""

    rows = [
        ("Zeit", f"{format_time(lesson.beginn)} - {format_time(lesson.ende)}"),
        ("Fach", lesson.fach or "-"),
        ("Lehrer", format_tuple(lesson.lehrer)),
        ("Raum", format_tuple(lesson.räume)),
        ("Status", get_lesson_status_text(lesson)),
        ("Info", lesson.info or "-"),
    ]

    return "".join(
        f"""
            <div class="popup-row">
                <span>{escape(label)}</span>
                <strong>{escape(value)}</strong>
            </div>
        """
        for label, value in rows
    )


def render_lesson_cell(lessons: list, period_labels: list[int] | None = None) -> str:
    """Rendert eine Tabellenzelle mit einer oder mehreren Stunden."""

    if not lessons:
        return '<div class="week-empty">-</div>'

    cards = []

    for index, lesson in enumerate(lessons):
        changed_class = "week-lesson--changed" if lesson.änderung or lesson.ausfall else ""
        period_label = (
            f'<span class="lesson-period-label">{period_labels[index]}. Stunde</span>'
            if period_labels and index < len(period_labels) else ""
        )

        cards.append(f"""
            <details class="week-lesson {changed_class}">
                <summary>
                    <strong>{escape(lesson.fach or "-")}</strong>
                    {period_label}
                    <span>{escape(format_tuple(lesson.lehrer))}</span>
                    <span>{escape(format_tuple(lesson.räume))}</span>
                </summary>

                <div class="popup-content">
                    {render_lesson_details(lesson)}
                </div>
            </details>
        """)

    return "".join(cards)


def render_week_table(
    week_plans: dict[date, object | None],
    selected_class: str,
    selected_subjects: list[str],
    weekly_dates: set[date] | None = None,
    block_mode: bool = False,
) -> str:
    """Rendert den Wochenplan."""

    week_lessons = collect_week_lessons(week_plans, selected_class, selected_subjects)

    if not week_lessons:
        return '<p class="empty">Für diese Woche wurden keine Stunden gefunden.</p>'

    dates = list(week_plans.keys())
    max_period = max(8, max(week_lessons.keys(), default=8))

    header_cells = []

    for plan_date in dates:
        day_name = DAY_NAMES.get(plan_date.weekday(), plan_date.strftime("%a"))
        today = date.today()
        head_class = ' class="day-head--today"' if today.weekday() < 5 and plan_date == today else ""
        day_plan = week_plans[plan_date]
        if day_plan is None:
            day_plan_info = "<small>Keine Plandaten vorhanden</small>"
        else:
            timestamp = getattr(day_plan, "zeitstempel", None)
            timestamp_text = timestamp.strftime("%d.%m.%Y %H:%M") if timestamp is not None else "unbekannt"
            if timestamp is not None or not "unbekannt":
                timestamp_text = timestamp.strftime("%d.%m.%Y %H:%M")
                day_plan_info = f"<small>Planstand: {escape(timestamp_text)}</small>"
            else:
                day_plan_info = "<small>Plan nicht verfügbar</small>"


        header_cells.append(f"""
            <th{head_class} data-plan-date="{plan_date.isoformat()}">
                <div class="day-head-marker">
                    <span>{escape(day_name)}</span>
                    <small>{plan_date.strftime("%d.%m.")}</small>
                    {day_plan_info}
                </div>
            </th>
        """)


    rows = []

    periods = list(range(1, max_period + 1))
    if block_mode:
        periods = [p for p in periods if p % 2 == 1]
    for period in periods:
        day_cells = []

        for plan_date in dates:
            lessons = week_lessons.get(period, {}).get(plan_date, [])
            period_labels = None
            if block_mode and period + 1 <= max_period:
                second = week_lessons.get(period + 1, {}).get(plan_date, [])
                signature = lambda items: [(getattr(x, 'fach', ''), tuple(getattr(x, 'lehrer', ())), tuple(getattr(x, 'räume', ()))) for x in items]
                if signature(lessons) == signature(second):
                    # Gleiche Doppelstunde: Fach/Lehrer/Raum bleiben sichtbar,
                    # die Tabellenzeile wird lediglich zu 1–2, 3–4 usw.
                    pass
                elif lessons != second:
                    period_labels = [period] * len(lessons) + [period + 1] * len(second)
                    lessons = lessons + second
            day_cells.append(f"<td>{render_lesson_cell(lessons, period_labels)}</td>")

        rows.append(f"""
            <tr>
                <th class="period-head">{((period + 1) // 2) if block_mode else period}</th>
                {"".join(day_cells)}
            </tr>
        """)

    return f"""
        <section class="week-table-wrap">
            <table class="week-table">
                <thead>
                    <tr>
                        <th class="period-head">{'Bl' if block_mode else 'Std.'}</th>
                        {"".join(header_cells)}
                    </tr>
                </thead>
                <tbody>
                    {"".join(rows)}
                </tbody>
            </table>
        </section>
    """


def get_week_title(week_plans: dict[date, object | None]) -> str:
    """Erzeugt einen Titel für die angezeigte Woche."""

    dates = list(week_plans.keys())

    if not dates:
        return "Woche"

    ab_label = get_ab_week_label(dates[0])
    return f"{dates[0].strftime('%d.%m.')} - {dates[-1].strftime('%d.%m.%Y')} {ab_label}"


def get_latest_timestamp_text(week_plans: dict[date, object | None]) -> str:
    """Gibt den neuesten bekannten Planstand der Woche zurück."""

    timestamps = [
        plan.zeitstempel
        for plan in week_plans.values()
        if plan is not None and plan.zeitstempel is not None
    ]

    if not timestamps:
        return "unbekannt"

    return max(timestamps).strftime("%d.%m.%Y %H:%M")


def render_week_navigation(selected_date: date, selected_class: str | None, filters_active: bool = True, block_mode: bool = False) -> str:
    """Rendert die Navigation für die vorherige und nächste Schulwoche."""

    previous_week = selected_date - timedelta(days=7)
    next_week = selected_date + timedelta(days=7)
    current_week = date.today() - timedelta(days=date.today().weekday())
    selected_week = selected_date - timedelta(days=selected_date.weekday())
    is_current_week = selected_week == current_week
    query_values = ({'klasse': selected_class} if selected_class else {})
    if not filters_active:
        query_values['show_all'] = '1'
    if block_mode:
        query_values['block'] = '1'
    class_query = f"&{urlencode(query_values)}" if query_values else ""

    return f"""
        <div class="week-navigation" aria-label="Wochennavigation">
            <a class="week-nav-button" href="/?woche={format_week_value(previous_week)}{class_query}" aria-label="Vorherige Woche">
                <svg class="week-nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m15 18-6-6 6-6"></path></svg>
                <small>Zurück</small>
            </a>

            <a class="week-nav-button {'week-nav-button--current' if is_current_week else ''}" href="/?woche={format_week_value(current_week)}{class_query}">
                <svg class="week-nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 10.5 12 3l9 7.5"></path><path d="M5 9.5V21h14V9.5M9 21v-6h6v6"></path></svg>
                <small>Aktuelle Woche</small>
            </a>

            <a class="week-nav-button" href="/?woche={format_week_value(next_week)}{class_query}" aria-label="Nächste Woche">
                <svg class="week-nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"></path></svg>
                <small>Weiter</small>
            </a>
        </div>
    """


def render_plan_page(
    selected_date: date,
    selected_class: str | None = None,
    selected_subjects: list[str] | None = None,
    error_message: str | None = None,
    filters_active: bool = True,
    block_mode: bool = False,
    logout_csrf_token: str | None = None,
    can_change_pin: bool = False,
    force_pin_change: bool = False,
    pin_modal_error: str | None = None,
    pin_modal_changed: bool = False,
    session_username: str | None = None,
) -> str:
    """Erzeugt die komplette Wochenplan-Seite."""

    selected_subjects = selected_subjects or []
    content = ""
    week_title = f"{selected_date.strftime('%d.%m.%Y')} {get_ab_week_label(selected_date)}"
    plan_timestamp_text = "unbekannt"
    week_version = ""

    if error_message:
        content = f"""
            <section class="message message--error">
                <h2>Keine Daten verfügbar</h2>
                <p>{escape(error_message)}</p>
            </section>
        """
    else:
        week_plans = get_week_plans_for_page(selected_date)
        weekly_dates: set[date] = set()
        week_title = get_week_title(week_plans)
        plan_timestamp_text = get_latest_timestamp_text(week_plans)
        week_version = get_week_version(week_plans)

        available_classes = get_available_classes(week_plans)

        if not selected_class or selected_class not in available_classes:
            content = render_class_selection(week_plans, selected_date)
        else:
            content = f"""
                <section class="message class-message">
                    <div>
                        <h2>Klasse {escape(selected_class)}</h2>
                    </div>

                    <form method="get" action="/" class="block-toggle-form">
                      <input type="hidden" name="woche" value="{selected_date.isoformat()}">
                      <input type="hidden" name="klasse" value="{escape(selected_class)}">
                      <input type="hidden" name="block" value="{'0' if block_mode else '1'}">
                      <label class="block-switch"><span>Block-Unterricht</span><input type="checkbox" {"checked" if block_mode else ""} onchange="this.form.submit()" aria-label="Block-Unterricht umschalten"><span class="block-switch-track" aria-hidden="true"><span></span></span></label>
                    </form>
                    <label class="class-select-label">Klasse anzeigen
                      <select class="class-select" data-plan-class-select>{''.join(f'<option value="/?woche={format_week_value(selected_date)}&amp;klasse={escape(class_name)}{"&amp;block=1" if block_mode else ""}"{" selected" if class_name == selected_class else ""}>{escape(class_name)}</option>' for class_name in available_classes)}</select>
                    </label>
                </section>

                {render_subject_filter(week_plans, selected_date, selected_class, selected_subjects, get_subject_catalog_plans_for_page() or [plan for plan in week_plans.values() if plan is not None], filters_active, block_mode)}

                {render_week_table(week_plans, selected_class, selected_subjects if filters_active else [], weekly_dates, block_mode)}
            """

    return f"""<!doctype html>
<html lang="de">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="icon" href="/icons/favicon.png" type="image/png">
    <title>Vertretungsplan</title>
    <style>
        {COMMON_CSS}

        main {{
            width: min(1320px, calc(100% - 24px));
        }}

        .class-message {{
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            .class-message .block-toggle-form {{ justify-self: start; margin-left: 0; }}
            justify-content: space-between;
        }}

        .block-toggle-form {{ margin: 0; }}
        .block-switch {{ display:inline-flex; align-items:center; gap:8px; min-height:36px; color:var(--text); font-size:.82rem; font-weight:700; cursor:pointer; user-select:none; }}
        .block-switch input {{ position:absolute; opacity:0; width:1px; height:1px; }}
        .block-switch-track {{ width:40px; height:22px; padding:2px; border:1px solid var(--border); border-radius:999px; background:var(--surface-muted); transition:background .15s,border-color .15s; }}
        .block-switch-track span {{ display:block; width:16px; height:16px; border-radius:50%; background:var(--muted); transition:transform .15s,background .15s; }}
        .block-switch input:checked + .block-switch-track {{ background:var(--primary); border-color:var(--primary); }}
        .block-switch input:checked + .block-switch-track span {{ transform:translateX(18px); background:#fff; }}
        .block-switch input:focus-visible + .block-switch-track {{ outline:3px solid color-mix(in srgb, var(--primary) 35%, transparent); outline-offset:2px; }}

        .filter-card {{
            margin-bottom: 18px;
            padding: 18px 20px;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
        }}

        .filter-card-header {{ display:flex; align-items:flex-start; gap:12px; }}
        .filter-card-header details {{ flex:1 1 auto; min-width:0; }}
        .filter-summary {{ min-height:32px; display:flex; align-items:center; font-weight:700; }}
        .filter-summary::marker {{ color:var(--muted); }}
        .filter-toggle-button {{ display:inline-flex; align-items:center; justify-content:center; width:32px; height:32px; border-radius:6px; text-decoration:none; font-size:1.25rem; line-height:1; font-weight:800; color:white; background:var(--primary); }}
        .logout-form {{ margin:0; display:flex; }}
        .logout-button {{ min-height:36px !important; height:36px !important; padding:0 12px !important; border:1px solid var(--border) !important; border-radius:6px !important; background:var(--surface) !important; color:var(--text) !important; font-size:.875rem !important; font-weight:650 !important; }}
        .logout-button:hover {{ border-color:var(--bad-border) !important; color:var(--bad-text) !important; }}

        details summary {{
            cursor: pointer;
        }}

        .subject-form {{
            margin-top: 16px;
        }}

        .subject-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
            gap: 10px;
            margin-bottom: 16px;
        }}

        .subject-option {{
            display: flex;
            gap: 8px;
            align-items: center;
            min-height: 42px;
            padding: 9px 11px;
            border: 1px solid var(--border);
            border-radius: 6px;
            background: var(--surface-muted);
            color: var(--text);
        }}

        .subject-option input {{
            width: auto;
            height: auto;
            flex: 0 0 auto;
        }}

        .subject-option span {{
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}

        .filter-actions {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }}

        .button-secondary {{
            background: var(--surface-muted);
            color: var(--text);
            border: 1px solid var(--border);
        }}

        .button-secondary:hover {{
            background: #e8edf5;
        }}

        .week-navigation {{
            display: grid;
            grid-template-columns: minmax(190px, 1fr) minmax(240px, 1.25fr) minmax(190px, 1fr);
            gap: 14px;
            align-items: center;
            width: 100%;
        }}

        .week-nav-button {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            min-height: 46px;
            padding: 6px 10px;
            border: 1px solid var(--border);
            border-radius: 7px;
            background: var(--surface-muted);
            color: var(--text);
            text-decoration: none;
            font-weight: 900;
        }}

        .week-nav-button--current {{
            background: var(--primary);
            border-color: var(--primary);
            color: white;
        }}

        .week-nav-button--current:hover {{
            background: var(--primary-dark);
            border-color: var(--primary-dark);
        }}

        .week-nav-button:hover {{
            background: var(--surface);
            border-color: var(--primary);
        }}

        .week-nav-button small {{
            font-size: 0.85rem;
        }}

        .week-table-wrap {{
            overflow-x: auto;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
        }}

        .week-table {{
            width: 100%;
            min-width: 760px;
            border-collapse: collapse;
            table-layout: fixed;
        }}

        .week-table th,
        .week-table td {{
            border-bottom: 1px solid var(--border);
            border-right: 1px solid var(--border);
            padding: 6px;
            vertical-align: top;
        }}

        .week-table th:last-child,
        .week-table td:last-child {{
            border-right: 0;
        }}

        .week-table tr:last-child th,
        .week-table tr:last-child td {{
            border-bottom: 0;
        }}

        .week-table thead th {{
            position: sticky;
            top: 0;
            z-index: 2;
            background: var(--surface-muted);
            font-size: 0.9rem;
            overflow: hidden;
        }}

        .week-table thead th[data-plan-date] > * {{
            position: relative;
            z-index: 1;
        }}

        .day-head-marker {{
            position: relative;
            display: inline-flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            width: fit-content;
            max-width: calc(100% - 4px);
            margin: 0 auto;
            padding: 0.22rem 0.88rem 0.26rem;
            box-sizing: border-box;
            line-height: 1.15;
            overflow-wrap: anywhere;
        }}

        .day-head-marker > * {{
            position: relative;
            z-index: 1;
        }}

        .week-table thead th.day-head--today::after {{
            content: "";
            position: absolute;
            right: 0;
            top: 0;
            left: 0;
            height: 2px;
            opacity: .72;
            background: var(--primary);
            pointer-events: none;
        }}

        .week-table thead span,
        .week-table thead small {{
            display: block;
        }}

        .week-table thead small {{
            color: var(--muted);
            font-weight: 700;
            max-width: 100%;
            overflow-wrap: anywhere;
        }}

        .period-head {{
            width: 44px;
            background: var(--surface-muted);
            text-align: center;
            font-weight: 900;
        }}

        .week-empty {{
            min-height: 58px;
            display: grid;
            place-items: center;
            color: var(--muted);
            font-weight: 700;
        }}

        .week-lesson {{
            position: relative;
            margin-bottom: 6px;
        }}

        .week-lesson:last-child {{
            margin-bottom: 0;
        }}

        .week-lesson summary {{
            display: grid;
            gap: 2px;
            min-height: 58px;
            padding: 7px;
            border: 1px solid var(--border);
            border-radius: 6px;
            background: var(--surface);
            list-style: none;
        }}

        .week-lesson summary::-webkit-details-marker {{
            display: none;
        }}

        .week-lesson--changed summary {{
            background: var(--changed-bg);
            border-color: var(--changed-border);
        }}

        .week-lesson strong,
        .week-lesson span {{
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}

        .week-lesson strong {{
            font-size: 0.88rem;
        }}

        .week-lesson span {{
            color: var(--muted);
            font-size: 0.78rem;
            font-weight: 700;
        }}

        .popup-content {{
            position: absolute;
            z-index: 20;
            left: 0;
            top: calc(100% + 6px);
            width: min(310px, calc(100vw - 32px));
            padding: 14px;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
        }}

        .week-lesson.popup-open-up .popup-content {{
            top: auto;
            bottom: calc(100% + 6px);
        }}

        .week-lesson.popup-open-left .popup-content {{
            right: 0;
            left: auto;
        }}

        .week-lesson.popup-fixed .popup-content {{
            position: fixed;
            top: var(--popup-top);
            right: auto;
            bottom: auto;
            left: var(--popup-left);
            max-height: calc(100vh - 24px);
            overflow-y: auto;
        }}

        .popup-row {{
            display: grid;
            grid-template-columns: 78px 1fr;
            gap: 10px;
            padding: 7px 0;
            border-bottom: 1px solid var(--border);
        }}

        .popup-row:last-child {{
            border-bottom: 0;
        }}

        .popup-row span {{
            color: var(--muted);
            font-size: 0.82rem;
            font-weight: 800;
        }}

        .popup-row strong {{
            min-width: 0;
            overflow-wrap: anywhere;
        }}

        @media (max-width: 900px) {{
            main {{
                width: min(100% - 14px, 900px);
            }}

            .week-table {{
                min-width: 0;
            }}

            .week-table th,
            .week-table td {{
                padding: 4px;
            }}

            .period-head {{
                width: 32px;
                font-size: 0.78rem;
            }}

            .week-table thead th {{
                font-size: 0.74rem;
            }}

            .day-head-marker {{
                max-width: calc(100% - 2px);
                padding: 0.16rem 0.44rem 0.18rem;
            }}

            .week-table thead small {{
                font-size: 0.66rem;
            }}

            .week-lesson summary {{
                min-height: 48px;
                padding: 5px;
                border-radius: 8px;
            }}

            .week-lesson strong {{
                font-size: 0.72rem;
            }}

            .week-lesson span {{
                font-size: 0.62rem;
            }}

            .week-empty {{
                min-height: 48px;
                font-size: 0.7rem;
            }}

            .subject-grid {{
                grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
            }}
        }}

        @media (max-width: 620px) {{
            main {{
                width: calc(100% - 8px);
                padding: 10px 0;
            }}

            .brand h1 {{
                font-size: 1.55rem;
            }}

            .brand p,
            .meta {{
                font-size: 0.82rem;
            }}

            .panel,
            .message,
            .filter-card {{
                padding: 12px;
                border-radius: 14px;
            }}

            .class-message {{
                display: grid;
            }}

            .block-toggle-form {{ justify-self: start; margin-left: 0; }}

            .week-navigation {{
                grid-template-columns: 1fr 1.25fr 1fr;
                gap: 10px;
            }}

            .week-nav-button {{
                min-height: 48px;
                padding: 6px 8px;
                gap: 4px;
            }}

            .week-nav-button small {{
                font-size: 0.72rem;
            }}

            .filter-actions {{
                display: grid;
            }}

            .subject-grid {{
                grid-template-columns: 1fr;
            }}

            .week-table-wrap {{
                border-radius: 12px;
            }}

            .week-table th,
            .week-table td {{
                padding: 3px;
            }}

            .period-head {{
                width: 26px;
                font-size: 0.68rem;
            }}

            .week-table thead th {{
                font-size: 0.66rem;
            }}

            .day-head-marker {{
                display: flex;
                width: 100%;
                max-width: 100%;
                padding: 0.1rem 0.08rem 0.12rem;
            }}

            .week-table thead small {{
                font-size: 0.58rem;
            }}

            .week-lesson summary {{
                min-height: 42px;
                padding: 4px;
            }}

            .week-lesson strong {{
                font-size: 0.64rem;
            }}

            .week-lesson span {{
                font-size: 0.56rem;
            }}

            .week-empty {{
                min-height: 42px;
                font-size: 0.62rem;
            }}

            .popup-content {{
                width: min(340px, calc(100vw - 24px));
            }}
        }}
    </style>
</head>
<body>
    <main>
        <header class="topbar">
            <div class="brand">
                <h1>Vertretungsplan</h1>
                {render_vp_user_identity(session_username)}
            </div>

            {render_vp_navigation("classes", logout_csrf_token, can_change_pin=can_change_pin, force_pin_change=force_pin_change, pin_modal_error=pin_modal_error, pin_modal_changed=pin_modal_changed, session_username=session_username)}
        </header>

        <section class="panel">
            <div class="selected-week-label">Ausgewählte Woche: <strong>{escape(week_title)}</strong></div>
            {render_week_navigation(selected_date, selected_class, filters_active, block_mode)}

            <div class="meta">
                Neuester Planstand: {escape("Keine Plandaten verfügbar" if plan_timestamp_text == "unbekannt" else plan_timestamp_text)}
            </div>
        </section>

        {content}
    </main>{render_theme_script()}{render_today_marker_script()}
    <script>document.querySelector('[data-plan-class-select]')?.addEventListener('change', (event) => window.location.assign(event.currentTarget.value));</script>
    {f'''<script>
        (() => {{
            const initialVersion = {json.dumps(week_version)};
            const checkUrl = "/api/plan-version?woche={format_week_value(selected_date)}";

            setInterval(() => {{
                fetch(checkUrl, {{cache: "no-store"}})
                    .then(response => response.json())
                    .then(data => {{
                        if (data.version !== initialVersion) {{
                            window.location.reload();
                        }}
                    }})
                    .catch(() => {{}});
            }}, 5000);

            document.querySelectorAll("details.week-lesson").forEach(details => {{
                details.addEventListener("toggle", () => {{
                    details.classList.remove("popup-open-up", "popup-open-left", "popup-fixed");
                    details.style.removeProperty("--popup-left");
                    details.style.removeProperty("--popup-top");

                    if (!details.open) {{
                        return;
                    }}

                    document.querySelectorAll("details.week-lesson[open]").forEach(otherDetails => {{
                        if (otherDetails !== details) {{
                            otherDetails.removeAttribute("open");
                            otherDetails.classList.remove("popup-open-up", "popup-open-left", "popup-fixed");
                        }}
                    }});

                    requestAnimationFrame(() => {{
                        const popup = details.querySelector(".popup-content");
                        const detailsRect = details.getBoundingClientRect();
                        const popupWidth = popup.offsetWidth;
                        const popupHeight = Math.min(popup.offsetHeight, window.innerHeight - 24);
                        const spaceAbove = detailsRect.top;
                        const spaceBelow = window.innerHeight - detailsRect.bottom;
                        const popupSpace = popupHeight + 12;
                        const opensUp = spaceBelow < popupSpace && spaceAbove >= popupSpace;
                        let left = detailsRect.left;
                        let top = opensUp
                            ? detailsRect.top - popupHeight - 6
                            : detailsRect.bottom + 6;

                        if (opensUp) {{
                            details.classList.add("popup-open-up");
                        }}

                        if (left + popupWidth > window.innerWidth - 12) {{
                            left = detailsRect.right - popupWidth;
                            details.classList.add("popup-open-left");
                        }}

                        left = Math.max(12, Math.min(left, window.innerWidth - popupWidth - 12));
                        top = Math.max(12, Math.min(top, window.innerHeight - popupHeight - 12));
                        // Das Detailfenster bleibt absichtlich absolut am
                        // Stundenfeld verankert und scrollt mit dem Plan.
                    }});
                }});
            }});

            document.addEventListener("click", event => {{
                if (event.target.closest("details.week-lesson")) {{
                    return;
                }}

                document.querySelectorAll("details.week-lesson[open]").forEach(details => {{
                    details.removeAttribute("open");
                }});
            }});
        }})();
    </script>''' if week_version else ""}
</body>
</html>"""


class PlanPageHandler(BaseHTTPRequestHandler):
    """HTTP-Handler für die Vertretungsplan-Seite."""

    def do_GET(self):
        """Verarbeitet GET-Anfragen."""

        parsed_url = urlparse(self.path)
        query = parse_qs(parsed_url.query)
        browser_cookies = parse_cookie_header(self.headers.get("Cookie"))

        selected_date = parse_week(query_value(query, "woche"))

        if parsed_url.path == "/api/plan-version":
            week_plans = get_week_plans_for_page(selected_date)
            version = get_week_version(week_plans)

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps({"version": version}).encode("utf-8"))
            return

        selected_class = query_value(query, "klasse") or browser_cookies.get("selected_class")
        selected_subjects = []
        cookie_headers = []

        if query_value(query, "klasse_clear") == "1":
            selected_class = None
            cookie_headers.append(make_cookie("selected_class", "", max_age=0))

        subject_cookie_name = get_selected_subject_cookie_name(selected_class) if selected_class else None

        if selected_class and subject_cookie_name:
            selected_subjects = (
                query_values(query, "fach")
                or split_cookie_list(browser_cookies.get(subject_cookie_name))
            )

        if query_value(query, "fach_clear") == "1":
            selected_subjects = []

            if subject_cookie_name:
                cookie_headers.append(make_cookie(subject_cookie_name, "", max_age=0))

        if selected_class:
            cookie_headers.append(make_cookie("selected_class", selected_class))

        if selected_class and subject_cookie_name and "fach" in query:
            cookie_headers.append(make_cookie(subject_cookie_name, join_cookie_list(selected_subjects)))

        try:
            html = render_plan_page(selected_date, selected_class, selected_subjects)
        except ResourceNotFound:
            html = render_plan_page(
                selected_date,
                selected_class,
                selected_subjects,
                error_message="Für diese Woche wurden keine Vertretungsplandaten gefunden.",
            )
        except Unauthorized:
            html = render_plan_page(
                selected_date,
                selected_class,
                selected_subjects,
                error_message="Die Zugangsdaten sind ungültig oder haben keinen Zugriff auf diese Daten.",
            )
        except Exception as error:
            html = render_plan_page(
                selected_date,
                selected_class,
                selected_subjects,
                error_message=f"Beim Laden der Daten ist ein Fehler aufgetreten: {error}",
            )

        if query_value(query, "klasse_clear") == "1":
            redirect(self, f"/?woche={selected_date.isoformat()}", cookie_headers)
            return

        send_html(self, html, cookie_headers)

    def log_message(self, format, *args):
        """Unterdrückt HTTP-Logs."""

        return


def main(additional_port: int = 0):
    """Startet nur die Vertretungsplan-Seite."""

    try:
        start_server(PlanPageHandler, "Vertretungsplan-Seite", port=DEFAULT_PORT + additional_port)
    except OSError:
        additional_port += 1
        main(additional_port)


if __name__ == "__main__":
    main()
