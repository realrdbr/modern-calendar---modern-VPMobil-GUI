from datetime import date, datetime, time
from pathlib import Path
from contextlib import nullcontext
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from accounts import AccountStore, NotifySettings
from account_page import render_login
from ntfy.service import resolve_ntfy_internal_url
from subscriptions import SubscriptionNotifier, subject_key, subject_options
from vp_data import get_future_week_dates, get_future_week_plans, get_subject_catalog_plans


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
        raw_database = (Path(self.temp.name) / "accounts.sqlite").read_bytes()
        self.assertNotIn(b"1234", raw_database)
        self.assertEqual(self.store.authenticate("alice", "1234", "127.0.0.1").id, self.alice.id)
        self.assertIsNone(self.store.authenticate("alice", "0000", "127.0.0.1"))
        token, csrf = self.store.create_session(self.alice.id)
        session = self.store.get_session(token)
        self.assertEqual(session.user.username, "alice")
        self.assertEqual(session.csrf_token, csrf)

    def test_login_is_two_step_and_pin_is_restricted_to_four_digits(self):
        username_page = render_login()
        self.assertIn('name="stage" value="username"', username_page)
        self.assertNotIn('name="pin"', username_page)

        pin_page = render_login(username="alice", pin_step=True)
        self.assertIn('name="stage" value="pin"', pin_page)
        self.assertIn('inputmode="numeric"', pin_page)
        self.assertIn('pattern="[0-9]{4}"', pin_page)
        self.assertIn('maxlength="4"', pin_page)

    def test_shared_calendar_identity_without_pin_does_not_require_pin(self):
        store = object.__new__(AccountStore)
        store._backend = "mysql"
        store._connection = lambda: nullcontext(object())
        store._fetchone = Mock(return_value={
            "username": "gustavd", "pin": None, "status": "ACTIVE",
            "active": None, "pin_hash": None,
        })
        self.assertEqual(store.get_login_identity("gustavd"), ("gustavd", False))

    def test_shared_calendar_identity_with_pin_requires_pin(self):
        store = object.__new__(AccountStore)
        store._backend = "mysql"
        store._connection = lambda: nullcontext(object())
        store._fetchone = Mock(return_value={
            "username": "gustavd", "pin": "scrypt$salt$hash", "status": "ACTIVE",
            "active": 1, "pin_hash": "argon-hash",
        })
        self.assertEqual(store.get_login_identity("gustavd"), ("gustavd", True))

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

    def test_local_ntfy_compose_hostname_resolves_to_loopback_port(self):
        with patch.dict("os.environ", {"NTFY_INTERNAL_URL": "http://ntfy", "NTFY_PORT": "8099"}, clear=False), patch(
            "ntfy.service.Path.exists", return_value=False,
        ):
            self.assertEqual(resolve_ntfy_internal_url(), "http://127.0.0.1:8099")

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

    def test_delete_user_removes_credentials_and_cascades_private_data(self):
        self.store.replace_subjects(self.alice.id, {subject_key("Mathe")})
        self.store.replace_selected_classes(self.alice.id, {"11", "12"})
        self.store.save_notify_settings(self.alice.id, NotifySettings(calendar_notification_types=("HAUSAUFGABE",)))
        self.store.create_session(self.alice.id)
        self.assertTrue(self.store.mark_delivery_once(self.alice.id, "morning:2026-08-20"))

        self.store.delete_user("alice")

        self.assertIsNone(self.store.authenticate("alice", "1234", "127.0.0.1"))
        with self.store._connection() as connection:
            for table in ("users", "user_subjects", "user_selected_classes", "user_subject_selections", "user_notification_settings", "sessions", "notification_deliveries"):
                count = connection.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (self.alice.id,)).fetchone()[0] if table != "users" else connection.execute("SELECT COUNT(*) FROM users WHERE id = ?", (self.alice.id,)).fetchone()[0]
                self.assertEqual(count, 0, table)


if __name__ == "__main__":
    unittest.main()
