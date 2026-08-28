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
    links = (("classes", "/", "Klassen"), ("teachers", "/lehrer", "Lehrer"),
             ("rooms", "/raeume", "Freie Räume"), ("notifications", "/abos", "Ankündigungen"))
    items = ""
    for key, href, label in links:
        active_class = ' class="active"' if key == active else ""
        items += f'<a{active_class} href="{href}">{label}</a>'
    if is_admin:
        items += '<button class="nav-button" type="button" data-admin-modal-open>Admin</button>'
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
    admin_modal = (
        render_admin_panel_modal(
            csrf_token,
            authenticated=admin_authenticated,
            users=admin_users or [],
            categories=admin_categories or [],
            courses=admin_courses or [],
            error=admin_modal_error or vp_user_modal_error,
            success=admin_modal_success or ("Der VP-Nutzer wurde angelegt." if vp_user_modal_created else None),
        )
        if is_admin and csrf_token else ""
    )
    user_attr = f' data-session-user="{escape(session_username)}"' if session_username else ""
    calendar_link = "" if can_change_pin else f'<a href="{escape(CALENDAR_PUBLIC_URL)}">Kalender</a>'
    return f'<nav class="nav"{user_attr}>{items}{calendar_link}{logout}{render_theme_toggle_button()}</nav>{modal}{admin_modal}'


def render_admin_panel_modal(
    csrf_token: str | None,
    *,
    authenticated: bool = False,
    users: list[dict[str, object]] | None = None,
    categories: list[dict[str, object]] | None = None,
    courses: list[dict[str, object]] | None = None,
    error: str | None = None,
    success: str | None = None,
) -> str:
    if not csrf_token:
        return ""
    message = ""
    open_without_message = success == "__open__"
    if success and not open_without_message:
        message = f'<p class="pin-modal-notice success">{escape(success)}</p>'
    if error:
        message = f'<p class="pin-modal-notice">{escape(error)}</p>'
    open_attr = " open" if error or success else ""
    if not authenticated:
        content = f"""
        <form method="post" action="/admin/auth" class="admin-auth-card">
          <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
          <div class="admin-auth-head">
            <h2>Admin-Bereich</h2>
            <button class="admin-close-button" type="button" data-admin-modal-close aria-label="Schließen">×</button>
          </div>
          <div class="admin-auth-body">
            {message}
            <label>Admin-Passwort eingeben<input name="admin_password" type="password" required minlength="12" maxlength="256" autocomplete="current-password" autofocus></label>
            <button type="submit">Verifizieren</button>
          </div>
        </form>
        """
    else:
        user_rows = "".join(
            _render_admin_user_row(csrf_token, row)
            for row in (users or [])
        ) or '<div class="admin-empty">Noch keine Benutzer vorhanden.</div>'
        category_rows = "".join(
            f"""<div class="admin-category-row">
              <div class="admin-category-main">
                <span class="color-dot" style="background:{escape(str(row.get('color', '#888')))}"></span>
                <strong>{escape(str(row.get('name', '')))}</strong>
                <code>{escape(str(row.get('id', '')))}</code>
              </div>
              <form method="post" action="/admin/categories/delete">
                <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
                <input type="hidden" name="id" value="{escape(str(row.get('id', '')))}">
                <button class="icon-danger-button" type="submit" aria-label="Kategorie löschen">×</button>
              </form>
            </div>"""
            for row in (categories or [])
        ) or '<div class="admin-empty">Noch keine globalen Kategorien vorhanden.</div>'
        courses_by_type = {
            course_type: [row for row in (courses or []) if str(row.get("type", "GK")).upper() == course_type]
            for course_type in ("LK", "GK", "AG")
        }
        course_sections = "".join(
            _render_admin_course_section(csrf_token, course_type, courses_by_type[course_type])
            for course_type in ("LK", "GK", "AG")
        )
        content = f"""
        <div class="calendar-admin-shell">
          <div class="calendar-admin-header">
            <div><span>Geschützt</span><h2>Admin-Einstellungen</h2></div>
            <button class="admin-close-button" type="button" data-admin-modal-close aria-label="Schließen">×</button>
          </div>
          <div class="calendar-admin-body">
            <aside class="calendar-admin-sidebar" aria-label="Admin-Menü">
              <button class="admin-tab-button active" type="button" data-admin-tab="users">Benutzer</button>
              <button class="admin-tab-button" type="button" data-admin-tab="categories">Kategorien</button>
              <button class="admin-tab-button" type="button" data-admin-tab="courses">Kurse</button>
            </aside>
            <div class="calendar-admin-content">
              {message}
              <section class="admin-tab-panel" data-admin-tab-panel="users">
                <div class="admin-section">
                  <h3>Benutzerverwaltung</h3>
                  <p class="admin-hint">Neue Kalender- oder VP-only-Benutzer anlegen, PINs setzen, Status ändern und gesperrte Accounts verwalten.</p>
                  <form method="post" action="/admin/users/create" class="admin-form-grid">
                    <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
                    <input name="username" required minlength="3" maxlength="64" autocomplete="off" spellcheck="false" placeholder="Benutzername" aria-label="Benutzername">
                    <input name="class_name" maxlength="64" value="{escape(os.getenv('VP_DEFAULT_CLASS', '11') or '11')}" placeholder="Klasse" aria-label="Klasse">
                    <input name="pin" type="password" inputmode="numeric" pattern="[0-9]{{4}}" minlength="4" maxlength="4" autocomplete="new-password" data-admin-pin placeholder="PIN optional" aria-label="Optionale PIN">
                    <label class="inline-check"><input type="checkbox" name="vp_only" value="1"><span>VP-only</span></label>
                    <button type="submit">Benutzer anlegen</button>
                  </form>
                </div>
                <div class="admin-section">
                  <h3>Alle Benutzer</h3>
                  <div class="admin-user-list">{user_rows}</div>
                </div>
              </section>
              <section class="admin-tab-panel" data-admin-tab-panel="categories" hidden>
                <div class="admin-section">
                  <h3>Globale Kategorien</h3>
                  <p class="admin-hint">Diese Kategorien erscheinen im Kalender für alle normalen Kalendernutzer.</p>
                  <form method="post" action="/admin/categories/save" class="admin-mini-form">
                    <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
                    <input name="name" placeholder="Name" required maxlength="64">
                    <input name="id" placeholder="ID optional" maxlength="64">
                    <input name="color" type="color" value="#0f766e" required>
                    <button type="submit">Speichern</button>
                  </form>
                  <div class="admin-card-list">{category_rows}</div>
                </div>
              </section>
              <section class="admin-tab-panel" data-admin-tab-panel="courses" hidden>
                <div class="admin-section">
                  <h3>Kurse</h3>
                  <p class="admin-hint">Globale Kursliste für Kalenderauswahl und gemeinsame Ereignisse.</p>
                  <form method="post" action="/admin/courses/save" class="admin-mini-form">
                    <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
                    <input name="name" placeholder="Kurs" required maxlength="64">
                    <input name="id" placeholder="ID optional" maxlength="64">
                    <input name="teacher" placeholder="Lehrer" maxlength="64">
                    <select name="type"><option>GK</option><option>LK</option><option>AG</option></select>
                    <button type="submit">Speichern</button>
                  </form>
                  <form method="post" action="/admin/courses/reorder" class="admin-course-reorder-form" data-admin-course-reorder-form>
                    <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
                  </form>
                  <div class="admin-course-sections">{course_sections}</div>
                </div>
              </section>
            </div>
          </div>
        </div>
        """
    modal_class = "pin-modal admin-modal admin-modal--auth" if not authenticated else "pin-modal admin-modal"
    return f"""
    <dialog class="{modal_class}"{open_attr} data-admin-modal data-admin-authenticated="{'1' if authenticated else '0'}">
      {content}
    </dialog>
    <script>
    (() => {{
      const dialog = document.querySelector('[data-admin-modal]');
      if (!dialog) return;
      const isModal = () => {{ try {{ return dialog.matches(':modal'); }} catch (_) {{ return false; }} }};
      const open = () => {{
        if (dialog.open && !isModal()) dialog.removeAttribute('open');
        if (typeof dialog.showModal === 'function' && !dialog.open) dialog.showModal();
        else dialog.setAttribute('open', '');
      }};
      let adminLocking = false;
      const lockAndReload = () => {{
        if (adminLocking) return;
        const authenticated = dialog.getAttribute('data-admin-authenticated') === '1';
        if (!authenticated) return;
        adminLocking = true;
        const csrf = dialog.querySelector('input[name="csrf_token"]');
        const body = new URLSearchParams();
        if (csrf) body.append('csrf_token', csrf.value);
        fetch('/admin/lock', {{
          method: 'POST',
          credentials: 'same-origin',
          headers: {{ 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8' }},
          body,
          keepalive: true
        }}).finally(() => {{
          window.location.replace(window.location.pathname);
        }});
      }};
      const close = () => {{
        dialog.close ? dialog.close() : dialog.removeAttribute('open');
        lockAndReload();
      }};
      dialog.addEventListener('close', lockAndReload);
      document.querySelectorAll('[data-admin-modal-open]').forEach((button) => button.addEventListener('click', open));
      document.querySelectorAll('[data-admin-modal-close]').forEach((button) => button.addEventListener('click', close));
      dialog.querySelectorAll('[data-admin-tab]').forEach((button) => {{
        button.addEventListener('click', () => {{
          const tab = button.getAttribute('data-admin-tab');
          dialog.querySelectorAll('[data-admin-tab]').forEach((item) => item.classList.toggle('active', item === button));
          dialog.querySelectorAll('[data-admin-tab-panel]').forEach((panel) => {{
            panel.hidden = panel.getAttribute('data-admin-tab-panel') !== tab;
          }});
        }});
      }});
      dialog.querySelectorAll('[data-admin-pin]').forEach((input) => input.addEventListener('input', () => input.value = input.value.replace(/\\D/g, '').slice(0, 4)));
      const reorderForm = dialog.querySelector('[data-admin-course-reorder-form]');
      const serializeCourses = () => {{
        const payload = new URLSearchParams();
        const csrf = reorderForm ? reorderForm.querySelector('input[name="csrf_token"]') : null;
        if (csrf) payload.append('csrf_token', csrf.value);
        dialog.querySelectorAll('[data-admin-course-section]').forEach((section) => {{
          const type = section.getAttribute('data-course-type') || 'GK';
          section.querySelectorAll('[data-admin-course-card]').forEach((card) => {{
            const id = card.getAttribute('data-course-id') || '';
            if (id) {{
              payload.append('course_id', id);
              payload.append('course_type', type);
            }}
          }});
        }});
        return payload;
      }};
      const saveCourseOrder = async () => {{
        if (!reorderForm) return;
        try {{
          const response = await fetch(reorderForm.getAttribute('action') || '/admin/courses/reorder', {{
            method: 'POST',
            credentials: 'same-origin',
            headers: {{ 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8' }},
            body: serializeCourses()
          }});
          if (!response.ok) window.location.reload();
        }} catch (_) {{
          window.location.reload();
        }}
      }};
      const saveCourseCard = async (form) => {{
        if (!form) return;
        const section = form.closest('[data-admin-course-section]');
        const typeInput = form.querySelector('[data-admin-course-type-input]');
        if (section && typeInput) typeInput.value = section.getAttribute('data-course-type') || 'GK';
        try {{
          const response = await fetch(form.getAttribute('action') || '/admin/courses/save', {{
            method: 'POST',
            credentials: 'same-origin',
            headers: {{ 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8' }},
            body: new URLSearchParams(new FormData(form))
          }});
          if (!response.ok) window.location.reload();
        }} catch (_) {{
          window.location.reload();
        }}
      }};
      const updateCourseEmptyStates = () => {{
        dialog.querySelectorAll('[data-admin-course-section]').forEach((section) => {{
          const hasCards = section.querySelectorAll('[data-admin-course-card]').length > 0;
          section.querySelectorAll('[data-admin-course-empty]').forEach((empty) => {{
            empty.hidden = hasCards;
          }});
        }});
      }};
      let draggedCourse = null;
      dialog.querySelectorAll('[data-admin-course-card]').forEach((card) => {{
        card.querySelectorAll('input').forEach((input) => {{
          input.addEventListener('pointerdown', (event) => event.stopPropagation());
          input.addEventListener('dragstart', (event) => event.preventDefault());
        }});
        card.addEventListener('dragstart', (event) => {{
          if (event.target && event.target.closest && event.target.closest('input, button, select, textarea')) {{
            event.preventDefault();
            return;
          }}
          draggedCourse = card;
          card.classList.add('dragging');
          if (event.dataTransfer) {{
            event.dataTransfer.effectAllowed = 'move';
            event.dataTransfer.setData('text/plain', card.getAttribute('data-course-id') || '');
          }}
        }});
        card.addEventListener('dragend', () => {{
          card.classList.remove('dragging');
          dialog.querySelectorAll('[data-admin-course-section]').forEach((section) => section.classList.remove('drag-over'));
          draggedCourse = null;
        }});
      }});
      dialog.querySelectorAll('[data-admin-course-field]').forEach((field) => {{
        let saveTimer = null;
        const form = field.closest('[data-admin-course-save-form]');
        field.addEventListener('input', () => {{
          window.clearTimeout(saveTimer);
          saveTimer = window.setTimeout(() => saveCourseCard(form), 550);
        }});
        field.addEventListener('blur', () => {{
          window.clearTimeout(saveTimer);
          saveCourseCard(form);
        }});
        field.addEventListener('keydown', (event) => {{
          if (event.key === 'Enter') {{
            event.preventDefault();
            window.clearTimeout(saveTimer);
            saveCourseCard(form);
            field.blur();
          }}
        }});
      }});
      dialog.querySelectorAll('[data-admin-course-move]').forEach((button) => {{
        button.addEventListener('click', () => {{
          const card = button.closest('[data-admin-course-card]');
          const direction = button.getAttribute('data-admin-course-move');
          if (!card) return;
          if (direction === 'left') {{
            const previous = card.previousElementSibling;
            if (previous && previous.matches('[data-admin-course-card]')) {{
              card.parentNode.insertBefore(card, previous);
              saveCourseOrder();
            }}
          }} else {{
            const next = card.nextElementSibling;
            if (next && next.matches('[data-admin-course-card]')) {{
              card.parentNode.insertBefore(next, card);
              saveCourseOrder();
            }}
          }}
        }});
      }});
      dialog.querySelectorAll('[data-admin-course-section]').forEach((section) => {{
        section.addEventListener('dragover', (event) => {{
          if (!draggedCourse) return;
          event.preventDefault();
          section.classList.add('drag-over');
          const grid = section.querySelector('.admin-course-grid');
          if (!grid) return;
          grid.querySelectorAll('[data-admin-course-empty]').forEach((empty) => empty.hidden = true);
          const afterElement = Array.from(grid.querySelectorAll('[data-admin-course-card]:not(.dragging)')).find((card) => {{
            const rect = card.getBoundingClientRect();
            return event.clientY < rect.top + rect.height / 2 && event.clientX < rect.right;
          }});
          if (afterElement) grid.insertBefore(draggedCourse, afterElement);
          else grid.appendChild(draggedCourse);
          updateCourseEmptyStates();
        }});
        section.addEventListener('dragleave', (event) => {{
          if (!section.contains(event.relatedTarget)) section.classList.remove('drag-over');
        }});
        section.addEventListener('drop', (event) => {{
          if (!draggedCourse) return;
          event.preventDefault();
          section.classList.remove('drag-over');
          updateCourseEmptyStates();
          saveCourseOrder();
        }});
      }});
      updateCourseEmptyStates();
      if (dialog.hasAttribute('open')) {{
        window.history.replaceState(null, '', window.location.pathname);
        window.setTimeout(open, 0);
        const first = dialog.querySelector('input:not([type="hidden"])');
        if (first) window.setTimeout(() => first.focus(), 50);
      }}
    }})();
    </script>
    """


def _render_admin_user_row(csrf_token: str, row: dict[str, object]) -> str:
    username = str(row.get("username", ""))
    escaped_user = escape(username)
    status = str(row.get("status", "ACTIVE"))
    is_admin = bool(row.get("is_admin")) or status == "ADMIN"
    is_vp_only = bool(row.get("vp_only")) or bool(row.get("vpOnly")) or status == "VP_ONLY"
    kind = "Admin" if is_admin else "Nur Vertretungsplan" if is_vp_only else (
        "Aktiv" if status == "ACTIVE" else "Nur Lesezugriff" if status == "READ_ONLY" else "Gesperrt"
    )
    badge_class = "admin" if is_admin else "vp" if is_vp_only else status.lower().replace("_", "-")
    actions = (
        '<span class="admin-db-note">Nur in der DB veränderbar</span>'
        if is_admin
        else _admin_user_actions(csrf_token, username, bool(row.get("can_set_calendar_status")))
    )
    checkbox = "disabled" if is_admin or is_vp_only else ""
    return f"""
    <div class="admin-user-row">
      <div class="admin-user-left">
        <input type="checkbox" aria-label="{escaped_user} auswählen" {checkbox}>
        <div>
          <strong>{escaped_user}</strong>
          <span class="admin-user-badge {badge_class}">{escape(kind)}</span>
          <span class="admin-row-muted">{escape(str(row.get('class_name', '')))} · {escape(str(row.get('kind', '')))}</span>
        </div>
      </div>
      <div class="admin-user-actions">{actions}</div>
    </div>
    """


def _admin_user_actions(csrf_token: str, username: str, can_set_status: bool) -> str:
    escaped_user = escape(username)
    status_form = ""
    if can_set_status:
        status_form = f"""
        <form method="post" action="/admin/users/status" class="inline-form">
          <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
          <input type="hidden" name="username" value="{escaped_user}">
          <select name="status" aria-label="Status für {escaped_user}">
            <option value="ACTIVE">Edit</option>
            <option value="READ_ONLY">Readonly</option>
            <option value="BLOCKED">Sperren</option>
          </select>
          <button type="submit">Setzen</button>
        </form>
        """
    return status_form + f"""
    <form method="post" action="/admin/users/pin" class="inline-form">
      <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
      <input type="hidden" name="username" value="{escaped_user}">
      <input name="pin" type="password" inputmode="numeric" pattern="[0-9]{{4}}" minlength="4" maxlength="4" required autocomplete="new-password" data-admin-pin aria-label="Neue PIN für {escaped_user}">
      <button type="submit">PIN ändern</button>
    </form>
    <form method="post" action="/admin/users/delete" class="inline-form" onsubmit="return confirm('Benutzer {escaped_user} wirklich löschen?')">
      <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
      <input type="hidden" name="username" value="{escaped_user}">
      <button class="danger-button" type="submit">Löschen</button>
    </form>
    """


def _render_admin_course_section(csrf_token: str, course_type: str, rows: list[dict[str, object]]) -> str:
    color = "#0f766e" if course_type == "LK" else "#3b82f6" if course_type == "GK" else "#10b981"
    title = "Leistungskurse (LK)" if course_type == "LK" else "Grundkurse (GK)" if course_type == "GK" else "Arbeitsgemeinschaften (AG)"
    cards = "".join(
        f"""
        <div class="admin-course-card" draggable="true" data-admin-course-card data-course-id="{escape(str(row.get('id', '')))}">
          <form method="post" action="/admin/courses/save" class="admin-course-edit-form" data-admin-course-save-form>
            <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
            <input type="hidden" name="id" value="{escape(str(row.get('id', '')))}">
            <input type="hidden" name="type" value="{escape(course_type)}" data-admin-course-type-input>
            <div class="admin-course-card-top">
              <span class="admin-course-grip" title="Verschieben" aria-hidden="true">⋮⋮</span>
              <div class="admin-course-fields">
                <input name="name" value="{escape(str(row.get('name', '')))}" placeholder="Name" maxlength="64" required data-admin-course-field aria-label="Kursname bearbeiten">
                <div class="admin-course-teacher-line">
                  <span>(</span>
                  <input name="teacher" value="{escape(str(row.get('teacher', '')))}" placeholder="Lehrer" maxlength="64" data-admin-course-field aria-label="Lehrer bearbeiten">
                  <span>)</span>
                </div>
                <code>{escape(str(row.get('id', '')))}</code>
              </div>
            </div>
          </form>
          <div class="admin-course-actions">
            <button class="admin-course-move" type="button" data-admin-course-move="left" aria-label="Nach links verschieben">‹</button>
            <button class="admin-course-move" type="button" data-admin-course-move="right" aria-label="Nach rechts verschieben">›</button>
            <form method="post" action="/admin/courses/delete">
              <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
              <input type="hidden" name="id" value="{escape(str(row.get('id', '')))}">
              <button class="icon-danger-button" type="submit" aria-label="Kurs löschen">×</button>
            </form>
          </div>
        </div>
        """
        for row in rows
    ) or f'<div class="admin-course-empty" data-admin-course-empty>Kurse hierher ziehen, um sie als {escape(course_type)} festzulegen</div>'
    return f"""
    <div class="admin-course-section" data-admin-course-section data-course-type="{escape(course_type)}">
      <div class="admin-course-head">
        <div><span class="course-dot" style="background:{color}"></span><strong>{title}</strong><small>{len(rows)} {"Kurs" if len(rows) == 1 else "Kurse"}</small></div>
        <span>Karten greifen & verschieben</span>
      </div>
      <div class="admin-course-grid">{cards}</div>
    </div>
    """


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
.admin-modal {
    width: min(896px, calc(100vw - 32px));
    max-height: 90vh;
    border-radius: 8px;
    background: #ffffff;
    border-color: #e5e7eb;
    box-shadow: 0 10px 15px -3px rgb(0 0 0 / .10), 0 4px 6px -4px rgb(0 0 0 / .10);
    overflow: hidden;
}
.admin-modal::backdrop { background: rgb(0 0 0 / .5); }
html[data-theme="dark"] .admin-modal,
html[data-theme="system"] .admin-modal {
    background: #1a1a1a;
    border-color: #333333;
}
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .admin-modal {
        background: #ffffff;
        border-color: #e5e7eb;
    }
}
@media (prefers-color-scheme: dark) {
    html[data-theme="system"] .admin-modal::backdrop { background: rgb(0 0 0 / .7); }
}
html[data-theme="dark"] .admin-modal::backdrop { background: rgb(0 0 0 / .7); }
.admin-modal.admin-modal--auth {
    width: min(384px, calc(100vw - 32px));
}
.calendar-admin-shell {
    display: flex;
    flex-direction: column;
    max-height: 90vh;
    overflow: hidden;
}
.calendar-admin-header,
.admin-auth-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 16px 24px;
    border-bottom: 1px solid #e5e7eb;
    background: #fcfcfc;
}
html[data-theme="dark"] .calendar-admin-header,
html[data-theme="dark"] .admin-auth-head,
html[data-theme="system"] .calendar-admin-header,
html[data-theme="system"] .admin-auth-head {
    border-bottom-color: #333333;
    background: #121212;
}
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .calendar-admin-header,
    html[data-theme="system"] .admin-auth-head {
        border-bottom-color: #e5e7eb;
        background: #fcfcfc;
    }
}
.calendar-admin-header span { display:none; }
.calendar-admin-header h2,
.admin-auth-head h2 {
    margin: 0;
    color: #1f2937;
    font-size: 1.25rem;
    line-height: 1.75rem;
    font-weight: 700;
}
html[data-theme="dark"] .calendar-admin-header h2,
html[data-theme="dark"] .admin-auth-head h2,
html[data-theme="system"] .calendar-admin-header h2,
html[data-theme="system"] .admin-auth-head h2 { color: #f3f4f6; }
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .calendar-admin-header h2,
    html[data-theme="system"] .admin-auth-head h2 { color: #1f2937; }
}
.admin-close-button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 36px;
    height: 36px;
    min-height: 36px;
    padding: 0;
    border: 0;
    border-radius: 12px;
    background: transparent;
    color: #4b5563;
    font: inherit;
    font-size: 1.4rem;
    line-height: 1;
    cursor: pointer;
    transition: background-color .15s ease, color .15s ease;
}
.admin-close-button:hover { background: #f3f4f6; }
html[data-theme="dark"] .admin-close-button,
html[data-theme="system"] .admin-close-button { color: #9ca3af; }
html[data-theme="dark"] .admin-close-button:hover,
html[data-theme="system"] .admin-close-button:hover { background: #252525; }
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .admin-close-button { color: #4b5563; }
    html[data-theme="system"] .admin-close-button:hover { background: #f3f4f6; }
}
.admin-auth-card {
    display: block;
    padding: 20px;
    background: #ffffff;
}
html[data-theme="dark"] .admin-auth-card,
html[data-theme="system"] .admin-auth-card { background: #1a1a1a; }
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .admin-auth-card { background: #ffffff; }
}
.admin-auth-card .admin-auth-head {
    margin: -20px -20px 24px;
    padding: 20px 20px 24px;
    border-bottom: 0;
    background: transparent;
}
.admin-auth-body {
    display: grid;
    gap: 16px;
}
.admin-auth-body label {
    display: grid;
    gap: 4px;
    color: #4b5563;
    font-size: .875rem;
    line-height: 1.25rem;
    font-weight: 600;
}
html[data-theme="dark"] .admin-auth-body label,
html[data-theme="system"] .admin-auth-body label { color: #9ca3af; }
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .admin-auth-body label { color: #4b5563; }
}
.admin-auth-body input {
    width: 100%;
    min-height: 46px;
    padding: 10px 16px;
    border: 1px solid #d1d5db;
    border-radius: 12px;
    background: #f9fafb;
    color: #1f2937;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
    font-size: 1.25rem;
    line-height: 1.75rem;
    text-align: center;
    letter-spacing: .1em;
    outline: none;
}
html[data-theme="dark"] .admin-auth-body input,
html[data-theme="system"] .admin-auth-body input {
    border-color: #444444;
    background: #222222;
    color: #f3f4f6;
}
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .admin-auth-body input {
        border-color: #d1d5db;
        background: #f9fafb;
        color: #1f2937;
    }
}
.admin-auth-body button[type="submit"] {
    width: 100%;
    min-height: 46px;
    border: 0;
    border-radius: 12px;
    padding: 10px 16px;
    background: var(--primary);
    color: #ffffff;
    font: inherit;
    font-weight: 700;
    cursor: pointer;
    box-shadow: 0 1px 2px 0 rgb(0 0 0 / .05);
}
.calendar-admin-body {
    display: flex;
    flex: 1 1 auto;
    min-height: 0;
    overflow: hidden;
}
.calendar-admin-sidebar {
    width: 224px;
    flex: 0 0 224px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 16px;
    border-right: 1px solid #e5e7eb;
    background: #fcfcfc;
}
html[data-theme="dark"] .calendar-admin-sidebar,
html[data-theme="system"] .calendar-admin-sidebar {
    border-right-color: #333333;
    background: #121212;
}
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .calendar-admin-sidebar {
        border-right-color: #e5e7eb;
        background: #fcfcfc;
    }
}
.admin-tab-button {
    width: 100%;
    min-height: 44px;
    padding: 12px 16px;
    border: 0;
    border-radius: 12px;
    background: transparent;
    color: #4b5563;
    font: inherit;
    font-size: .875rem;
    line-height: 1.25rem;
    font-weight: 600;
    text-align: left;
    cursor: pointer;
    transition: background-color .15s ease, color .15s ease;
}
.admin-tab-button:hover { background: #f3f4f6; }
.admin-tab-button.active {
    background: var(--primary);
    color: #ffffff;
    box-shadow: 0 1px 2px 0 rgb(0 0 0 / .05);
}
html[data-theme="dark"] .admin-tab-button,
html[data-theme="system"] .admin-tab-button { color: #9ca3af; }
html[data-theme="dark"] .admin-tab-button:hover,
html[data-theme="system"] .admin-tab-button:hover { background: #252525; }
html[data-theme="dark"] .admin-tab-button.active,
html[data-theme="system"] .admin-tab-button.active { color: #ffffff; }
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .admin-tab-button { color: #4b5563; }
    html[data-theme="system"] .admin-tab-button:hover { background: #f3f4f6; }
    html[data-theme="system"] .admin-tab-button.active { color: #ffffff; }
}
.calendar-admin-content {
    flex: 1 1 auto;
    min-width: 0;
    padding: 24px;
    overflow-y: auto;
    background: #ffffff;
}
html[data-theme="dark"] .calendar-admin-content,
html[data-theme="system"] .calendar-admin-content { background: #1a1a1a; }
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .calendar-admin-content { background: #ffffff; }
}
.admin-tab-panel {
    display: grid;
    gap: 24px;
}
.admin-tab-panel[hidden] { display: none; }
.admin-hint { margin:0; color:#6b7280; font-size:.875rem; line-height:1.25rem; }
html[data-theme="dark"] .admin-hint,
html[data-theme="system"] .admin-hint { color:#6b7280; }
.admin-section {
    display:grid;
    gap:12px;
    min-width:0;
}
.admin-section h3 {
    margin:0;
    color:#1f2937;
    font-size:1.25rem;
    line-height:1.75rem;
    font-weight:700;
}
html[data-theme="dark"] .admin-section h3,
html[data-theme="system"] .admin-section h3 { color:#f3f4f6; }
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .admin-section h3 { color:#1f2937; }
}
.admin-form-grid,
.admin-mini-form {
    display:grid;
    gap:12px;
    align-items:center;
    padding:16px;
    border:1px solid #e5e7eb;
    border-radius:12px;
    background:#f9fafb;
}
.admin-form-grid { grid-template-columns:minmax(0,1fr) 90px 105px auto auto; }
.admin-mini-form { grid-template-columns:minmax(0,1fr) minmax(0,.8fr) auto auto; }
html[data-theme="dark"] .admin-form-grid,
html[data-theme="dark"] .admin-mini-form,
html[data-theme="system"] .admin-form-grid,
html[data-theme="system"] .admin-mini-form {
    border-color:#333333;
    background:#222222;
}
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .admin-form-grid,
    html[data-theme="system"] .admin-mini-form {
        border-color:#e5e7eb;
        background:#f9fafb;
    }
}
.admin-form-grid input,
.admin-mini-form input,
.admin-mini-form select,
.inline-form input,
.inline-form select {
    min-width:0;
    min-height:38px;
    border:1px solid #d1d5db;
    border-radius:8px;
    padding:8px 12px;
    background:#ffffff;
    color:#1f2937;
    font:inherit;
    font-size:.875rem;
    outline:none;
}
html[data-theme="dark"] .admin-form-grid label,
html[data-theme="system"] .admin-form-grid label { color:#9ca3af; }
html[data-theme="dark"] .admin-form-grid input,
html[data-theme="dark"] .admin-mini-form input,
html[data-theme="dark"] .admin-mini-form select,
html[data-theme="dark"] .inline-form input,
html[data-theme="dark"] .inline-form select,
html[data-theme="system"] .admin-form-grid input,
html[data-theme="system"] .admin-mini-form input,
html[data-theme="system"] .admin-mini-form select,
html[data-theme="system"] .inline-form input,
html[data-theme="system"] .inline-form select {
    border-color:#444444;
    background:#1a1a1a;
    color:#f3f4f6;
}
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .admin-form-grid label { color:#4b5563; }
    html[data-theme="system"] .admin-form-grid input,
    html[data-theme="system"] .admin-mini-form input,
    html[data-theme="system"] .admin-mini-form select,
    html[data-theme="system"] .inline-form input,
    html[data-theme="system"] .inline-form select {
        border-color:#d1d5db;
        background:#ffffff;
        color:#1f2937;
    }
}
.admin-form-grid button[type="submit"],
.admin-mini-form button[type="submit"] {
    min-height:38px;
    border:0;
    border-radius:8px;
    padding:8px 16px;
    background:var(--primary);
    color:white;
    font:inherit;
    font-size:.875rem;
    font-weight:700;
    cursor:pointer;
    box-shadow:0 1px 2px 0 rgb(0 0 0 / .05);
}
.inline-check {
    display:flex !important;
    align-items:center;
    justify-content:center;
    gap:8px;
    min-height:38px;
    height:38px;
    padding:8px 12px;
    border:1px solid #d1d5db;
    border-radius:8px;
    background:#ffffff;
    color:#1f2937 !important;
    font-size:.75rem !important;
    font-weight:700 !important;
}
.inline-check input { width:16px; height:16px; min-height:16px; padding:0; }
html[data-theme="dark"] .inline-check,
html[data-theme="system"] .inline-check {
    border-color:#444444;
    background:#1a1a1a;
    color:#f3f4f6 !important;
}
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .inline-check {
        border-color:#d1d5db;
        background:#ffffff;
        color:#1f2937 !important;
    }
}
.admin-table-wrap {
    overflow:auto;
    border:1px solid #e5e7eb;
    border-radius:12px;
    background:#ffffff;
}
html[data-theme="dark"] .admin-table-wrap,
html[data-theme="system"] .admin-table-wrap {
    border-color:#333333;
    background:#222222;
}
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .admin-table-wrap {
        border-color:#e5e7eb;
        background:#ffffff;
    }
}
.admin-table-wrap table { width:100%; min-width:620px; border-collapse:collapse; }
.admin-table-wrap.compact table { min-width:420px; }
.admin-table-wrap th,
.admin-table-wrap td {
    padding:12px 16px;
    border-bottom:1px solid #e5e7eb;
    text-align:left;
    vertical-align:middle;
    font-size:.875rem;
}
html[data-theme="dark"] .admin-table-wrap th,
html[data-theme="dark"] .admin-table-wrap td,
html[data-theme="system"] .admin-table-wrap th,
html[data-theme="system"] .admin-table-wrap td { border-bottom-color:#333333; }
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .admin-table-wrap th,
    html[data-theme="system"] .admin-table-wrap td { border-bottom-color:#e5e7eb; }
}
.admin-table-wrap tr:last-child td { border-bottom:0; }
.admin-row-muted { display:block; margin-top:4px; color:#6b7280; font-size:.75rem; font-weight:600; }
.admin-user-list {
    border:1px solid #e5e7eb;
    border-radius:12px;
    overflow:hidden;
}
html[data-theme="dark"] .admin-user-list,
html[data-theme="system"] .admin-user-list { border-color:#333333; }
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .admin-user-list { border-color:#e5e7eb; }
}
.admin-user-row {
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:16px;
    padding:16px;
    border-bottom:1px solid #e5e7eb;
    background:#f9fafb;
}
.admin-user-row:last-child { border-bottom:0; }
html[data-theme="dark"] .admin-user-row,
html[data-theme="system"] .admin-user-row {
    border-bottom-color:#333333;
    background:#222222;
}
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .admin-user-row {
        border-bottom-color:#e5e7eb;
        background:#f9fafb;
    }
}
.admin-user-left {
    display:flex;
    align-items:center;
    gap:12px;
    min-width:0;
}
.admin-user-left input[type="checkbox"] {
    width:16px;
    height:16px;
    min-height:16px;
    padding:0;
    flex:0 0 auto;
}
.admin-user-left strong {
    display:block;
    color:#1f2937;
    font-size:1rem;
    line-height:1.5rem;
}
html[data-theme="dark"] .admin-user-left strong,
html[data-theme="system"] .admin-user-left strong { color:#f3f4f6; }
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .admin-user-left strong { color:#1f2937; }
}
.admin-user-badge {
    display:block;
    margin-top:4px;
    font-size:.75rem;
    line-height:1rem;
    font-weight:600;
}
.admin-user-badge.active { color:#10b981; }
.admin-user-badge.read-only { color:#f59e0b; }
.admin-user-badge.blocked { color:#f43f5e; }
.admin-user-badge.admin { color:#a855f7; }
.admin-user-badge.vp { color:#3b82f6; }
.admin-user-actions {
    display:flex;
    flex-wrap:wrap;
    justify-content:flex-end;
    gap:6px;
    min-width:0;
}
.admin-db-note {
    color:#6b7280;
    font-size:.75rem;
    font-style:italic;
    font-weight:500;
    padding:0 8px;
}
.admin-card-list {
    display:grid;
    gap:8px;
}
.admin-category-row {
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:16px;
    padding:12px;
    border:1px solid #e5e7eb;
    border-radius:12px;
    background:#ffffff;
}
html[data-theme="dark"] .admin-category-row,
html[data-theme="system"] .admin-category-row {
    border-color:#333333;
    background:#1a1a1a;
}
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .admin-category-row {
        border-color:#e5e7eb;
        background:#ffffff;
    }
}
.admin-category-main {
    display:flex;
    align-items:center;
    gap:12px;
    min-width:0;
}
.admin-category-main strong {
    color:#1f2937;
    font-size:.875rem;
}
html[data-theme="dark"] .admin-category-main strong,
html[data-theme="system"] .admin-category-main strong { color:#f3f4f6; }
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .admin-category-main strong { color:#1f2937; }
}
.admin-category-main code,
.admin-course-card code {
    color:#6b7280;
    font-size:.7rem;
}
.icon-danger-button {
    display:inline-flex;
    align-items:center;
    justify-content:center;
    width:32px;
    height:32px;
    min-height:32px !important;
    border:0 !important;
    border-radius:8px !important;
    background:#fff1f2 !important;
    color:#f43f5e !important;
    font:inherit !important;
    font-size:1.1rem !important;
    line-height:1 !important;
    font-weight:700 !important;
    cursor:pointer;
}
html[data-theme="dark"] .icon-danger-button,
html[data-theme="system"] .icon-danger-button { background:rgb(76 5 25 / .30) !important; color:#fb7185 !important; }
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .icon-danger-button { background:#fff1f2 !important; color:#f43f5e !important; }
}
.admin-empty {
    padding:16px;
    color:#6b7280;
    font-size:.875rem;
    border:1px dashed #d1d5db;
    border-radius:12px;
}
.admin-course-sections {
    display:grid;
    gap:24px;
}
.admin-course-section {
    padding:16px;
    border:1px solid #e5e7eb;
    border-radius:6px;
    background:#fcfcfc;
    transition:border-color .15s ease, background-color .15s ease, box-shadow .15s ease;
}
.admin-course-section.drag-over {
    border-color:#0f766e;
    background:rgb(20 184 166 / .05);
    box-shadow:0 0 0 1px #0f766e;
}
html[data-theme="dark"] .admin-course-section,
html[data-theme="system"] .admin-course-section {
    border-color:#333333;
    background:#121212;
}
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .admin-course-section {
        border-color:#e5e7eb;
        background:#fcfcfc;
    }
}
.admin-course-head {
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:12px;
    margin-bottom:12px;
}
.admin-course-head div {
    display:flex;
    align-items:center;
    gap:8px;
    min-width:0;
}
.admin-course-head strong {
    color:#1f2937;
    font-size:.75rem;
    line-height:1rem;
    font-weight:700;
    text-transform:uppercase;
    letter-spacing:.05em;
}
html[data-theme="dark"] .admin-course-head strong,
html[data-theme="system"] .admin-course-head strong { color:#f3f4f6; }
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .admin-course-head strong { color:#1f2937; }
}
.admin-course-head small {
    padding:2px 8px;
    border-radius:999px;
    background:rgb(229 229 229 / .70);
    color:#374151;
    font-size:11px;
    font-weight:600;
}
html[data-theme="dark"] .admin-course-head small,
html[data-theme="system"] .admin-course-head small { background:#282828; color:#d4d4d4; }
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .admin-course-head small { background:rgb(229 229 229 / .70); color:#374151; }
}
.admin-course-head > span {
    color:#6b7280;
    font-size:11px;
}
.course-dot {
    width:10px;
    height:10px;
    border-radius:999px;
    flex:0 0 auto;
}
.admin-course-grid {
    display:grid;
    grid-template-columns:repeat(4, minmax(0, 1fr));
    gap:10px;
}
.admin-course-card {
    display:flex;
    flex-direction:column;
    justify-content:space-between;
    gap:8px;
    min-width:0;
    min-height:84px;
    padding:10px;
    border:1px solid #e5e7eb;
    border-radius:12px;
    background:#ffffff;
    box-shadow:0 1px 2px 0 rgb(0 0 0 / .03);
    cursor:grab;
    user-select:none;
    transition:opacity .15s ease, border-color .15s ease, background-color .15s ease, box-shadow .15s ease;
}
.admin-course-card:active { cursor:grabbing; }
.admin-course-card.dragging {
    opacity:.3;
    border-style:dashed;
    border-color:#0f766e;
}
.admin-course-card:hover {
    border-color:#cbd5e1;
}
html[data-theme="dark"] .admin-course-card,
html[data-theme="system"] .admin-course-card {
    border-color:#383838;
    background:#222222;
}
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .admin-course-card {
        border-color:#e5e7eb;
        background:#ffffff;
    }
    html[data-theme="system"] .admin-course-card:hover {
        border-color:#cbd5e1;
    }
}
html[data-theme="dark"] .admin-course-card:hover,
html[data-theme="system"] .admin-course-card:hover {
    border-color:#555555;
}
.admin-course-edit-form {
    display:block;
    min-width:0;
}
.admin-course-card-top {
    display:flex;
    align-items:flex-start;
    justify-content:space-between;
    gap:6px;
    min-width:0;
}
.admin-course-grip {
    flex:0 0 auto;
    padding:2px 0;
    color:#9ca3af;
    font-size:.9rem;
    line-height:1;
    letter-spacing:-.18em;
    cursor:grab;
}
.admin-course-fields {
    display:flex;
    flex-direction:column;
    flex:1 1 auto;
    gap:1px;
    min-width:0;
}
.admin-course-fields input {
    width:100%;
    min-height:0;
    border:0;
    border-radius:4px;
    padding:0 2px;
    background:transparent;
    outline:none;
}
.admin-course-fields input[name="name"] {
    color:#1f2937;
    font-size:.875rem;
    line-height:1.25rem;
    font-weight:700;
}
.admin-course-teacher-line {
    display:flex;
    align-items:center;
    gap:1px;
    color:#9ca3af;
    font-size:.75rem;
    min-width:0;
}
.admin-course-teacher-line input {
    color:#4b5563;
    font-size:.75rem;
    line-height:1rem;
}
.admin-course-fields input:focus {
    box-shadow:0 0 0 1px #0f766e;
    background:rgb(20 184 166 / .08);
}
html[data-theme="dark"] .admin-course-fields input,
html[data-theme="system"] .admin-course-fields input {
    color:#f3f4f6;
}
html[data-theme="dark"] .admin-course-teacher-line input,
html[data-theme="system"] .admin-course-teacher-line input {
    color:#9ca3af;
}
html[data-theme="dark"] .admin-course-card strong,
html[data-theme="system"] .admin-course-card strong { color:#f3f4f6; }
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .admin-course-card strong { color:#1f2937; }
}
.admin-course-actions {
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:4px;
    margin-top:8px;
}
.admin-course-actions form { margin-left:auto; }
.admin-course-move {
    display:inline-flex;
    align-items:center;
    justify-content:center;
    width:24px;
    height:24px;
    min-height:24px;
    border:0;
    border-radius:8px;
    background:transparent;
    color:#6b7280;
    font:inherit;
    font-size:1rem;
    font-weight:800;
    cursor:pointer;
}
.admin-course-move:hover {
    background:#f3f4f6;
    color:#1f2937;
}
html[data-theme="dark"] .admin-course-move,
html[data-theme="system"] .admin-course-move { color:#9ca3af; }
html[data-theme="dark"] .admin-course-move:hover,
html[data-theme="system"] .admin-course-move:hover { background:#252525; color:#f3f4f6; }
.admin-course-empty {
    display:flex;
    align-items:center;
    justify-content:center;
    min-height:72px;
    padding:24px;
    border:2px dashed #d4d4d4;
    border-radius:12px;
    color:#9ca3af;
    font-size:.75rem;
    font-weight:500;
    text-align:center;
}
html[data-theme="dark"] .admin-course-empty,
html[data-theme="system"] .admin-course-empty { border-color:#404040; color:#737373; }
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .admin-course-empty { border-color:#d4d4d4; color:#9ca3af; }
}
.inline-form {
    display:inline-flex;
    gap:6px;
    align-items:center;
    margin:0 6px 6px 0;
    max-width:100%;
    vertical-align:middle;
}
.inline-form select { min-height:34px; font-size:.75rem; padding:6px 8px; }
.inline-form input { width:64px; min-height:34px; padding:6px 8px; text-align:center; letter-spacing:.1em; font-size:.75rem; }
.inline-form button,
.danger-button {
    min-height:34px !important;
    padding:6px 8px !important;
    border:0 !important;
    border-radius:8px !important;
    font:inherit !important;
    font-size:.75rem !important;
    font-weight:600 !important;
    box-shadow:none !important;
    cursor:pointer;
}
.inline-form button { background:#fff7ed !important; color:#b45309 !important; }
.danger-button { background:#fff1f2 !important; color:#f43f5e !important; }
html[data-theme="dark"] .inline-form button,
html[data-theme="system"] .inline-form button { background:rgb(120 53 15 / .30) !important; color:#fcd34d !important; }
html[data-theme="dark"] .danger-button,
html[data-theme="system"] .danger-button { background:rgb(76 5 25 / .30) !important; color:#fb7185 !important; }
@media (prefers-color-scheme: light) {
    html[data-theme="system"] .inline-form button { background:#fff7ed !important; color:#b45309 !important; }
    html[data-theme="system"] .danger-button { background:#fff1f2 !important; color:#f43f5e !important; }
    html[data-theme="system"] .calendar-admin-sidebar { border-bottom-color:#e5e7eb; }
}
.color-dot { display:inline-block; width:24px; height:24px; border-radius:4px; margin-right:12px; vertical-align:-7px; border:0; }
@media (max-width: 760px) {
    .admin-modal {
        width:min(100vw - 12px, 896px);
        max-height:calc(100dvh - 12px);
    }
    .calendar-admin-shell { max-height:calc(100dvh - 12px); }
    .calendar-admin-header,
    .admin-auth-head { padding:14px 16px; }
    .admin-auth-card { padding:20px; }
    .admin-auth-card .admin-auth-head { margin:-20px -20px 24px; padding:20px 20px 24px; }
    .calendar-admin-body { flex-direction:column; }
    .calendar-admin-sidebar {
        width:100%;
        flex:0 0 auto;
        flex-direction:row;
        overflow-x:auto;
        border-right:0;
        border-bottom:1px solid #e5e7eb;
        padding:12px;
    }
    html[data-theme="dark"] .calendar-admin-sidebar,
    html[data-theme="system"] .calendar-admin-sidebar { border-bottom-color:#333333; }
    .admin-tab-button {
        flex:1 1 0;
        min-width:max-content;
        text-align:center;
        white-space:nowrap;
    }
    .calendar-admin-content { padding:16px; }
    .admin-form-grid,
    .admin-mini-form { grid-template-columns:1fr; }
    .inline-form { display:flex; flex-wrap:wrap; }
    .admin-table-wrap table { min-width:560px; }
    .admin-table-wrap.compact table { min-width:360px; }
    .admin-user-row {
        align-items:flex-start;
        flex-direction:column;
    }
    .admin-user-actions { justify-content:flex-start; width:100%; }
    .admin-course-grid { grid-template-columns:repeat(2, minmax(0, 1fr)); }
    .admin-course-head { align-items:flex-start; flex-direction:column; }
}
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
