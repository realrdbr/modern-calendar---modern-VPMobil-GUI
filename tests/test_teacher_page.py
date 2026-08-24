from datetime import date, time
from types import SimpleNamespace
import unittest

from teacher_page import render_teacher_week_table


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
        plan = SimpleNamespace(lehrer={"KÖN": teacher})

        html = render_teacher_week_table({monday: plan, tuesday: None}, "KÖN")

        self.assertIn("Keine Plandaten vorhanden", html)
        self.assertEqual(html.count('class="period-head">'), 9)
        self.assertIn("25.08.", html)


if __name__ == "__main__":
    unittest.main()
