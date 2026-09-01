from datetime import date, datetime, time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from teacher_page import (
    collect_teacher_lessons,
    get_available_teachers,
    render_teacher_page,
    render_teacher_week_table,
)


class TeacherPageTests(unittest.TestCase):
    def test_missing_day_keeps_full_lesson_grid_and_shows_plan_notice(self):
        monday = date(2026, 8, 24)
        tuesday = date(2026, 8, 25)
        lesson = SimpleNamespace(
            fach="MA",
            klassen=("11",),
            lehrer=("KÖN",),
            räume=("101",),
            beginn=time(7, 45),
            ende=time(8, 30),
            änderung=False,
            ausfall=False,
            info=None,
        )
        teacher = SimpleNamespace(stunden={1: [lesson]})
        plan = SimpleNamespace(lehrer={"KÖN": teacher}, zeitstempel=datetime(2026, 8, 24, 6, 30))

        html = render_teacher_week_table({monday: plan, tuesday: None}, "KÖN")

        self.assertIn("Keine Plandaten vorhanden", html)
        self.assertIn("Planstand: 24.08.2026 06:30", html)
        self.assertEqual(html.count('class="period-head">'), 9)
        self.assertIn("25.08.", html)

    def test_teacher_data_can_be_derived_from_class_lessons(self):
        monday = date(2026, 8, 24)
        lesson = SimpleNamespace(
            fach="MA",
            lehrer=("KÖN",),
            räume=("101",),
            beginn=time(7, 45),
            ende=time(8, 30),
            änderung=False,
            ausfall=False,
            info=None,
        )
        class_item = SimpleNamespace(stunden={1: [lesson]})
        weekly_plan = SimpleNamespace(klassen={"11a": class_item}, zeitstempel=None)

        teachers = get_available_teachers({monday: weekly_plan})
        lessons = collect_teacher_lessons({monday: weekly_plan}, "KÖN")

        self.assertEqual(teachers, ["KÖN"])
        self.assertIn("KÖN", lessons[1][monday][0].lehrer)
        self.assertEqual(lessons[1][monday][0].klassen, ("11a",))

    def test_render_teacher_page_uses_week_loader_with_normal_plan_fallback(self):
        selected_date = date(2026, 8, 24)
        lesson = SimpleNamespace(
            fach="MA",
            lehrer=("KÖN",),
            räume=("101",),
            beginn=time(7, 45),
            ende=time(8, 30),
            änderung=False,
            ausfall=False,
            info=None,
        )
        class_item = SimpleNamespace(stunden={1: [lesson]})
        weekly_plan = SimpleNamespace(klassen={"11a": class_item}, zeitstempel=None)

        week_plans = {
            date(2026, 8, 24): weekly_plan,
            date(2026, 8, 25): None,
            date(2026, 8, 26): None,
            date(2026, 8, 27): None,
            date(2026, 8, 28): None,
        }
        with patch("teacher_page.get_week_plans_for_page", return_value=week_plans):
            html = render_teacher_page(selected_date)

        self.assertIn("KÖN", html)


if __name__ == "__main__":
    unittest.main()
