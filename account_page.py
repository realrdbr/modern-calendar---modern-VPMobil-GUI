"""HTML für Anmeldung und persönliche Benachrichtigungsabonnements."""

from __future__ import annotations

from html import escape
from urllib.parse import quote

from accounts import CalendarEventTypeOption, NotifySettings, User
from subscriptions import SubjectOption
from web_utils import CALENDAR_PUBLIC_URL, COMMON_CSS, render_theme_script, render_vp_navigation


def _layout(title: str, body: str) -> str:
    main_class = ' class="login-root"' if title.startswith("Anmelden") else ""
    return f"""<!doctype html><html lang=\"de\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{escape(title)}</title><style>{COMMON_CSS}
:root {{ color-scheme: light; }}
body {{ background: var(--background); color: var(--text); font-family:system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
main {{ width:min(1320px, calc(100% - 24px)); min-height:100vh; }}
.topbar {{ max-width: 1040px; margin: 0 auto; padding: 18px 14px 0; display:flex; justify-content:space-between; align-items:center; gap:12px; }}
.brand {{ display:flex; flex-direction:column; gap:3px; }}
.brand h1, .brand p {{ margin:0; }}
.brand h1 {{ font-size: clamp(1.3rem, 2vw, 2rem); }}
.brand p {{ color: var(--muted); font-size: 0.85rem; }}
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
.form-grid {{ display:grid; gap:12px; grid-template-columns:repeat(3, minmax(0, 1fr)); align-items:end; }}
.table-wrap {{ overflow:auto; border:1px solid var(--border); border-radius:8px; }}
table {{ width:100%; border-collapse:collapse; min-width:520px; }}
th, td {{ padding:10px 12px; border-bottom:1px solid var(--border); text-align:left; font-size:.875rem; }}
th {{ color:var(--muted); font-size:.75rem; text-transform:uppercase; letter-spacing:.04em; }}
tr:last-child td {{ border-bottom:0; }}
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
.time-tab {{ display:grid; grid-template-rows:minmax(18px, auto) 40px auto; align-content:start; gap:5px; min-width:0; padding:10px; border:1px solid var(--border); border-radius:13px; background:rgba(148,163,184,.04); }}
.time-label {{ min-width:0; color:var(--muted); font-size:.72rem; font-weight:800; text-transform:uppercase; letter-spacing:.03em; overflow-wrap:anywhere; }}
.time-tab > input {{ width:100%; min-width:0; height:40px; background:var(--surface); }}
.day-before-toggle {{ display:flex; align-items:center; justify-content:flex-start; gap:6px; min-width:0; max-width:100%; font-size:.75rem; font-weight:700; color:var(--muted); line-height:1.2; }}
.day-before-toggle > input {{ flex:0 0 auto; width:16px; height:16px; min-width:16px; min-height:16px; margin:0; }}
.day-before-toggle > span {{ min-width:0; overflow-wrap:anywhere; }}
.calendar-row {{ display:grid; grid-template-columns:minmax(125px, .8fr) minmax(240px, 1.4fr); gap:10px; align-items:end; }}
.category-schedule {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; min-width:0; }}
.category-block {{ display:grid; gap:9px; }}
.category-block h3 {{ font-size:.9rem; }}
.class-select-label {{ display:grid; }}
.secondary-button {{ justify-self:start; background:var(--background) !important; color:var(--text) !important; border:1px solid var(--border) !important; box-shadow:none !important; }}
.save-row {{ position:sticky; bottom:10px; z-index:3; display:flex; justify-content:flex-end; padding:10px; border:1px solid var(--border); border-radius:15px; background:color-mix(in srgb, var(--surface) 90%, transparent); backdrop-filter:blur(12px); }}
.muted {{ color: var(--muted); }}
code {{ word-break:break-all; }}
@media (max-width: 760px) {{
  .topbar {{ padding-top:12px; align-items:flex-start; }}
  .settings-grid {{ grid-template-columns:1fr; }}
  .panel {{ padding: 0 10px 22px; }}
  .settings-card {{ padding:14px; }}
  .form-grid {{ grid-template-columns:1fr; }}
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
  .time-tab {{ grid-template-columns:minmax(0, 1fr) 120px; grid-template-rows:auto auto; align-items:center; gap:6px 10px; }}
  .time-label {{ overflow-wrap:normal; }}
  .time-tab > input {{ width:120px; min-width:120px; min-height:40px; padding-left:8px; padding-right:6px; text-align:center; }}
  .time-tab > input::-webkit-datetime-edit {{ display:flex; justify-content:center; width:100%; padding:0; }}
  .time-tab > input::-webkit-calendar-picker-indicator {{ margin:0; padding:2px; }}
  .day-before-toggle {{ grid-column:2; grid-row:2; align-self:start; white-space:normal; }}
  .calendar-row {{ grid-template-columns:minmax(88px, .7fr) minmax(0, 1.5fr); }}
  .category-schedule {{ grid-template-columns:minmax(0,1fr) minmax(82px,.72fr); }}
  .save-row button {{ width:100%; }}
}}
/* Compact dashboard treatment */
.topbar {{ max-width:none; margin:0 0 16px; padding:0; }}
.panel {{ max-width:none; margin:0; padding:0 0 32px; }}
.auth-card {{ max-width:440px; margin-top:8vh; display:grid; gap:20px; padding:24px; border:1px solid var(--border); border-radius:8px; background:var(--surface); }}
.login-heading {{ display:grid; gap:6px; padding-bottom:16px; border-bottom:1px solid var(--border); }}
.login-heading h1, .login-heading p {{ margin:0; }}
.login-heading h1 {{ font-size:1.5rem; letter-spacing:-.02em; }}
.login-heading p {{ color:var(--muted); font-size:.875rem; line-height:1.5; }}
.login-account {{ display:flex; justify-content:space-between; align-items:center; gap:12px; padding:10px 12px; border:1px solid var(--border); border-radius:6px; background:var(--surface-muted); font-size:.875rem; }}
.login-account span {{ color:var(--muted); }}
.settings-card {{ gap:12px; padding:16px; border-radius:8px; box-shadow:none; }}
input, select {{ min-height:38px; border-radius:6px; padding:7px 10px; }}
button:not(.theme-toggle) {{ min-height:38px; border:1px solid var(--primary); border-radius:6px; padding:8px 14px; background:var(--primary); font-weight:650; box-shadow:none; gap:6px; }}
.notice {{ border-radius:6px; }}
.choice-pill, .chip, .subject-card, .section-group, .time-tab {{ border-radius:6px; }}
.choice-pill input:checked + span, .chip input:checked + span {{ background:var(--primary); }}
.save-row {{ bottom:8px; padding:8px; border-radius:8px; background:var(--surface); backdrop-filter:none; }}
.calendar-login {{ min-height:100vh; display:flex; flex-direction:column; align-items:center; justify-content:space-between; background:var(--surface); color:var(--text); }}
.login-root {{ width:100%; max-width:none; min-height:100vh; margin:0; padding:0; overflow:hidden; }}
.calendar-login-shell {{ width:calc(100% - 48px); max-width:432px; margin:auto; padding:32px 0; display:flex; flex-direction:column; align-items:center; }}
.calendar-login-header {{ margin-bottom:24px; text-align:center; }}
.login-product-icon {{ width:48px; height:48px; margin:0 auto 14px; display:grid; place-items:center; border:1px solid var(--border); border-radius:10px; background:var(--surface-muted); color:#e91e63; }}
.login-product-icon svg {{ width:26px; height:26px; }}
.calendar-login-header h1 {{ margin:0 0 8px; font-size:clamp(1.75rem, 5vw, 2rem); line-height:1.15; letter-spacing:-.025em; }}
.calendar-login-header p {{ max-width:330px; margin:0 auto; color:var(--muted); font-size:.94rem; line-height:1.4; font-weight:500; }}
.calendar-login-header a {{ display:inline-flex; margin-top:12px; min-height:36px; align-items:center; padding:6px 12px; border:1px solid var(--border); border-radius:8px; color:var(--text); background:var(--surface); text-decoration:none; font-size:.78rem; font-weight:650; }}
.calendar-login-form {{ width:100%; display:grid; gap:12px; }}
.calendar-login-control {{ width:100%; display:flex; align-items:center; padding:6px 6px 6px 14px; border:1.5px solid var(--border); border-radius:12px; background:var(--surface); transition:border-color .15s; }}
.calendar-login-control:focus-within {{ border-color:#e91e63; }}
.calendar-login-control input {{ flex:1 1 auto; width:auto; min-width:0; min-height:38px; padding:0; border:0; outline:0; background:transparent; color:var(--text); }}
.calendar-login-control button {{ flex:0 0 auto; width:auto; min-height:40px; padding:8px 16px; border:0; border-radius:8px; background:#e91e63; font-weight:750; }}
.calendar-login-back {{ justify-self:center; min-height:auto !important; padding:4px 8px !important; border:0 !important; background:transparent !important; color:var(--muted) !important; font-size:.78rem !important; }}
.calendar-login .notice {{ width:100%; margin:0 0 12px; }}
.calendar-login-footer {{ width:100%; padding:16px 24px; border-top:1px solid var(--border); display:flex; justify-content:space-between; color:var(--muted); font-size:.8rem; font-weight:650; }}
.calendar-login-footer a {{ color:var(--text); text-decoration:none; }}
</style></head><body><main{main_class}>{body}</main>{render_theme_script()}</body></html>"""


def render_login(error: str | None = None, *, username: str = "", pin_step: bool = False) -> str:
    notice = f'<p class="notice">{escape(error)}</p>' if error else ""
    if pin_step:
        form = f"""
        <form class="calendar-login-form" method="post" action="/login">
          <input type="hidden" name="stage" value="pin">
          <input type="hidden" name="username" value="{escape(username)}">
          <div class="calendar-login-control"><input name="pin" type="password" inputmode="numeric" pattern="[0-9]{{4}}" minlength="4" maxlength="4"
              autocomplete="current-password" aria-label="Vierstellige PIN für {escape(username)}" placeholder="PIN für {escape(username)}" required autofocus data-pin-input>
            <button type="submit">Entsperren <span aria-hidden="true">→</span></button></div>
          <button class="calendar-login-back" type="submit" name="stage" value="restart" formnovalidate>‹ Anderen Benutzer verwenden</button>
        </form>
        <script>(() => {{ const input=document.querySelector('[data-pin-input]'); if(input) input.addEventListener('input',()=>input.value=input.value.replace(/\\D/g,'').slice(0,4)); }})();</script>
        """
        step_label = "Schritt 2 von 2 · PIN"
    else:
        form = f"""
        <form class="calendar-login-form" method="post" action="/login">
          <input type="hidden" name="stage" value="username">
          <div class="calendar-login-control"><input name="username" value="{escape(username)}" autocomplete="username" placeholder="z.B. SophiaM" aria-label="Dein Benutzername" required minlength="3" maxlength="64" autofocus spellcheck="false">
            <button type="submit">Weiter <span aria-hidden="true">→</span></button></div>
        </form>
        """
        step_label = "Schritt 1 von 2 · Konto"
    return _layout("Anmelden · VpMobil", f"""
    <div class="calendar-login"><section class="calendar-login-shell"><header class="calendar-login-header"><div class="login-product-icon" data-login-product="clock" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3 2"></path></svg></div><h1>Vertretungsplan</h1><p>Stundenplan, Änderungen und persönliche Ankündigungen auf einen Blick.</p><a href="{escape(CALENDAR_PUBLIC_URL)}">Zum Kalender</a></header>
    {notice}{form}</section><footer class="calendar-login-footer"><span>{step_label}</span><a href="{escape(CALENDAR_PUBLIC_URL)}">cal11.de</a></footer></div>""")


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
    is_admin: bool = False,
    admin_authenticated: bool = False,
    admin_users: list[dict[str, object]] | None = None,
    admin_categories: list[dict[str, object]] | None = None,
    admin_courses: list[dict[str, object]] | None = None,
    admin_modal_error: str | None = None,
    admin_modal_success: str | None = None,
    can_change_pin: bool = False,
    force_pin_change: bool = False,
    pin_modal_error: str | None = None,
    pin_modal_changed: bool = False,
    vp_user_modal_error: str | None = None,
    vp_user_modal_created: bool = False,
    session_username: str | None = None,
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
        '<div class="calendar-row">'
        + _choice_checkbox("calendar_event_type", option.id, option.label, checked=option.id in notify_settings.calendar_notification_types)
        + f'<div class="category-schedule"><label>Uhrzeit<input type="time" name="calendar_notification_time__{quote(option.id, safe="")}" '
          f'value="{escape((notify_settings.calendar_notification_times or {}).get(option.id, notify_settings.calendar_notification_time))}" required step="60"></label>'
        + f'<label>Tage vorher<input type="number" min="0" max="365" inputmode="numeric" '
          f'name="calendar_notification_days_before__{quote(option.id, safe="")}" value="{(notify_settings.calendar_notification_days_before_by_type or {}).get(option.id, notify_settings.calendar_notification_days_before)}" required></label></div></div>'
        for option in calendar_event_types
    ) or "<p class=\"muted\">Im Kalender sind aktuell keine Kategorien verfügbar.</p>"

    topic_url = f"{ntfy_url.rstrip('/')}/{user.ntfy_topic}"
    class_select_options = "".join(
        f'<option value="{escape(class_name)}"{" selected" if class_name == first_selected_class else ""}>{escape(class_name)}</option>'
        for class_name in class_options
    )
    lesson_time_inputs = "".join(
        f'<label class="time-tab"><span class="time-label">{"Tagesübersicht" if index == 0 else f"Nächste Stunde {index}"}</span>'
        f'<input type="time" name="lesson_notification_time" value="{escape(value)}" required step="60">'
        + ('<span class="day-before-toggle"><input type="checkbox" name="daily_summary_day_before"'
           + (' checked' if notify_settings.daily_summary_day_before else '') + '> <span>Vortag</span></span>' if index == 0 else '')
        + '</label>'
        for index, value in enumerate(notify_settings.lesson_notification_times)
    )
    calendar_card = "" if user.vp_only else f"""
          <article class=\"settings-card\">
            <div class=\"settings-card-header\"><div><div class=\"settings-kicker\">Kalender</div><h2>Erinnerungen</h2></div></div>
            <div class=\"field-grid\">
              <label class=\"toggle-row\"><input type=\"checkbox\" id=\"calendar_notifications_enabled\" name=\"calendar_notifications_enabled\"{" checked" if notify_settings.calendar_notifications_enabled else ""}><span>Kalender-Benachrichtigungen aktiv</span></label>
              <div class=\"category-block\"><h3>Kategorien und Uhrzeiten</h3><div class=\"field-grid\">{event_type_checkboxes}</div></div>
            </div>
          </article>
    """
    return _layout("Ankündigungen · VpMobil", f"""
    <header class=\"topbar\"><div class=\"brand\"><h1>Meine Ankündigungen</h1><p>{escape(user.username)} · Klasse {escape(user.class_name)}</p></div>
      {render_vp_navigation("notifications", csrf_token, is_admin=is_admin, admin_authenticated=admin_authenticated, admin_users=admin_users, admin_categories=admin_categories, admin_courses=admin_courses, admin_modal_error=admin_modal_error, admin_modal_success=admin_modal_success, can_change_pin=can_change_pin, force_pin_change=force_pin_change, pin_modal_error=pin_modal_error, pin_modal_changed=pin_modal_changed, vp_user_modal_error=vp_user_modal_error, vp_user_modal_created=vp_user_modal_created, session_username=session_username)}</header>
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
          {calendar_card}
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
