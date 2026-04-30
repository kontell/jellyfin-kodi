# -*- coding: utf-8 -*-
from __future__ import division, absolute_import, print_function, unicode_literals

import pytest

from jellyfin_kodi.syncplay.api import SyncPlayApi


class FakeApiClient(object):
    """Records every _get/_post/_delete call for assertion."""

    def __init__(self):
        self.calls = []
        self.next_response = None

    def _get(self, handler, params=None):
        self.calls.append(("GET", handler, params, None))
        return self.next_response

    def _post(self, handler, json=None, params=None):
        self.calls.append(("POST", handler, params, json))
        return self.next_response

    def _delete(self, handler, params=None):
        self.calls.append(("DELETE", handler, params, None))
        return self.next_response


@pytest.fixture
def api():
    return SyncPlayApi(FakeApiClient())


@pytest.fixture
def fake(api):
    return api._api


# ----------------------------------------------------------------------
# Group lifecycle
# ----------------------------------------------------------------------


def test_create_group_posts_name(api, fake):
    api.create_group("Movie night")
    assert fake.calls == [("POST", "SyncPlay/New", None, {"GroupName": "Movie night"})]


def test_join_group_posts_id(api, fake):
    api.join_group("abc-123")
    assert fake.calls == [("POST", "SyncPlay/Join", None, {"GroupId": "abc-123"})]


def test_leave_group_posts_no_body(api, fake):
    api.leave_group()
    assert fake.calls == [("POST", "SyncPlay/Leave", None, None)]


def test_list_groups_returns_server_response(api, fake):
    fake.next_response = [{"GroupId": "g1"}, {"GroupId": "g2"}]
    result = api.list_groups()
    assert result == [{"GroupId": "g1"}, {"GroupId": "g2"}]
    assert fake.calls == [("GET", "SyncPlay/List", None, None)]


def test_get_group_targets_id(api, fake):
    api.get_group("abc-123")
    assert fake.calls == [("GET", "SyncPlay/abc-123", None, None)]


# ----------------------------------------------------------------------
# Queue management
# ----------------------------------------------------------------------


def test_set_new_queue_serializes_ints(api, fake):
    api.set_new_queue(("item-1", "item-2"), position=1, start_position_ticks=12345)
    assert fake.calls == [
        (
            "POST",
            "SyncPlay/SetNewQueue",
            None,
            {
                "PlayingQueue": ["item-1", "item-2"],
                "PlayingItemPosition": 1,
                "StartPositionTicks": 12345,
            },
        )
    ]


def test_set_playlist_item(api, fake):
    api.set_playlist_item("pli-1")
    assert fake.calls == [
        ("POST", "SyncPlay/SetPlaylistItem", None, {"PlaylistItemId": "pli-1"})
    ]


def test_remove_from_playlist_defaults(api, fake):
    api.remove_from_playlist(["a", "b"])
    assert fake.calls == [
        (
            "POST",
            "SyncPlay/RemoveFromPlaylist",
            None,
            {
                "PlaylistItemIds": ["a", "b"],
                "ClearPlaylist": False,
                "ClearPlayingItem": False,
            },
        )
    ]


def test_move_playlist_item(api, fake):
    api.move_playlist_item("pli-1", 3)
    assert fake.calls == [
        (
            "POST",
            "SyncPlay/MovePlaylistItem",
            None,
            {"PlaylistItemId": "pli-1", "NewIndex": 3},
        )
    ]


def test_queue_default_mode(api, fake):
    api.queue(["a"])
    assert fake.calls == [
        ("POST", "SyncPlay/Queue", None, {"ItemIds": ["a"], "Mode": "Default"})
    ]


def test_set_repeat_mode(api, fake):
    api.set_repeat_mode("RepeatAll")
    assert fake.calls == [
        ("POST", "SyncPlay/SetRepeatMode", None, {"Mode": "RepeatAll"})
    ]


def test_set_shuffle_mode(api, fake):
    api.set_shuffle_mode("Sorted")
    assert fake.calls == [("POST", "SyncPlay/SetShuffleMode", None, {"Mode": "Sorted"})]


# ----------------------------------------------------------------------
# Playback control
# ----------------------------------------------------------------------


def test_pause(api, fake):
    api.pause()
    assert fake.calls == [("POST", "SyncPlay/Pause", None, None)]


def test_unpause(api, fake):
    api.unpause()
    assert fake.calls == [("POST", "SyncPlay/Unpause", None, None)]


def test_stop(api, fake):
    api.stop()
    assert fake.calls == [("POST", "SyncPlay/Stop", None, None)]


def test_seek_serializes_ticks_as_int(api, fake):
    api.seek(123456789012)
    assert fake.calls == [
        ("POST", "SyncPlay/Seek", None, {"PositionTicks": 123456789012})
    ]


# ----------------------------------------------------------------------
# Client state reporting
# ----------------------------------------------------------------------


def test_buffering_payload(api, fake):
    api.buffering(
        when_iso="2024-01-01T00:00:00Z",
        position_ticks=42_000_000,
        is_playing=True,
        playlist_item_id="pli-99",
    )
    assert fake.calls == [
        (
            "POST",
            "SyncPlay/Buffering",
            None,
            {
                "When": "2024-01-01T00:00:00Z",
                "PositionTicks": 42_000_000,
                "IsPlaying": True,
                "PlaylistItemId": "pli-99",
            },
        )
    ]


def test_ready_payload(api, fake):
    api.ready(
        when_iso="2024-01-01T00:00:00Z",
        position_ticks=0,
        is_playing=False,
        playlist_item_id="pli-99",
    )
    assert fake.calls == [
        (
            "POST",
            "SyncPlay/Ready",
            None,
            {
                "When": "2024-01-01T00:00:00Z",
                "PositionTicks": 0,
                "IsPlaying": False,
                "PlaylistItemId": "pli-99",
            },
        )
    ]


def test_set_ignore_wait(api, fake):
    api.set_ignore_wait(True)
    assert fake.calls == [
        ("POST", "SyncPlay/SetIgnoreWait", None, {"IgnoreWait": True})
    ]


def test_ping_serializes_int(api, fake):
    api.ping(42.7)
    assert fake.calls == [("POST", "SyncPlay/Ping", None, {"Ping": 42})]
