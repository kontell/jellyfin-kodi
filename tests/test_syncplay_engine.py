# -*- coding: utf-8 -*-
from __future__ import division, absolute_import, print_function, unicode_literals

from datetime import datetime, timezone

import pytest

from jellyfin_kodi.syncplay.engine import (
    DRIFT_SEEK_S,
    SYNC_TOLERANCE_S,
    TICKS_PER_SECOND,
    SyncEngine,
    render_badge_state,
    render_badge_top,
)
from jellyfin_kodi.syncplay import controller as controller_mod

# ----------------------------------------------------------------------
# Test doubles
# ----------------------------------------------------------------------


class FakeApi(object):
    def __init__(self):
        self.calls = []

    def pause(self):
        self.calls.append(("pause",))

    def unpause(self):
        self.calls.append(("unpause",))

    def seek(self, position_ticks):
        self.calls.append(("seek", position_ticks))

    def stop(self):
        self.calls.append(("stop",))

    def buffering(self, when_iso, position_ticks, is_playing, playlist_item_id):
        self.calls.append(
            ("buffering", when_iso, position_ticks, is_playing, playlist_item_id)
        )

    def ready(self, when_iso, position_ticks, is_playing, playlist_item_id):
        self.calls.append(
            ("ready", when_iso, position_ticks, is_playing, playlist_item_id)
        )


class FakeClock(object):
    def now_utc(self):
        return datetime(2024, 1, 1, tzinfo=timezone.utc)


class FakeController(object):
    def __init__(self, in_group=True, group_info=None, state=None):
        self.api = FakeApi()
        self.clock = FakeClock()
        self.in_group = in_group
        self.group_info = group_info or {}
        self.state = state


class FakeScheduler(object):
    """Records (deadline, action) and runs the action immediately when invoked."""

    def __init__(self):
        self.scheduled = []

    def run_at(self, deadline, action):
        self.scheduled.append((deadline, action))
        action()
        return None


class FakePlayer(object):
    def __init__(self, playing=True, position=0.0, item_id=None, tempo_supported=True):
        self.playing = playing
        self.position = position
        self.item_id = item_id
        self.tempo_supported = tempo_supported
        self.calls = []

    def is_playing(self):
        return self.playing

    def get_time(self):
        return self.position

    def get_playing_item_id(self):
        return self.item_id

    def pause(self):
        self.calls.append(("pause",))
        self.playing = False

    def unpause(self):
        self.calls.append(("unpause",))
        self.playing = True

    def seek_seconds(self, seconds):
        self.calls.append(("seek", seconds))
        self.position = seconds

    def stop(self):
        self.calls.append(("stop",))
        self.playing = False

    def set_tempo(self, tempo):
        self.calls.append(("set_tempo", tempo))
        return self.tempo_supported


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


@pytest.fixture
def make_engine():
    def _make(player=None, controller=None, monotonic=None):
        controller = controller or FakeController()
        player = player if player is not None else FakePlayer()
        scheduler = FakeScheduler()
        engine = SyncEngine(
            controller,
            player=player,
            scheduler=scheduler,
            monotonic=monotonic or (lambda: 100.0),
        )
        return engine, controller, player, scheduler

    return _make


# ----------------------------------------------------------------------
# Inbound command dispatch
# ----------------------------------------------------------------------


def test_on_command_pause_schedules_player_pause(make_engine):
    engine, controller, player, scheduler = make_engine()
    engine.on_command("Pause", deadline_monotonic=200.0, position_ticks=None, raw={})

    assert ("pause",) in player.calls
    assert scheduler.scheduled == [(200.0, scheduler.scheduled[0][1])]


def test_on_command_unpause_seeks_then_unpauses(make_engine):
    engine, controller, player, scheduler = make_engine()
    engine.on_command(
        "Unpause", deadline_monotonic=200.0, position_ticks=5 * TICKS_PER_SECOND, raw={}
    )

    # Seek must precede unpause so playback resumes at the right position.
    assert player.calls == [("seek", 5.0), ("unpause",)]


def test_on_command_seek_calls_player(make_engine):
    engine, _, player, _ = make_engine()
    engine.on_command(
        "Seek", deadline_monotonic=200.0, position_ticks=42 * TICKS_PER_SECOND, raw={}
    )

    assert player.calls == [("seek", 42.0)]


def test_on_command_stop_calls_player(make_engine):
    engine, _, player, _ = make_engine()
    engine.on_command("Stop", deadline_monotonic=200.0, position_ticks=None, raw={})

    assert player.calls == [("stop",)]


def test_on_command_unknown_is_logged_not_executed(make_engine):
    engine, _, player, scheduler = make_engine()
    engine.on_command(
        "BogusCommand", deadline_monotonic=200.0, position_ticks=None, raw={}
    )

    assert player.calls == []
    assert scheduler.scheduled == []


# ----------------------------------------------------------------------
# Outbound echo (local player events -> server)
# ----------------------------------------------------------------------


def test_on_local_pause_echoes_to_server(make_engine):
    engine, controller, _, _ = make_engine()
    engine.on_local_pause()

    assert controller.api.calls == [("pause",)]


def test_engine_driven_pause_does_not_echo(make_engine):
    engine, controller, player, _ = make_engine()

    # Server told us to pause; the echo from the player event should not loop back.
    engine.on_command("Pause", deadline_monotonic=100.0, position_ticks=None, raw={})
    assert player.calls == [("pause",)]
    assert controller.api.calls == []  # no echo

    engine.on_local_pause()
    assert controller.api.calls == []  # still no echo (suppression window active)


def test_on_local_seek_echoes_with_ticks(make_engine):
    engine, controller, _, _ = make_engine()
    engine.on_local_seek(12.5)

    assert controller.api.calls == [("seek", int(12.5 * TICKS_PER_SECOND))]


def test_outside_group_no_echo(make_engine):
    engine, controller, _, _ = make_engine(controller=FakeController(in_group=False))
    engine.on_local_pause()
    engine.on_local_resume()
    engine.on_local_seek(10.0)

    assert controller.api.calls == []


def test_echo_suppression_expires(monkeypatch, make_engine):
    now = [100.0]

    def monotonic():
        return now[0]

    engine, controller, _, _ = make_engine(monotonic=monotonic)

    # Server-driven pause sets a 1s suppression window starting at 100.0.
    engine.on_command("Pause", deadline_monotonic=100.0, position_ticks=None, raw={})
    controller.api.calls.clear()  # ignore any setup noise

    # A user pause arriving 0.5s later still falls inside the window.
    now[0] = 100.5
    engine.on_local_pause()
    assert controller.api.calls == []

    # A user pause arriving 1.5s later is outside the window and echoes.
    now[0] = 101.5
    engine.on_local_pause()
    assert controller.api.calls == [("pause",)]


# ----------------------------------------------------------------------
# Buffer/Ready handshake
# ----------------------------------------------------------------------


def test_on_local_play_started_posts_buffer_then_ready(make_engine):
    engine, controller, _, _ = make_engine()
    engine.on_local_play_started({"Id": "abc-123"})

    kinds = [c[0] for c in controller.api.calls]
    assert kinds == ["buffering", "ready"]
    # Both should carry the playlist item id known so far (None until PlayQueue arrives)
    assert controller.api.calls[0][4] is None


def test_play_started_uses_playlist_item_id_from_queue_update(make_engine):
    engine, controller, _, _ = make_engine()
    engine.on_group_update(
        controller_mod.UPDATE_PLAY_QUEUE,
        payload={
            "Playlist": [
                {"ItemId": "abc-123", "PlaylistItemId": "pli-xyz"},
            ],
            "PlayingItemIndex": 0,
            "StartPositionTicks": 0,
            "IsPlaying": True,
        },
        raw={},
    )
    engine.on_local_play_started({"Id": "abc-123"})

    assert controller.api.calls[0][4] == "pli-xyz"


# ----------------------------------------------------------------------
# Play queue auto-load
# ----------------------------------------------------------------------


def test_play_queue_update_invokes_item_loader(make_engine):
    loaded = []

    def loader(item_id, ticks):
        loaded.append((item_id, ticks))

    controller = FakeController()
    engine = SyncEngine(
        controller,
        player=FakePlayer(item_id=None),
        scheduler=FakeScheduler(),
        monotonic=lambda: 100.0,
        item_loader=loader,
    )
    engine.on_group_update(
        controller_mod.UPDATE_PLAY_QUEUE,
        payload={
            "Playlist": [{"ItemId": "abc-123", "PlaylistItemId": "pli-1"}],
            "PlayingItemIndex": 0,
            "StartPositionTicks": 99 * TICKS_PER_SECOND,
            "IsPlaying": True,
        },
        raw={},
    )

    assert loaded == [("abc-123", 99 * TICKS_PER_SECOND)]


def test_play_queue_skips_loader_if_already_playing_item(make_engine):
    loaded = []

    def loader(item_id, ticks):
        loaded.append((item_id, ticks))

    controller = FakeController()
    engine = SyncEngine(
        controller,
        player=FakePlayer(item_id="abc-123"),
        scheduler=FakeScheduler(),
        monotonic=lambda: 100.0,
        item_loader=loader,
    )
    engine.on_group_update(
        controller_mod.UPDATE_PLAY_QUEUE,
        payload={
            "Playlist": [{"ItemId": "abc-123", "PlaylistItemId": "pli-1"}],
            "PlayingItemIndex": 0,
            "StartPositionTicks": 0,
            "IsPlaying": True,
        },
        raw={},
    )

    assert loaded == []


def test_play_queue_handles_empty_playlist(make_engine):
    engine, controller, _, _ = make_engine()
    engine.on_group_update(
        controller_mod.UPDATE_PLAY_QUEUE,
        payload={"Playlist": [], "PlayingItemIndex": 0},
        raw={},
    )
    # Should not crash and should not call any loader.
    assert controller.api.calls == []


# ----------------------------------------------------------------------
# Drift correction
# ----------------------------------------------------------------------


def test_drift_within_tolerance_does_nothing():
    controller = FakeController()
    player = FakePlayer(playing=True, position=10.0)
    engine = SyncEngine(
        controller,
        player=player,
        scheduler=FakeScheduler(),
        monotonic=lambda: 100.0,
    )

    # Set a target: position 10s, anchored now, playing.
    engine._target_position_ticks = 10 * TICKS_PER_SECOND
    engine._target_anchor_monotonic = 100.0
    engine._target_is_playing = True

    engine._tick()

    # Player at 10.0, expected 10.0, drift 0 → no action.
    assert player.calls == []


def test_drift_above_seek_threshold_corrects():
    controller = FakeController()
    player = FakePlayer(playing=True, position=20.0)
    engine = SyncEngine(
        controller,
        player=player,
        scheduler=FakeScheduler(),
        monotonic=lambda: 100.0,
    )
    engine._target_position_ticks = 10 * TICKS_PER_SECOND
    engine._target_anchor_monotonic = 100.0
    engine._target_is_playing = True

    # Player is 10s ahead of expected. Above DRIFT_SEEK_S → corrective seek.
    assert abs(20.0 - 10.0) >= DRIFT_SEEK_S
    engine._tick()
    assert player.calls == [("seek", pytest.approx(10.0))]


def test_drift_between_tolerance_and_seek_uses_tempo_when_unsupported_falls_through():
    """Mid-range drift on a stream that rejects tempo never reaches the seek path."""
    controller = FakeController()
    player = FakePlayer(playing=True, position=11.0, tempo_supported=False)
    engine = SyncEngine(
        controller,
        player=player,
        scheduler=FakeScheduler(),
        monotonic=lambda: 100.0,
    )
    engine._target_position_ticks = 10 * TICKS_PER_SECOND
    engine._target_anchor_monotonic = 100.0
    engine._target_is_playing = True

    # Drift = 1s — above SYNC_TOLERANCE_S but below DRIFT_SEEK_S.
    assert SYNC_TOLERANCE_S <= 1.0 < DRIFT_SEEK_S
    engine._tick()
    # The probe is attempted; nothing else.
    assert player.calls == [("set_tempo", 1.0)]


def test_drift_loop_skips_when_not_playing():
    controller = FakeController()
    player = FakePlayer(playing=False)
    engine = SyncEngine(
        controller,
        player=player,
        scheduler=FakeScheduler(),
        monotonic=lambda: 100.0,
    )
    engine._target_position_ticks = 10 * TICKS_PER_SECOND
    engine._target_anchor_monotonic = 100.0
    engine._target_is_playing = True

    engine._tick()
    assert player.calls == []


# ----------------------------------------------------------------------
# Badge rendering helpers (pure)
# ----------------------------------------------------------------------


def test_render_badge_top_with_members():
    assert render_badge_top("Movie night", 3) == "Movie night · 3"


def test_render_badge_top_zero_members():
    assert render_badge_top("Solo", 0) == "Solo"


def test_render_badge_top_default_name():
    assert render_badge_top(None, 2) == "Watch group · 2"


def test_render_badge_state_synced():
    assert (
        render_badge_state(controller_mod.STATE_PLAYING, drift_ms=42) == "Synced ±42 ms"
    )


def test_render_badge_state_catching_up():
    # Above tolerance, below seek threshold.
    drift_ms = (SYNC_TOLERANCE_S * 1000) + 100
    assert (
        render_badge_state(controller_mod.STATE_PLAYING, drift_ms=drift_ms)
        == "Catching up..."
    )


def test_render_badge_state_resyncing():
    drift_ms = (DRIFT_SEEK_S * 1000) + 500
    assert (
        render_badge_state(controller_mod.STATE_PLAYING, drift_ms=drift_ms)
        == "Resyncing..."
    )


def test_render_badge_state_paused():
    assert render_badge_state(controller_mod.STATE_PAUSED, drift_ms=None) == "Paused"


def test_render_badge_state_waiting():
    assert (
        render_badge_state(controller_mod.STATE_WAITING, drift_ms=None)
        == "Waiting for members..."
    )


def test_render_badge_state_unknown_returns_empty():
    assert render_badge_state(None, drift_ms=42) == ""


# ----------------------------------------------------------------------
# Tempo-based drift correction
# ----------------------------------------------------------------------


def _seed_target(engine, ticks=10 * TICKS_PER_SECOND, anchor=100.0):
    engine._target_position_ticks = ticks
    engine._target_anchor_monotonic = anchor
    engine._target_is_playing = True


def test_mid_band_drift_applies_tempo_when_supported(make_engine):
    # Player is 1.0s ahead of expected: tempo should pull it back.
    engine, _, player, _ = make_engine(
        player=FakePlayer(playing=True, position=11.0, tempo_supported=True)
    )
    _seed_target(engine)

    engine._tick()

    # First call is the probe (tempo=1.0); second is the corrective tempo.
    tempo_calls = [c for c in player.calls if c[0] == "set_tempo"]
    assert len(tempo_calls) == 2
    assert tempo_calls[0] == ("set_tempo", 1.0)
    # Drift = +1.0 / 5.0 convergence horizon = -0.2 offset, clamped to 0.95.
    assert tempo_calls[1] == ("set_tempo", pytest.approx(0.95))
    # No corrective seek in this band.
    assert ("seek", pytest.approx(10.0)) not in player.calls


def test_mid_band_drift_falls_through_when_tempo_unsupported(make_engine):
    engine, _, player, _ = make_engine(
        player=FakePlayer(playing=True, position=11.0, tempo_supported=False)
    )
    _seed_target(engine)

    engine._tick()

    # Probe attempt at 1.0 fails; no corrective tempo applied; no seek
    # because we're not over the seek threshold.
    tempo_calls = [c for c in player.calls if c[0] == "set_tempo"]
    assert tempo_calls == [("set_tempo", 1.0)]
    assert all(c[0] != "seek" for c in player.calls)


def test_returning_to_tolerance_resets_tempo(make_engine):
    engine, _, player, _ = make_engine(
        player=FakePlayer(playing=True, position=11.0, tempo_supported=True)
    )
    _seed_target(engine)

    # First tick applies tempo.
    engine._tick()
    player.calls.clear()

    # Now drift returns to tolerance.
    player.position = 10.05
    engine._tick()

    # Tempo should be restored to 1.0.
    assert ("set_tempo", 1.0) in player.calls


def test_seek_threshold_resets_tempo(make_engine):
    engine, _, player, _ = make_engine(
        player=FakePlayer(playing=True, position=20.0, tempo_supported=True)
    )
    _seed_target(engine)
    # Pre-arm a non-1.0 tempo so we can observe the reset.
    engine._tempo_supported = True
    engine._tempo_active = 0.97

    engine._tick()

    # Should seek and reset tempo to 1.0.
    assert ("seek", pytest.approx(10.0)) in player.calls
    assert ("set_tempo", 1.0) in player.calls


def test_play_started_re_probes_tempo(make_engine):
    engine, controller, player, _ = make_engine(
        player=FakePlayer(playing=True, position=11.0, tempo_supported=True)
    )
    # Cache a stale "unsupported" probe.
    engine._tempo_supported = False
    _seed_target(engine)

    # New playback session.
    engine.on_local_play_started({"Id": "abc"})
    # Cache should be reset.
    assert engine._tempo_supported is None

    # And a tick now re-probes successfully.
    engine._tick()
    tempo_calls = [c for c in player.calls if c[0] == "set_tempo"]
    assert tempo_calls and tempo_calls[0] == ("set_tempo", 1.0)


def test_tempo_clamped_to_min_max(make_engine):
    # Drift large enough to push tempo well beyond the clamp.
    engine, _, player, _ = make_engine(
        player=FakePlayer(playing=True, position=10.0 + 1.5, tempo_supported=True)
    )
    _seed_target(engine)

    engine._tick()

    tempo_calls = [c for c in player.calls if c[0] == "set_tempo"]
    # Probe + clamped tempo applied.
    assert tempo_calls[-1][1] == pytest.approx(0.95)
