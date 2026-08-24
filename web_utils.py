import os
from datetime import date, datetime, timedelta
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dotenv import load_dotenv

load_dotenv()

DEFAULT_PORT = int(os.getenv("VP_PORT", os.getenv("PORT", 8000)))
_requested_host = os.getenv("BIND_HOST", os.getenv("HOST", "127.0.0.1"))
DEFAULT_HOST = "0.0.0.0" if os.path.exists("/.dockerenv") and _requested_host in {"127.0.0.1", "localhost"} else _requested_host
CALENDAR_PUBLIC_URL = os.getenv("CALENDAR_PUBLIC_URL", "http://127.0.0.1:3000").rstrip("/")
VERTRETUNGSPLAN_PUBLIC_URL = os.getenv("VERTRETUNGSPLAN_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")


def parse_date(value: str | None) -> date:
    """Wandelt einen Formularwert in ein gültiges date-Objekt um."""

    if not value:
        return date.today()

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return date.today()


def parse_week(value: str | None) -> date:
    """Wandelt einen HTML-Wochenwert wie '2026-W34' in den Montag dieser Woche um."""

    if not value:
        today = date.today()
        return today - timedelta(days=today.weekday())

    try:
        year_text, week_text = value.split("-W", 1)
        return date.fromisocalendar(int(year_text), int(week_text), 1)
    except (ValueError, TypeError):
        today = date.today()
        return today - timedelta(days=today.weekday())


def format_week_value(selected_date: date) -> str:
    """Formatiert ein Datum als HTML-Wochenwert."""

    year, week, _ = selected_date.isocalendar()
    return f"{year}-W{week:02d}"


def parse_hour(value: str | None) -> int:
    """Wandelt einen Formularwert in eine gültige Unterrichtsstunde um."""

    try:
        hour = int(value or "1")
    except ValueError:
        return 1

    if hour < 1 or hour > 8:
        return 1

    return hour


def parse_cookie_header(cookie_header: str | None) -> dict[str, str]:
    """Liest Cookies aus dem Request-Header."""

    if not cookie_header:
        return {}

    parsed_cookies = cookies.SimpleCookie()
    parsed_cookies.load(cookie_header)

    return {
        key: morsel.value
        for key, morsel in parsed_cookies.items()
    }


def make_cookie(
    name: str, value: str, max_age: int = 60 * 60 * 24 * 180, *,
    http_only: bool = False, secure: bool = False,
) -> str:
    """Erzeugt einen Cookie-Header."""

    cookie = cookies.SimpleCookie()
    cookie[name] = value
    cookie[name]["path"] = "/"
    cookie[name]["max-age"] = str(max_age)
    cookie[name]["samesite"] = "Lax"
    if http_only:
        cookie[name]["httponly"] = True
    if secure:
        cookie[name]["secure"] = True

    return cookie.output(header="").strip()


def send_html(handler: BaseHTTPRequestHandler, html: str, cookie_headers: list[str] | None = None) -> None:
    """Sendet eine HTML-Antwort an den Browser."""

    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("X-Frame-Options", "DENY")
    handler.send_header("Referrer-Policy", "same-origin")
    handler.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")
    handler.send_header("Cache-Control", "no-store")

    for cookie_header in cookie_headers or []:
        handler.send_header("Set-Cookie", cookie_header)

    handler.end_headers()
    handler.wfile.write(html.encode("utf-8"))


def redirect(handler: BaseHTTPRequestHandler, location: str, cookie_headers: list[str] | None = None) -> None:
    """Leitet den Browser weiter."""

    handler.send_response(303)
    handler.send_header("Location", location)

    for cookie_header in cookie_headers or []:
        handler.send_header("Set-Cookie", cookie_header)

    handler.end_headers()


def query_value(query: dict[str, list[str]], name: str, default: str | None = None) -> str | None:
    """Liest den ersten Wert eines Query-Parameters aus."""

    return query.get(name, [default])[0]


def query_values(query: dict[str, list[str]], name: str) -> list[str]:
    """Liest alle Werte eines Query-Parameters aus."""

    return query.get(name, [])


def split_cookie_list(value: str | None) -> list[str]:
    """Wandelt eine kommaseparierte Cookie-Liste in einzelne Werte um."""

    if not value:
        return []

    return [
        item.strip()
        for item in value.split(",")
        if item.strip()
    ]


def join_cookie_list(values: list[str]) -> str:
    """Wandelt eine Liste in einen kompakten Cookie-Wert um."""

    return ",".join(values)


def start_server(handler_class: type[BaseHTTPRequestHandler], title: str, port: int = DEFAULT_PORT) -> None:
    """Startet einen lokalen HTTP-Server."""

    server = ThreadingHTTPServer((DEFAULT_HOST, port), handler_class)

    print(f"{title} läuft unter http://{DEFAULT_HOST}:{port}")
    print("Zum Beenden Strg+C drücken.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer wird beendet.")
    finally:
        server.server_close()


def render_theme_toggle_button() -> str:
    return (
        '<label class="theme-toggle" title="Darstellung wechseln">'
        '<input type="checkbox" data-theme-toggle aria-label="Dunkelmodus umschalten">'
        '<span class="theme-slider" aria-hidden="true">'
        '<svg class="theme-icon theme-icon--sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.42"></path></svg>'
        '<svg class="theme-icon theme-icon--moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.99 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 20.99 12.79z"></path></svg>'
        '</span>'
        '</label>'
    )


def render_theme_script() -> str:
    return """
    <script>
        (() => {
            const COOKIE_NAME = "vp_theme";
            const root = document.documentElement;
            const toggle = document.querySelector("[data-theme-toggle]");
            const prefersDark = window.matchMedia("(prefers-color-scheme: dark)");

            const readCookie = () => {
                const match = document.cookie.split("; ").find((item) => item.startsWith(COOKIE_NAME + "="));
                if (!match) return "";
                return decodeURIComponent(match.split("=", 2)[1] || "");
            };

            const writeCookie = (value) => {
                document.cookie = `${COOKIE_NAME}=${encodeURIComponent(value)}; Max-Age=31536000; Path=/; SameSite=Lax`;
            };

            const applyTheme = (isDark) => {
                root.setAttribute("data-theme", isDark ? "dark" : "light");
                if (toggle) toggle.checked = isDark;
            };

            const cookieValue = readCookie();
            const initialDark = cookieValue === "dark" || (cookieValue === "" && prefersDark.matches);
            applyTheme(initialDark);
            if (!toggle) {
                return;
            }

            toggle.addEventListener("change", () => {
                const isDark = !!toggle.checked;
                writeCookie(isDark ? "dark" : "light");
                applyTheme(isDark);
            });
        })();
    </script>
    """


COMMON_CSS = """
:root {
    --background: #f4f6f8;
    --surface: #ffffff;
    --surface-muted: #f8fafc;
    --primary: #2454d6;
    --primary-dark: #1d43aa;
    --text: #172033;
    --muted: #667085;
    --border: #d0d5dd;
    --changed-bg: #fee2e2;
    --changed-border: #fca5a5;
    --cancelled-bg: #fef2f2;
    --error-bg: #fff1f1;
    --error-text: #a40000;
    --good-bg: #dcfce7;
    --good-border: #86efac;
    --good-text: #166534;
    --medium-bg: #fef3c7;
    --medium-border: #fcd34d;
    --medium-text: #92400e;
    --bad-bg: #fee2e2;
    --bad-border: #fca5a5;
    --bad-text: #991b1b;
    --unknown-bg: #f1f5f9;
    --unknown-border: #cbd5e1;
    --unknown-text: #334155;
}

html[data-theme="system"] {
    color-scheme: light dark;
}

@media (prefers-color-scheme: dark) {
    html[data-theme="system"] {
        --background: #111827;
        --surface: #1f2937;
        --surface-muted: #111827;
        --text: #f3f4f6;
        --muted: #9ca3af;
        --border: #374151;
        --error-bg: #3f1d1d;
        --error-text: #fecaca;
        --changed-bg: #4b1d1d;
        --changed-border: #7f1d1d;
        --good-bg: #052e16;
        --good-border: #14532d;
        --good-text: #bbf7d0;
        --medium-bg: #422006;
        --medium-border: #78350f;
        --medium-text: #fde68a;
        --bad-bg: #450a0a;
        --bad-border: #7f1d1d;
        --bad-text: #fecaca;
        --unknown-bg: #1f2937;
        --unknown-border: #4b5563;
        --unknown-text: #d1d5db;
    }
}

html[data-theme="dark"] {
    color-scheme: dark;
    --background: #111827;
    --surface: #1f2937;
    --surface-muted: #111827;
    --text: #f3f4f6;
    --muted: #9ca3af;
    --border: #374151;
    --error-bg: #3f1d1d;
    --error-text: #fecaca;
    --changed-bg: #4b1d1d;
    --changed-border: #7f1d1d;
    --good-bg: #052e16;
    --good-border: #14532d;
    --good-text: #bbf7d0;
    --medium-bg: #422006;
    --medium-border: #78350f;
    --medium-text: #fde68a;
    --bad-bg: #450a0a;
    --bad-border: #7f1d1d;
    --bad-text: #fecaca;
    --unknown-bg: #1f2937;
    --unknown-border: #4b5563;
    --unknown-text: #d1d5db;
}

html[data-theme="light"] {
    color-scheme: light;
}

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    min-height: 100vh;
    background: var(--background);
    color: var(--text);
    font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

main {
    width: min(1180px, calc(100% - 32px));
    margin: 0 auto;
    padding: 32px 0;
}

.topbar {
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 22px;
}

.brand h1 {
    margin: 0 0 6px;
    font-size: clamp(2rem, 4vw, 3rem);
    line-height: 1.1;
}

.brand p {
    margin: 0;
    color: var(--muted);
}

.nav {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    justify-content: flex-end;
}

.nav a {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 42px;
    padding: 0 16px;
    border-radius: 10px;
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    text-decoration: none;
    font-weight: 800;
}

.theme-toggle {
    display: inline-flex;
    align-items: center;
    min-height: 42px;
    padding: 4px;
    border-radius: 10px;
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    cursor: pointer;
    width: fit-content;
    max-width: fit-content;
    flex: 0 0 auto;
}

.theme-toggle input {
    position: absolute;
    width: 1px;
    height: 1px;
    opacity: 0;
}

.theme-slider {
    width: 56px;
    height: 28px;
    background: color-mix(in srgb, var(--primary) 10%, var(--surface));
    border: 1px solid var(--border);
    border-radius: 999px;
    position: relative;
    display: grid;
    grid-template-columns: 1fr 1fr;
    place-items: center;
    transition: background 0.3s ease, border-color 0.3s ease;
}

.theme-slider::after {
    content: "";
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: var(--primary);
    position: absolute;
    top: 4px;
    left: 4px;
    box-shadow: 0 3px 9px color-mix(in srgb, var(--primary) 35%, transparent);
    transition: transform 0.32s cubic-bezier(.22, 1, .36, 1);
}

.theme-toggle input:checked + .theme-slider {
    background: color-mix(in srgb, var(--primary) 18%, var(--surface));
    border-color: color-mix(in srgb, var(--primary) 45%, var(--border));
}

.theme-toggle input:checked + .theme-slider::after {
    transform: translateX(28px);
}

.theme-icon {
    width: 15px;
    height: 15px;
    z-index: 1;
    color: var(--muted);
    transition: color .25s ease, transform .32s cubic-bezier(.22, 1, .36, 1);
}

.theme-icon--sun {
    color: white;
}

.theme-toggle input:checked + .theme-slider .theme-icon--sun {
    color: var(--muted);
    transform: rotate(90deg);
}

.theme-toggle input:checked + .theme-slider .theme-icon--moon {
    color: white;
    transform: rotate(-12deg);
}

.theme-toggle:focus-within {
    outline: 3px solid color-mix(in srgb, var(--primary) 25%, transparent);
    outline-offset: 2px;
}

@media (prefers-reduced-motion: reduce) {
    .theme-slider,
    .theme-slider::after,
    .theme-icon {
        transition: none;
    }
}

.nav a.active {
    background: var(--primary);
    border-color: var(--primary);
    color: white;
}

.panel {
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    align-items: end;
    justify-content: space-between;
    padding: 20px;
    margin-bottom: 20px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 18px;
    box-shadow: 0 12px 32px rgba(16, 24, 40, 0.08);
}

.form-row {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: end;
}

label {
    display: grid;
    gap: 7px;
    color: var(--muted);
    font-weight: 700;
}

input,
select,
button {
    height: 42px;
    border-radius: 10px;
    font: inherit;
}

input,
select {
    border: 1px solid var(--border);
    padding: 0 12px;
    background: var(--surface);
    color: var(--text);
}

button:not(.theme-toggle),
.button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 42px;
    border: 0;
    border-radius: 10px;
    padding: 0 18px;
    background: var(--primary);
    color: white;
    cursor: pointer;
    font: inherit;
    font-weight: 800;
    text-decoration: none;
}

button:not(.theme-toggle):hover,
.button:hover {
    background: var(--primary-dark);
}

.theme-toggle:hover {
    border-color: var(--primary);
}

.meta {
    color: var(--muted);
    font-size: 0.95rem;
}

.message {
    padding: 18px 20px;
    margin-bottom: 18px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
}

.message h2 {
    margin: 0 0 8px;
}

.message p {
    margin: 0;
    color: var(--muted);
}

.message--error {
    background: var(--error-bg);
    color: var(--error-text);
    border-color: #ffc9c9;
}

.empty {
    margin: 0;
    padding: 20px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
    color: var(--muted);
}

.choice-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(88px, 1fr));
    gap: 12px;
}

.choice-card {
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 58px;
    padding: 12px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    color: var(--text);
    text-decoration: none;
    font-weight: 900;
    box-shadow: 0 6px 18px rgba(16, 24, 40, 0.06);
}

.choice-card:hover {
    border-color: var(--primary);
    color: var(--primary);
}

@media (max-width: 900px) {
    main {
        width: min(100% - 24px, 820px);
        padding: 24px 0;
    }

    .topbar {
        align-items: stretch;
    }

    .brand {
        width: 100%;
    }

    .nav {
        width: 100%;
    }

    .nav a {
        flex: 1;
    }

    .theme-toggle {
        flex: 0 0 auto;
        width: fit-content;
    }

    .panel {
        align-items: stretch;
    }

    .form-row {
        width: 100%;
        display: grid;
        grid-template-columns: 1fr 1fr auto;
    }
}

@media (max-width: 620px) {
    main {
        width: min(100% - 18px, 520px);
        padding: 18px 0;
    }

    .brand h1 {
        font-size: 2rem;
    }

    .form-row {
        grid-template-columns: 1fr;
    }

    label,
    input,
    select,
    button,
    .button {
        width: 100%;
    }

    label.theme-toggle {
        width: fit-content;
        max-width: fit-content;
    }

    .panel {
        padding: 16px;
    }

    .choice-grid {
        grid-template-columns: repeat(auto-fill, minmax(76px, 1fr));
        gap: 10px;
    }
}
"""
