from datetime import date
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import vp_data
from vp_data import get_cached_plan_for_page, get_official_weekly_plans_for_page, get_plan_for_page, get_subject_catalog_plans_for_page, get_week_plans_for_page
from plan_page import get_selected_class_cookie_name, render_plan_page, resolve_initial_class
from vpmobil.weekly import fetch_weekly_plans


class Response:
    def __init__(self, content, status_code=200):
        self.content = content.encode()
        self.status_code = status_code

    @property
    def ok(self):
        return self.status_code < 400

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


BASIS = """
<splan><Schulwochen><Sw SwDatumVon="24.08.2026" SwDatumBis="28.08.2026" SwWo="B">2</Sw></Schulwochen></splan>
"""

WEEK = """
<splan><Klassen><Kl><Kurz>11</Kurz><Pl>
<Std><PlTg>1</PlTg><PlSt>1</PlSt><PlFa>MA1</PlFa><PlLe>Kön</PlLe><PlRa>224</PlRa><PlWo>B</PlWo></Std>
<Std><PlTg>1</PlTg><PlSt>1</PlSt><PlFa>MA2</PlFa><PlLe>Ein</PlLe><PlRa>309</PlRa><PlWo>A</PlWo></Std>
</Pl></Kl></Klassen></splan>
"""


class WeeklyPlanTests(unittest.TestCase):
    def tearDown(self):
        vp_data._weekly_plan_cache.clear()
        vp_data._weekly_refreshing.clear()
        vp_data._weekly_last_refresh.clear()
        vp_data._page_plan_cache.clear()
        vp_data._page_refreshing.clear()
        vp_data._page_last_refresh.clear()
        vp_data._subject_catalog_cache = None
        vp_data._subject_catalog_last_refresh = 0.0
        vp_data._subject_catalog_refreshing = False

    def test_weekly_plan_falls_back_to_latest_published_xml_and_keeps_week_type(self):
        responses = [Response(BASIS), Response("", 404), Response(WEEK)]
        access = SimpleNamespace(
            schulnummer=10237223,
            benutzername="user",
            passwort="password",
        )
        with patch("vpmobil.weekly.requests.get", side_effect=responses):
            plans = fetch_weekly_plans(access, date(2026, 8, 24))

        monday = plans[date(2026, 8, 24)]
        lessons = monday.klassen["11"].stunden[1]
        self.assertEqual(monday.week_type, "B")
        self.assertEqual([lesson.fach for lesson in lessons], ["MA1"])
        self.assertEqual(lessons[0].lehrer, ("Kön",))

    def test_cached_weekly_plan_avoids_synchronous_network_fetch(self):
        plans = {date(2026, 8, 24): SimpleNamespace()}
        with TemporaryDirectory() as directory, patch.object(vp_data, "CACHE_DIR", __import__("pathlib").Path(directory)):
            vp_data._save_weekly_cache(date(2026, 8, 24), plans)
            with patch("vp_data.fetch_official_weekly_plans", side_effect=AssertionError("synchroner Abruf")), patch(
                "vp_data.refresh_official_weekly_plans_in_background"
            ):
                self.assertEqual(get_official_weekly_plans_for_page(date(2026, 8, 24)), plans)

    def test_plan_page_delegates_missing_day_fallback_to_shared_week_loader(self):
        monday = date(2026, 8, 24)
        cached_plan = SimpleNamespace(klassen={}, zeitstempel=None)
        week_plans = {
            monday: cached_plan,
            date(2026, 8, 25): None,
            date(2026, 8, 26): None,
            date(2026, 8, 27): None,
            date(2026, 8, 28): None,
        }
        with patch("plan_page.get_week_plans_for_page", return_value=week_plans), patch(
            "plan_page.get_subject_catalog_plans_for_page", return_value=[]
        ):
            render_plan_page(monday)

    def test_week_loader_returns_cache_immediately_and_refreshes_missing_normal_plan(self):
        monday = date(2026, 8, 24)
        cached_vp_plan = SimpleNamespace(klassen={}, zeitstempel=None)
        normal_plan = SimpleNamespace(klassen={"11": object()}, zeitstempel=None)
        vp_data._page_plan_cache[monday] = cached_vp_plan
        with patch("vp_data.get_cached_official_weekly_plans_for_page", return_value=None), patch(
            "vp_data.refresh_official_weekly_plans_in_background"
        ) as refresh_week, patch("vp_data.refresh_plan_in_background"):
            plans = get_week_plans_for_page(monday)
        self.assertIs(plans[monday], cached_vp_plan)
        self.assertIsNone(plans[date(2026, 8, 25)])
        refresh_week.assert_called_once_with(monday)

        with patch("vp_data.get_cached_official_weekly_plans_for_page", return_value={
            date(2026, 8, 25): normal_plan,
        }), patch("vp_data.refresh_plan_in_background"):
            refreshed = get_week_plans_for_page(monday)
        self.assertIs(refreshed[date(2026, 8, 25)], normal_plan)

    def test_rooms_daily_loader_prefers_cached_normal_weekly_plan(self):
        monday = date(2026, 8, 24)
        normal_plan = SimpleNamespace(klassen={"11": object()}, zeitstempel=None)
        vp_data._weekly_plan_cache[monday] = {monday: normal_plan}
        with patch("vp_data.refresh_official_weekly_plans_in_background"), patch(
            "vp_data.refresh_plan_in_background"
        ), patch("vp_data.fetch_plan", side_effect=AssertionError("kein langsamer Tagesabruf")):
            self.assertIs(get_plan_for_page(monday), normal_plan)

    def test_cache_only_daily_loader_never_performs_a_synchronous_fetch(self):
        monday = date(2026, 8, 24)
        with patch("vp_data.load_plan_from_cache", return_value=None), patch(
            "vp_data.get_cached_official_weekly_plans_for_page", return_value=None
        ), patch("vp_data.refresh_plan_in_background") as refresh_day, patch(
            "vp_data.refresh_official_weekly_plans_in_background"
        ) as refresh_week, patch("vp_data.fetch_plan", side_effect=AssertionError("kein Netzwerk im Seitenrequest")):
            self.assertIsNone(get_cached_plan_for_page(monday))
        refresh_day.assert_called_once_with(monday)
        refresh_week.assert_called_once_with(monday)

    def test_page_subject_catalog_never_loads_synchronously(self):
        with patch("vp_data.Thread") as thread:
            self.assertEqual(get_subject_catalog_plans_for_page(), [])
        thread.assert_called_once()

    def test_initial_class_prefers_exact_then_same_grade_then_first_class(self):
        available = ["5a", "5b", "10b", "11"]
        self.assertEqual(resolve_initial_class("10b", available), "10b")
        self.assertEqual(resolve_initial_class("5g", available), "5a")
        self.assertEqual(resolve_initial_class("9c", available), "5a")
        self.assertEqual(resolve_initial_class(None, available), "5a")

    def test_selected_class_cookie_is_scoped_to_the_logged_in_user(self):
        self.assertEqual(get_selected_class_cookie_name("Gustav.D"), "selected_class_gustavd")
        self.assertNotEqual(get_selected_class_cookie_name("gustavd"), get_selected_class_cookie_name("anderer"))
