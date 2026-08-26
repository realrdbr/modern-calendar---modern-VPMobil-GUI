import json
from datetime import date
from html import escape
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from vp_data import (
    ResourceNotFound,
    Unauthorized,
    _json_env_list,
    find_free_rooms_in_plan,
    get_plan_for_page,
)
from web_utils import (
    CALENDAR_PUBLIC_URL,
    COMMON_CSS,
    DEFAULT_PORT,
    parse_date,
    parse_hour,
    query_value,
    render_theme_script,
    render_vp_navigation,
    send_html,
    start_server,
)

load_dotenv()

GOOD_ROOMS = set(_json_env_list("GOOD_ROOMS"))
MEDIUM_ROOMS = set(_json_env_list("MEDIUM_ROOMS"))
BAD_ROOMS = set(_json_env_list("BAD_ROOMS"))


def get_room_quality(room: int) -> str:
    """Gibt die Qualitätsklasse eines Raums zurück."""

    if room in GOOD_ROOMS:
        return "good"

    if room in MEDIUM_ROOMS:
        return "medium"

    if room in BAD_ROOMS:
        return "bad"

    return "unknown"


def sort_rooms_by_quality(rooms: list[int]) -> list[int]:
    """Sortiert Räume nach Qualität und Raumnummer."""

    quality_order = {
        "good": 0,
        "medium": 1,
        "bad": 2,
        "unknown": 3,
    }

    return sorted(
        rooms,
        key=lambda room: (quality_order[get_room_quality(room)], room),
    )


def render_rooms_page(
    selected_date: date,
    selected_hour: int,
    free_rooms: list[int] | None = None,
    error_message: str | None = None,
    plan_version: str = "",
    logout_csrf_token: str | None = None,
) -> str:
    """Erzeugt die HTML-Seite für freie Räume."""

    room_cards = ""

    if free_rooms is not None:
        free_rooms = sort_rooms_by_quality(free_rooms)

        if free_rooms:
            room_cards = "\n".join(
                f'<div class="room-card room-card--{get_room_quality(room)}">{room}</div>'
                for room in free_rooms
            )
        else:
            room_cards = '<p class="empty">In dieser Stunde wurde kein freier Raum gefunden.</p>'

    result_block = ""

    if error_message:
        result_block = f"""
            <section class="message message--error">
                <h2>Keine Daten verfügbar</h2>
                <p>{escape(error_message)}</p>
            </section>
        """
    elif free_rooms is not None:
        result_block = f"""
            <section class="result">
                <h2>Freie Räume in der {selected_hour}. Stunde</h2>
                <p class="summary">
                    Datum: {selected_date.strftime("%d.%m.%Y")} ·
                    Anzahl freier Räume: {len(free_rooms)}
                </p>

                <div class="legend">
                    <span><span class="legend-dot legend-dot--good"></span>Gut</span>
                    <span><span class="legend-dot legend-dot--medium"></span>Mittel gut</span>
                    <span><span class="legend-dot legend-dot--bad"></span>Schlecht</span>
                </div>

                <div class="room-grid">
                    {room_cards}
                </div>
            </section>
        """

    hour_options = "\n".join(
        f'<option value="{hour}" {"selected" if hour == selected_hour else ""}>{hour}. Stunde</option>'
        for hour in range(1, 9)
    )

    return f"""<!doctype html>
<html lang="de">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Freie Räume</title>
    <style>
        {COMMON_CSS}

        main {{
            width: min(1320px, calc(100% - 24px));
        }}

        .result {{
            padding: 22px;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
        }}

        .result h2 {{
            margin: 0 0 8px;
        }}

        .summary {{
            margin: 0 0 16px;
            color: var(--muted);
        }}

        .legend {{
            display: flex;
            flex-wrap: wrap;
            gap: 14px;
            margin-bottom: 20px;
            color: var(--muted);
            font-size: 0.95rem;
            font-weight: 700;
        }}

        .legend span {{
            display: inline-flex;
            align-items: center;
            gap: 7px;
        }}

        .legend-dot {{
            width: 12px;
            height: 12px;
            border-radius: 999px;
            border: 1px solid transparent;
        }}

        .legend-dot--good {{
            background: var(--good-bg);
            border-color: var(--good-border);
        }}

        .legend-dot--medium {{
            background: var(--medium-bg);
            border-color: var(--medium-border);
        }}

        .legend-dot--bad {{
            background: var(--bad-bg);
            border-color: var(--bad-border);
        }}

        .room-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(86px, 1fr));
            gap: 12px;
        }}

        .room-card {{
            padding: 14px 10px;
            text-align: center;
            border-radius: 6px;
            font-size: 1.15rem;
            font-weight: 900;
            border: 1px solid transparent;
        }}

        .room-card--good {{
            background: var(--good-bg);
            border-color: var(--good-border);
            color: var(--good-text);
        }}

        .room-card--medium {{
            background: var(--medium-bg);
            border-color: var(--medium-border);
            color: var(--medium-text);
        }}

        .room-card--bad {{
            background: var(--bad-bg);
            border-color: var(--bad-border);
            color: var(--bad-text);
        }}

        .room-card--unknown {{
            background: var(--unknown-bg);
            border-color: var(--unknown-border);
            color: var(--unknown-text);
        }}

        @media (max-width: 620px) {{
            .room-grid {{
                grid-template-columns: repeat(auto-fill, minmax(72px, 1fr));
                gap: 10px;
            }}

            .room-card {{
                padding: 12px 8px;
                font-size: 1rem;
            }}
        }}
    </style>
</head>
<body>
    <main>
        <header class="topbar">
            <div class="brand">
                <h1>Freie Räume</h1>
                <p>Freie Räume nach Datum und Stunde anzeigen.</p>
            </div>

            {render_vp_navigation("rooms", logout_csrf_token)}
        </header>

        <section class="panel">
            <form method="get" action="/raeume" class="form-row">
                <label>
                    Datum
                    <input type="date" name="datum" value="{selected_date.isoformat()}">
                </label>

                <label>
                    Stunde
                    <select name="stunde">
                        {hour_options}
                    </select>
                </label>

                <button type="submit">Anzeigen</button>
            </form>

            <div class="meta">
                Räume werden farblich nach Qualität sortiert.
            </div>
        </section>

        {result_block}
    </main>{render_theme_script()}
    {f'''<script>
        (() => {{
            const initialVersion = {json.dumps(plan_version)};

            if (!initialVersion) {{
                return;
            }}

            setInterval(() => {{
                fetch("/api/room-version?datum={selected_date.isoformat()}", {{cache: "no-store"}})
                    .then(response => response.json())
                    .then(data => {{
                        if (data.version !== initialVersion) {{
                            window.location.reload();
                        }}
                    }})
                    .catch(() => {{}});
            }}, 30000);
        }})();
    </script>''' if plan_version else ""}
</body>
</html>"""


class RoomsPageHandler(BaseHTTPRequestHandler):
    """HTTP-Handler für die Freie-Räume-Seite."""

    def do_GET(self):
        """Verarbeitet GET-Anfragen für die Freie-Räume-Seite."""

        parsed_url = urlparse(self.path)
        query = parse_qs(parsed_url.query)

        selected_date = parse_date(query_value(query, "datum"))

        if parsed_url.path == "/api/room-version":
            plan = get_plan_for_page(selected_date)

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(
                json.dumps({"version": str(getattr(plan, "zeitstempel", "") or "")}).encode("utf-8")
            )
            return

        selected_hour = parse_hour(query_value(query, "stunde"))

        free_rooms = None
        error_message = None
        plan_version = ""

        try:
            plan = get_plan_for_page(selected_date)
            free_rooms = find_free_rooms_in_plan(plan, selected_hour)
            plan_version = str(getattr(plan, "zeitstempel", "") or "")
        except ResourceNotFound:
            error_message = "Für dieses Datum wurden keine Vertretungsplandaten gefunden."
        except Unauthorized:
            error_message = "Die Zugangsdaten sind ungültig oder haben keinen Zugriff auf diese Daten."
        except Exception as error:
            error_message = f"Beim Laden der Daten ist ein Fehler aufgetreten: {error}"

        html = render_rooms_page(
            selected_date=selected_date,
            selected_hour=selected_hour,
            free_rooms=free_rooms,
            error_message=error_message,
            plan_version=plan_version,
        )

        send_html(self, html)

    def log_message(self, format, *args):
        """Unterdrückt die normalen HTTP-Logs im Terminal."""

        return


def main(additional_port: int = 0):
    """Startet nur die Freie-Räume-Seite."""

    try:
        start_server(RoomsPageHandler, "Freie-Räume-Seite", port = DEFAULT_PORT + additional_port)
    except OSError:
        additional_port += 1
        main(additional_port)


if __name__ == "__main__":
    main()
