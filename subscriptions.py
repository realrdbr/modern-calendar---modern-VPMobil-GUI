"""Kurs-/Facherkennung und gezielter Versand für angemeldete Nutzer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from email.header import Header
import hashlib
import re
from typing import Iterable

import requests

from accounts import AccountStore, User
from ntfy.notifications import DEFAULT_BLOCKS, Block


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


class SubscriptionNotifier:
    """Erstellt persönliche Nachrichten und dedupliziert sie persistent pro Nutzer."""

    def __init__(self, store: AccountStore, ntfy_url: str, *, timeout: float = 10.0):
        self.store = store
        self.ntfy_url = ntfy_url.rstrip("/")
        self.timeout = timeout
        self.blocks = DEFAULT_BLOCKS
        self._known_plan_signatures: dict[date, dict[str, str]] = {}

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

    def _publish(self, user: User, message: str, title: str, priority: str = "default") -> None:
        response = requests.post(
            f"{self.ntfy_url}/{user.ntfy_topic}", data=message.encode("utf-8"),
            headers={"Title": Header(title, "utf-8").encode(), "Priority": priority, "Tags": "calendar"},
            timeout=self.timeout,
        )
        response.raise_for_status()

    def _deliver(self, user: User, event_key: str, message: str, title: str, priority: str = "default") -> bool:
        if not self.store.mark_delivery_once(user.id, event_key):
            return False
        try:
            self._publish(user, message, title, priority)
        except requests.RequestException:
            self.store.forget_delivery(user.id, event_key)
            raise
        return True

    def send_test(self, user: User, selected: set[str], plan: object, kind: str, block_number: int | None = None) -> None:
        class_item = getattr(plan, "klassen", {}).get(user.class_name)
        if class_item is None:
            raise ValueError("Für die Klasse des Nutzers ist im aktuellen Plan kein Eintrag vorhanden.")

        if kind == "morning":
            entries = []
            for block in self.blocks:
                lessons = matching_lessons(class_item, self._block_lessons(class_item, block), selected)
                if lessons:
                    entries.append(f"{block.number}. Block: {self._lesson_text(class_item, lessons)}")
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
            changes = []
            for block in self.blocks:
                lessons = matching_lessons(class_item, self._block_lessons(class_item, block), selected)
                if any(getattr(lesson, "änderung", False) for lesson in lessons):
                    changes.append(f"{block.number}. Block: {self._lesson_text(class_item, lessons)}")
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
            block = self.blocks[block_number - 1]
            next_block = self.blocks[block_number]
            lessons = matching_lessons(class_item, self._block_lessons(class_item, next_block), selected)
            if not lessons:
                raise ValueError("Für die ausgewählten Fächer enthält der nächste Block keine Stunden.")
            rooms = sorted({room for lesson in lessons for room in getattr(lesson, "räume", ()) if room})
            self._publish(
                user,
                f"Nächster ({next_block.number}.) Block: {self._lesson_text(class_item, lessons)}",
                "(VPrintfy) Nächster Raum: " + (", ".join(rooms) if rooms else "Freistunde"),
                "high",
            )
            return

        raise ValueError("Unbekannter Benachrichtigungstyp.")

    def poll_once(self, plan: object, now: datetime | None = None) -> int:
        now = now or datetime.now()
        plan_date = getattr(plan, "datum", None) or now.date()
        if plan_date.weekday() >= 5:
            return 0
        known_signatures = self._known_plan_signatures.setdefault(plan_date, {})
        changed_classes: set[str] = set()
        for class_name, class_item in getattr(plan, "klassen", {}).items():
            signature = self._plan_signature(class_item)
            previous_signature = known_signatures.get(class_name)
            if previous_signature is not None and previous_signature != signature:
                changed_classes.add(class_name)
            known_signatures[class_name] = signature
        sent = 0
        for user, selected in self.store.subscribed_users():
            if not selected:
                continue
            class_item = getattr(plan, "klassen", {}).get(user.class_name)
            if class_item is None:
                continue
            if now.time() >= time(7, 0):
                entries = []
                for block in self.blocks:
                    lessons = matching_lessons(class_item, self._block_lessons(class_item, block), selected)
                    if lessons:
                        entries.append(f"{block.number}. Block: {self._lesson_text(class_item, lessons)}")
                if entries:
                    sent += self._deliver(
                        user, f"morning:{plan_date.isoformat()}",
                        "Heute, " + plan_date.strftime("%d.%m.%Y") + ":\n" + "\n".join(entries),
                        "(VPrintfy) Heute",
                    )
            timestamp = getattr(plan, "zeitstempel", None)
            if timestamp and user.class_name in changed_classes:
                changes = []
                for block in self.blocks:
                    lessons = matching_lessons(class_item, self._block_lessons(class_item, block), selected)
                    if any(getattr(lesson, "änderung", False) for lesson in lessons):
                        changes.append(f"{block.number}. Block: {self._lesson_text(class_item, lessons)}")
                if changes:
                    sent += self._deliver(
                        user, f"publication:{plan_date.isoformat()}:{self._plan_signature(class_item)}",
                        "Plan veröffentlicht/aktualisiert (" + timestamp.strftime("%d.%m.%Y %H:%M") + "):\n" + "\n".join(changes),
                        "(VPrintfy) Plan-Änderung", "high",
                    )
            for position, block in enumerate(self.blocks[:-1]):
                block_end = self._period_end(plan, block)
                if block_end is None:
                    continue
                start = (datetime.combine(now.date(), block_end) - timedelta(minutes=5)).time()
                if not start <= now.time() < block_end:
                    continue
                next_block = self.blocks[position + 1]
                lessons = matching_lessons(class_item, self._block_lessons(class_item, next_block), selected)
                if not lessons:
                    continue
                rooms = sorted({room for lesson in lessons for room in getattr(lesson, "räume", ()) if room})
                sent += self._deliver(
                    user, f"next:{plan_date.isoformat()}:{block.number}",
                    f"Nächster ({next_block.number}.) Block: {self._lesson_text(class_item, lessons)}",
                    "(VPrintfy) Nächster Raum: " + (", ".join(rooms) if rooms else "Freistunde"), "high",
                )
        return sent
