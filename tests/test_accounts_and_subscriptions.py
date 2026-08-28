from datetime import date, datetime, time
from pathlib import Path
from contextlib import nullcontext
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import ANY, Mock, patch

from cryptography.fernet import Fernet
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from accounts import AccountStore, CalendarEventTypeOption, NotifySettings
from account_page import render_login, render_subscriptions
from main import resolve_cookie_domain
from main import cleanup_ntfy_history_once_per_day
from ntfy.service import NtfyService, resolve_ntfy_internal_url
from subscriptions import SubscriptionNotifier, subject_key, subject_options
from vp_data import get_future_week_dates, get_future_week_plans, get_subject_catalog_plans
from web_utils import CALENDAR_PUBLIC_URL, COMMON_CSS, SESSION_WATCH_SCRIPT, cookie_values


def lesson(subject, room, period, *, changed=False, course_number=None):
    return SimpleNamespace(
        fach=subject, räume=(room,), periode=period, änderung=changed,
        ausfall=False, kursnummer=course_number, info=None,
    )


class AccountAndSubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.encryption_key = Fernet.generate_key().decode()
        self.store = AccountStore(Path(self.temp.name) / "accounts.sqlite", self.encryption_key)
        self.alice = self.store.create_user("alice", "1234", "11", ntfy_topic="vpmobil-alice", ntfy_username="ntfy_alice", ntfy_password="secret")
        self.bob = self.store.create_user("bob", "5678", "11", ntfy_topic="vpmobil-bob", ntfy_username="ntfy_bob", ntfy_password="secret")

    def tearDown(self):
        self.temp.cleanup()

    def test_pin_is_hashed_and_session_is_server_side(self):
        import sqlite3
        with sqlite3.connect(Path(self.temp.name) / "accounts.sqlite") as connection:
            stored_pin = connection.execute("SELECT pin_hash FROM users WHERE username = ?", ("alice",)).fetchone()[0]
        self.assertNotEqual(stored_pin, "1234")
        self.assertTrue(stored_pin.startswith("scrypt$") or stored_pin.startswith("$argon2"))
        self.assertEqual(self.store.authenticate("alice", "1234", "127.0.0.1").id, self.alice.id)
        self.assertIsNone(self.store.authenticate("alice", "0000", "127.0.0.1"))
        token, csrf = self.store.create_session(self.alice.id)
        session = self.store.get_session(token)
        self.assertEqual(session.user.username, "alice")
        self.assertEqual(session.csrf_token, csrf)

    def test_vp_only_user_has_separate_session_and_must_change_pin(self):
        vp_user = self.store.create_vp_only_user(
            "vpguest", "2468", "11", created_by="alice",
            ntfy_topic="vpmobil-vpguest", ntfy_username="ntfy_vpguest", ntfy_password="secret",
        )
        self.assertTrue(vp_user.vp_only)
        self.assertTrue(vp_user.must_change_pin)

        import sqlite3
        with sqlite3.connect(Path(self.temp.name) / "accounts.sqlite") as connection:
            stored_pin = connection.execute("SELECT pin_hash FROM vp_only_users WHERE username = ?", ("vpguest",)).fetchone()[0]
            calendar_row = connection.execute("SELECT 1 FROM calendar_users WHERE username = ?", ("vpguest",)).fetchone()
        self.assertNotEqual(stored_pin, "2468")
        self.assertIsNone(calendar_row)

        authenticated = self.store.authenticate("vpguest", "2468", "127.0.0.1")
        self.assertIsNotNone(authenticated)
        self.assertTrue(authenticated.vp_only)
        token, csrf = self.store.create_session(authenticated.id)
        session = self.store.get_session(token)
        self.assertEqual(session.user.username, "vpguest")
        self.assertTrue(session.user.must_change_pin)
        self.assertEqual(session.csrf_token, csrf)

        with sqlite3.connect(Path(self.temp.name) / "accounts.sqlite") as connection:
            shared_sessions = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            vp_sessions = connection.execute("SELECT COUNT(*) FROM vp_only_sessions").fetchone()[0]
        self.assertEqual(shared_sessions, 0)
        self.assertEqual(vp_sessions, 1)

        self.store.change_vp_only_pin("vpguest", "1357")
        self.assertIsNone(self.store.authenticate("vpguest", "2468", "127.0.0.1"))
        changed = self.store.authenticate("vpguest", "1357", "127.0.0.1")
        self.assertIsNotNone(changed)
        self.assertFalse(changed.must_change_pin)

    def test_admin_set_pin_forces_next_user_pin_change(self):
        import json
        vp_user = self.store.create_vp_only_user(
            "vpguest", "2468", "11", created_by="alice",
            ntfy_topic="vpmobil-vpguest", ntfy_username="ntfy_vpguest", ntfy_password="secret",
        )
        self.store.change_vp_only_pin("vpguest", "1357")
        self.store.admin_set_user_pin("alice", "9999")
        self.store.admin_set_user_pin("vpguest", "8642")

        self.assertIsNone(self.store.authenticate("alice", "1234", "127.0.0.1"))
        self.assertEqual(self.store.authenticate("alice", "9999", "127.0.0.1").username, "alice")
        self.assertIsNone(self.store.authenticate("vpguest", "1357", "127.0.0.1"))
        changed_vp = self.store.authenticate("vpguest", "8642", "127.0.0.1")
        self.assertIsNotNone(changed_vp)
        self.assertTrue(changed_vp.must_change_pin)

        with self.store._connection() as connection:
            preferences = connection.execute(
                "SELECT preferences FROM calendar_users WHERE username = ?",
                ("alice",),
            ).fetchone()[0]
        self.assertTrue(json.loads(preferences)["forcePinChange"])

    def test_global_courses_can_be_reordered_and_moved_between_sections(self):
        self.store.save_global_course("MA1", "Mathe", "Kön", "GK")
        self.store.save_global_course("DE1", "Deutsch", "Hof", "GK")
        self.store.save_global_course("CH1", "Chemie", "Ada", "LK")

        self.store.reorder_global_courses(["DE1", "CH1", "MA1"], ["LK", "LK", "AG"])

        courses = self.store.get_global_courses()
        self.assertEqual([course["id"] for course in courses], ["DE1", "CH1", "MA1"])
        self.assertEqual([course["type"] for course in courses], ["LK", "LK", "AG"])

    def test_delete_user_removes_calendar_only_leftover_account(self):
        import sqlite3
        with sqlite3.connect(Path(self.temp.name) / "accounts.sqlite") as connection:
            connection.execute(
                "INSERT INTO calendar_users(username, courses, pin, preferences, status) VALUES (?, ?, ?, ?, ?)",
                ("leftover", "[]", None, "{}", "ACTIVE"),
            )

        self.assertTrue(any(row["username"] == "leftover" for row in self.store.list_admin_panel_users()))
        self.store.delete_user("leftover")
        self.assertFalse(any(row["username"] == "leftover" for row in self.store.list_admin_panel_users()))

    def test_admin_navigation_is_only_rendered_for_admin_flag(self):
        vp_user = self.store.create_vp_only_user(
            "vpguest", "2468", "11", created_by="alice",
            ntfy_topic="vpmobil-vpguest", ntfy_username="ntfy_vpguest", ntfy_password="secret",
        )
        normal_page = render_subscriptions(
            self.alice, ["11"], ("11",), {"11": []}, {"11": set()},
            NotifySettings(), [], "csrf", "https://ntfy.invalid",
        )
        admin_page = render_subscriptions(
            self.alice, ["11"], ("11",), {"11": []}, {"11": set()},
            NotifySettings(), [], "csrf", "https://ntfy.invalid", is_admin=True,
        )
        vp_only_page = render_subscriptions(
            vp_user, ["11"], ("11",), {"11": []}, {"11": set()},
            NotifySettings(
                calendar_notifications_enabled=True,
                calendar_notification_types=("KLAUSUR",),
            ),
            [CalendarEventTypeOption("KLAUSUR", "Klausur")],
            "csrf", "https://ntfy.invalid", can_change_pin=True,
        )
        self.assertNotIn("data-admin-modal-open", normal_page)
        self.assertIn("data-admin-modal-open", admin_page)
        self.assertIn("Admin-Passwort", admin_page)
        self.assertNotIn("Alle Benutzer", admin_page)
        elevated_admin_page = render_subscriptions(
            self.alice, ["11"], ("11",), {"11": []}, {"11": set()},
            NotifySettings(), [], "csrf", "https://ntfy.invalid", is_admin=True, admin_authenticated=True,
            admin_courses=[{"id": "MA1", "name": "Mathe", "teacher": "Kön", "type": "LK", "sort_order": 0}],
            admin_modal_success="__open__",
        )
        self.assertIn("calendar-admin-sidebar", elevated_admin_page)
        self.assertIn("admin-tab-button", elevated_admin_page)
        self.assertIn("data-admin-course-save-form", elevated_admin_page)
        self.assertIn('data-admin-course-move="left"', elevated_admin_page)
        self.assertIn('name="teacher"', elevated_admin_page)
        self.assertIn('data-admin-authenticated="1"', elevated_admin_page)
        self.assertIn("/admin/lock", elevated_admin_page)
        self.assertNotIn("__open__", elevated_admin_page)
        self.assertNotIn("Adminbereich entsperrt", elevated_admin_page)
        self.assertNotIn("data-admin-modal-open", vp_only_page)
        self.assertIn("data-pin-modal-open", vp_only_page)
        self.assertNotIn("Kalender-Benachrichtigungen aktiv", vp_only_page)
        self.assertNotIn('name="calendar_event_type"', vp_only_page)
        self.assertNotIn(f'href="{CALENDAR_PUBLIC_URL}"', vp_only_page)

    def test_forced_pin_change_opens_centered_modal_and_allows_explicit_close(self):
        page = render_subscriptions(
            self.alice, ["11"], ("11",), {"11": []}, {"11": set()},
            NotifySettings(), [], "csrf", "https://ntfy.invalid",
            can_change_pin=True, force_pin_change=True,
        )
        self.assertIn('data-force-pin-change="1"', page)
        self.assertIn('data-pin-modal-close', page)
        self.assertIn("showModal", page)
        self.assertIn("window.history.replaceState", page)

    def test_mobile_navigation_allows_wrapped_button_text_without_growing(self):
        self.assertIn("white-space: normal", COMMON_CSS)
        self.assertIn("overflow-wrap: anywhere", COMMON_CSS)
        self.assertIn("height: 36px !important", COMMON_CSS)
        self.assertIn("font-size: clamp(.68rem, 2.6vw, .875rem) !important", COMMON_CSS)

    def test_calendar_edge_swipe_is_enabled_for_all_touch_devices(self):
        calendar_view = (Path(__file__).resolve().parent.parent / "src/components/CalendarView.tsx").read_text(encoding="utf-8")
        function_body = calendar_view.split("const isTouchCalendarNavigationAvailable = () => {", 1)[1].split("  };", 1)[0]
        self.assertIn("navigator.maxTouchPoints", function_body)
        self.assertIn("'ontouchstart' in window", function_body)
        self.assertNotIn("innerWidth", function_body)

    def test_calendar_edge_swipe_direction_is_resolved_after_drag(self):
        calendar_view = (Path(__file__).resolve().parent.parent / "src/components/CalendarView.tsx").read_text(encoding="utf-8")
        self.assertIn("canPullFromLeft", calendar_view)
        self.assertIn("canPullFromRight", calendar_view)
        self.assertIn("dx > 0 && gesture.canPullFromLeft", calendar_view)
        self.assertIn("dx < 0 && gesture.canPullFromRight", calendar_view)
        self.assertIn("edge: null", calendar_view)

    def test_calendar_layout_uses_dynamic_viewport_and_scrolls_only_calendar_area(self):
        calendar_view = (Path(__file__).resolve().parent.parent / "src/components/CalendarView.tsx").read_text(encoding="utf-8")
        self.assertIn("fixed inset-0", calendar_view)
        self.assertIn("h-[100dvh]", calendar_view)
        self.assertIn("max-h-[100dvh]", calendar_view)
        self.assertIn("flex-1 min-h-0 overflow-auto", calendar_view)
        index_css = (Path(__file__).resolve().parent.parent / "src/index.css").read_text(encoding="utf-8")
        self.assertIn("#root", index_css)
        self.assertIn("overflow: hidden", index_css)

    def test_calendar_admin_modal_mobile_tabs_are_top_compact_and_reauths_every_open(self):
        admin_modal = (Path(__file__).resolve().parent.parent / "src/components/AdminModal.tsx").read_text(encoding="utf-8")
        self.assertIn("flex-col sm:flex-row", admin_modal)
        self.assertIn("border-b sm:border-b-0 sm:border-r", admin_modal)
        self.assertIn("p-2 sm:p-4 flex flex-row sm:flex-col", admin_modal)
        self.assertIn("setIsAuthenticated(false)", admin_modal)
        self.assertIn("setAdminToken('')", admin_modal)

    def test_ntfy_history_is_cleared_only_once_per_calendar_day(self):
        cache = Path(self.temp.name) / "cache.db"
        marker = Path(self.temp.name) / "cleanup-date"
        import sqlite3
        with sqlite3.connect(cache) as connection:
            connection.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, message TEXT)")
            connection.execute("INSERT INTO messages(message) VALUES ('secret')")
        with patch.dict("os.environ", {"NTFY_CACHE_FILE": str(cache), "NTFY_HISTORY_CLEANUP_MARKER": str(marker)}):
            self.assertTrue(cleanup_ntfy_history_once_per_day(datetime(2026, 8, 27, 0, 0)))
            with sqlite3.connect(cache) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
                connection.execute("INSERT INTO messages(message) VALUES ('new')")
            self.assertFalse(cleanup_ntfy_history_once_per_day(datetime(2026, 8, 27, 12, 0)))
            self.assertTrue(cleanup_ntfy_history_once_per_day(datetime(2026, 8, 28, 0, 0)))

    def test_login_is_two_step_and_pin_is_restricted_to_four_digits(self):
        username_page = render_login()
        self.assertIn('data-login-product="clock"', username_page)
        self.assertIn('name="stage" value="username"', username_page)
        self.assertNotIn('name="pin"', username_page)

        pin_page = render_login(username="alice", pin_step=True)
        self.assertIn('name="stage" value="pin"', pin_page)
        self.assertIn('inputmode="numeric"', pin_page)
        self.assertIn('pattern="[0-9]{4}"', pin_page)
        self.assertIn('maxlength="4"', pin_page)

    def test_notification_form_only_has_category_specific_days_and_shared_navigation(self):
        page = render_subscriptions(
            self.alice, ["11"], ("11",), {"11": []}, {"11": set()},
            NotifySettings(
                calendar_notification_types=("KLAUSUR",),
                calendar_notification_days_before_by_type={"KLAUSUR": 5},
            ),
            [CalendarEventTypeOption("KLAUSUR", "Klausur"), CalendarEventTypeOption("FERIEN", "Ferien")],
            "csrf", "https://ntfy.invalid",
        )
        self.assertNotIn('name="calendar_notification_days_before"', page)
        self.assertIn('name="calendar_notification_days_before__KLAUSUR" value="5"', page)
        self.assertIn('<a class="active" href="/abos">Ankündigungen</a>', page)
        ferien_input = page.split('value="FERIEN"', 1)[1].split('</label>', 1)[0]
        self.assertNotIn(" checked", ferien_input)

    def test_shared_calendar_identity_without_pin_does_not_require_pin(self):
        store = object.__new__(AccountStore)
        store._backend = "mysql"
        store._connection = lambda: nullcontext(object())
        store._fetchone = Mock(return_value={
            "username": "shareduser", "pin": None, "status": "ACTIVE",
            "active": None, "pin_hash": None,
        })
        self.assertEqual(store.get_login_identity("shareduser"), ("shareduser", False))

    def test_shared_calendar_identity_with_pin_requires_pin(self):
        store = object.__new__(AccountStore)
        store._backend = "mysql"
        store._connection = lambda: nullcontext(object())
        store._fetchone = Mock(return_value={
            "username": "shareduser", "pin": "scrypt$salt$hash", "status": "ACTIVE",
            "active": 1, "pin_hash": "argon-hash",
        })
        self.assertEqual(store.get_login_identity("shareduser"), ("shareduser", True))

    def test_shared_calendar_pin_rejects_matching_stale_vp_pin(self):
        store = object.__new__(AccountStore)
        store._backend = "mysql"
        store._connection = lambda: nullcontext(object())
        store._fetchall = Mock(return_value=[])
        store._fetchone = Mock(return_value={
            "id": 7, "username": "shareduser", "class_name": "11", "active": 1,
            "ntfy_topic": "vp-shareduser", "ntfy_username": "vp_shareduser",
            "ntfy_password_encrypted": b"unused", "pin_hash": "old-vp-hash",
            "calendar_username": "shareduser", "calendar_pin": "1234",
            "calendar_status": "ACTIVE",
        })
        store._run = Mock()
        store._hasher = Mock()
        store._hasher.verify.return_value = True

        self.assertIsNone(store.authenticate("shareduser", "9999", "127.0.0.1"))
        store._hasher.verify.assert_not_called()

    def test_cookie_domain_is_derived_for_calendar_and_vp_subdomain(self):
        with patch.dict("os.environ", {
            "COOKIE_DOMAIN": "",
            "CALENDAR_PUBLIC_URL": "https://cal11.de",
            "VERTRETUNGSPLAN_PUBLIC_URL": "https://vp.cal11.de",
        }, clear=False):
            self.assertEqual(resolve_cookie_domain(), "cal11.de")

    def test_duplicate_session_cookies_remain_readable_during_migration(self):
        self.assertEqual(
            cookie_values("cal11_session=old; theme=dark; cal11_session=shared", "cal11_session"),
            ["old", "shared"],
        )

    def test_vp_session_watch_does_not_expose_session_token(self):
        self.assertIn("/api/session-status", SESSION_WATCH_SCRIPT)
        self.assertIn("response.status === 401", SESSION_WATCH_SCRIPT)
        self.assertIn("onLoginPage && response.ok", SESSION_WATCH_SCRIPT)
        self.assertIn("payload?.username", SESSION_WATCH_SCRIPT)
        self.assertNotIn("cal11_session", SESSION_WATCH_SCRIPT)

    def test_trusted_calendar_session_bootstraps_missing_vp_profile(self):
        store = object.__new__(AccountStore)
        store._backend = "mysql"
        store._connection = lambda: nullcontext(object())
        store._fetchone = Mock(side_effect=[
            {"username": "alice", "csrf_token": "csrf", "status": "ACTIVE"},
            None,
        ])
        vp_row = {"id": 9, "username": "alice", "active": 1}
        store._bootstrap_vp_user_from_calendar = Mock(return_value=vp_row)
        store._run = Mock()
        store._user_from_row = Mock(return_value="alice")

        session = store.get_session("shared-token")

        self.assertEqual(session.user, "alice")
        self.assertEqual(session.csrf_token, "csrf")
        store._bootstrap_vp_user_from_calendar.assert_called_once_with(
            ANY, "alice", None, trusted_session=True,
        )

    def test_logout_can_invalidate_all_sessions_for_account(self):
        first, _ = self.store.create_session(self.alice.id)
        second, _ = self.store.create_session(self.alice.id)
        self.store.delete_user_sessions("alice")
        self.assertIsNone(self.store.get_session(first))
        self.assertIsNone(self.store.get_session(second))

    def test_calendar_categories_keep_individual_notification_times(self):
        settings = NotifySettings(
            calendar_notifications_enabled=True,
            calendar_notification_types=("KLAUSUR", "HAUSAUFGABE"),
            calendar_notification_times={"KLAUSUR": "07:30", "HAUSAUFGABE": "18:15"},
        )
        self.store.save_notify_settings(self.alice.id, settings)
        loaded, exists = self.store.load_notify_settings(self.alice.id)
        self.assertTrue(exists)
        self.assertEqual(loaded.calendar_notification_times["KLAUSUR"], "07:30")
        self.assertEqual(loaded.calendar_notification_times["HAUSAUFGABE"], "18:15")

        event = SimpleNamespace(date="2026-08-27", event_type="HAUSAUFGABE")
        self.assertEqual(
            SubscriptionNotifier._calendar_notification_at(event, loaded),
            datetime(2026, 8, 26, 18, 15),
        )

    def test_calendar_categories_keep_individual_days_before(self):
        settings = NotifySettings(
            calendar_notifications_enabled=True,
            calendar_notification_types=("KLAUSUR", "HAUSAUFGABE"),
            calendar_notification_times={"KLAUSUR": "07:30", "HAUSAUFGABE": "18:15"},
            calendar_notification_days_before_by_type={"KLAUSUR": 7, "HAUSAUFGABE": 2},
        )
        self.store.save_notify_settings(self.alice.id, settings)
        loaded, _ = self.store.load_notify_settings(self.alice.id)
        event = SimpleNamespace(date="2026-08-27", event_type="KLAUSUR")
        self.assertEqual(
            SubscriptionNotifier._calendar_notification_at(event, loaded),
            datetime(2026, 8, 20, 7, 30),
        )

    def test_automatic_publish_uses_personal_topic_credentials(self):
        notifier = SubscriptionNotifier(self.store, "https://ntfy.invalid")
        with patch("subscriptions.requests.post") as post:
            post.return_value.raise_for_status.return_value = None
            notifier._publish(self.alice, "Nachricht", "Titel")
        self.assertEqual(post.call_args.kwargs["auth"], (self.alice.ntfy_username, self.alice.ntfy_password))

    def test_failed_ntfy_recipient_does_not_abort_later_deliveries(self):
        notifier = SubscriptionNotifier(self.store, "https://ntfy.invalid")
        notifier._publish = Mock(side_effect=[requests.ConnectionError("offline"), None])
        self.assertFalse(notifier._deliver(self.alice, "first", "Nachricht", "Titel"))
        self.assertTrue(notifier._deliver(self.bob, "second", "Nachricht", "Titel"))
        self.assertEqual(len(notifier.delivery_errors), 1)
        self.assertTrue(self.store.mark_delivery_once(self.alice.id, "first"))

    def test_emulated_scheduler_can_be_restricted_to_one_user(self):
        selected = {subject_key("Mathe")}
        self.store.replace_subjects(self.alice.id, {"11": selected})
        self.store.replace_subjects(self.bob.id, {"11": selected})
        plan = SimpleNamespace(
            datum=date(2026, 8, 20), zeitstempel=None,
            zeitplan={1: (time(7, 45), time(9, 15))},
            klassen={"11": SimpleNamespace(kurse={}, stunden={
                1: [lesson("Mathe", "101", 1)], 2: [lesson("Mathe", "101", 2)],
            })},
        )
        notifier = SubscriptionNotifier(self.store, "https://ntfy.invalid")
        delivered_to = []
        notifier._publish = lambda user, *_args, **_kwargs: delivered_to.append(user.username)

        self.assertEqual(
            notifier.poll_once(plan, datetime(2026, 8, 20, 7, 0), recipient_username="alice"),
            1,
        )
        self.assertEqual(delivered_to, ["alice"])

    def test_local_ntfy_compose_hostname_resolves_to_loopback_port(self):
        with patch.dict("os.environ", {"NTFY_INTERNAL_URL": "http://ntfy", "NTFY_PORT": "8099"}, clear=False), patch(
            "ntfy.service.Path.exists", return_value=False,
        ):
            self.assertEqual(resolve_ntfy_internal_url(), "http://127.0.0.1:8099")

    def test_ntfy_reader_uses_signed_internal_provisioner(self):
        response = Mock(ok=True, status_code=200)
        with patch.dict("os.environ", {
            "NTFY_PROVISIONER_URL": "http://ntfy-provisioner:8080",
            "NTFY_PROVISIONER_SECRET": "test-secret",
        }, clear=False), patch("ntfy.service.requests.post", return_value=response) as post:
            NtfyService(Path(self.temp.name)).ensure_reader_credentials(
                "vp-alice", "vp_alice", "a-secure-password-123",
            )

        args, kwargs = post.call_args
        self.assertEqual(args[0], "http://ntfy-provisioner:8080/ensure")
        self.assertIn("X-Provisioner-Signature", kwargs["headers"])
        self.assertNotIn(b"test-secret", kwargs["data"])

    def test_ntfy_create_reader_uses_provisioner_without_docker(self):
        response = Mock(ok=True, status_code=200)
        with patch.dict("os.environ", {
            "NTFY_PROVISIONER_URL": "http://ntfy-provisioner:8080",
            "NTFY_PROVISIONER_SECRET": "test-secret",
        }, clear=False), patch("ntfy.service.requests.post", return_value=response) as post, patch("ntfy.service.subprocess.run") as run:
            topic, username, password = NtfyService(Path(self.temp.name)).create_reader()

        self.assertTrue(topic.startswith("vpmobil-"))
        self.assertTrue(username.startswith("u_"))
        self.assertGreaterEqual(len(password), 16)
        run.assert_not_called()
        self.assertEqual(post.call_args.args[0], "http://ntfy-provisioner:8080/ensure")

    def test_subject_options_include_courses_without_today_lesson(self):
        plan = SimpleNamespace(klassen={"11": SimpleNamespace(
            kurse={1: SimpleNamespace(fach="Mathe"), 2: SimpleNamespace(fach="Chemie")},
            stunden={1: [lesson("Deutsch", "101", 1)]},
        )})
        self.assertEqual(
            [option.key for option in subject_options(plan, "11")],
            ["subject:Chemie", "subject:Deutsch", "subject:Mathe"],
        )

    def test_lesson_subject_keeps_course_code_when_present(self):
        from subscriptions import lesson_subject
        class_item = SimpleNamespace(kurse={12: SimpleNamespace(fach="SPO")})
        self.assertEqual(lesson_subject(class_item, lesson("spo1", "101", 1, course_number=12)), "spo1")

    def test_subject_keys_keep_course_code_case_and_distinguish_groups(self):
        plan = SimpleNamespace(klassen={"11": SimpleNamespace(
            kurse={}, stunden={1: [
                lesson("MA1", "101", 1), lesson("MA2", "102", 1),
                lesson("ma1", "103", 1), lesson("spo1", "104", 1),
                lesson("spo2", "105", 1), lesson("spo3", "106", 1),
            ]}
        )})
        self.assertEqual(
            [option.key for option in subject_options(plan, "11")],
            ["subject:MA1", "subject:MA2", "subject:ma1", "subject:spo1", "subject:spo2", "subject:spo3"],
        )

    def test_subject_labels_include_teacher(self):
        plan = SimpleNamespace(klassen={"11": SimpleNamespace(
            kurse={12: SimpleNamespace(kürzel="MA1", fach="MA", lehrer="Kön")},
            stunden={1: [lesson("MA1", "101", 1, course_number=12)]},
        )})
        self.assertEqual(subject_options(plan, "11")[0].label, "MA1 (Kön)")

    def test_subject_options_sort_uppercase_keys_before_lowercase_with_mixed_labels(self):
        plan = SimpleNamespace(klassen={"11": SimpleNamespace(
            kurse={
                1: SimpleNamespace(kürzel="DE1", fach="Deutsch", lehrer="Müller"),
                2: SimpleNamespace(kürzel="de1", fach="Deutsch", lehrer="schmidt"),
                3: SimpleNamespace(kürzel="DE2", fach="Deutsch", lehrer="Klein"),
                4: SimpleNamespace(kürzel="de2", fach="Deutsch", lehrer="andres"),
            },
            stunden={},
        )})
        self.assertEqual(
            [option.key for option in subject_options(plan, "11")],
            ["subject:DE1", "subject:DE2", "subject:de1", "subject:de2"],
        )

    def test_notifications_are_sent_only_for_subscribed_subjects(self):
        self.store.replace_subjects(self.alice.id, {subject_key("Mathe")})
        self.store.replace_subjects(self.bob.id, {subject_key("Chemie")})
        plan = SimpleNamespace(
            datum=date(2026, 8, 20), zeitstempel=datetime(2026, 8, 20, 6, 30),
            zeitplan={1: (time(7, 45), time(9, 15))},
            klassen={"11": SimpleNamespace(kurse={}, stunden={
                1: [lesson("Chemie", "201", 1, changed=True)],
                2: [lesson("Chemie", "201", 2, changed=True)],
                3: [lesson("Mathe", "101", 3, changed=True)],
                4: [lesson("Mathe", "101", 4, changed=True)],
            })},
        )
        notifier = SubscriptionNotifier(self.store, "https://ntfy.invalid")
        published = []
        notifier._publish = lambda user, message, title, priority="default": published.append((user.username, message))
        notifier.poll_once(plan, datetime(2026, 8, 20, 7, 1))
        alice_messages = "\n".join(message for name, message in published if name == "alice")
        bob_messages = "\n".join(message for name, message in published if name == "bob")
        self.assertIn("Mathe", alice_messages)
        self.assertNotIn("Chemie", alice_messages)
        self.assertIn("Chemie", bob_messages)
        self.assertNotIn("Mathe", bob_messages)
        self.assertEqual(notifier.poll_once(plan, datetime(2026, 8, 20, 7, 2)), 0)

    def test_multiple_classes_can_be_stored_with_class_specific_subjects(self):
        self.store.replace_selected_classes(self.alice.id, {"11", "12"})
        self.store.replace_subjects(
            self.alice.id,
            {"11": {subject_key("Mathe")}, "12": {subject_key("Chemie")}},
        )
        selected_classes, has_saved_classes = self.store.get_selected_classes(self.alice.id, self.alice.class_name)
        selected_subjects, has_saved_subjects = self.store.get_subject_selections(self.alice.id, self.alice.class_name)

        self.assertTrue(has_saved_classes)
        self.assertEqual(selected_classes, ("11", "12"))
        self.assertTrue(has_saved_subjects)
        self.assertEqual(
            selected_subjects,
            {"11": {subject_key("Mathe")}, "12": {subject_key("Chemie")}},
        )

    def test_multi_class_notifications_include_class_name(self):
        self.store.replace_selected_classes(self.alice.id, {"11", "12"})
        self.store.replace_subjects(
            self.alice.id,
            {"11": {subject_key("Mathe")}, "12": {subject_key("Chemie")}},
        )
        plan = SimpleNamespace(
            datum=date(2026, 8, 20), zeitstempel=None,
            zeitplan={1: (time(7, 45), time(9, 15))},
            klassen={
                "11": SimpleNamespace(kurse={}, stunden={1: [lesson("Mathe", "101", 1)], 2: [lesson("Mathe", "101", 2)]}),
                "12": SimpleNamespace(kurse={}, stunden={1: [lesson("Chemie", "201", 1)], 2: [lesson("Chemie", "201", 2)]}),
            },
        )
        notifier = SubscriptionNotifier(self.store, "https://ntfy.invalid")
        published = []
        notifier._publish = lambda user, message, title, priority="default": published.append((title, message))

        self.assertEqual(notifier.poll_once(plan, datetime(2026, 8, 20, 7, 0)), 1)
        self.assertIn("1. Block: 11: Mathe in 101 | 12: Chemie in 201", published[0][1])

    def test_calendar_notifications_respect_type_and_schedule(self):
        with self.store._connection() as connection:
            connection.execute(
                """
                INSERT INTO calendar_users(username, courses, pin, preferences, status)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET courses = excluded.courses
                """,
                ("alice", '["MA1"]', None, "{}", "ACTIVE"),
            )
            connection.execute(
                """
                INSERT INTO calendar_event_categories(id, name, color, sort_order)
                VALUES (?, ?, ?, ?)
                """,
                ("HAUSAUFGABE", "Hausaufgabe", "#59b3cb", 0),
            )
            connection.execute(
                """
                INSERT INTO calendar_events(id, title, date, end_date, start_time, end_time, course_id, type, description, author)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("evt-1", "Blatt 5", "2026-08-21", "2026-08-21", None, None, "MA1", "HAUSAUFGABE", "Bis Freitag erledigen.", "lehrer"),
            )
        self.store.save_notify_settings(
            self.alice.id,
            NotifySettings(
                calendar_notifications_enabled=True,
                calendar_notification_time="16:00",
                calendar_notification_days_before=1,
                calendar_notification_types=("HAUSAUFGABE",),
            ),
        )
        notifier = SubscriptionNotifier(self.store, "https://ntfy.invalid")
        published = []
        notifier._publish = lambda user, message, title, priority="default": published.append((title, message))
        empty_plan = SimpleNamespace(datum=date(2026, 8, 20), zeitstempel=None, zeitplan={}, klassen={})

        self.assertEqual(notifier.poll_once(empty_plan, datetime(2026, 8, 20, 15, 59)), 0)
        self.assertEqual(notifier.poll_once(empty_plan, datetime(2026, 8, 20, 16, 0)), 1)
        self.assertEqual(notifier.poll_once(empty_plan, datetime(2026, 8, 20, 16, 1)), 0)
        self.assertEqual(published[0][0], "(VPrintfy) Kalender: Blatt 5")
        self.assertIn("Typ: HAUSAUFGABE", published[0][1])
        self.assertIn("Kurs: MA1", published[0][1])

    def test_calendar_notifications_ignore_courses_the_user_does_not_have(self):
        with self.store._connection() as connection:
            connection.execute(
                """
                INSERT INTO calendar_users(username, courses, pin, preferences, status)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET courses = excluded.courses
                """,
                ("alice", '["MA1"]', None, "{}", "ACTIVE"),
            )
            connection.execute(
                "INSERT INTO calendar_event_categories(id, name, color, sort_order) VALUES (?, ?, ?, ?)",
                ("HAUSAUFGABE", "Hausaufgabe", "#59b3cb", 0),
            )
            connection.execute(
                """
                INSERT INTO calendar_events(id, title, date, end_date, start_time, end_time, course_id, type, description, author)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("evt-foreign", "Chemie-Blatt", "2026-08-21", "2026-08-21", None, None, "CH2", "HAUSAUFGABE", "", "lehrer"),
            )
        self.store.save_notify_settings(
            self.alice.id,
            NotifySettings(
                calendar_notifications_enabled=True,
                calendar_notification_time="16:00",
                calendar_notification_days_before=1,
                calendar_notification_types=("HAUSAUFGABE",),
            ),
        )
        notifier = SubscriptionNotifier(self.store, "https://ntfy.invalid")
        notifier._publish = lambda *args, **kwargs: self.fail("Ein fremder Kurs darf keine Benachrichtigung auslösen.")
        empty_plan = SimpleNamespace(datum=date(2026, 8, 20), zeitstempel=None, zeitplan={}, klassen={})

        self.assertEqual(notifier.poll_once(empty_plan, datetime(2026, 8, 20, 16, 0)), 0)

    def test_calendar_notifications_send_general_events_without_calendar_courses(self):
        with self.store._connection() as connection:
            connection.execute(
                "INSERT INTO calendar_event_categories(id, name, color, sort_order) VALUES (?, ?, ?, ?)",
                ("SONSTIGES", "Sonstiges", "#3d60c7", 0),
            )
            connection.execute(
                """
                INSERT INTO calendar_events(id, title, date, end_date, start_time, end_time, course_id, type, description, author)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("evt-general", "Info", "2026-08-21", "2026-08-21", None, None, "ALLGEMEIN", "SONSTIGES", "", "lehrer"),
            )
        self.store.save_notify_settings(
            self.alice.id,
            NotifySettings(
                calendar_notifications_enabled=True,
                calendar_notification_time="16:00",
                calendar_notification_days_before=1,
                calendar_notification_types=("SONSTIGES",),
            ),
        )
        notifier = SubscriptionNotifier(self.store, "https://ntfy.invalid")
        published = []
        notifier._publish = lambda user, message, title, priority="default": published.append((user.username, title, message))
        empty_plan = SimpleNamespace(datum=date(2026, 8, 20), zeitstempel=None, zeitplan={}, klassen={})

        self.assertEqual(notifier.poll_once(empty_plan, datetime(2026, 8, 20, 16, 0)), 1)
        self.assertEqual(published[0][0], "alice")
        self.assertIn("Info", published[0][1])

    def test_vp_only_users_never_receive_calendar_notifications(self):
        vp_user = self.store.create_vp_only_user(
            "vpguest", "2468", "11", created_by="alice",
            ntfy_topic="vpmobil-vpguest", ntfy_username="ntfy_vpguest", ntfy_password="secret",
        )
        with self.store._connection() as connection:
            connection.execute(
                "INSERT INTO calendar_event_categories(id, name, color, sort_order) VALUES (?, ?, ?, ?)",
                ("SONSTIGES", "Sonstiges", "#3d60c7", 0),
            )
            connection.execute(
                """
                INSERT INTO calendar_events(id, title, date, end_date, start_time, end_time, course_id, type, description, author)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("evt-general", "Info", "2026-08-21", "2026-08-21", None, None, "ALLGEMEIN", "SONSTIGES", "", "lehrer"),
            )
        self.store.save_notify_settings(
            vp_user.id,
            NotifySettings(
                calendar_notifications_enabled=True,
                calendar_notification_time="16:00",
                calendar_notification_days_before=1,
                calendar_notification_types=("SONSTIGES",),
            ),
        )
        notifier = SubscriptionNotifier(self.store, "https://ntfy.invalid")
        notifier._publish = lambda *args, **kwargs: self.fail("VP-only darf keine Kalender-Benachrichtigung erhalten.")
        empty_plan = SimpleNamespace(datum=date(2026, 8, 20), zeitstempel=None, zeitplan={}, klassen={})

        self.assertEqual(notifier.poll_once(empty_plan, datetime(2026, 8, 20, 16, 0)), 0)

    def test_notifications_are_suppressed_on_weekends(self):
        self.store.replace_subjects(self.alice.id, {subject_key("Mathe")})
        plan = SimpleNamespace(
            datum=date(2026, 8, 22), zeitstempel=datetime(2026, 8, 22, 6, 30),
            zeitplan={1: (time(7, 45), time(9, 15))},
            klassen={"11": SimpleNamespace(kurse={}, stunden={
                1: [lesson("Mathe", "101", 1, changed=True)],
                2: [lesson("Mathe", "101", 2, changed=True)],
            })},
        )
        notifier = SubscriptionNotifier(self.store, "https://ntfy.invalid")
        notifier._publish = lambda *args, **kwargs: self.fail("Am Wochenende darf nichts gesendet werden.")
        self.assertEqual(notifier.poll_once(plan, datetime(2026, 8, 22, 10, 0)), 0)

    def test_future_subject_window_contains_two_complete_workweeks(self):
        future_dates = get_future_week_dates(date(2026, 8, 21))
        self.assertEqual(len(future_dates), 10)
        self.assertEqual(future_dates[0], date(2026, 8, 24))
        self.assertEqual(future_dates[-1], date(2026, 9, 4))
        self.assertTrue(all(plan_date.weekday() < 5 for plan_date in future_dates))

    def test_future_week_plans_keep_reachable_days_when_one_day_fails(self):
        available_plan = SimpleNamespace(klassen={})
        with patch("vp_data.get_plan_for_page", side_effect=[available_plan, OSError("network")]):
            plans = get_future_week_plans()
        self.assertIs(plans[get_future_week_dates()[0]], available_plan)
        self.assertIsNone(plans[get_future_week_dates()[1]])

    def test_subject_catalog_combines_reference_plan_and_future_plans(self):
        reference_plan = SimpleNamespace(name="reference")
        future_plan = SimpleNamespace(name="future")
        with patch("vp_data.VertretungsplanZugang") as access_class, patch(
            "vp_data.get_official_weekly_plans_for_page",
            side_effect=[{date(2026, 8, 24): future_plan}, {}],
        ):
            access_class.return_value.get.return_value = reference_plan
            self.assertEqual(get_subject_catalog_plans(), [reference_plan, future_plan])

    def test_next_block_notification_is_sent_for_each_block_transition(self):
        self.store.replace_subjects(
            self.alice.id,
            {subject_key(subject) for subject in ("Mathe", "Deutsch", "Englisch", "Physik")},
        )
        plan = SimpleNamespace(
            datum=date(2026, 8, 20), zeitstempel=None,
            zeitplan={
                1: (time(7, 45), time(9, 15)),
                3: (time(9, 35), time(11, 5)),
                5: (time(11, 50), time(13, 20)),
            },
            klassen={"11": SimpleNamespace(kurse={}, stunden={
                1: [lesson("Mathe", "101", 1)], 2: [lesson("Mathe", "101", 2)],
                3: [lesson("Deutsch", "102", 3)], 4: [lesson("Deutsch", "102", 4)],
                5: [lesson("Englisch", "103", 5)], 6: [lesson("Englisch", "103", 6)],
                7: [lesson("Physik", "104", 7)], 8: [lesson("Physik", "104", 8)],
            })},
        )
        notifier = SubscriptionNotifier(self.store, "https://ntfy.invalid")
        published = []
        notifier._publish = lambda user, message, title, priority="default": published.append((title, message))
        self.assertTrue(self.store.mark_delivery_once(self.alice.id, "morning:2026-08-20"))

        self.assertEqual(notifier.poll_once(plan, datetime(2026, 8, 20, 9, 9)), 0)
        self.assertEqual(notifier.poll_once(plan, datetime(2026, 8, 20, 9, 10)), 1)
        self.assertEqual(notifier.poll_once(plan, datetime(2026, 8, 20, 10, 59)), 0)
        self.assertEqual(notifier.poll_once(plan, datetime(2026, 8, 20, 11, 0)), 1)
        self.assertEqual(notifier.poll_once(plan, datetime(2026, 8, 20, 13, 14)), 0)
        self.assertEqual(notifier.poll_once(plan, datetime(2026, 8, 20, 13, 15)), 1)

        next_messages = [message for title, message in published if title.startswith("(VPrintfy) Nächster Raum")]
        self.assertEqual(len(next_messages), 3)
        self.assertIn("Nächster (2.) Block: Deutsch in 102", next_messages[0])
        self.assertIn("Nächster (3.) Block: Englisch in 103", next_messages[1])
        self.assertIn("Nächster (4.) Block: Physik in 104", next_messages[2])

    def test_morning_notification_starts_at_seven(self):
        subjects = ("Mathe", "Deutsch", "Englisch", "Physik")
        self.store.replace_subjects(self.alice.id, {subject_key(subject) for subject in subjects})
        plan = SimpleNamespace(
            datum=date(2026, 8, 20), zeitstempel=None,
            zeitplan={
                1: (time(7, 45), time(9, 15)),
                3: (time(9, 35), time(11, 5)),
                5: (time(11, 50), time(13, 20)),
            },
            klassen={"11": SimpleNamespace(kurse={}, stunden={
                1: [lesson("Mathe", "101", 1)], 2: [lesson("Mathe", "101", 2)],
                3: [lesson("Deutsch", "102", 3)], 4: [lesson("Deutsch", "102", 4)],
                5: [lesson("Englisch", "103", 5)], 6: [lesson("Englisch", "103", 6)],
                7: [lesson("Physik", "104", 7)], 8: [lesson("Physik", "104", 8)],
            })},
        )
        notifier = SubscriptionNotifier(self.store, "https://ntfy.invalid")
        published = []
        notifier._publish = lambda user, message, title, priority="default": published.append((title, message))

        self.assertEqual(notifier.poll_once(plan, datetime(2026, 8, 20, 6, 59)), 0)
        self.assertEqual(notifier.poll_once(plan, datetime(2026, 8, 20, 7, 0)), 1)
        self.assertEqual(notifier.poll_once(plan, datetime(2026, 8, 20, 7, 1)), 0)
        self.assertEqual(published[0][0], "(VPrintfy) Heute")
        for block, subject, room in ((1, "Mathe", "101"), (2, "Deutsch", "102"), (3, "Englisch", "103"), (4, "Physik", "104")):
            self.assertIn(f"{block}. Block: {subject} in {room}", published[0][1])

    def test_plan_change_notification_is_sent_once_for_changed_lessons(self):
        self.store.replace_subjects(self.alice.id, {subject_key("Mathe")})
        initial_plan = SimpleNamespace(
            datum=date(2026, 8, 20), zeitstempel=datetime(2026, 8, 20, 6, 30),
            zeitplan={1: (time(7, 45), time(9, 15))},
            klassen={"11": SimpleNamespace(kurse={}, stunden={
                1: [lesson("Mathe", "101", 1)],
                2: [lesson("Mathe", "101", 2)],
            })},
        )
        changed_plan = SimpleNamespace(
            datum=initial_plan.datum, zeitstempel=initial_plan.zeitstempel,
            zeitplan=initial_plan.zeitplan,
            klassen={"11": SimpleNamespace(kurse={}, stunden={
                1: [lesson("Mathe", "102", 1, changed=True)],
                2: [lesson("Mathe", "102", 2, changed=True)],
            })},
        )
        notifier = SubscriptionNotifier(self.store, "https://ntfy.invalid")
        published = []
        notifier._publish = lambda user, message, title, priority="default": published.append((title, message))

        self.assertEqual(notifier.poll_once(initial_plan, datetime(2026, 8, 20, 6, 59)), 0)
        self.assertEqual(notifier.poll_once(changed_plan, datetime(2026, 8, 20, 6, 59)), 1)
        self.assertEqual(notifier.poll_once(changed_plan, datetime(2026, 8, 20, 7, 1)), 1)
        self.assertEqual(notifier.poll_once(changed_plan, datetime(2026, 8, 20, 7, 2)), 0)
        change_messages = [message for title, message in published if title == "(VPrintfy) Plan-Änderung"]
        self.assertEqual(len(change_messages), 1)
        self.assertIn("1. Block: Mathe in 102 [Änderung]", change_messages[0])

    def test_new_plan_day_is_saved_without_change_notification(self):
        self.store.replace_subjects(self.alice.id, {subject_key("Mathe")})
        plan = SimpleNamespace(
            datum=date(2026, 8, 21), zeitstempel=datetime(2026, 8, 20, 6, 30),
            zeitplan={1: (time(7, 45), time(9, 15))},
            klassen={"11": SimpleNamespace(kurse={}, stunden={
                1: [lesson("Mathe", "101", 1, changed=True)],
                2: [lesson("Mathe", "101", 2, changed=True)],
            })},
        )
        notifier = SubscriptionNotifier(self.store, "https://ntfy.invalid")
        published = []
        notifier._publish = lambda user, message, title, priority="default": published.append((title, message))

        self.assertEqual(notifier.poll_once(plan, datetime(2026, 8, 21, 6, 59)), 0)
        self.assertEqual(notifier.poll_once(plan, datetime(2026, 8, 21, 7, 1)), 1)
        self.assertFalse(any(title == "(VPrintfy) Plan-Änderung" for title, _ in published))

    def test_send_test_supports_all_notification_types_and_filters_subjects(self):
        self.store.replace_subjects(self.alice.id, {subject_key("Mathe")})
        plan = SimpleNamespace(
            datum=date(2026, 8, 20), zeitstempel=datetime(2026, 8, 20, 6, 30),
            zeitplan={
                1: (time(7, 45), time(9, 15)),
                3: (time(9, 35), time(11, 5)),
            },
            klassen={"11": SimpleNamespace(kurse={}, stunden={
                1: [lesson("Chemie", "201", 1, changed=True)],
                2: [lesson("Chemie", "201", 2, changed=True)],
                3: [lesson("Mathe", "101", 3, changed=True)],
                4: [lesson("Mathe", "101", 4, changed=True)],
            })},
        )
        notifier = SubscriptionNotifier(self.store, "https://ntfy.invalid")
        published = []
        notifier._publish = lambda user, message, title, priority="default": published.append((title, message))

        for kind, block in (("morning", None), ("change", None), ("next", 1)):
            notifier.send_test(self.alice, {subject_key("Mathe")}, plan, kind, block)

        self.assertEqual([title for title, _ in published], [
            "(VPrintfy) Heute", "(VPrintfy) Plan-Änderung", "(VPrintfy) Nächster Raum: 101",
        ])
        for _, message in published:
            self.assertIn("Mathe", message)
            self.assertNotIn("Chemie", message)

    def test_user_can_send_a_personal_test_notification(self):
        notifier = SubscriptionNotifier(self.store, "https://ntfy.invalid")
        with patch("subscriptions.requests.post") as post:
            post.return_value.raise_for_status.return_value = None
            notifier.send_user_test(self.alice)

        _args, kwargs = post.call_args
        self.assertEqual(kwargs["auth"], (self.alice.ntfy_username, self.alice.ntfy_password))
        self.assertIn(b"richtig verbunden", kwargs["data"])
        self.assertEqual(kwargs["headers"]["Priority"], "high")

    def test_subscription_preferences_are_atomic(self):
        self.store.replace_selected_classes(self.alice.id, {"11"})
        self.store.replace_subjects(self.alice.id, {"11": {subject_key("Mathe")}})
        with patch.object(self.store, "_save_notify_settings_with_connection", side_effect=RuntimeError("write failed")):
            with self.assertRaises(RuntimeError):
                self.store.save_subscription_preferences(
                    self.alice.id,
                    {"12"},
                    {"12": {subject_key("Chemie")}},
                    NotifySettings(calendar_notification_time="15:30"),
                )

        self.assertEqual(self.store.get_selected_classes(self.alice.id)[0], ("11",))
        self.assertEqual(self.store.get_subject_selections(self.alice.id)[0], {"11": {subject_key("Mathe")}})

    def test_existing_database_gets_new_subscription_tables_without_data_loss(self):
        database = Path(self.temp.name) / "accounts.sqlite"
        with self.store._connection() as connection:
            connection.execute("DROP TABLE user_notification_settings")
            connection.execute("DROP TABLE user_subject_selections")
            connection.execute("DROP TABLE user_selected_classes")

        reopened = AccountStore(database, self.encryption_key)

        self.assertEqual(reopened.get_user("alice").id, self.alice.id)
        reopened.save_subscription_preferences(
            self.alice.id,
            {"11"},
            {"11": {subject_key("Mathe")}},
            NotifySettings(calendar_notification_time="15:30"),
        )
        self.assertEqual(reopened.load_notify_settings(self.alice.id)[0].calendar_notification_time, "15:30")

    def test_legacy_sqlite_tables_get_missing_columns_migrated(self):
        import sqlite3

        database = Path(self.temp.name) / "legacy.sqlite"
        encrypted_password = Fernet(self.encryption_key.encode("ascii")).encrypt(b"secret")
        with sqlite3.connect(database) as connection:
            connection.execute(
                """CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    class_name TEXT NOT NULL,
                    ntfy_topic TEXT NOT NULL UNIQUE,
                    ntfy_username TEXT NOT NULL UNIQUE,
                    ntfy_password_encrypted BLOB NOT NULL,
                    created_at TEXT NOT NULL
                )""",
            )
            connection.execute(
                """INSERT INTO users(id, username, class_name, ntfy_topic, ntfy_username, ntfy_password_encrypted, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (1, "legacy", "11", "vpmobil-legacy", "ntfy_legacy", encrypted_password, "2026-08-27T00:00:00+00:00"),
            )
            connection.execute(
                """CREATE TABLE calendar_users (
                    username TEXT PRIMARY KEY COLLATE NOCASE,
                    courses TEXT NOT NULL
                )""",
            )
            connection.execute("INSERT INTO calendar_users(username, courses) VALUES (?, ?)", ("legacy", '["MA1"]'))
            connection.execute(
                """CREATE TABLE calendar_events (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    date TEXT NOT NULL,
                    course_id TEXT NOT NULL,
                    type TEXT NOT NULL
                )""",
            )
            connection.execute(
                """INSERT INTO calendar_events(id, title, date, course_id, type)
                VALUES (?, ?, ?, ?, ?)""",
                ("legacy-event", "Alttermin", "2026-08-28", "ALLGEMEIN", "SONSTIGES"),
            )

        reopened = AccountStore(database, self.encryption_key)

        user = reopened.get_user("legacy")
        self.assertEqual(user.username, "legacy")
        self.assertTrue(user.active)
        self.assertEqual(reopened.get_login_identity("legacy"), ("legacy", False))
        self.assertEqual(reopened.get_calendar_events("legacy")[0].title, "Alttermin")
        with reopened._connection() as connection:
            user_columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
            calendar_user_columns = {row["name"] for row in connection.execute("PRAGMA table_info(calendar_users)")}
            calendar_event_columns = {row["name"] for row in connection.execute("PRAGMA table_info(calendar_events)")}
            courses_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'courses'",
            ).fetchone()
        self.assertIn("pin_hash", user_columns)
        self.assertIn("active", user_columns)
        self.assertIn("status", calendar_user_columns)
        self.assertIn("start_time", calendar_event_columns)
        self.assertIsNotNone(courses_table)

    def test_mysql_index_migrations_do_not_depend_on_if_not_exists_syntax(self):
        self.assertNotIn(
            "CREATE INDEX IF NOT EXISTS",
            Path(__file__).resolve().parent.parent.joinpath("server/db.ts").read_text(encoding="utf-8"),
        )
        accounts_source = Path(__file__).resolve().parent.parent.joinpath("accounts.py").read_text(encoding="utf-8")
        self.assertIn("CREATE INDEX idx_vp_login_attempts_lookup", accounts_source)
        self.assertNotIn("CREATE INDEX IF NOT EXISTS idx_vp_login_attempts_lookup", accounts_source)

    def test_legacy_subject_table_is_migrated_and_removed(self):
        database = Path(self.temp.name) / "accounts.sqlite"
        with self.store._connection() as connection:
            connection.execute(
                """CREATE TABLE user_subjects (
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    subject_key TEXT NOT NULL,
                    PRIMARY KEY (user_id, subject_key)
                )""",
            )
            connection.execute(
                "INSERT INTO user_subjects(user_id, subject_key) VALUES (?, ?)",
                (self.alice.id, subject_key("Mathe")),
            )

        reopened = AccountStore(database, self.encryption_key)

        self.assertEqual(
            reopened.get_subject_selections(self.alice.id),
            ({"11": {subject_key("Mathe")}}, True),
        )
        with reopened._connection() as connection:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'user_subjects'",
            ).fetchone()
        self.assertIsNone(table)

    def test_delete_user_removes_credentials_and_cascades_private_data(self):
        self.store.replace_subjects(self.alice.id, {subject_key("Mathe")})
        self.store.replace_selected_classes(self.alice.id, {"11", "12"})
        self.store.save_notify_settings(self.alice.id, NotifySettings(calendar_notification_types=("HAUSAUFGABE",)))
        self.store.create_session(self.alice.id)
        self.assertTrue(self.store.mark_delivery_once(self.alice.id, "morning:2026-08-20"))

        self.store.delete_user("alice")

        self.assertIsNone(self.store.authenticate("alice", "1234", "127.0.0.1"))
        with self.store._connection() as connection:
            for table in ("users", "user_selected_classes", "user_subject_selections", "user_notification_settings", "sessions", "notification_deliveries"):
                count = connection.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (self.alice.id,)).fetchone()[0] if table != "users" else connection.execute("SELECT COUNT(*) FROM users WHERE id = ?", (self.alice.id,)).fetchone()[0]
                self.assertEqual(count, 0, table)


if __name__ == "__main__":
    unittest.main()
