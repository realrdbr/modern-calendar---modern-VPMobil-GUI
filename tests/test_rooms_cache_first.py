from datetime import date, timedelta
from threading import Event, Thread
from unittest.mock import patch

import pytest
import vp_data
from rooms_page import get_room_plan_version
from vpmobil.models import Stunde, Vertretungsplan

DAY = date(2026, 9, 10)


def make_plan(room):
    return Vertretungsplan(datum=DAY, stunden=[Stunde(periode=4, fach='MA', räume=(room,), klassen=('11',))])


@pytest.fixture
def background(monkeypatch):
    threads = []
    release = Event()
    def spawn(**kwargs):
        thread = Thread(**kwargs)
        threads.append(thread)
        return thread
    monkeypatch.setattr(vp_data, '_room_plan_cache', {})
    monkeypatch.setattr(vp_data, '_room_plan_refreshing', set())
    monkeypatch.setattr(vp_data, '_page_plan_cache', {})
    monkeypatch.setattr(vp_data, '_weekly_plan_cache', {})
    monkeypatch.setattr(vp_data, 'load_plan_from_cache', lambda day: None)
    monkeypatch.setattr(vp_data, '_load_weekly_cache', lambda day: None)
    monkeypatch.setattr(vp_data, 'Thread', spawn)
    yield threads, release
    release.set()
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()


def test_cached_page_returns_while_refresh_is_still_loading(background):
    threads, release = background
    old, fresh = make_plan('101'), make_plan('102')
    started = Event()
    vp_data._room_plan_cache[DAY] = (0, old)
    def fetch(day):
        started.set()
        assert release.wait(2)
        return fresh
    with patch.object(vp_data, 'fetch_room_plan', side_effect=fetch) as fetch_mock:
        assert vp_data.get_room_plan_for_page(DAY) is old
        assert started.wait(1)
        assert vp_data.get_room_plan_for_page(DAY) is old
        assert vp_data.get_room_plan_for_page(DAY, refresh=False) is old
        assert len(threads) == 1
        # A slow request for one date must not hold a lock needed by another.
        assert vp_data.get_room_plan_for_page(DAY + timedelta(days=1), refresh=False) is None
        release.set()
        threads[0].join(2)
        assert vp_data.get_room_plan_for_page(DAY, refresh=False) is fresh
        assert get_room_plan_version(old, DAY) != get_room_plan_version(fresh, DAY)
        assert fetch_mock.call_count == 1


def test_cold_page_returns_loading_then_exposes_finished_plan(background):
    threads, release = background
    fresh = make_plan('101')
    def fetch(day):
        assert release.wait(2)
        return fresh
    with patch.object(vp_data, 'fetch_room_plan', side_effect=fetch):
        assert vp_data.get_room_plan_for_page(DAY) is None
        release.set()
        threads[0].join(2)
        assert vp_data.get_room_plan_for_page(DAY, refresh=False) is fresh


def test_each_new_visit_refreshes_but_version_poll_does_not(background):
    threads, _ = background
    fresh = make_plan('101')
    vp_data._room_plan_cache[DAY] = (0, fresh)
    with patch.object(vp_data, 'fetch_room_plan', return_value=fresh) as fetch:
        for _ in range(2):
            assert vp_data.get_room_plan_for_page(DAY) is fresh
            threads[-1].join(2)
        for _ in range(5):
            assert vp_data.get_room_plan_for_page(DAY, refresh=False) is fresh
        assert fetch.call_count == 2


def test_failed_background_refresh_preserves_cached_plan(background):
    threads, _ = background
    old = make_plan('101')
    vp_data._room_plan_cache[DAY] = (0, old)
    with patch.object(vp_data, 'fetch_room_plan', side_effect=TimeoutError('offline')):
        assert vp_data.get_room_plan_for_page(DAY) is old
        threads[0].join(2)
        assert vp_data.get_room_plan_for_page(DAY, refresh=False) is old
        assert DAY not in vp_data._room_plan_refreshing


def test_disk_daily_cache_takes_priority_over_weekly_cache(background):
    daily, normal = make_plan('101'), make_plan('102')
    with patch.object(vp_data, 'load_plan_from_cache', return_value=daily), \
         patch.object(vp_data, '_load_weekly_cache', return_value={DAY: normal}) as weekly, \
         patch.object(vp_data, 'fetch_room_plan') as fetch:
        assert vp_data.get_room_plan_for_page(DAY, refresh=False) is daily
        weekly.assert_not_called()
        fetch.assert_not_called()


def test_cached_normal_plan_can_be_shown_without_network(background):
    normal = make_plan('102')
    with patch.object(vp_data, '_load_weekly_cache', return_value={DAY: normal}), \
         patch.object(vp_data, 'fetch_room_plan') as fetch:
        assert vp_data.get_room_plan_for_page(DAY, refresh=False) is normal
        fetch.assert_not_called()
