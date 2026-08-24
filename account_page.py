"""HTML für Anmeldung und persönliche Benachrichtigungsabonnements."""

from __future__ import annotations

from html import escape
from urllib.parse import quote

from accounts import CalendarEventTypeOption, NotifySettings, User
from subscriptions import SubjectOption
from web_utils import CALENDAR_PUBLIC_URL, COMMON_CSS, render_theme_script, render_theme_toggle_button


def _layout(title: str, body: str) -> str:
    return f"""<!doctype html><html lang=\"de\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{escape(title)}</title><style>{COMMON_CSS}
:root {{ color-scheme: light; }}
body {{ background: var(--background); color: var(--text); font-family: Inter, "Segoe UI", sans-serif; }}
main {{ min-height:100vh; }}
.topbar {{ max-width: 1040px; margin: 0 auto; padding: 18px 14px 0; display:flex; justify-content:space-between; align-items:center; gap:12px; }}
.brand {{ display:flex; flex-direction:column; gap:3px; }}
.brand h1, .brand p {{ margin:0; }}
.brand h1 {{ font-size: clamp(1.3rem, 2vw, 2rem); }}
.brand p {{ color: var(--muted); font-size: 0.85rem; }}
.nav {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; }}
.nav a {{ display:inline-flex; align-items:center; justify-content:center; min-height:42px; color:var(--text); text-decoration:none; padding:8px 12px; border-radius:10px; border:1px solid var(--border); background:var(--surface); font-weight:800; }}
.panel {{ max-width: 1040px; margin: 18px auto 0; padding: 0 14px 32px; }}
.auth-card {{ max-width: 620px; margin: 8vh auto 0; }}
.stack {{ display:grid; gap:16px; }}
.settings-shell {{ display:grid; gap:16px; }}
.settings-grid {{ display:grid; gap:16px; grid-template-columns: repeat(2, minmax(0, 1fr)); }}
.settings-card {{ display:grid; align-content:start; gap:14px; min-width:0; padding:18px; border:1px solid var(--border); border-radius:18px; background: var(--surface); box-shadow: 0 6px 24px rgba(15, 23, 42, 0.06); }}
.settings-card h2, .settings-card h3, .settings-card h4 {{ margin:0; }}
.settings-card-header {{ display:flex; justify-content:space-between; align-items:center; gap:12px; }}
.settings-kicker {{ font-size: 0.72rem; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); font-weight:700; }}
.card-hint {{ margin:0; color: var(--muted); font-size: 0.86rem; line-height:1.5; }}
.field-grid {{ display:grid; gap:12px; }}
label {{ font-weight:700; display:grid; gap:6px; }}
input, select {{ min-height:44px; border:1px solid var(--border); border-radius:10px; padding:8px 12px; font:inherit; background: var(--background); color: var(--text); }}
input[type="checkbox"] {{ min-height:auto; width:18px; height:18px; padding:0; accent-color: var(--primary); }}
input[type="number"] {{ max-width: 120px; }}
button:not(.theme-toggle) {{ min-height:44px; border:0; border-radius:10px; padding:10px 18px; background: linear-gradient(135deg, var(--primary), var(--primary-2, var(--primary))); color:white; font:inherit; font-weight:800; cursor:pointer; box-shadow: 0 8px 18px rgba(65, 105, 225, 0.22); }}
.notice {{ padding:12px 14px; border-radius:12px; border:1px solid transparent; background: var(--error-bg); color: var(--error-text); }}
.success {{ background: var(--good-bg); color: var(--good-text); border-color: rgba(34,197,94,.18); }}
.choice-list, .subject-list, .option-list {{ display:grid; gap:8px; }}
.choice-list {{ grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); }}
.subject-list {{ grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }}
.option-list {{ grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }}
.choice-pill, .chip {{ position:relative; display:flex; align-items:center; min-height:42px; border:1px solid var(--border); border-radius:10px; background: var(--input-bg, rgba(255,255,255,.02)); overflow:hidden; }}
.choice-pill input, .chip input {{ position:absolute; inset:0; opacity:0; width:100%; height:100%; margin:0; cursor:pointer; }}
.choice-pill span, .chip span {{ display:flex; align-items:center; width:100%; min-height:42px; padding:8px 12px; font-weight:700; color: var(--text); }}
.choice-pill input:checked + span, .chip input:checked + span {{ color: var(--primary-contrast, white); background: linear-gradient(135deg, var(--primary), var(--primary-2, var(--primary))); border-color: transparent; }}
.toggle-row {{ display:flex; align-items:center; gap:10px; font-weight:700; }}
.toggle-row input {{ width:18px; height:18px; }}
.section-group {{ display:grid; gap:12px; padding:14px; border:1px solid var(--border); border-radius:12px; background: rgba(148,163,184,.04); }}
.subject-card {{ display:grid; gap:12px; padding:14px; border:1px solid var(--border); border-radius:14px; background: rgba(148,163,184,.04); }}
.subject-card[hidden], .empty-selection[hidden] {{ display:none; }}
.subject-card h3 {{ font-size:1.02rem; }}
.time-tabs {{ display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:9px; }}
.time-tab {{ display:grid; gap:5px; min-width:0; padding:10px; border:1px solid var(--border); border-radius:13px; background:rgba(148,163,184,.04); }}
.time-tab span {{ min-width:0; color:var(--muted); font-size:.72rem; font-weight:800; text-transform:uppercase; letter-spacing:.03em; overflow-wrap:anywhere; }}
.time-tab input {{ width:100%; min-width:0; background:var(--surface); }}
.calendar-row {{ display:grid; grid-template-columns:minmax(150px, 1fr) minmax(120px, .75fr); gap:10px; align-items:end; }}
.category-block {{ display:grid; gap:9px; }}
.category-block h3 {{ font-size:.9rem; }}
.class-select-label {{ display:grid; }}
.secondary-button {{ justify-self:start; background:var(--background) !important; color:var(--text) !important; border:1px solid var(--border) !important; box-shadow:none !important; }}
.save-row {{ position:sticky; bottom:10px; z-index:3; display:flex; justify-content:flex-end; padding:10px; border:1px solid var(--border); border-radius:15px; background:color-mix(in srgb, var(--surface) 90%, transparent); backdrop-filter:blur(12px); }}
.muted {{ color: var(--muted); }}
code {{ word-break:break-all; }}
@media (max-width: 760px) {{
  .topbar {{ padding-top:12px; align-items:flex-start; }}
  .nav {{ width:100%; display:flex; flex-wrap:wrap; }}
  .nav a {{ flex:1 1 auto; }}
  .nav form {{ flex:1 1 auto; }}
  .nav form button {{ width:100%; }}
  .nav .theme-toggle {{ flex:0 0 auto; }}
  .settings-grid {{ grid-template-columns:1fr; }}
  .panel {{ padding: 0 10px 22px; }}
  .settings-card {{ padding:14px; }}
  .choice-list, .subject-list, .option-list {{ grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); }}
  .settings-card-header {{ align-items:flex-start; flex-direction:column; }}
}}
@media (max-width: 430px) {{
  .topbar, .panel {{ padding-left:8px; padding-right:8px; }}
  .brand h1 {{ font-size:1.28rem; }}
  .settings-card {{ padding:13px; border-radius:15px; }}
  .choice-list, .subject-list, .option-list {{ grid-template-columns:repeat(2, minmax(0, 1fr)); gap:7px; }}
  .choice-pill span, .chip span {{ padding:7px 9px; overflow-wrap:anywhere; }}
  .time-tabs {{ grid-template-columns:1fr; }}
  .time-tab {{ display:flex; align-items:center; justify-content:space-between; gap:10px; }}
  .time-tab span {{ flex:1 1 auto; overflow-wrap:normal; }}
  .time-tab input {{ flex:0 0 120px; width:120px; min-width:120px; min-height:40px; padding-left:8px; padding-right:6px; text-align:center; }}
  .time-tab input::-webkit-datetime-edit {{ display:flex; justify-content:center; width:100%; padding:0; }}
  .time-tab input::-webkit-calendar-picker-indicator {{ margin:0; padding:2px; }}
  .calendar-row {{ grid-template-columns:1fr; }}
  .save-row button {{ width:100%; }}
}}
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


def _choice_checkbox(name: str, value: str, label: str, *, checked: bool) -> str:
    return (
        f'<label class="choice-pill">'
        f'<input type="checkbox" name="{escape(name)}" value="{escape(value)}"{" checked" if checked else ""}>'
        f'<span>{escape(label)}</span>'
        f'</label>'
    )


def render_subscriptions(
    user: User,
    class_options: list[str],
    selected_classes: tuple[str, ...],
    subject_options_by_class: dict[str, list[SubjectOption]],
    selected_subjects_by_class: dict[str, set[str]],
    notify_settings: NotifySettings,
    calendar_event_types: list[CalendarEventTypeOption],
    csrf_token: str,
    ntfy_url: str,
    saved: bool = False,
    error: str | None = None,
    test_sent: bool = False,
) -> str:
    message = '<p class="notice success">Deine Auswahl wurde gespeichert.</p>' if saved else ""
    if test_sent:
        message = '<p class="notice success">Die Testbenachrichtigung wurde gesendet.</p>'
    if error:
        message = f'<p class="notice">{escape(error)}</p>'

    class_inputs = "".join(
        f'<input type="checkbox" name="class_name" value="{escape(class_name)}" data-class-input hidden'
        f'{" checked" if class_name in selected_classes else ""}>'
        for class_name in class_options
    )

    subject_sections = []
    first_selected_class = selected_classes[0] if selected_classes else (class_options[0] if class_options else None)
    for class_name in class_options:
        field_name = f"subject__{quote(class_name, safe='')}"
        options = subject_options_by_class.get(class_name, [])
        checkboxes = "".join(
            _choice_checkbox(field_name, option.key, option.label, checked=option.key in selected_subjects_by_class.get(class_name, set()))
            for option in options
        ) or "<p class=\"muted\">Für diese Klasse sind im aktuellen Plan noch keine Fächer vorhanden.</p>"
        subject_sections.append(
            f'<div class="subject-card" role="tabpanel" data-class-panel="{escape(class_name)}"'
            f'{"" if class_name == first_selected_class else " hidden"}><h3>Unterrichtseinheiten · {escape(class_name)}</h3>'
            f'<div class="subject-list">{checkboxes}</div></div>'
        )

    event_type_checkboxes = "".join(
        _choice_checkbox("calendar_event_type", option.id, option.label, checked=option.id in notify_settings.calendar_notification_types)
        for option in calendar_event_types
    ) or "<p class=\"muted\">Im Kalender sind aktuell keine Kategorien verfügbar.</p>"

    topic_url = f"{ntfy_url.rstrip('/')}/{user.ntfy_topic}"
    class_select_options = "".join(
        f'<option value="{escape(class_name)}"{" selected" if class_name == first_selected_class else ""}>{escape(class_name)}</option>'
        for class_name in class_options
    )
    lesson_time_inputs = "".join(
        f'<label class="time-tab"><span>{"Tagesübersicht" if index == 0 else f"Nächste Stunde {index}"}</span>'
        f'<input type="time" name="lesson_notification_time" value="{escape(value)}" required step="60"></label>'
        for index, value in enumerate(notify_settings.lesson_notification_times)
    )
    return _layout("Ankündigungen · VpMobil", f"""
    <header class=\"topbar\"><div class=\"brand\"><h1>Meine Ankündigungen</h1><p>{escape(user.username)} · Klasse {escape(user.class_name)}</p></div>
      <nav class=\"nav\"><a href=\"/\">Stundenplan</a><a href=\"{escape(CALENDAR_PUBLIC_URL)}\">Kalender</a>{render_theme_toggle_button()}<form method=\"post\" action=\"/logout\" style=\"margin:0;\"><input type=\"hidden\" name=\"csrf_token\" value=\"{escape(csrf_token)}\"><button type=\"submit\">Abmelden</button></form></nav></header>
    {message}<section class=\"panel\"><form class=\"stack\" method=\"post\" action=\"/abos\">
      <input type=\"hidden\" name=\"csrf_token\" value=\"{escape(csrf_token)}\">
      {class_inputs}
      <div class=\"settings-shell\">
        <article class=\"settings-card\">
            <div class=\"settings-card-header\"><div><div class=\"settings-kicker\">Stunden</div><h2>Benachrichtigungen</h2></div></div>
            <div class=\"field-grid\">
              <label class=\"toggle-row\"><input type=\"checkbox\" id=\"lesson_notifications_enabled\" name=\"lesson_notifications_enabled\"{" checked" if notify_settings.lesson_notifications_enabled else ""}><span>Stundenbenachrichtigungen aktiv</span></label>
              <div class=\"time-tabs\">{lesson_time_inputs}</div>
            </div>
        </article>
        <div class=\"settings-grid\">
          <article class=\"settings-card\">
            <div class=\"settings-card-header\"><div><div class=\"settings-kicker\">Fächer</div><h2>Je Klasse</h2></div></div>
            <label class=\"class-select-label\">Klasse anzeigen<select class=\"class-select\" data-class-select>{class_select_options}</select></label>
            {''.join(subject_sections)}
          </article>
          <article class=\"settings-card\">
            <div class=\"settings-card-header\"><div><div class=\"settings-kicker\">Kalender</div><h2>Erinnerungen</h2></div></div>
            <div class=\"field-grid\">
              <label class=\"toggle-row\"><input type=\"checkbox\" id=\"calendar_notifications_enabled\" name=\"calendar_notifications_enabled\"{" checked" if notify_settings.calendar_notifications_enabled else ""}><span>Kalender-Benachrichtigungen aktiv</span></label>
              <div class=\"calendar-row\"><label>Uhrzeit<input type=\"time\" name=\"calendar_notification_time\" value=\"{escape(notify_settings.calendar_notification_time)}\" required step=\"60\"></label>
              <label>Tage vorher<input name=\"calendar_notification_days_before\" type=\"number\" inputmode=\"numeric\" min=\"0\" value=\"{notify_settings.calendar_notification_days_before}\"></label></div>
              <div class=\"category-block\"><h3>Kategorien</h3><div class=\"option-list\">{event_type_checkboxes}</div></div>
            </div>
          </article>
        </div>
        <article class=\"settings-card\">
          <div class=\"settings-card-header\"><div><div class=\"settings-kicker\">ntfy</div><h2>App verbinden</h2></div></div>
          <div class=\"field-grid\">
            <p class=\"card-hint\"><strong>Server:</strong> <code>{escape(ntfy_url)}</code><br><strong>Topic:</strong> <code>{escape(user.ntfy_topic)}</code><br><strong>Benutzer:</strong> <code>{escape(user.ntfy_username)}</code><br><strong>Passwort:</strong> <code>{escape(user.ntfy_password)}</code></p>
            <p class=\"card-hint\">Abonnement-Adresse: <a href=\"{escape(topic_url)}\">{escape(topic_url)}</a></p>
            <p class=\"card-hint\">Mit diesen Zugangsdaten kannst du nur dein eigenes Topic lesen und beschreiben. Der Server nutzt einen eigenen Systemzugang für automatische Benachrichtigungen.</p>
            <button class=\"secondary-button\" type=\"submit\" formaction=\"/abos/test\" formmethod=\"post\" formnovalidate>Testbenachrichtigung senden</button>
          </div>
        </article>
        <div class=\"save-row\"><button type=\"submit\">Änderungen speichern</button></div>
      </div>
    </form></section>
    <script>
      (() => {{
        const classInputs = [...document.querySelectorAll('[data-class-input]')];
        const panels = [...document.querySelectorAll('[data-class-panel]')];
        const classSelect = document.querySelector('[data-class-select]');

        const activate = (className) => {{
          panels.forEach((panel) => panel.hidden = panel.dataset.classPanel !== className);
          if (classSelect) classSelect.value = className;
        }};

        const syncClassSelection = (className) => {{
          const panel = panels.find((item) => item.dataset.classPanel === className);
          const classInput = classInputs.find((item) => item.value === className);
          if (!panel || !classInput) return;
          classInput.checked = !!panel.querySelector('input[type="checkbox"]:checked');
        }};

        panels.forEach((panel) => panel.addEventListener('change', () => syncClassSelection(panel.dataset.classPanel)));
        if (classSelect) classSelect.addEventListener('change', () => activate(classSelect.value));
        if (classSelect) activate(classSelect.value);
      }})();
    </script>
    """)
