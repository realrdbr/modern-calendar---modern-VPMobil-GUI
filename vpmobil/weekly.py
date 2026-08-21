"""Client and models for the official Stundenplan24 weekly-plan module."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from xml.etree import ElementTree

import requests


@dataclass(frozen=True)
class WeeklyLesson:
    periode: int
    fach: str | None
    beginn: time | None = None
    ende: time | None = None
    lehrer: tuple[str, ...] = ()
    räume: tuple[str, ...] = ()
    woche: str | None = None
    änderung: bool = False
    ausfall: bool = False
    info: str | None = None


@dataclass
class WeeklyClass:
    kürzel: str
    stunden: dict[int, list[WeeklyLesson]] = field(default_factory=dict)
    kurse: dict[int, object] = field(default_factory=dict)


@dataclass
class WeeklyDayPlan:
    datum: date
    week_type: str
    week_number: str
    klassen: dict[str, WeeklyClass]
    zeitstempel: datetime | None = None


def _parse_time(value: str | None) -> time | None:
    if not value:
        return None
    return datetime.strptime(value, "%H:%M").time()


def _split_values(value: str | None) -> tuple[str, ...]:
    if not value or value == "&nbsp;":
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _week_dates(week_start: date) -> list[date]:
    return [week_start + timedelta(days=offset) for offset in range(5)]


def fetch_weekly_plans(access, start_date: date | None = None) -> dict[date, WeeklyDayPlan]:
    """Fetch the official Stundenplan24 Version 6 plan for a school week."""

    start_date = start_date or date.today()
    monday = start_date - timedelta(days=start_date.weekday())
    base_url = f"https://www.stundenplan24.de/{access.schulnummer}/wplan/"
    auth = (access.benutzername, access.passwort)
    basis_response = requests.get(base_url + "wdatenk/SPlanKl_Basis.xml", auth=auth, timeout=15)
    basis_response.raise_for_status()
    basis = ElementTree.fromstring(basis_response.content)

    selected = None
    for week in basis.findall(".//Sw"):
        begin = datetime.strptime(week.attrib["SwDatumVon"], "%d.%m.%Y").date()
        end = datetime.strptime(week.attrib["SwDatumBis"], "%d.%m.%Y").date()
        if begin <= monday <= end:
            selected = week
            break
    if selected is None:
        raise requests.HTTPError(f"Kein Stundenplan24-Wochenplan für {monday.isoformat()} vorhanden.")

    week_number = selected.text or ""
    week_type = selected.attrib.get("SwWo", "")
    plan_response = requests.get(base_url + f"wdatenk/SPlanKl_Sw{week_number}.xml", auth=auth, timeout=15)
    if plan_response.status_code == 404:
        # Schools often publish one current XML containing both A/B variants,
        # while the index already lists future weeks. Reuse the newest
        # published weekly XML as a template when the requested file is absent.
        for fallback_number in range(int(week_number) - 1, 0, -1):
            fallback = requests.get(
                base_url + f"wdatenk/SPlanKl_Sw{fallback_number}.xml",
                auth=auth,
                timeout=15,
            )
            if fallback.ok:
                plan_response = fallback
                break
    plan_response.raise_for_status()
    root = ElementTree.fromstring(plan_response.content)
    daily_classes: dict[int, dict[str, WeeklyClass]] = {day: {} for day in range(1, 6)}

    for class_node in root.findall(".//Kl"):
        class_name = (class_node.findtext("Kurz") or "").strip()
        if not class_name:
            continue
        for lesson_node in class_node.findall("./Pl/Std"):
            lesson_week = (lesson_node.findtext("PlWo") or "").strip() or None
            if lesson_week and lesson_week != week_type:
                continue
            day = int(lesson_node.findtext("PlTg") or "0")
            period = int(lesson_node.findtext("PlSt") or "0")
            if not 1 <= day <= 5 or period < 1:
                continue
            weekly_class = daily_classes[day].setdefault(class_name, WeeklyClass(class_name))
            period_node = next(
                (node for node in class_node.findall("./Stunden/St") if (node.text or "").strip() == str(period)),
                None,
            )
            lesson = WeeklyLesson(
                periode=period,
                fach=(lesson_node.findtext("PlFa") or "").strip() or None,
                beginn=_parse_time(period_node.attrib.get("StZeit") if period_node is not None else None),
                ende=_parse_time(period_node.attrib.get("StZeitBis") if period_node is not None else None),
                lehrer=_split_values(lesson_node.findtext("PlLe")),
                räume=_split_values(lesson_node.findtext("PlRa")),
                woche=lesson_week,
            )
            weekly_class.stunden.setdefault(period, []).append(lesson)

    return {
        day: WeeklyDayPlan(day, week_type, week_number, daily_classes[index + 1])
        for index, day in enumerate(_week_dates(monday))
    }
