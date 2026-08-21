"""Modular ntfy notifications for VpMobil daily schedules.

The module deliberately knows nothing about credentials or a specific course
selection. Pass a ``NotificationConfig`` for each course/topic combination and
call ``poll_once`` from a cron job, task scheduler, or the web application's
background worker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from email.header import Header
import argparse
import json
from pathlib import Path
from time import sleep
from typing import Callable, Iterable, Mapping, Protocol

import requests
import sys

sys.path.append(str(Path(__file__).resolve().parent.parent))
from vp_data import fetch_plan


class Plan(Protocol):
    datum: date | None
    zeitstempel: datetime | None
    zeitplan: Mapping[int, tuple[time | None, time | None]]
    klassen: Mapping[str, object]


@dataclass(frozen=True)
class Block:
    number: int
    start: time
    end: time | None = None
    period: int = 1


DEFAULT_BLOCKS = (
    # Ein Block umfasst jeweils zwei Unterrichtsstunden. Der Plan führt den
    # Stundenbeginn deshalb unter 1, 3, 5 und 7.
    Block(1, time(7, 45), time(9, 15), period=1),
    Block(2, time(9, 35), time(11, 5), period=3),
    Block(3, time(11, 50), time(13, 20), period=5),
    Block(4, time(13, 45), None, period=7),
)


@dataclass(frozen=True)
class NotificationConfig:
    """Topic and course-specific formatting for one notification stream."""

    classes: tuple[str, ...]
    topic: str = "vprintfy"
    ntfy_url: str = "https://ntfy.sh"
    morning_time: time = time(7, 0)
    blocks: tuple[Block, ...] = DEFAULT_BLOCKS
    timeout: float = 10.0
    state_file: Path | None = None
    title: str = "Stundenplan"


@dataclass
class NotificationState:
    morning_sent: set[str] = field(default_factory=set)
    block_sent: set[str] = field(default_factory=set)
    publication_sent: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: Path | None) -> "NotificationState":
        if path is None or not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(*(set(data.get(name, [])) for name in (
                "morning_sent", "block_sent", "publication_sent"
            )))
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path | None) -> None:
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "morning_sent": sorted(self.morning_sent),
            "block_sent": sorted(self.block_sent),
            "publication_sent": sorted(self.publication_sent),
        }, indent=2), encoding="utf-8")


class NtfyClient:
    def __init__(self, config: NotificationConfig):
        self.config = config

    def publish(
        self,
        message: str,
        *,
        title: str | bytes | None = None,
        priority: str = "default",
        tags: str | None = None,
    ) -> None:
        raw_title = title or self.config.title
        if isinstance(raw_title, bytes):
            raw_title = raw_title.decode("utf-8", errors="replace")
        # ntfy empfiehlt RFC 2047 für Unicode-Titel, wenn die HTTP-Bibliothek
        # keine UTF-8-Header unterstützt. Das bleibt reiner ASCII-Text auf
        # dem Transportweg und wird von ntfy wieder korrekt dekodiert.
        header_title = Header(raw_title, "utf-8").encode()
        headers = {
            "Title": header_title,
            "Priority": priority,
        }
        if tags:
            headers["Tags"] = tags
        response = requests.post(
            f"{self.config.ntfy_url.rstrip('/')}/{self.config.topic}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=self.config.timeout,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as error:
            detail = response.text.strip()
            if detail:
                raise requests.HTTPError(
                    f"ntfy hat die Nachricht abgelehnt: {detail}",
                    response=response,
                ) from error
            raise


class ScheduleNotifier:
    def __init__(self, config: NotificationConfig):
        self.config = config
        self.client = NtfyClient(config)
        self.state = NotificationState.load(config.state_file)


    def _classes(self, plan: Plan) -> Iterable[object]:
        return (
            plan.klassen[name]
            for name in self.config.classes
            if name in plan.klassen
        )


    def _lessons(self, plan: Plan) -> dict[int, list[object]]:
        result: dict[int, list[object]] = {}
        for class_item in self._classes(plan):
            for period, lessons in getattr(class_item, "stunden", {}).items():
                result.setdefault(int(period), []).extend(lessons)
        return result


    def _period_time(self, plan: Plan, block: Block) -> tuple[time, time | None]:
        return plan.zeitplan.get(block.period, (block.start, block.end))


    @staticmethod
    def _block_periods(block: Block) -> range:
        """Die beiden Einzelstunden, die zu einem Unterrichtsblock gehören."""

        return range(block.period, block.period + 2)


    def _block_lessons(self, lessons: Mapping[int, list[object]], block: Block) -> list[object]:
        return [
            lesson
            for period in self._block_periods(block)
            for lesson in lessons.get(period, [])
        ]


    def _next_block(self, block: Block) -> Block | None:
        """Gibt den nächsten konfigurierten Block zurück."""

        for index, configured_block in enumerate(self.config.blocks):
            if configured_block == block:
                return next(iter(self.config.blocks[index + 1:]), None)

        return None


    def _lesson_text(self, lessons: list[object]) -> str:
        if not lessons:
            return "Freistunde"
        changed = any(getattr(lesson, "änderung", False) for lesson in lessons)
        parts = []
        for lesson in lessons:
            if getattr(lesson, "ausfall", False):
                info = getattr(lesson, "info", None)
                parts.append("Entfall" + (f" ({info})" if info else ""))
                continue
            subject = getattr(lesson, "fach", None) or "Unterricht"
            rooms = ", ".join(getattr(lesson, "räume", ())) or "Raum unbekannt"
            parts.append(f"{subject} in {rooms}")
        # VpMobil liefert Doppelstunden üblicherweise für beide Einzelstunden
        # identisch. In einer Block-Benachrichtigung soll das nur einmal stehen.
        text = "; ".join(dict.fromkeys(parts))
        return f"{text} [Änderung]" if changed else text


    def send_morning(self, plan: Plan) -> bool:
        plan_date = plan.datum or date.today()
        key = f"{self.config.topic}:{plan_date.isoformat()}"
        if key in self.state.morning_sent:
            return False
        lessons = self._lessons(plan)
        entries = [
            f"{block.number}. Block: {self._lesson_text(self._block_lessons(lessons, block))}"
            for block in self.config.blocks
            if self._block_lessons(lessons, block) or block.number != 4
        ]
        self.client.publish(
            "Heute, " + plan_date.strftime("%d.%m.%Y") + ":\n" + "\n".join(entries),
            title="(VPrintfy) Heute",
            priority="default",
            tags="calendar"
        )
        self.state.morning_sent.add(key)
        return True


    def send_next_room(self, plan: Plan, block: Block, now: datetime) -> bool:
        plan_date = plan.datum or now.date()
        key = f"{self.config.topic}:{plan_date.isoformat()}:{block.number}"
        if key in self.state.block_sent:
            return False
        _, block_end = self._period_time(plan, block)
        if block_end is None:
            return False
        notification_start = (
            datetime.combine(now.date(), block_end) - timedelta(minutes=5)
        ).time()
        if not notification_start <= now.time() < block_end:
            return False
        lessons = self._lessons(plan)
        next_block = self._next_block(block)

        if next_block is None:
            return False

        next_lessons = self._block_lessons(lessons, next_block)
        next_rooms = sorted({
            room
            for lesson in next_lessons
            for room in getattr(lesson, "räume", ())
            if room
        })
        room_title = ", ".join(next_rooms) if next_rooms else "Freistunde"
        self.client.publish(
            f"Nächster ({next_block.number}.) Block: "
            f"{self._lesson_text(next_lessons)}",
            title=f"(VPrintfy) Nächster Raum: {room_title}".encode("utf-8"),
            priority="high",
            tags="calendar",
        )
        self.state.block_sent.add(key)
        return True


    def send_publication_change(self, plan: Plan) -> bool:
        timestamp = getattr(plan, "zeitstempel", None)
        if timestamp is None:
            return False
        key = f"{self.config.topic}:{timestamp.isoformat()}"
        lessons = self._lessons(plan)
        changed = []
        for block in self.config.blocks:
            block_lessons = self._block_lessons(lessons, block)
            if any(getattr(lesson, "änderung", False) for lesson in block_lessons):
                changed.append(
                    f"{block.number}. Block: {self._lesson_text(block_lessons)}"
                )
        if not changed or key in self.state.publication_sent:
            return False
        self.client.publish(
            "Plan veröffentlicht/aktualisiert ("
            + timestamp.strftime("%d.%m.%Y %H:%M")
            + "):\n" + "\n".join(changed),
            title="(VPrintfy) Plan-Änderung",
            priority="high",
            tags="calendar",
        )
        self.state.publication_sent.add(key)
        return True


    def poll_once(self, plan: Plan, now: datetime | None = None) -> int:
        now = now or datetime.now()
        sent = 0
        if now.time() >= self.config.morning_time:
            sent += self.send_morning(plan)
        sent += self.send_publication_change(plan)
        for block in self.config.blocks:
            sent += self.send_next_room(plan, block, now)
        self.state.save(self.config.state_file)
        return sent


    def run_forever(
        self,
        fetch_plan: Callable[[date], Plan],
        interval: timedelta = timedelta(minutes=1),
    ) -> None:
        while True:
            today = date.today()
            self.poll_once(fetch_plan(today))
            sleep(interval.total_seconds())



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VpMobil-ntfy-Benachrichtigungen")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nachrichten nur ausgeben und nicht an ntfy senden.",
    )
    parser.add_argument(
        "--now",
        type=datetime.fromisoformat,
        metavar="YYYY-MM-DDTHH:MM",
        help="Zeitpunkt für einen einmaligen Testlauf simulieren.",
    )
    args = parser.parse_args()

    config = NotificationConfig(
        classes=("11",),  # Klasse
        topic="vprintfy",
        state_file=Path(__file__).with_name("notification_state.json"),
    )

    notifier = ScheduleNotifier(config)
    if args.dry_run:
        def print_message(
            message: str,
            *,
            title: str | bytes | None = None,
            **_kwargs: object,
        ) -> None:
            if isinstance(title, bytes):
                title = title.decode("utf-8")
            print(f"{title or config.title}:\n{message}\n")

        notifier.client.publish = print_message
        # Ein Testlauf darf den produktiven Versandstatus nicht verändern.
        notifier.state = NotificationState()
        notifier.config = NotificationConfig(
            **{**config.__dict__, "state_file": None}
        )

    if args.now:
        notifier.poll_once(fetch_plan(args.now.date()), now=args.now)
    else:
        notifier.run_forever(fetch_plan=fetch_plan, interval=timedelta(minutes=1))
