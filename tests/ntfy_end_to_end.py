"""Explicit integration check; run only in an isolated stack with NTFY_E2E=1."""
import json
import os
from datetime import date, datetime, time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import requests

from accounts import AccountStore, NotifySettings
from ntfy.service import NtfyService, resolve_ntfy_internal_url
from ntfy.sync_users import sync_users
from subscriptions import SubscriptionNotifier, subject_key


def plan(day):
    def lesson(period, room):
        return SimpleNamespace(fach="Mathe", räume=(room,), periode=period,
                               änderung=False, ausfall=False, kursnummer=None, info=None)
    return SimpleNamespace(datum=day, zeitstempel=None,
                           zeitplan={1: (time(7, 45), time(9, 15)), 3: (time(9, 35), time(11, 5))},
                           klassen={"11": SimpleNamespace(kurse={}, stunden={
                               1: [lesson(1, "101")], 2: [lesson(2, "101")],
                               3: [lesson(3, "102")], 4: [lesson(4, "102")],
                           })})


def main():
    assert os.getenv("NTFY_E2E") == "1", "Only run in the isolated test stack"
    assert resolve_ntfy_internal_url() == "http://ntfy-delivery"
    with TemporaryDirectory() as directory:
        store = AccountStore(Path(directory) / "test.sqlite", os.environ["APP_ENCRYPTION_KEY"])
        user = store.create_user("check_user", "1234", "11", ntfy_topic="check-topic",
                                 ntfy_username="check_reader", ntfy_password="isolated-test-password")
        store.replace_subjects(user.id, {subject_key("Mathe")})
        with store._connection() as connection:
            for kind in ("EXAM", "TASK"):
                connection.execute("INSERT INTO calendar_event_categories(id,name,color,sort_order) VALUES (?,?,?,?)",
                                   (kind, kind, "#000000", 0))
            for key, day, kind in (("exam", "2026-09-10", "EXAM"), ("task", "2026-09-08", "TASK")):
                connection.execute(
                    "INSERT INTO calendar_events(id,title,date,end_date,course_id,type,description,author) VALUES (?,?,?,?,?,?,?,?)",
                    (key, key, day, day, "ALLGEMEIN", kind, "", "test"))
        settings = dict(lesson_notification_times=("07:00", "09:15"),
                        calendar_notifications_enabled=True, calendar_notification_types=("EXAM", "TASK"),
                        calendar_notification_times={"EXAM": "16:00", "TASK": "18:00"},
                        calendar_notification_days_before_by_type={"EXAM": 3, "TASK": 1})
        store.save_notify_settings(user.id, NotifySettings(**settings))
        assert sync_users(store, NtfyService(Path.cwd())) == 1
        notifier = SubscriptionNotifier(store, resolve_ntfy_internal_url())
        today = plan(date(2026, 9, 7))
        tomorrow = plan(date(2026, 9, 8))
        for hour, minute, expected in [(6, 59, 0), (7, 0, 1), (7, 1, 0),
                                        (9, 14, 0), (9, 15, 1), (15, 59, 0),
                                        (16, 0, 1), (17, 59, 0), (18, 0, 1)]:
            sent = notifier.poll_once(today, datetime(2026, 9, 7, hour, minute))
            assert sent == expected, (hour, minute, sent, notifier.delivery_errors)
            assert not notifier.delivery_errors
        # A fresh notifier still honors persistent deduplication.
        notifier = SubscriptionNotifier(store, resolve_ntfy_internal_url())
        assert notifier.poll_once(today, datetime(2026, 9, 7, 18, 1)) == 0
        settings.update(lesson_notification_times=("19:00",), daily_summary_day_before=True)
        store.save_notify_settings(user.id, NotifySettings(**settings))
        assert notifier.poll_once(today, datetime(2026, 9, 7, 18, 59), day_before_plan=tomorrow) == 0
        assert notifier.poll_once(today, datetime(2026, 9, 7, 19, 0), day_before_plan=tomorrow) == 1
        for _ in range(20):
            notifier.send_user_test(user)
        auth = (user.ntfy_username, user.ntfy_password)
        response = requests.get(f"{notifier.ntfy_url}/{user.ntfy_topic}/json?poll=1&since=all", auth=auth, timeout=5)
        response.raise_for_status()
        messages = [json.loads(line) for line in response.text.splitlines()]
        assert len(messages) == 25, len(messages)
        titles = [m.get("title") for m in messages]
        assert titles.count("(VPrintfy) Kalender: exam") == 1
        assert titles.count("(VPrintfy) Kalender: task") == 1
        assert titles.count("(VPrintfy) Heute") == 1
        assert titles.count("(VPrintfy) Morgen") == 1
        assert titles.count("(VPrintfy) Nächster Raum: 102") == 1
        public = [requests.post("http://ntfy-public/check-topic", data="public-test", auth=auth, timeout=5).status_code
                  for _ in range(5)]
        assert public == [200, 200, 200, 429, 429], public
        notifier.send_user_test(user)
        assert requests.post(f"{notifier.ntfy_url}/forbidden", data="test", auth=auth, timeout=5).status_code == 403
        print("PASS: actual provisioning, calendar 3/1 days before with per-type times, morning/next/day-before, restart deduplication, 20 tests above burst=3, cached messages verified; public 429 and private ACLs preserved.")


if __name__ == "__main__":
    main()
