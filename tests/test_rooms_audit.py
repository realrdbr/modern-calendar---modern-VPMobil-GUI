from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import vp_data
from vpmobil.models import Stunde, Vertretungsplan
from rooms_page import describe_room_plan, get_free_rooms_for_page, render_rooms_page

DAY = date(2026, 9, 10)


def plan(room, *, stamp=None, day=DAY):
    return Vertretungsplan(datum=day, zeitstempel=stamp, stunden=[
        Stunde(periode=4, fach='MA', klassen=('Q2',), lehrer=('AB',), räume=(room,))
    ])


@pytest.fixture(autouse=True)
def isolated_cache():
    with patch.dict(vp_data._room_plan_cache, clear=True):
        yield


def test_expired_cached_room_plan_does_not_mask_network_error():
    old = plan('101')
    vp_data._room_plan_cache[DAY] = (0, old)
    with patch.object(vp_data, 'monotonic', return_value=100), \
         patch.object(vp_data, 'fetch_plan_from_vpmobil', side_effect=TimeoutError), \
         patch.object(vp_data, 'fetch_plan', return_value=old) as stale_loader, \
         patch.object(vp_data, 'fetch_official_weekly_plans') as normal:
        with pytest.raises(TimeoutError):
            vp_data.fetch_room_plan(DAY)
        stale_loader.assert_not_called()
        normal.assert_not_called()


@pytest.mark.parametrize('timestamp', [None, datetime(2026, 9, 9, 14, 3)])
def test_refresh_uses_changed_rooms_even_with_same_or_missing_timestamp(timestamp):
    old, fresh = plan('101', stamp=timestamp), plan('102', stamp=timestamp)
    vp_data._room_plan_cache[DAY] = (0, old)
    with patch.object(vp_data, 'ALL_ROOMS', [101, 102]), \
         patch.object(vp_data, 'monotonic', return_value=100), \
         patch.object(vp_data, 'fetch_plan_from_vpmobil', return_value=fresh):
        assert get_free_rooms_for_page(old, DAY, 4) == [102]
        loaded = vp_data.fetch_room_plan(DAY)
        assert loaded is fresh
        assert get_free_rooms_for_page(loaded, DAY, 4) == [101]


def test_new_daily_plan_replaces_normal_plan_after_refresh_interval():
    normal, daily = plan('101'), plan('102')
    with patch.object(vp_data, 'monotonic', return_value=100), \
         patch.object(vp_data, 'fetch_plan_from_vpmobil', side_effect=vp_data.ResourceNotFound('missing')), \
         patch.object(vp_data, 'fetch_official_weekly_plans', return_value={DAY: normal}):
        assert vp_data.fetch_room_plan(DAY) is normal
    with patch.object(vp_data, 'monotonic', return_value=131), \
         patch.object(vp_data, 'fetch_plan_from_vpmobil', return_value=daily), \
         patch.object(vp_data, 'fetch_official_weekly_plans') as fallback:
        assert vp_data.fetch_room_plan(DAY) is daily
        fallback.assert_not_called()


@pytest.mark.parametrize('exception', [vp_data.Unauthorized('denied'), TimeoutError('timeout')])
def test_fetch_errors_never_fall_back_to_normal_plan(exception):
    with patch.object(vp_data, 'fetch_plan_from_vpmobil', side_effect=exception), \
         patch.object(vp_data, 'fetch_official_weekly_plans') as fallback:
        with pytest.raises(type(exception)):
            vp_data.fetch_room_plan(DAY)
        fallback.assert_not_called()


def test_no_daily_or_normal_plan_does_not_produce_free_rooms():
    with patch.object(vp_data, 'fetch_plan_from_vpmobil', side_effect=vp_data.ResourceNotFound('missing')), \
         patch.object(vp_data, 'fetch_official_weekly_plans', return_value={}):
        with pytest.raises(vp_data.ResourceNotFound):
            vp_data.fetch_room_plan(DAY)


def test_wrong_date_is_rejected():
    with patch.object(vp_data, 'fetch_plan_from_vpmobil', return_value=plan('101', day=date(2026, 9, 9))):
        with pytest.raises(ValueError, match='Plandatum'):
            vp_data.fetch_room_plan(DAY)


def test_room_remains_busy_when_one_of_two_parallel_lessons_is_cancelled():
    daily = plan('101')
    daily.stunden.extend([
        Stunde(periode=4, klassen=('11',), info='entfällt'),
        Stunde(periode=5, fach='EN', klassen=('11',), räume=('102',)),
    ])
    with patch.object(vp_data, 'ALL_ROOMS', [101, 102]):
        assert vp_data.find_free_rooms_in_plan(daily, 4) == [102]
        assert vp_data.find_free_rooms_in_plan(daily, 5) == [101]


@pytest.mark.parametrize('rooms', ['101-103', '101 - 103', '103–101', '101/102/103', '101,102;103'])
def test_room_ranges_and_lists_reserve_all_rooms(rooms):
    with patch.object(vp_data, 'ALL_ROOMS', [101, 102, 103, 104]):
        assert vp_data.find_free_rooms_in_plan(plan(rooms), 4) == [104]


def test_normal_plan_source_is_visible_and_errors_show_no_room_cards():
    normal = SimpleNamespace(week_type='B')
    description = describe_room_plan(normal)
    html = render_rooms_page(DAY, 4, [101], plan_description=description)
    assert 'Grundlage: normaler Stundenplan' in html
    assert 'kein Tagesplan veröffentlicht' in html
    html = render_rooms_page(DAY, 4, None, error_message='Abruf fehlgeschlagen', plan_version='loading')
    assert 'Abruf fehlgeschlagen' in html
    assert '<div class="room-card ' not in html
    assert '/api/room-version' in html


def test_lesson_without_class_still_reserves_its_room():
    from rooms_page import get_room_plan_version

    empty = Vertretungsplan(datum=DAY)
    daily = Vertretungsplan(datum=DAY, stunden=[Stunde(periode=4, fach='MA', lehrer=('AB',), räume=('101',))])
    assert not daily.klassen
    with patch.object(vp_data, 'ALL_ROOMS', [101, 102]):
        assert vp_data.find_free_rooms_in_plan(daily, 4) == [102]
        assert get_room_plan_version(daily, DAY) != get_room_plan_version(empty, DAY)
