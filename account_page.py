"""HTML für Anmeldung und persönliche Benachrichtigungsabonnements."""

from __future__ import annotations

from html import escape
from urllib.parse import quote

from accounts import User
from subscriptions import SubjectOption
from web_utils import CALENDAR_PUBLIC_URL, COMMON_CSS, render_theme_script, render_theme_toggle_button


def _layout(title: str, body: str) -> str:
    return f"""<!doctype html><html lang=\"de\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{escape(title)}</title><style>{COMMON_CSS}
.auth-card {{ max-width: 620px; margin: 8vh auto; }} .stack {{ display:grid; gap:14px; }} label {{ font-weight:700; display:grid; gap:6px; }} input {{ min-height:44px; border:1px solid var(--border); border-radius:8px; padding:8px 10px; font:inherit; }} button:not(.theme-toggle) {{ min-height:44px; border:0; border-radius:8px; padding:8px 16px; background:var(--primary); color:white; font:inherit; font-weight:800; cursor:pointer; }} .notice {{ padding:12px; border-radius:8px; background:var(--error-bg); color:var(--error-text); }} .success {{ background:var(--good-bg); color:var(--good-text); }} .subject-list {{ display:grid; gap:9px; }} .subject-list label {{ display:flex; align-items:center; gap:10px; font-weight:600; }} .subject-list input {{ min-height:auto; width:18px; height:18px; }} code {{ word-break:break-all; }}
</style></head><body><main>{body}</main>{render_theme_script()}</body></html>"""


def render_login(error: str | None = None) -> str:
    notice = f'<p class="notice">{escape(error)}</p>' if error else ""
    return _layout("Anmelden · VpMobil", f"""
    <header class="topbar"><div class="brand"><h1>VPrintfy</h1><p>Ankündigungen für deine Kurse</p></div>
      <nav class="nav"><a href="/">Stundenplan</a><a href="{escape(CALENDAR_PUBLIC_URL)}">Kalender</a>{render_theme_toggle_button()}</nav></header>
    <section class=\"panel auth-card\"><div class=\"brand\"><h1>VPrintfy</h1><p>Ankündigungen für deine Kurse</p></div>
    {notice}<form class=\"stack\" method=\"post\" action=\"/login\">
      <label>Benutzername<input name=\"username\" autocomplete=\"username\" required maxlength=\"64\"></label>
      <label>Vierstellige PIN<input name=\"pin\" type=\"password\" inputmode=\"numeric\" pattern=\"[0-9]{{4}}\" autocomplete=\"current-password\" required></label>
      <button type=\"submit\">Anmelden</button>
    </form></section>""")


def render_subscriptions(
    user: User, options: list[SubjectOption], selected: set[str], csrf_token: str,
    ntfy_url: str, saved: bool = False, error: str | None = None,
) -> str:
    message = '<p class="notice success">Deine Auswahl wurde gespeichert.</p>' if saved else ""
    if error:
        message = f'<p class="notice">{escape(error)}</p>'
    checkboxes = "".join(
        f'<label><input type="checkbox" name="subject" value="{escape(option.key)}"'
        f'{" checked" if option.key in selected else ""}> {escape(option.label)}</label>'
        for option in options
    ) or "<p>Für deine Klasse sind im aktuellen Plan noch keine Fächer vorhanden.</p>"
    topic_url = f"{ntfy_url.rstrip('/')}/{user.ntfy_topic}"
    return _layout("Ankündigungen · VpMobil", f"""
    <header class=\"topbar\"><div class=\"brand\"><h1>Meine Ankündigungen</h1><p>{escape(user.username)} · Klasse {escape(user.class_name)}</p></div>
      <nav class=\"nav\"><a href=\"/\">Stundenplan</a><a href=\"{escape(CALENDAR_PUBLIC_URL)}\">Kalender</a>{render_theme_toggle_button()}<a href=\"/logout\" onclick=\"return false\">Abmelden</a></nav></header>
    {message}<section class=\"panel\"><form class=\"stack\" method=\"post\" action=\"/abos\">
      <input type=\"hidden\" name=\"csrf_token\" value=\"{escape(csrf_token)}\">
      <div><h2>Fächer auswählen</h2><p>Die Liste stammt aus den nächsten zwei vollständigen Schulwochen. Du erhältst nur Meldungen zu den ausgewählten Fächern.</p><div class=\"subject-list\">{checkboxes}</div></div>
      <button type=\"submit\">Auswahl speichern</button>
    </form></section>
    <section class=\"panel\"><div><h2>ntfy-App verbinden</h2><p>Server: <code>{escape(ntfy_url)}</code><br>Topic: <code>{escape(user.ntfy_topic)}</code><br>Benutzer: <code>{escape(user.ntfy_username)}</code><br>Passwort: <code>{escape(user.ntfy_password)}</code></p><p>Abonnement-Adresse: <a href=\"{escape(topic_url)}\">{escape(topic_url)}</a></p><p>Für den lokalen Betrieb kannst du das Topic direkt abonnieren. Falls deine ntfy-App Login verlangt, trage zusätzlich Benutzer und Passwort aus dieser Seite ein. <code>127.0.0.1</code> funktioniert nur auf diesem Computer und nicht auf einem Smartphone.</p><p>Diese Zugangsdaten sind privat. Teile weder Topic noch Passwort.</p></div></section>
    <form method=\"post\" action=\"/logout\"><input type=\"hidden\" name=\"csrf_token\" value=\"{escape(csrf_token)}\"><button type=\"submit\">Abmelden</button></form>
    """)
