import json
from datetime import date, timedelta
from html import escape
from http.server import BaseHTTPRequestHandler
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlparse

from vp_data import (
    ResourceNotFound,
    Unauthorized,
    get_week_plans_for_page,
)
from web_utils import (
    CALENDAR_PUBLIC_URL,
    COMMON_CSS,
    format_week_value,
    get_ab_week_label,
    make_cookie,
    parse_cookie_header,
    parse_week,
    query_value,
    redirect,
    send_html,
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


def format_time(value) -> str:
    """Formatiert eine Uhrzeit."""

    if value is None:
        return ""

    return value.strftime("%H:%M")


def format_tuple(values: tuple[str, ...]) -> str:
    """Formatiert mehrere Werte als Text."""

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


def get_available_teachers(week_plans: dict[date, object | None]) -> list[str]:
    """Sammelt alle Lehrerkürzel, die in der Woche vorkommen."""

    teachers = set()

    for plan in week_plans.values():
        if plan is None:
            continue

        if hasattr(plan, "lehrer"):
            teachers.update(getattr(plan, "lehrer").keys())
            continue

        for class_item in getattr(plan, "klassen", {}).values():
            for lesson_items in class_item.stunden.values():
                for lesson in lesson_items:
                    teachers.update(teacher for teacher in getattr(lesson, "lehrer", ()) if teacher)

    return sorted(teacher for teacher in teachers if teacher)


def _normalize_teacher_lesson(lesson, class_name: str):
    if hasattr(lesson, "klassen"):
        return lesson

    return SimpleNamespace(
        fach=getattr(lesson, "fach", None),
        klassen=(class_name,),
        lehrer=getattr(lesson, "lehrer", ()),
        räume=getattr(lesson, "räume", ()),
        beginn=getattr(lesson, "beginn", None),
        ende=getattr(lesson, "ende", None),
        änderung=getattr(lesson, "änderung", False),
        ausfall=getattr(lesson, "ausfall", False),
        info=getattr(lesson, "info", None),
    )


def collect_teacher_lessons(
    week_plans: dict[date, object | None],
    selected_teacher: str,
) -> dict[int, dict[date, list]]:
    """Sammelt alle Stunden eines Lehrers von Montag bis Freitag."""

    week_lessons: dict[int, dict[date, list]] = {}

    for plan_date, plan in week_plans.items():
        if plan is None:
            continue

        if hasattr(plan, "lehrer"):
            if selected_teacher not in plan.lehrer:
                continue

            teacher_item = plan.lehrer[selected_teacher]

            for period, lesson_items in teacher_item.stunden.items():
                for lesson in lesson_items:
                    week_lessons.setdefault(int(period), {}).setdefault(plan_date, []).append(lesson)
            continue

        for class_name, class_item in getattr(plan, "klassen", {}).items():
            for period, lesson_items in class_item.stunden.items():
                for lesson in lesson_items:
                    if selected_teacher not in getattr(lesson, "lehrer", ()):
                        continue

                    normalized_lesson = _normalize_teacher_lesson(lesson, class_name)
                    week_lessons.setdefault(int(period), {}).setdefault(plan_date, []).append(normalized_lesson)

    return week_lessons


def render_teacher_selection(week_plans: dict[date, object | None], selected_date: date) -> str:
    """Rendert die Auswahl aller Lehrer."""

    teacher_links = []

    for teacher in get_available_teachers(week_plans):
        query = urlencode({
            "woche": format_week_value(selected_date),
            "lehrer": teacher,
        })

        teacher_links.append(
            f'<a class="choice-card" href="/lehrer?{query}">{escape(teacher)}</a>'
        )

    if not teacher_links:
        return '<p class="empty">Es wurden keine Lehrer gefunden.</p>'

    return f"""
        <section class="message">
            <h2>Lehrer auswählen</h2>
        </section>

        <section class="choice-grid">
            {"".join(teacher_links)}
        </section>
    """


def render_day_info_marker(day_plan) -> str:
    """Rendert das kleine "i"-Symbol mit PopUp für Tages-Zusatzinformationen.

    Nutzt dieselbe PopUp-Optik/-Logik wie die Stunden-Infofenster
    (details.week-lesson + .popup-content), inklusive Scroll-Verhalten.
    """

    zusatzinfo = getattr(day_plan, "zusatzinfo", None) if day_plan is not None else None
    if not zusatzinfo or not zusatzinfo.strip():
        return ""

    info_html = "".join(
        f"<p>{escape(line)}</p>" if line.strip() else "<br>"
        for line in zusatzinfo.splitlines()
    )

    return f"""
        <details class="week-lesson day-info-marker">
            <summary aria-label="Zusätzliche Informationen zum Tag" title="Zusätzliche Informationen zum Tag">i</summary>
            <div class="popup-content day-info-popup">
                {info_html}
            </div>
        </details>
    """


def render_lesson_details(lesson) -> str:
    """Erzeugt den Detailbereich einer Stunde."""

    rows = [
        ("Zeit", f"{format_time(lesson.beginn)} - {format_time(lesson.ende)}"),
        ("Klasse", format_tuple(lesson.klassen)),
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
    """Rendert eine Tabellenzelle."""

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
                    <span>{escape(format_tuple(lesson.klassen))}</span>
                    <span>{escape(format_tuple(lesson.räume))}</span>
                </summary>

                <div class="popup-content">
                    {render_lesson_details(lesson)}
                </div>
            </details>
        """)

    return "".join(cards)


def render_teacher_week_table(
    week_plans: dict[date, object | None],
    selected_teacher: str,
    block_mode: bool = False,
) -> str:
    """Rendert den Wochenplan eines Lehrers."""

    week_lessons = collect_teacher_lessons(week_plans, selected_teacher)

    if not week_lessons:
        return '<p class="empty">Für diesen Lehrer wurden in dieser Woche keine Stunden gefunden.</p>'

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
                {render_day_info_marker(day_plan)}
                <div class="day-head-marker">
                    <span>{escape(day_name)}</span>
                    <small>{plan_date.strftime("%d.%m.")}</small>
                    {day_plan_info}
                </div>
            </th>
        """)

    rows = []

    periods = [p for p in range(1, max_period + 1) if not block_mode or p % 2 == 1]
    for period in periods:
        day_cells = []

        for plan_date in dates:
            lessons = week_lessons.get(period, {}).get(plan_date, [])
            period_labels = None
            if block_mode and period + 1 <= max_period:
                second = week_lessons.get(period + 1, {}).get(plan_date, [])
                signature = lambda items: [(getattr(x, 'fach', ''), tuple(getattr(x, 'klassen', ())), tuple(getattr(x, 'räume', ()))) for x in items]
                if signature(lessons) == signature(second):
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
    """Erzeugt den Titel für die Woche."""

    dates = list(week_plans.keys())

    if not dates:
        return "Woche"

    ab_label = get_ab_week_label(dates[0])
    return f"{dates[0].strftime('%d.%m.')} - {dates[-1].strftime('%d.%m.%Y')} {ab_label}"


def get_latest_timestamp_text(week_plans: dict[date, object | None]) -> str:
    """Gibt den neuesten Planstand der Woche zurück."""

    timestamps = [
        plan.zeitstempel
        for plan in week_plans.values()
        if plan is not None and plan.zeitstempel is not None
    ]

    if not timestamps:
        return "unbekannt"

    return max(timestamps).strftime("%d.%m.%Y %H:%M")


def get_week_version(week_plans: dict[date, object | None]) -> str:
    """Erzeugt eine kurze Version aus den Zeitstempeln der Wochenpläne."""

    return "|".join(
        f"{plan_date.isoformat()}:{'vorhanden' if plan is not None else 'fehlend'}:{getattr(plan, 'zeitstempel', '') or ''}"
        for plan_date, plan in week_plans.items()
    )


def render_week_navigation(selected_date: date, selected_teacher: str | None, block_mode: bool = False) -> str:
    """Rendert die Navigation für die vorherige und nächste Schulwoche."""

    previous_week = selected_date - timedelta(days=7)
    next_week = selected_date + timedelta(days=7)
    current_week = date.today() - timedelta(days=date.today().weekday())
    selected_week = selected_date - timedelta(days=selected_date.weekday())
    is_current_week = selected_week == current_week
    query_values = ({'lehrer': selected_teacher} if selected_teacher else {})
    if block_mode:
        query_values['block'] = '1'
    teacher_query = f"&{urlencode(query_values)}" if query_values else ""

    return f"""
        <div class="week-navigation" aria-label="Wochennavigation">
            <a class="week-nav-button" href="/lehrer?woche={format_week_value(previous_week)}{teacher_query}" aria-label="Vorherige Woche">
                <svg class="week-nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m15 18-6-6 6-6"></path></svg>
                <small>Zurück</small>
            </a>

            <a class="week-nav-button {'week-nav-button--current' if is_current_week else ''}" href="/lehrer?woche={format_week_value(current_week)}{teacher_query}">
                <svg class="week-nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 10.5 12 3l9 7.5"></path><path d="M5 9.5V21h14V9.5M9 21v-6h6v6"></path></svg>
                <small>Aktuelle Woche</small>
            </a>

            <a class="week-nav-button" href="/lehrer?woche={format_week_value(next_week)}{teacher_query}" aria-label="Nächste Woche">
                <svg class="week-nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"></path></svg>
                <small>Weiter</small>
            </a>
        </div>
    """


def render_teacher_page(
    selected_date: date,
    selected_teacher: str | None = None,
    error_message: str | None = None,
    logout_csrf_token: str | None = None,
    can_change_pin: bool = False,
    force_pin_change: bool = False,
    pin_modal_error: str | None = None,
    pin_modal_changed: bool = False,
    session_username: str | None = None,
    block_mode: bool = False,
) -> str:
    """Erzeugt die komplette Lehrerplan-Seite."""

    week_title = f"{selected_date.strftime('%d.%m.%Y')} {get_ab_week_label(selected_date)}"
    plan_timestamp_text = "unbekannt"
    week_version = ""
    content = ""

    if error_message:
        content = f"""
            <section class="message message--error">
                <h2>Keine Daten verfügbar</h2>
                <p>{escape(error_message)}</p>
            </section>
        """
    else:
        week_plans = get_week_plans_for_page(selected_date)
        week_title = get_week_title(week_plans)
        plan_timestamp_text = get_latest_timestamp_text(week_plans)
        week_version = get_week_version(week_plans)

        available_teachers = get_available_teachers(week_plans)

        if not selected_teacher or selected_teacher not in available_teachers:
            content = render_teacher_selection(week_plans, selected_date)
        else:
            content = f"""
                <section class="message class-message">
                    <div>
                        <h2>Lehrer {escape(selected_teacher)}</h2>
                    </div>

                    <label class="block-switch"><span>Block-Unterricht</span><input type="checkbox" {'checked' if block_mode else ''} onchange="window.location.href='/lehrer?woche={format_week_value(selected_date)}&lehrer={escape(selected_teacher)}&block='+(this.checked?'1':'0')" aria-label="Block-Unterricht umschalten"><span class="block-switch-track" aria-hidden="true"><span></span></span></label>
                    <select class="class-select" aria-label="Lehrer auswählen" data-plan-teacher-select>{''.join(f'<option value="/lehrer?woche={format_week_value(selected_date)}&amp;lehrer={escape(teacher_name)}{"&amp;block=1" if block_mode else ""}"{" selected" if teacher_name == selected_teacher else ""}>{escape(teacher_name)}</option>' for teacher_name in available_teachers)}</select>
                </section>

                {render_teacher_week_table(week_plans, selected_teacher, block_mode)}
            """

    return f"""<!doctype html>
<html lang="de">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="icon" href="/icons/favicon.png" type="image/png">
    <title>Lehrerplan</title>
    <style>
        {COMMON_CSS}

        main {{
            width: min(1320px, calc(100% - 24px));
        }}

        .class-message {{
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            align-items: center;
            justify-content: space-between;
        }}

        .button-secondary {{
            background: var(--surface-muted);
            color: var(--text);
            border: 1px solid var(--border);
        }}

        .block-switch {{ display:inline-flex; align-items:center; gap:8px; min-height:36px; margin-left:auto; color:var(--text); font-size:.82rem; font-weight:700; cursor:pointer; user-select:none; }}
        .block-switch input {{ position:absolute; opacity:0; width:1px; height:1px; }}
        .block-switch-track {{ width:40px; height:22px; padding:2px; border:1px solid var(--border); border-radius:999px; background:var(--surface-muted); transition:background .15s,border-color .15s; }}
        .block-switch-track span {{ display:block; width:16px; height:16px; border-radius:50%; background:var(--muted); transition:transform .15s,background .15s; }}
        .block-switch input:checked + .block-switch-track {{ background:var(--primary); border-color:var(--primary); }}
        .block-switch input:checked + .block-switch-track span {{ transform:translateX(18px); background:#fff; }}
        .block-switch input:focus-visible + .block-switch-track {{ outline:3px solid color-mix(in srgb, var(--primary) 35%, transparent); outline-offset:2px; }}

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
            background: var(--surface-muted);
            font-size: 0.9rem;
            position: relative;
            overflow: hidden;
        }}

        .week-table thead th[data-plan-date] > *:not(.day-info-marker) {{
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
            cursor: pointer;
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
            transform: none;
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

        .day-info-marker {{
            position: absolute;
            top: 4px;
            right: 4px;
            z-index: 3;
            margin: 0;
        }}

        .day-info-marker summary {{
            display: grid;
            place-items: center;
            width: 16px;
            height: 16px;
            min-height: 0;
            min-width: 0;
            flex: none;
            box-sizing: border-box;
            padding: 0;
            border: 1px solid var(--border);
            border-radius: 50%;
            background: var(--surface-muted);
            color: var(--muted);
            font-size: 0.68rem;
            font-weight: 900;
            font-style: italic;
            line-height: 1;
            cursor: pointer;
            list-style: none;
        }}

        .day-info-marker summary::-webkit-details-marker {{
            display: none;
        }}

        .day-info-popup {{
            left: 50%;
            transform: translateX(-50%);
            max-height: min(320px, calc(100vh - 24px));
            overflow-y: auto;
            text-align: left;
            white-space: normal;
        }}

        .week-lesson.day-info-marker.popup-open-left .popup-content {{
            right: auto;
        }}

        .day-info-popup p {{
            margin: 0 0 8px;
            font-size: 0.82rem;
            line-height: 1.4;
        }}

        .day-info-popup p:last-child {{
            margin-bottom: 0;
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
            }}

            .day-info-marker summary {{
                width: 16px;
                height: 16px;
                min-height: 0;
                padding: 0;
                border-radius: 50%;
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
            .message {{
                padding: 12px;
                border-radius: 14px;
            }}

            .class-message {{
                display: grid;
                grid-template-columns: 1fr auto;
                align-items: center;
            }}

            .class-message .block-switch {{ justify-self: end; margin-left: 0; }}

            .class-message .class-select {{
                grid-column: 1 / -1;
                width: 100%;
            }}

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

            .day-info-marker summary {{
                width: 16px;
                height: 16px;
                min-height: 0;
                padding: 0;
                border-radius: 50%;
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

            {render_vp_navigation("teachers", logout_csrf_token, can_change_pin=can_change_pin, force_pin_change=force_pin_change, pin_modal_error=pin_modal_error, pin_modal_changed=pin_modal_changed, session_username=session_username)}
        </header>

        <section class="panel">
            <div class="selected-week-label">Ausgewählte Woche: <strong>{escape(week_title)}</strong></div>
            {render_week_navigation(selected_date, selected_teacher, block_mode)}

            <div class="meta">
                Neuester Planstand: {escape("Keine Plandaten verfügbar" if plan_timestamp_text == "unbekannt" else plan_timestamp_text)}
            </div>
        </section>

        {content}
    </main>{render_theme_script()}{render_today_marker_script()}
    <script>document.querySelector('[data-plan-teacher-select]')?.addEventListener('change', (event) => window.location.assign(event.currentTarget.value));</script>
    {f'''<script>
        (() => {{
            const initialVersion = {json.dumps(week_version)};
            const checkUrl = "/api/plan-version?woche={format_week_value(selected_date)}";

            if (initialVersion) {{
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
            }}

            document.querySelectorAll("details.week-lesson").forEach(details => {{
                const originalParent = details.parentNode;
                const originalNextSibling = details.nextSibling;

                details.addEventListener("toggle", () => {{
                    details.classList.remove("popup-open-up", "popup-open-left", "popup-fixed");
                    details.style.removeProperty("--popup-left");
                    details.style.removeProperty("--popup-top");

                    if (!details.open) {{
                        if (details.classList.contains("day-info-marker") && details.parentNode === document.body) {{
                            details.style.removeProperty("position");
                            details.style.removeProperty("top");
                            details.style.removeProperty("left");
                            details.style.removeProperty("right");
                            details.style.removeProperty("margin");
                            if (originalNextSibling) {{
                                originalParent.insertBefore(details, originalNextSibling);
                            }} else {{
                                originalParent.appendChild(details);
                            }}
                        }}
                        return;
                    }}

                    document.querySelectorAll("details.week-lesson[open]").forEach(otherDetails => {{
                        if (otherDetails !== details) {{
                            otherDetails.removeAttribute("open");
                        }}
                    }});

                    if (details.classList.contains("day-info-marker")) {{
                        // Tages-Info-Popups liegen in einer sticky
                        // Tageskopfzeile, die einen eigenen Stacking-Context
                        // bildet. Damit das Popup wirklich über allem liegt,
                        // wird es kurzzeitig direkt an <body> gehängt. Die
                        // Position wird vorher gemerkt, damit das i beim
                        // Umhängen nicht an eine andere Stelle springt.
                        const markerRect = details.getBoundingClientRect();
                        document.body.appendChild(details);
                        details.style.position = "fixed";
                        details.style.top = `${{markerRect.top}}px`;
                        details.style.left = `${{markerRect.left}}px`;
                        details.style.right = "auto";
                        details.style.margin = "0";
                    }}

                    requestAnimationFrame(() => {{
                        const popup = details.querySelector(".popup-content");
                        const detailsRect = details.getBoundingClientRect();
                        const popupWidth = popup.offsetWidth;
                        const popupHeight = Math.min(popup.offsetHeight, window.innerHeight - 24);
                        const popupSpace = popupHeight + 12;
                        const opensUp = (
                            window.innerHeight - detailsRect.bottom < popupSpace
                            && detailsRect.top >= popupSpace
                        );
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

                        // Fixed positioning verhindert, dass Popups von
                        // Vorfahren mit overflow:hidden (z.B. sticky
                        // Tageskopfzeilen) abgeschnitten werden.
                        details.classList.add("popup-fixed");
                        details.style.setProperty("--popup-left", `${{left}}px`);
                        details.style.setProperty("--popup-top", `${{top}}px`);
                    }});
                }});
            }});

            document.addEventListener("click", event => {{
                if (!event.target.closest("details.week-lesson")) {{
                    document.querySelectorAll("details.week-lesson[open]").forEach(details => {{
                        details.removeAttribute("open");
                    }});
                }}
            }});
        }})();
    </script>''' if week_version else ""}
</body>
</html>"""


class TeacherPageHandler(BaseHTTPRequestHandler):
    """HTTP-Handler für die Lehrerplan-Seite."""

    def do_GET(self):
        """Verarbeitet GET-Anfragen."""

        parsed_url = urlparse(self.path)
        query = parse_qs(parsed_url.query)

        if parsed_url.path == "/api/plan-version":
            selected_date = parse_week(query_value(query, "woche"))
            week_plans = get_week_plans_for_page(selected_date)

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(
                json.dumps({"version": get_week_version(week_plans)}).encode("utf-8")
            )
            return

        browser_cookies = parse_cookie_header(self.headers.get("Cookie"))

        selected_date = parse_week(query_value(query, "woche"))
        selected_teacher = query_value(query, "lehrer") or browser_cookies.get("selected_teacher")
        cookie_headers = []

        if query_value(query, "lehrer_clear") == "1":
            selected_teacher = None
            cookie_headers.append(make_cookie("selected_teacher", "", max_age=0))

        if selected_teacher:
            cookie_headers.append(make_cookie("selected_teacher", selected_teacher))

        if query_value(query, "lehrer_clear") == "1":
            redirect(self, f"/lehrer?woche={format_week_value(selected_date)}", cookie_headers)
            return

        try:
            html = render_teacher_page(selected_date, selected_teacher)
        except ResourceNotFound:
            html = render_teacher_page(
                selected_date,
                selected_teacher,
                error_message="Für diese Woche wurden keine Lehrerdaten gefunden.",
            )
        except Unauthorized:
            html = render_teacher_page(
                selected_date,
                selected_teacher,
                error_message="Die Zugangsdaten sind ungültig oder haben keinen Zugriff auf diese Daten.",
            )
        except Exception as error:
            html = render_teacher_page(
                selected_date,
                selected_teacher,
                error_message=f"Beim Laden der Daten ist ein Fehler aufgetreten: {error}",
            )

        send_html(self, html, cookie_headers)

    def log_message(self, format, *args):
        """Unterdrückt HTTP-Logs."""

        return


def main(additional_port: int = 0):
    """Startet nur die Lehrerplan-Seite."""

    try:
        start_server(TeacherPageHandler, "Lehrerplan-Seite", port=DEFAULT_PORT + additional_port)
    except OSError:
        additional_port += 1
        main(additional_port)


if __name__ == "__main__":
    main()
