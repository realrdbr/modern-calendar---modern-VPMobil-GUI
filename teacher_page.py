import json
from datetime import date, timedelta
from html import escape
from http.server import BaseHTTPRequestHandler
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlparse

from vp_data import (
    ResourceNotFound,
    Unauthorized,
    get_official_weekly_plans_for_page,
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
    render_theme_toggle_button,
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
            <p>Wähle ein Lehrerkürzel aus. Die Auswahl wird im Browser gespeichert.</p>
        </section>

        <section class="choice-grid">
            {"".join(teacher_links)}
        </section>
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


def render_lesson_cell(lessons: list) -> str:
    """Rendert eine Tabellenzelle."""

    if not lessons:
        return '<div class="week-empty">-</div>'

    cards = []

    for lesson in lessons:
        changed_class = "week-lesson--changed" if lesson.änderung or lesson.ausfall else ""

        cards.append(f"""
            <details class="week-lesson {changed_class}">
                <summary>
                    <strong>{escape(lesson.fach or "-")}</strong>
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
            <th>
                <span>{escape(day_name)}</span>
                <small>{plan_date.strftime("%d.%m.")}</small>
                {day_plan_info}
            </th>
        """)

    rows = []

    for period in range(1, max_period + 1):
        day_cells = []

        for plan_date in dates:
            lessons = week_lessons.get(period, {}).get(plan_date, [])
            day_cells.append(f"<td>{render_lesson_cell(lessons)}</td>")

        rows.append(f"""
            <tr>
                <th class="period-head">{period}</th>
                {"".join(day_cells)}
            </tr>
        """)

    return f"""
        <section class="week-table-wrap">
            <table class="week-table">
                <thead>
                    <tr>
                        <th class="period-head">Std.</th>
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
        f"{plan_date.isoformat()}:{getattr(plan, 'zeitstempel', '') or ''}"
        for plan_date, plan in week_plans.items()
    )


def render_week_navigation(selected_date: date, selected_teacher: str | None) -> str:
    """Rendert die Navigation für die vorherige und nächste Schulwoche."""

    previous_week = selected_date - timedelta(days=7)
    next_week = selected_date + timedelta(days=7)
    current_week = date.today() - timedelta(days=date.today().weekday())
    teacher_query = f"&{urlencode({'lehrer': selected_teacher})}" if selected_teacher else ""

    return f"""
        <div class="week-navigation" aria-label="Wochennavigation">
            <a class="week-nav-button" href="/lehrer?woche={format_week_value(previous_week)}{teacher_query}" aria-label="Vorherige Woche">
                <span aria-hidden="true">‹</span>
                <small>Zurück</small>
            </a>

            <a class="week-nav-button week-nav-button--current" href="/lehrer?woche={format_week_value(current_week)}{teacher_query}">
                <span aria-hidden="true">⌂</span>
                <small>Aktuelle Woche</small>
            </a>

            <a class="week-nav-button" href="/lehrer?woche={format_week_value(next_week)}{teacher_query}" aria-label="Nächste Woche">
                <span aria-hidden="true">›</span>
                <small>Weiter</small>
            </a>
        </div>
    """


def render_teacher_page(
    selected_date: date,
    selected_teacher: str | None = None,
    error_message: str | None = None,
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
        try:
            official_week_plans = get_official_weekly_plans_for_page(selected_date)
            for plan_date, weekly_plan in official_week_plans.items():
                if week_plans.get(plan_date) is None:
                    week_plans[plan_date] = weekly_plan
        except Exception:
            pass
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
                        <p>Wochenplan von Montag bis Freitag. Tippe eine Stunde an, um Details zu sehen.</p>
                    </div>

                    <a class="button button-secondary" href="/lehrer?woche={format_week_value(selected_date)}&lehrer_clear=1">
                        Anderen Lehrer wählen
                    </a>
                </section>

                {render_teacher_week_table(week_plans, selected_teacher)}
            """

    return f"""<!doctype html>
<html lang="de">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
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
            min-height: 64px;
            padding: 8px 14px;
            border: 1px solid var(--border);
            border-radius: 8px;
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

        .week-nav-button span {{
            font-size: 2rem;
            line-height: 1;
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
        }}

        .week-table thead span,
        .week-table thead small {{
            display: block;
        }}

        .week-table thead small {{
            color: var(--muted);
            font-weight: 700;
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

            .week-table thead small {{
                font-size: 0.66rem;
            }}

            .week-lesson summary {{
                min-height: 48px;
                padding: 5px;
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

            .week-nav-button span {{
                font-size: 1.55rem;
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
                <h1>Lehrerplan</h1>
                <p>Woche {escape(week_title)}</p>
            </div>

            <nav class="nav">
                <a href="/">Klassen</a>
                <a class="active" href="/lehrer">Lehrer</a>
                <a href="/raeume">Freie Räume</a>
                <a href="/abos">Ankündigungen</a>
                <a href="{escape(CALENDAR_PUBLIC_URL)}">Kalender</a>
                {render_theme_toggle_button()}
            </nav>
        </header>

        <section class="panel">
            {render_week_navigation(selected_date, selected_teacher)}

            <div class="meta">
                Neuester Planstand: {escape("Keine Plandaten verfügbar" if plan_timestamp_text == "unbekannt" else plan_timestamp_text)}
            </div>
        </section>

        {content}
    </main>{render_theme_script()}
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
                }}, 30000);
            }}

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
                        details.style.setProperty("--popup-left", `${{left}}px`);
                        details.style.setProperty("--popup-top", `${{top}}px`);
                        details.classList.add("popup-fixed");
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
