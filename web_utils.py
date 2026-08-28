import os
from html import escape
from datetime import date, datetime, timedelta
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from dotenv import load_dotenv

load_dotenv()

DEFAULT_PORT = int(os.getenv("VP_PORT", os.getenv("PORT", 8000)))
_requested_host = os.getenv("BIND_HOST", os.getenv("HOST", "127.0.0.1"))
DEFAULT_HOST = "0.0.0.0" if os.path.exists("/.dockerenv") and _requested_host in {"127.0.0.1", "localhost"} else _requested_host
CALENDAR_PUBLIC_URL = os.getenv("CALENDAR_PUBLIC_URL", "http://127.0.0.1:3000").rstrip("/")
VERTRETUNGSPLAN_PUBLIC_URL = os.getenv("VERTRETUNGSPLAN_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")


def render_vp_navigation(
    active: str,
    csrf_token: str | None = None,
    *,
    can_change_pin: bool = False,
    force_pin_change: bool = False,
    pin_modal_error: str | None = None,
    pin_modal_changed: bool = False,
    session_username: str | None = None,
) -> str:
    links = (("classes", "/", "Klassen"), ("teachers", "/lehrer", "Lehrer"),
             ("rooms", "/raeume", "Freie Räume"), ("notifications", "/abos", "Ankündigungen"))
    items = ""
    for key, href, label in links:
        active_class = ' class="active"' if key == active else ""
        items += f'<a{active_class} href="{href}">{label}</a>'
    if can_change_pin:
        items += '<button class="nav-button" type="button" data-pin-modal-open>PIN ändern</button>'
    logout = (
        f'<form class="logout-form" method="post" action="/logout"><input type="hidden" name="csrf_token" value="{escape(csrf_token)}">'
        f'<button class="logout-button" type="submit">Abmelden</button></form>' if csrf_token else ""
    )
    modal = (
        render_pin_change_modal(
            csrf_token,
            force=force_pin_change,
            error=pin_modal_error,
            changed=pin_modal_changed,
        )
        if can_change_pin and csrf_token else ""
    )
    escaped_session_username = escape(session_username) if session_username else ""
    user_attr = f' data-session-user="{escaped_session_username}"' if session_username else ""
    user_badge = (
        f'<span class="session-user-badge" title="Aktuell angemeldet">Angemeldet: {escaped_session_username}</span>'
        if session_username else ""
    )
    calendar_link = "" if can_change_pin else f'<a href="{escape(CALENDAR_PUBLIC_URL)}">Kalender</a>'
    return f'<div class="nav-wrap">{user_badge}<nav class="nav"{user_attr}>{items}{calendar_link}{logout}{render_theme_toggle_button()}</nav></div>{modal}'


def render_pin_change_modal(csrf_token: str | None, *, force: bool = False, error: str | None = None, changed: bool = False) -> str:
    if not csrf_token:
        return ""
    message = ""
    if changed:
        message = '<p class="pin-modal-notice success">Deine PIN wurde geändert.</p>'
    if error:
        message = f'<p class="pin-modal-notice">{escape(error)}</p>'
    close_button = '<button class="pin-modal-close" type="button" data-pin-modal-close aria-label="Schließen">×</button>'
    open_attr = " open" if force or error or changed else ""
    return f"""
    <dialog class="pin-modal"{open_attr} data-pin-modal data-force-pin-change="{'1' if force else '0'}">
      <form method="post" action="/pin-aendern" class="pin-modal-card">
        <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
        <div class="pin-modal-head">
          <div><span>VP-only</span><h2>PIN ändern</h2></div>
          {close_button}
        </div>
        {message}
        <label>Neue PIN<input name="pin" type="password" inputmode="numeric" pattern="[0-9]{{4}}" minlength="4" maxlength="4" required autocomplete="new-password" autofocus></label>
        <label>Neue PIN wiederholen<input name="pin_confirm" type="password" inputmode="numeric" pattern="[0-9]{{4}}" minlength="4" maxlength="4" required autocomplete="new-password"></label>
        <button type="submit">PIN speichern</button>
      </form>
    </dialog>
    <script>
    (() => {{
      const dialog = document.querySelector('[data-pin-modal]');
      if (!dialog) return;
      const force = dialog.dataset.forcePinChange === '1';
      const openers = document.querySelectorAll('[data-pin-modal-open]');
      const closers = document.querySelectorAll('[data-pin-modal-close]');
      const isModal = () => {{
        try {{ return dialog.matches(':modal'); }} catch (_) {{ return false; }}
      }};
      const open = () => {{
        if (dialog.open && !isModal()) dialog.removeAttribute('open');
        if (typeof dialog.showModal === 'function' && !dialog.open) dialog.showModal();
        else dialog.setAttribute('open', '');
      }};
      const close = () => {{ dialog.close ? dialog.close() : dialog.removeAttribute('open'); }};
      openers.forEach((button) => button.addEventListener('click', open));
      closers.forEach((button) => button.addEventListener('click', close));
      dialog.addEventListener('cancel', (event) => {{ if (force) event.preventDefault(); }});
      dialog.querySelectorAll('input[type="password"]').forEach((input) => {{
        input.addEventListener('input', () => input.value = input.value.replace(/\\D/g, '').slice(0, 4));
      }});
      if (dialog.hasAttribute('open')) {{
        window.history.replaceState(null, '', '/');
        window.setTimeout(open, 0);
        const first = dialog.querySelector('input[type="password"]');
        if (first) window.setTimeout(() => first.focus(), 50);
      }}
    }})();
    </script>
    """


SESSION_WATCH_SCRIPT = """<script>
(() => {
  let checking = false;
  const checkSession = async () => {
    if (checking) return;
    checking = true;
    try {
      const response = await fetch('/api/session-status', {
        credentials: 'same-origin', cache: 'no-store', headers: {'Accept': 'application/json'}
      });
      const onLoginPage = window.location.pathname === '/login';
      if (onLoginPage && response.ok) {
        window.location.replace('/');
      } else if (!onLoginPage && (response.status === 401 || response.status === 403)) {
        window.location.replace('/login');
      } else if (!onLoginPage && response.ok) {
        const current = document.querySelector('.nav')?.dataset.sessionUser || '';
        const payload = await response.json().catch(() => null);
        if (current && payload?.username && current.toLowerCase() !== String(payload.username).toLowerCase()) {
          window.location.reload();
        }
      }
    } catch (_) {
      // Temporäre Netzwerkfehler dürfen keine gültige Sitzung beenden.
    } finally {
      checking = false;
    }
  };
  const timer = window.setInterval(checkSession, 3000);
  window.addEventListener('focus', checkSession);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') checkSession();
  });
  window.addEventListener('pagehide', () => window.clearInterval(timer), {once: true});
})();
</script>"""


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


def get_ab_week_label(d: date) -> str:
    """Gibt '(A)' für gerade ISO-Wochen (A-Woche) und '(B)' für ungerade (B-Woche) zurück."""
    return "(A)" if d.isocalendar().week % 2 == 0 else "(B)"


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


def cookie_values(cookie_header: str | None, name: str) -> list[str]:
    """Liest auch während einer Cookie-Migration mehrfach vorhandene Werte."""
    values: list[str] = []
    for part in (cookie_header or "").split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key == name:
            decoded = unquote(value)
            if decoded and decoded not in values:
                values.append(decoded)
    return values


def make_cookie(
    name: str, value: str, max_age: int = 60 * 60 * 24 * 180, *,
    http_only: bool = False, secure: bool = False, domain: str | None = None,
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
    if domain:
        cookie[name]["domain"] = domain

    return cookie.output(header="").strip()


def send_html(handler: BaseHTTPRequestHandler, html: str, cookie_headers: list[str] | None = None) -> None:
    """Sendet eine HTML-Antwort an den Browser."""

    if "</body>" in html and SESSION_WATCH_SCRIPT not in html:
        html = html.replace("</body>", f"{SESSION_WATCH_SCRIPT}</body>", 1)

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
*, *::before, *::after { box-sizing: border-box; }
:root {
    --background: #ffffff;
    --surface: #ffffff;
    --surface-muted: #f8f9fa;
    --primary: #e91e63;
    --primary-dark: #d81b60;
    --text: #0f172a;
    --muted: #64748b;
    --border: #cbd5e1;
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
        --background: #121212;
        --surface: #1e1e1e;
        --surface-muted: #181818;
        --text: #eeeeee;
        --muted: #aaaaaa;
        --border: #333333;
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
    --background: #121212;
    --surface: #1e1e1e;
    --surface-muted: #181818;
    --text: #eeeeee;
    --muted: #aaaaaa;
    --border: #333333;
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
    width: min(1380px, calc(100% - 32px));
    margin: 0 auto;
    padding: 20px 0 32px;
}

.topbar {
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
}

.brand h1 {
    margin: 0 0 6px;
    font-size: clamp(1.25rem, 2vw, 1.65rem);
    line-height: 1.2;
    letter-spacing: -0.02em;
}

.brand p {
    margin: 0;
    color: var(--muted);
}

.nav-wrap {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 6px;
    min-width: 0;
}

.session-user-badge {
    display: inline-flex;
    align-items: center;
    min-height: 24px;
    max-width: min(100%, 280px);
    padding: 2px 8px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--surface-muted);
    color: var(--muted);
    font-size: .74rem;
    font-weight: 750;
    line-height: 1.15;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.nav {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    justify-content: flex-end;
}

.nav a,
.nav-button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 36px;
    padding: 0 12px;
    border-radius: 6px;
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    text-decoration: none;
    font-size: .875rem;
    font-weight: 650;
    font-family: inherit;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    cursor: pointer;
}

.theme-toggle {
    display: inline-flex;
    align-items: center;
    min-height: 36px;
    padding: 4px;
    border-radius: 6px;
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
.nav-button:hover,
.nav a:hover {
    border-color: color-mix(in srgb, var(--primary) 35%, var(--border));
}
.pin-modal {
    position: fixed;
    inset: 0;
    margin: auto;
    width: min(420px, calc(100vw - 24px));
    max-height: calc(100dvh - 24px);
    padding: 0;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--surface);
    color: var(--text);
    overflow: auto;
}
.pin-modal::backdrop { background: rgba(15, 23, 42, .55); }
.pin-modal-card { display:grid; gap:12px; padding:16px; }
.pin-modal-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
.pin-modal-head span { display:block; margin-bottom:3px; color:var(--muted); font-size:.72rem; font-weight:750; text-transform:uppercase; letter-spacing:.04em; }
.pin-modal-head h2 { margin:0; font-size:1.15rem; }
.pin-modal-close { width:32px; height:32px; min-height:32px; padding:0; border:1px solid var(--border); border-radius:6px; background:var(--surface-muted); color:var(--text); font:inherit; font-size:1.15rem; cursor:pointer; }
.pin-modal label { display:grid; gap:6px; font-weight:700; }
.pin-modal input { min-height:40px; border:1px solid var(--border); border-radius:8px; padding:7px 10px; background:var(--background); color:var(--text); font:inherit; }
.pin-modal button[type="submit"] { min-height:40px; border:0; border-radius:8px; padding:8px 14px; background:var(--primary); color:white; font:inherit; font-weight:800; cursor:pointer; }
.pin-modal-notice { margin:0; padding:10px 12px; border-radius:8px; background:var(--error-bg); color:var(--error-text); }
.pin-modal-notice.success { background:var(--good-bg); color:var(--good-text); }
.logout-form { margin:0; }
.logout-button { min-height:36px !important; height:36px !important; padding:0 12px !important; border:1px solid var(--border) !important; border-radius:6px !important; background:var(--surface) !important; color:var(--text) !important; font-size:.875rem !important; font-weight:650 !important; box-shadow:none !important; }
.logout-button { min-width:0; max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.logout-button:hover { border-color:var(--bad-border) !important; color:var(--bad-text) !important; }
.class-select-label { display:grid; gap:4px; min-width:180px; color:var(--muted); font-size:.75rem; font-weight:700; }
.class-select { min-height:36px; padding:6px 32px 6px 10px; border:1px solid var(--border); border-radius:6px; background:var(--surface); color:var(--text); font:inherit; font-size:.875rem; font-weight:650; }
.class-select:focus { outline:3px solid color-mix(in srgb, var(--primary) 20%, transparent); border-color:var(--primary); }

.panel {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: end;
    justify-content: space-between;
    padding: 16px;
    margin-bottom: 16px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
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
    height: 38px;
    border-radius: 6px;
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
    min-height: 38px;
    border: 0;
    border-radius: 6px;
    padding: 0 18px;
    background: var(--primary);
    color: white;
    cursor: pointer;
    font: inherit;
    font-weight: 650;
    text-decoration: none;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
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
    padding: 14px 16px;
    margin-bottom: 12px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
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
    border-radius: 8px;
    color: var(--muted);
}

.choice-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(88px, 1fr));
    gap: 8px;
}

.choice-card {
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 44px;
    padding: 8px 10px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text);
    text-decoration: none;
    font-weight: 700;
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

    .nav-wrap {
        width: 100%;
        align-items: stretch;
    }

    .session-user-badge {
        justify-self: start;
        max-width: 100%;
        width: fit-content;
    }

    .nav {
        width: 100%;
    }

    .nav {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        align-items: stretch;
    }

    .nav > a,
    .nav > .nav-button,
    .nav > .logout-form,
    .nav > .theme-toggle {
        min-width: 0;
        width: 100%;
    }

    .nav > a,
    .nav > .nav-button,
    .nav > .logout-form .logout-button {
        height: 36px !important;
        min-height: 36px !important;
        max-height: 36px;
        padding-left: 6px !important;
        padding-right: 6px !important;
        white-space: normal;
        overflow-wrap: anywhere;
        word-break: normal;
        hyphens: auto;
        line-height: 1.05;
        font-size: clamp(.68rem, 2.6vw, .875rem) !important;
        text-align: center;
    }

    .nav > .logout-form {
        display: flex;
    }

    .nav > .logout-form .logout-button {
        width: 100%;
    }

    .nav > .theme-toggle {
        justify-content: center;
        width: fit-content;
        max-width: fit-content;
        justify-self: start;
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

    .nav > label.theme-toggle {
        width: fit-content;
        max-width: fit-content;
        justify-content: center;
        justify-self: start;
    }

    .panel {
        padding: 16px;
    }

    .panel, .settings-card, .settings-shell, .settings-grid, .field-grid,
    .time-tabs, .calendar-row, .category-schedule, .nav, .nav > * {
        min-width: 0;
        max-width: 100%;
    }

    .day-before-toggle { display:flex; align-items:center; gap:6px; min-width:0; font-size:.72rem; white-space:normal; }
    .day-before-toggle input { flex:0 0 auto; width:16px; height:16px; }

    .choice-grid {
        grid-template-columns: repeat(auto-fill, minmax(76px, 1fr));
        gap: 10px;
    }
}
"""
