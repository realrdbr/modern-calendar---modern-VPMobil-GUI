from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

import pytest
import vp_data
from vpmobil.models import Stunde, Kurs, Vertretungsplan
from plan_page import render_lesson_cell, render_week_table
from teacher_page import collect_teacher_lessons, render_teacher_week_table
from rooms_page import get_free_rooms_for_page, get_room_plan_version

DAY = date(2026, 9, 7)


def lesson(**kwargs):
    return Stunde(periode=1, klassen=("11",), **kwargs)


@pytest.mark.parametrize("subject", [None, "", "-", "---"])
def test_note_without_teacher_is_visible_cancellation(subject):
    item = lesson(fach=subject, info="Kurs entfällt <heute>", kursnummer=7)
    plan = Vertretungsplan(stunden=[item], kurse=[Kurs(kursnummer=7, klassen=("11",), lehrer="AB", fach="MA")])
    assert item.ausfall and item.änderung
    assert collect_teacher_lessons({DAY: plan}, "AB")[1][DAY] == [item]
    for html in (render_lesson_cell([item]), render_teacher_week_table({DAY: plan}, "AB")):
        assert 'week-lesson--changed' in html
        assert 'Ausfall' in html
        assert 'Kurs entfällt &lt;heute&gt;' in html
        assert '<details' in html


def test_note_with_teacher_is_not_cancellation():
    assert not lesson(fach="MA", lehrer=("AB",), info="Material mitbringen").ausfall


def test_second_block_lesson_keeps_note_and_status():
    first = lesson(fach="MA", lehrer=("AB",))
    second = lesson(fach="MA", lehrer=("AB",), info="Andere Aufgabe", fachänderung=True)
    second.periode = 2
    plan = Vertretungsplan(stunden=[first, second])
    for html in (render_week_table({DAY: plan}, "11", [], block_mode=True),
                 render_teacher_week_table({DAY: plan}, "AB", block_mode=True)):
        assert 'Andere Aufgabe' in html
        assert 'week-lesson--changed' in html
        assert '2. Stunde' in html


def test_parser_does_not_merge_unrelated_empty_lessons():
    plan = Vertretungsplan.from_xml(fromstring('''<VpMobil><Kopf><planart>K</planart></Kopf><Klassen>
      <Kl><Kurz>5a</Kurz><Pl><Std><St>1</St><Fa>---</Fa><If>Ausfall A</If></Std></Pl></Kl>
      <Kl><Kurz>5b</Kurz><Pl><Std><St>1</St><Fa>---</Fa><If>Ausfall B</If></Std></Pl></Kl>
    </Klassen></VpMobil>'''))
    assert len(plan.stunden) == 2
    assert {s.info for s in plan.stunden} == {"Ausfall A", "Ausfall B"}


def test_all_classes_and_parallel_room_occupancy_are_counted():
    items = [lesson(fach="MA", lehrer=("AB",), räume=("101", "102")),
             lesson(fach=None, räume=("103",), info="Ausfall")]
    items[0].klassen = ("13", "5/1", "DaZ")
    plan = Vertretungsplan(stunden=items)
    with patch.object(vp_data, "ALL_ROOMS", [101, 102, 103, 104]):
        assert vp_data.find_free_rooms_in_plan(plan, 1) == [104]
        assert vp_data.find_free_rooms_in_plan(plan, 2) == [101, 102, 103, 104]


def test_room_cache_changes_with_occupancy_even_with_same_timestamp():
    before = Vertretungsplan(stunden=[lesson(fach="MA", lehrer=("AB",), räume=("101",))])
    after = Vertretungsplan(stunden=[lesson(fach="MA", lehrer=("AB",), räume=("102",))])
    assert get_room_plan_version(before, DAY) != get_room_plan_version(after, DAY)
    with patch.object(vp_data, "ALL_ROOMS", [101, 102]):
        assert get_free_rooms_for_page(before, DAY, 1) == [102]
        assert get_free_rooms_for_page(after, DAY, 1) == [101]


def test_rooms_prefer_daily_plan_and_do_not_fill_empty_periods():
    daily = Vertretungsplan()
    with patch.dict(vp_data._room_plan_cache, clear=True), patch.object(vp_data, "fetch_plan_from_vpmobil", return_value=daily), patch.object(vp_data, "fetch_official_weekly_plans") as normal:
        assert vp_data.fetch_room_plan(DAY) is daily
        normal.assert_not_called()


def test_rooms_use_normal_plan_only_after_missing_daily_plan():
    normal = Vertretungsplan()
    with patch.dict(vp_data._room_plan_cache, clear=True), patch.object(vp_data, "fetch_plan_from_vpmobil", side_effect=vp_data.ResourceNotFound("missing")), patch.object(vp_data, "fetch_official_weekly_plans", return_value={DAY: normal}):
        assert vp_data.fetch_room_plan(DAY) is normal


def test_rooms_do_not_treat_network_failure_as_missing_daily_plan():
    with patch.dict(vp_data._room_plan_cache, clear=True), patch.object(vp_data, "fetch_plan_from_vpmobil", side_effect=TimeoutError), patch.object(vp_data, "fetch_official_weekly_plans") as normal:
        with pytest.raises(TimeoutError):
            vp_data.fetch_room_plan(DAY)
        normal.assert_not_called()


def test_combined_room_values_reserve_each_room():
    plan = Vertretungsplan(stunden=[lesson(fach="MA", lehrer=("AB",), räume=("101/102", "R103,104"))])
    assert vp_data.find_occupied_rooms(list(plan.klassen.values()), 1) == {101, 102, 103, 104}


def test_teacher_xml_reads_rooms_from_room_field():
    item = Stunde.from_xml(fromstring('<Std><St>1</St><Fa>MA</Fa><Le>11</Le><Ra>101</Ra></Std>'), planart="L", kontext=("AB",))
    assert item.räume == ("101",)


def test_unassigned_cancellation_survives_subject_filter():
    item = lesson(info="Unterricht entfällt")
    plan = Vertretungsplan(stunden=[item])
    assert "Unterricht entfällt" in render_week_table({DAY: plan}, "11", ["MA (AB)"])


def test_teacher_without_course_is_inferred_from_unique_normal_lesson():
    cancelled = lesson(info="Entfällt")
    normal = lesson(fach="MA", lehrer=("AB",))
    with patch('teacher_page.get_cached_official_weekly_plans_for_page', return_value={DAY: Vertretungsplan(stunden=[normal])}):
        result = collect_teacher_lessons({DAY: Vertretungsplan(stunden=[cancelled])}, "AB")
    assert result[1][DAY] == [cancelled]


def test_ambiguous_parallel_normal_lessons_are_not_assigned_to_teacher():
    cancelled = lesson(info="Entfällt")
    normal = Vertretungsplan(stunden=[lesson(fach="MA", lehrer=("AB",)), lesson(fach="EN", lehrer=("CD",))])
    with patch('teacher_page.get_cached_official_weekly_plans_for_page', return_value={DAY: normal}):
        assert collect_teacher_lessons({DAY: Vertretungsplan(stunden=[cancelled])}, "AB") == {}


@pytest.mark.parametrize("subject", [None, "MA"])
@pytest.mark.parametrize("teachers", [(), ("AB",)])
@pytest.mark.parametrize("rooms", [(), ("101",)])
@pytest.mark.parametrize("note", [None, "   ", "Hinweis"])
def test_cancellation_requires_all_three_fields_empty_and_note(subject, teachers, rooms, note):
    from lesson_status import is_cancelled
    from teacher_page import render_lesson_cell as render_teacher_cell

    item = lesson(fach=subject, lehrer=teachers, räume=rooms, info=note)
    expected = subject is None and not teachers and not rooms and note == "Hinweis"
    assert item.ausfall is expected
    assert is_cancelled(item) is expected
    assert item.änderung is expected
    for render in (render_lesson_cell, render_teacher_cell):
        html = render([item])
        assert ('week-lesson--changed' in html) is expected
        assert ('Ausfall' in html) is expected


def test_legacy_cancellation_flag_does_not_override_new_rule():
    from lesson_status import is_cancelled

    assert not is_cancelled(SimpleNamespace(fach="MA", lehrer=(), räume=(), info="Hinweis", ausfall=True))


def test_placeholder_fields_with_note_are_cancelled():
    from lesson_status import is_cancelled

    item = lesson(fach="---", lehrer=("-", " "), räume=("—",), info="Hinweis")
    assert item.ausfall
    assert is_cancelled(item)


def test_september_10_ast1_cancellation_survives_selected_course_filter():
    """Real ast1 course/lesson fields from the published 10 September plan."""
    from subscriptions import lesson_display_label

    day = date(2026, 9, 10)
    plan = Vertretungsplan.from_xml(fromstring('''<VpMobil><Kopf><planart>K</planart></Kopf>
      <Klassen><Kl><Kurz>11</Kurz><Unterricht>
        <Ue><UeNr UeLe="Ein" UeGr="ast1" UeFa="AST">327</UeNr></Ue>
      </Unterricht><Pl>
        <Std><St>7</St><Beginn>13:45</Beginn><Ende>15:15</Ende><Fa>---</Fa><Ku2>ast1</Ku2>
          <Le LeAe="LeGeaendert"/><Ra RaAe="RaGeaendert"/><Nr>327</Nr><If>ast1 Herr Einhorn fällt aus</If></Std>
        <Std><St>8</St><Fa>---</Fa><Ku2>ast1</Ku2>
          <Le LeAe="LeGeaendert"/><Ra RaAe="RaGeaendert"/><Nr>327</Nr><If>ast1 Herr Einhorn fällt aus</If></Std>
      </Pl></Kl></Klassen></VpMobil>'''))
    item = plan.klassen['11'].stunden[7][0]
    assert item.ausfall
    assert lesson_display_label(plan.klassen['11'], item) == 'ast1 (Ein)'
    for block_mode in (False, True):
        for html in (render_week_table({day: plan}, '11', ['ast1 (Ein)'], block_mode=block_mode),
                     render_teacher_week_table({day: plan}, 'Ein', block_mode=block_mode)):
            assert 'ast1 Herr Einhorn fällt aus' in html
            assert 'week-lesson--changed' in html
            assert '<strong>Ausfall</strong>' in html
            assert '<details' in html
    assert 'ast1 Herr Einhorn fällt aus' not in render_week_table({day: plan}, '11', ['eth2 (Bin)'])


@pytest.mark.parametrize('metadata, course, expected', [
    ('ast1', None, 'ast1'),
    (None, Kurs(kursnummer=327, kürzel='ast1', fach='AST'), 'ast1'),
    (None, Kurs(kursnummer=327, fach='AST'), 'AST'),
])
def test_cancelled_subject_uses_metadata_then_course_code_then_subject(metadata, course, expected):
    from subscriptions import lesson_subject

    item = lesson(fachmeta=metadata, kursnummer=327, info='Hinweis')
    class_item = SimpleNamespace(kurse={327: course} if course else {})
    assert lesson_subject(class_item, item) == expected


@pytest.mark.parametrize('class_name, code, subject, teacher, number, metadata', [
    ('Q2', 'BIO-LK-B', 'Biologie', 'XYZ', 9081, 'BIO-LK-B'),
    ('10/3', 'FR-Gruppe-2', 'Französisch', 'DEF', 42, None),
    ('12B', None, 'Mathematics', 'Smith', 700, None),
])
def test_course_cancellation_is_independent_of_school_names(class_name, code, subject, teacher, number, metadata):
    import re
    from subscriptions import lesson_display_label

    course = Kurs(kursnummer=number, kürzel=code, fach=subject, lehrer=teacher, klassen=(class_name,))
    item = Stunde(periode=4, klassen=(class_name,), kursnummer=number, fachmeta=metadata, info='Independent note')
    plan = Vertretungsplan(stunden=[item], kurse=[course])
    label = f'{code or subject} ({teacher})'
    assert lesson_display_label(plan.klassen[class_name], item) == label
    for html in (render_week_table({DAY: plan}, class_name, [label]),
                 render_teacher_week_table({DAY: plan}, teacher)):
        assert 'week-lesson--changed' in html
        assert 'Independent note' in html
        summaries = re.findall(r'<summary>(.*?)</summary>', html, re.S)
        assert any('<strong>-</strong>' in summary for summary in summaries)
        assert all('Ausfall' not in summary for summary in summaries)
        assert '<strong>Ausfall</strong>' in html
