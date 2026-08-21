from datetime import date
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import vp_data
from vp_data import get_official_weekly_plans_for_page
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
