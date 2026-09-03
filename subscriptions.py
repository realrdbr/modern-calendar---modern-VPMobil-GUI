"""Kurs-/Facherkennung und gezielter Versand für angemeldete Nutzer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from email.header import Header
import hashlib
import re
from typing import Iterable
from urllib.parse import quote

import requests

from accounts import AccountStore, CalendarEvent, MAX_CALENDAR_NOTIFICATION_DAYS_BEFORE, NotifySettings, NotificationRecipient, User
from ntfy.notifications import DEFAULT_BLOCKS, Block
from ntfy.service import resolve_ntfy_publisher_auth

CLIENT_NOTIFICATION_RETENTION = timedelta(hours=23)


def subject_key(subject: str | None) -> str | None:
    if not subject:
        return None
    normalized = re.sub(r"\s+", " ", subject.strip())
    return f"subject:{normalized}" if normalized else None


@dataclass(frozen=True)
class SubjectOption:
    key: str
    label: str


def _subject_option_sort_key(key: str, label: str) -> tuple[bool, str, str, str, str]:
    raw_subject = key.split("subject:", 1)[1] if key.startswith("subject:") else label
    return (
        not raw_subject.isupper(),
        raw_subject.casefold(),
        raw_subject,
        label.casefold(),
        label,
    )


def subject_display_label(subject: str | None, teacher: str | None = None) -> str | None:
    if not subject:
        return None
    subject = re.sub(r"\s+", " ", subject.strip())
    teacher = re.sub(r"\s+", " ", (teacher or "").strip())
    return f"{subject} ({teacher})" if teacher else subject


def lesson_subject(class_item: object, lesson: object) -> str | None:
    """Liest bei Ausfällen das Fach über die Kursnummer aus dem Plan nach."""
    subject = getattr(lesson, "fach", None)
    if subject:
        return subject
    course = getattr(class_item, "kurse", {}).get(getattr(lesson, "kursnummer", None))
    course_subject = getattr(course, "fach", None) if course is not None else None
    return course_subject


def lesson_teacher(class_item: object, lesson: object) -> str | None:
    teachers = getattr(lesson, "lehrer", ())
    if teachers:
        return ", ".join(teachers)
    course = getattr(class_item, "kurse", {}).get(getattr(lesson, "kursnummer", None))
    return getattr(course, "lehrer", None) if course is not None else None


def lesson_display_label(class_item: object, lesson: object) -> str:
    return subject_display_label(lesson_subject(class_item, lesson), lesson_teacher(class_item, lesson)) or "Unbekannt"


def subject_options(plan: object, class_name: str) -> list[SubjectOption]:
    class_item = getattr(plan, "klassen", {}).get(class_name)
    if class_item is None:
        return []
    labels: dict[str, str] = {}
    lesson_groups = getattr(class_item, "stunden", {})
    for course in getattr(class_item, "kurse", {}).values():
        subject = getattr(course, "kürzel", None) or getattr(course, "fach", None)
        key = subject_key(subject)
        if key:
            labels.setdefault(key, subject_display_label(subject, getattr(course, "lehrer", None)) or str(subject))
    for lessons in lesson_groups.values():
        for lesson in lessons:
            subject = lesson_subject(class_item, lesson)
            key = subject_key(subject)
            if key:
                labels.setdefault(key, lesson_display_label(class_item, lesson))
    return [SubjectOption(key, labels[key]) for key in sorted(labels, key=lambda item: _subject_option_sort_key(item, labels[item]))]


def subject_options_from_plans(plans: Iterable[object | None], class_name: str) -> list[SubjectOption]:
    """Sammelt verfügbare Fächer einer Klasse aus mehreren Tagesplänen."""

    labels: dict[str, str] = {}
    for plan in plans:
        if plan is None:
            continue
        for option in subject_options(plan, class_name):
            labels.setdefault(option.key, option.label)
    return [SubjectOption(key, labels[key]) for key in sorted(labels, key=lambda item: _subject_option_sort_key(item, labels[item]))]


def matching_lessons(class_item: object, lessons: Iterable[object], selected: set[str]) -> list[object]:
    return [
        lesson for lesson in lessons
        if subject_key(lesson_subject(class_item, lesson)) in selected
    ]


def class_sort_key(class_name: str) -> list[object]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", class_name)]


def available_class_names_from_plans(plans: Iterable[object | None]) -> list[str]:
    classes: set[str] = set()
    for plan in plans:
        if plan is None:
            continue
        classes.update(getattr(plan, "klassen", {}).keys())
    return sorted(classes, key=class_sort_key)


class SubscriptionNotifier:
    """Erstellt persönliche Nachrichten und dedupliziert sie persistent pro Nutzer."""

    def __init__(self, store: AccountStore, ntfy_url: str, *, timeout: float = 10.0):
        self.store = store
        self.ntfy_url = ntfy_url.rstrip("/")
        self.timeout = timeout
        self.blocks = DEFAULT_BLOCKS
        self._known_plan_signatures: dict[date, dict[str, str]] = {}
        self._publisher_auth = resolve_ntfy_publisher_auth()
        self.delivery_errors: list[str] = []

    @staticmethod
    def _plan_signature(class_item: object) -> str:
        lessons_by_period = []
        for period, lessons in sorted(getattr(class_item, "stunden", {}).items()):
            period_lessons = []
            for lesson in lessons:
                period_lessons.append((
                    getattr(lesson, "fach", None),
                    tuple(getattr(lesson, "räume", ())),
                    getattr(lesson, "periode", None),
                    getattr(lesson, "änderung", False),
                    getattr(lesson, "ausfall", False),
                    getattr(lesson, "kursnummer", None),
                    getattr(lesson, "info", None),
                    tuple(getattr(lesson, "lehrer", ())),
                ))
            lessons_by_period.append((period, tuple(period_lessons)))
        return hashlib.sha256(repr(tuple(lessons_by_period)).encode("utf-8")).hexdigest()

    @staticmethod
    def _lesson_text(class_item: object, lessons: list[object]) -> str:
        if not lessons:
            return "Freistunde"
        changed = any(getattr(lesson, "änderung", False) for lesson in lessons)
        parts: list[str] = []
        for lesson in lessons:
            if getattr(lesson, "ausfall", False):
                info = getattr(lesson, "info", None)
                subject = lesson_subject(class_item, lesson)
                parts.append((subject + ": " if subject else "") + "Entfall" + (f" ({info})" if info else ""))
                continue
            subject = lesson_subject(class_item, lesson) or "Unterricht"
            rooms = ", ".join(getattr(lesson, "räume", ())) or "Raum unbekannt"
            parts.append(f"{subject} in {rooms}")
        text = "; ".join(dict.fromkeys(parts))
        return f"{text} [Änderung]" if changed else text

    @staticmethod
    def _block_lessons(class_item: object, block: Block) -> list[object]:
        return [
            lesson for period in range(block.period, block.period + 2)
            for lesson in getattr(class_item, "stunden", {}).get(period, [])
        ]

    @staticmethod
    def _period_end(plan: object, block: Block) -> time | None:
        return getattr(plan, "zeitplan", {}).get(block.period, (block.start, block.end))[1]

    @staticmethod
    def _period_start(plan: object, block: Block) -> time | None:
        return getattr(plan, "zeitplan", {}).get(block.period, (block.start, block.end))[0]

    @staticmethod
    def _time_from_text(value: str) -> time:
        return datetime.strptime(value, "%H:%M").time()

    @staticmethod
    def _format_block_line(block_number: int, entries: list[tuple[str, str]], *, include_class_name: bool) -> str:
        if include_class_name:
            rendered = " | ".join(f"{class_name}: {text}" for class_name, text in entries)
        else:
            rendered = entries[0][1]
        return f"{block_number}. Block: {rendered}"

    def _class_block_entries(
        self,
        recipient: NotificationRecipient,
        plan: object,
        block: Block,
        *,
        changed_only: bool = False,
    ) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        for class_name in recipient.selected_classes:
            class_item = getattr(plan, "klassen", {}).get(class_name)
            if class_item is None:
                continue
            selected = recipient.subject_selections.get(class_name, set())
            if not selected:
                continue
            lessons = matching_lessons(class_item, self._block_lessons(class_item, block), selected)
            if not lessons:
                continue
            if changed_only and not any(getattr(lesson, "änderung", False) for lesson in lessons):
                continue
            entries.append((class_name, self._lesson_text(class_item, lessons)))
        return entries

    def _daily_summary_lines(self, recipient: NotificationRecipient, plan: object) -> list[str]:
        include_class_name = len(recipient.selected_classes) > 1
        lines: list[str] = []
        for block in self.blocks:
            entries = self._class_block_entries(recipient, plan, block)
            if entries:
                lines.append(self._format_block_line(block.number, entries, include_class_name=include_class_name))
        return lines

    def _change_lines(self, recipient: NotificationRecipient, plan: object) -> list[str]:
        include_class_name = len(recipient.selected_classes) > 1
        lines: list[str] = []
        for block in self.blocks:
            entries = self._class_block_entries(recipient, plan, block, changed_only=True)
            if entries:
                lines.append(self._format_block_line(block.number, entries, include_class_name=include_class_name))
        return lines

    def _next_block_notification(self, recipient: NotificationRecipient, plan: object, trigger_time: time) -> tuple[int, str, str] | None:
        include_class_name = len(recipient.selected_classes) > 1
        for block in self.blocks:
            block_start = self._period_start(plan, block)
            if block_start is None or block_start <= trigger_time:
                continue
            entries = self._class_block_entries(recipient, plan, block)
            if not entries:
                continue
            title_suffix = ", ".join(
                sorted(
                    {
                        room
                        for class_name in recipient.selected_classes
                        for lesson in matching_lessons(
                            getattr(plan, "klassen", {}).get(class_name, object()),
                            self._block_lessons(getattr(plan, "klassen", {}).get(class_name, object()), block)
                            if getattr(plan, "klassen", {}).get(class_name) is not None else [],
                            recipient.subject_selections.get(class_name, set()),
                        )
                        for room in getattr(lesson, "räume", ())
                        if room
                    }
                )
            )
            return (
                block.number,
                f"(VPrintfy) Nächster Raum: {title_suffix or 'Freistunde'}",
                f"Nächster ({block.number}.) Block: "
                + (
                    " | ".join(f"{class_name}: {text}" for class_name, text in entries)
                    if include_class_name else entries[0][1]
                ),
            )
        return None

    @staticmethod
    def _sequence_id(user: User, event_key: str) -> str:
        digest = hashlib.sha256(f"{user.id}:{event_key}".encode("utf-8")).hexdigest()[:32]
        return f"vprintfy-{digest}"

    def _publish(self, user: User, message: str, title: str, priority: str = "default") -> None:
        headers = {"Title": Header(title, "utf-8").encode(), "Priority": priority, "Tags": "calendar"}
        sequence_id = getattr(self, "_pending_sequence_id", None)
        if sequence_id:
            headers["X-Sequence-ID"] = sequence_id
        response = requests.post(
            f"{self.ntfy_url}/{user.ntfy_topic}", data=message.encode("utf-8"),
            headers=headers,
            timeout=self.timeout,
            # Der dedizierte Server-Publisher besitzt ausschließlich
            # Schreibrechte. Dadurch bleiben persönliche Topics lesegeschützt,
            # während ältere Nutzer-ACLs den Hintergrundversand nicht brechen.
            auth=self._publisher_auth,
        )
        response.raise_for_status()

    def _deliver(self, user: User, event_key: str, message: str, title: str, priority: str = "default") -> bool:
        if not self.store.mark_delivery_once(user.id, event_key):
            return False
        self._pending_sequence_id = self._sequence_id(user, event_key)
        try:
            self._publish(user, message, title, priority)
        except requests.RequestException as error:
            self.store.forget_delivery(user.id, event_key)
            self.delivery_errors.append(f"{user.username} ({event_key}): {error}")
            return False
        finally:
            self._pending_sequence_id = None
        return True

    def delete_expired_client_notifications(self, now: datetime | None = None) -> int:
        now = now or datetime.now()
        cutoff = now - CLIENT_NOTIFICATION_RETENTION
        deleted = 0
        for user, event_key in self.store.delivery_deletion_candidates(cutoff):
            sequence_id = self._sequence_id(user, event_key)
            try:
                response = requests.delete(
                    f"{self.ntfy_url}/{user.ntfy_topic}/{quote(sequence_id, safe='')}",
                    timeout=self.timeout,
                    auth=(user.ntfy_username, user.ntfy_password),
                )
                if response.status_code not in {200, 202, 204, 404}:
                    response.raise_for_status()
            except requests.RequestException as error:
                self.delivery_errors.append(f"{user.username} ({event_key}:delete): {error}")
                continue
            self.store.mark_delivery_deleted(user.id, event_key)
            deleted += 1
        return deleted

    def send_user_test(self, user: User) -> None:
        """Sendet auf Wunsch eine nicht deduplizierte Probe an das persönliche Topic."""
        # Der persönliche ntfy-Nutzer besitzt Schreibrecht auf genau sein Topic.
        # Dadurch funktioniert der Test unabhängig vom globalen Publisher sowohl
        # über 127.0.0.1 als auch im Compose-Netz und über die Produktionsdomain.
        response = requests.post(
            f"{self.ntfy_url}/{user.ntfy_topic}",
            data="Deine Benachrichtigungen sind richtig verbunden.".encode("utf-8"),
            headers={"Title": Header("(VPrintfy) Test erfolgreich", "utf-8").encode(), "Priority": "high", "Tags": "white_check_mark"},
            timeout=self.timeout,
            auth=(user.ntfy_username, user.ntfy_password),
        )
        response.raise_for_status()

    def send_test(self, user: User, selected: set[str], plan: object, kind: str, block_number: int | None = None) -> None:
        recipient = NotificationRecipient(
            user=user,
            selected_classes=(user.class_name,),
            subject_selections={user.class_name: selected},
            notify_settings=NotifySettings(),
            calendar_courses=set(),
        )

        if kind == "morning":
            entries = self._daily_summary_lines(recipient, plan)
            if not entries:
                raise ValueError("Für die ausgewählten Fächer enthält der aktuelle Plan keine Stunden.")
            plan_date = getattr(plan, "datum", None) or date.today()
            self._publish(
                user,
                "Heute, " + plan_date.strftime("%d.%m.%Y") + ":\n" + "\n".join(entries),
                "(VPrintfy) Heute",
            )
            return

        if kind == "change":
            timestamp = getattr(plan, "zeitstempel", None)
            changes = self._change_lines(recipient, plan)
            if timestamp is None or not changes:
                raise ValueError("Für die ausgewählten Fächer enthält der aktuelle Plan keine Änderung.")
            self._publish(
                user,
                "Plan veröffentlicht/aktualisiert (" + timestamp.strftime("%d.%m.%Y %H:%M") + "):\n" + "\n".join(changes),
                "(VPrintfy) Plan-Änderung",
                "high",
            )
            return

        if kind == "next":
            if block_number is None or not 1 <= block_number < len(self.blocks):
                raise ValueError("Für next muss --block zwischen 1 und 3 liegen.")
            trigger_time = self._period_end(plan, self.blocks[block_number - 1])
            if trigger_time is None:
                raise ValueError("Für den gewählten Block fehlt die Uhrzeit.")
            notification = self._next_block_notification(recipient, plan, trigger_time)
            if notification is None:
                raise ValueError("Für die ausgewählten Fächer enthält der nächste Block keine Stunden.")
            self._publish(
                user,
                notification[2],
                notification[1],
                "high",
            )
            return

        raise ValueError("Unbekannter Benachrichtigungstyp.")

    @staticmethod
    def _event_matches_recipient(recipient: NotificationRecipient, event: CalendarEvent, selected_types: set[str]) -> bool:
        if recipient.user.vp_only:
            return False
        if event.event_type not in selected_types:
            return False
        if not event.course_id or event.course_id == "ALLGEMEIN":
            return True
        return event.course_id in recipient.calendar_courses

    @staticmethod
    def _calendar_days_before(event: CalendarEvent, settings: NotifySettings) -> int:
        days_before = (settings.calendar_notification_days_before_by_type or {}).get(
            event.event_type, settings.calendar_notification_days_before
        )
        return max(0, min(MAX_CALENDAR_NOTIFICATION_DAYS_BEFORE, int(days_before)))

    @staticmethod
    def _calendar_notification_time(event: CalendarEvent, settings: NotifySettings) -> time:
        notification_time = (settings.calendar_notification_times or {}).get(
            event.event_type, settings.calendar_notification_time
        )
        return datetime.strptime(notification_time, "%H:%M").time()

    @classmethod
    def _calendar_notification_at(cls, event: CalendarEvent, settings: NotifySettings) -> datetime:
        event_date = date.fromisoformat(event.date)
        days_before = cls._calendar_days_before(event, settings)
        return datetime.combine(
            event_date - timedelta(days=days_before),
            cls._calendar_notification_time(event, settings),
        )

    @classmethod
    def _calendar_notification_is_due(cls, event: CalendarEvent, settings: NotifySettings, now: datetime) -> bool:
        event_date = date.fromisoformat(event.date)
        today = now.date()
        days_before = cls._calendar_days_before(event, settings)
        if event_date < today:
            return False
        if event_date > today + timedelta(days=days_before):
            return False
        notify_at = cls._calendar_notification_at(event, settings)
        if notify_at.date() >= today:
            return now >= notify_at
        return now.time() >= cls._calendar_notification_time(event, settings)

    @staticmethod
    def _calendar_message(event: CalendarEvent) -> tuple[str, str]:
        title = f"(VPrintfy) Kalender: {event.title}"
        when = event.date
        if event.start_time:
            when = f"{when} ab {event.start_time}"
        lines = [event.title, f"Typ: {event.event_type}", f"Datum: {when}"]
        if event.course_id:
            lines.append(f"Kurs: {event.course_id}")
        if event.description:
            lines.append("")
            lines.append(event.description)
        return title, "\n".join(lines)

    def poll_once(
        self,
        plan: object,
        now: datetime | None = None,
        *,
        recipient_username: str | None = None,
        day_before_plan: object | None = None,
    ) -> int:
        self.delivery_errors.clear()
        now = now or datetime.now()
        plan_date = getattr(plan, "datum", None) or now.date()
        known_signatures = self._known_plan_signatures.setdefault(plan_date, {})
        changed_classes: set[str] = set()
        for class_name, class_item in getattr(plan, "klassen", {}).items():
            signature = self._plan_signature(class_item)
            previous_signature = known_signatures.get(class_name)
            if previous_signature is not None and previous_signature != signature:
                changed_classes.add(class_name)
            known_signatures[class_name] = signature
        sent = 0
        for recipient in self.store.notification_recipients():
            user = recipient.user
            if recipient_username is not None and user.username != recipient_username:
                continue
            settings = recipient.notify_settings
            # Private calendar data is decrypted and loaded only for its owner.
            # VP-only accounts intentionally have no calendar access and must
            # never receive calendar-derived notifications, even if an old DB
            # still contains stale calendar notification settings for them.
            calendar_events = [] if user.vp_only else self.store.get_calendar_events(user.username)
            has_subject_selection = any(recipient.subject_selections.values())
            if settings.lesson_notifications_enabled and has_subject_selection:
                lesson_times = sorted(
                    [(value, self._time_from_text(value)) for value in dict.fromkeys(settings.lesson_notification_times)],
                    key=lambda item: item[1],
                )
                if lesson_times:
                    _first_key, first_time = lesson_times[0]
                    summary_plan = day_before_plan if settings.daily_summary_day_before else plan
                    summary_date = getattr(summary_plan, "datum", None) or plan_date
                    summary_due = now.time() >= first_time and summary_date.weekday() < 5
                    if summary_due:
                        lines = self._daily_summary_lines(recipient, summary_plan)
                        if lines:
                            sent += self._deliver(
                                user,
                                f"morning:{summary_date.isoformat()}" if not settings.daily_summary_day_before else f"morning-day-before:{summary_date.isoformat()}",
                                ("Morgen, " if settings.daily_summary_day_before else "Heute, ") + summary_date.strftime("%d.%m.%Y") + ":\n" + "\n".join(lines),
                                "(VPrintfy) " + ("Morgen" if settings.daily_summary_day_before else "Heute"),
                            )
                    timestamp = getattr(plan, "zeitstempel", None)
                    if timestamp and any(class_name in changed_classes for class_name in recipient.selected_classes):
                        changes = self._change_lines(recipient, plan)
                        if changes:
                            signature = "|".join(
                                f"{class_name}:{self._plan_signature(getattr(plan, 'klassen', {})[class_name])}"
                                for class_name in recipient.selected_classes
                                if class_name in getattr(plan, "klassen", {})
                            )
                            sent += self._deliver(
                                user,
                                f"publication:{plan_date.isoformat()}:{signature}",
                                "Plan veröffentlicht/aktualisiert (" + timestamp.strftime("%d.%m.%Y %H:%M") + "):\n" + "\n".join(changes),
                                "(VPrintfy) Plan-Änderung",
                                "high",
                            )
                    for time_key, trigger_time in lesson_times[1:]:
                        if now.time() < trigger_time:
                            continue
                        trigger_datetime = datetime.combine(plan_date, trigger_time)
                        if now - trigger_datetime > timedelta(minutes=20):
                            # Zu spät ausgelöst (z.B. weil kein aktueller Plan
                            # verfügbar war) - lieber auslassen als eine sehr
                            # verspätete Benachrichtigung zu versenden.
                            continue
                        notification = self._next_block_notification(recipient, plan, trigger_time)
                        if notification is None:
                            continue
                        sent += self._deliver(
                            user,
                            f"next:{plan_date.isoformat()}:{notification[0]}",
                            notification[2],
                            notification[1],
                            "high",
                        )
            if not user.vp_only and settings.calendar_notifications_enabled and settings.calendar_notification_types:
                selected_types = set(settings.calendar_notification_types)
                for event in calendar_events:
                    if not self._event_matches_recipient(recipient, event, selected_types):
                        continue
                    if not self._calendar_notification_is_due(event, settings, now):
                        continue
                    title, message = self._calendar_message(event)
                    days_before = self._calendar_days_before(event, settings)
                    notification_time = self._calendar_notification_time(event, settings).strftime("%H:%M")
                    sent += self._deliver(
                        user,
                        f"calendar:{event.id}:{event.date}:{days_before}:{notification_time}",
                        message,
                        title,
                        "high",
                    )
        return sent
