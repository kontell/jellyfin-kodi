# -*- coding: utf-8 -*-
from __future__ import division, absolute_import, print_function, unicode_literals

import threading
import time

from ..helper import LazyLogger
from .clock import format_utc
from .controller import (
    CMD_PAUSE,
    CMD_SEEK,
    CMD_STOP,
    CMD_UNPAUSE,
    UPDATE_GROUP_JOINED,
    UPDATE_PLAY_QUEUE,
)

LOG = LazyLogger(__name__)


# Drift thresholds.
# - SYNC_TOLERANCE_S: acceptable drift; below this the engine is silent.
# - DRIFT_SEEK_S: above this absolute drift, the engine does a corrective seek.
SYNC_TOLERANCE_S = 0.5
DRIFT_SEEK_S = 2.0
DRIFT_TICK_S = 0.5

# 100-ns ticks per second (Jellyfin's PositionTicks unit).
TICKS_PER_SECOND = 10_000_000


# ----------------------------------------------------------------------
# Scheduler
# ----------------------------------------------------------------------


class _ThreadScheduler(object):
    """Production scheduler: each scheduled action gets its own daemon thread."""

    def __init__(self, monotonic=None):
        self._monotonic = monotonic or time.monotonic

    def run_at(self, deadline_monotonic, action):
        thread = threading.Thread(
            target=self._runner, args=(deadline_monotonic, action), daemon=True
        )
        thread.start()
        return thread

    def _runner(self, deadline, action):
        try:
            self._wait_until(deadline)
            action()
        except Exception as error:
            LOG.exception("Scheduled action failed: %s", error)

    def _wait_until(self, deadline):
        if deadline is None:
            return
        try:
            import xbmc

            monitor = xbmc.Monitor()
        except Exception:
            monitor = None
        while True:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return
            slice_s = min(remaining, 0.05)
            if monitor is not None:
                if monitor.waitForAbort(slice_s):
                    return
            else:
                time.sleep(slice_s)


# ----------------------------------------------------------------------
# Player proxy
# ----------------------------------------------------------------------


class PlayerProxy(object):
    """Abstract over xbmc.Player for testability.

    Implementations must be thread-safe; the engine calls these from its
    scheduler threads as well as the WebSocket dispatch thread.
    """

    def is_playing(self):
        raise NotImplementedError

    def get_time(self):
        """Current playback position in seconds (float)."""
        raise NotImplementedError

    def get_playing_item_id(self):
        """Jellyfin item ID currently playing, or None if not in a Jellyfin item."""
        raise NotImplementedError

    def pause(self):
        raise NotImplementedError

    def unpause(self):
        raise NotImplementedError

    def seek_seconds(self, seconds):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError


class XbmcPlayerProxy(PlayerProxy):
    """Production implementation backed by xbmc.Player and JSON-RPC."""

    def __init__(self):
        import xbmc  # noqa: F401  -- imported lazily so tests can fake it

    def _player(self):
        import xbmc

        return xbmc.Player()

    def is_playing(self):
        try:
            return bool(self._player().isPlaying())
        except Exception:
            return False

    def get_time(self):
        try:
            return float(self._player().getTime())
        except Exception:
            return 0.0

    def get_playing_item_id(self):
        # The active player publishes the current ItemId via window properties.
        try:
            from ..helper import window

            items = window("jellyfin_play.json") or []
        except Exception:
            return None
        for item in items:
            if isinstance(item, dict) and "Id" in item:
                return item["Id"]
        return None

    def pause(self):
        from ..helper import JSONRPC

        JSONRPC("Player.PlayPause").execute({"playerid": 1, "play": False})

    def unpause(self):
        from ..helper import JSONRPC

        JSONRPC("Player.PlayPause").execute({"playerid": 1, "play": True})

    def seek_seconds(self, seconds):
        try:
            self._player().seekTime(float(seconds))
        except Exception as error:
            LOG.warning("seekTime(%s) failed: %s", seconds, error)

    def stop(self):
        try:
            self._player().stop()
        except Exception as error:
            LOG.warning("stop() failed: %s", error)


# ----------------------------------------------------------------------
# SyncEngine
# ----------------------------------------------------------------------


class SyncEngine(object):
    """Drives the local Kodi player to honour SyncPlay group commands.

    The controller calls :meth:`on_command` and :meth:`on_group_update` for
    every inbound WebSocket message. The Player calls
    :meth:`on_local_play_started` and friends for every outbound playback
    event. The engine bridges between them, posts ``Buffering``/``Ready``
    handshakes, and runs a drift-correction tick loop.

    Phase 3 scope: corrective seek only. Tempo-based smoothing lands in a
    follow-up.
    """

    def __init__(
        self,
        controller,
        player=None,
        scheduler=None,
        monotonic=None,
        item_loader=None,
    ):
        self._controller = controller
        self._player = player or XbmcPlayerProxy()
        self._scheduler = scheduler or _ThreadScheduler(monotonic=monotonic)
        self._monotonic = monotonic or time.monotonic
        self._item_loader = item_loader  # callable(item_id, position_ticks) -> None

        self._lock = threading.Lock()
        self._suppress_echo_until = 0.0  # monotonic deadline for echo suppression
        self._current_playlist_item_id = None
        self._current_item_id = None
        self._is_playing_locally = False
        self._target_position_ticks = None  # last server-known position
        self._target_anchor_monotonic = None  # local monotonic when target was set
        self._target_is_playing = False

        self._drift_thread = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        if self._drift_thread is not None and self._drift_thread.is_alive():
            return
        self._stop_event.clear()
        self._drift_thread = threading.Thread(
            target=self._drift_loop, name="SyncPlayDrift", daemon=True
        )
        self._drift_thread.start()

    def stop(self):
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Inbound: server-pushed commands and group updates
    # ------------------------------------------------------------------

    def on_command(self, cmd, deadline_monotonic, position_ticks, raw):
        """Server pushed a SendCommand. Schedule the action at deadline."""
        LOG.info(
            "engine on_command cmd=%s deadline=%s pos=%s",
            cmd,
            deadline_monotonic,
            position_ticks,
        )

        if cmd == CMD_PAUSE:
            self._scheduler.run_at(deadline_monotonic, self._fire_pause)
        elif cmd == CMD_UNPAUSE:
            self._scheduler.run_at(
                deadline_monotonic,
                lambda: self._fire_unpause(position_ticks),
            )
        elif cmd == CMD_SEEK:
            self._scheduler.run_at(
                deadline_monotonic,
                lambda: self._fire_seek(position_ticks),
            )
        elif cmd == CMD_STOP:
            self._scheduler.run_at(deadline_monotonic, self._fire_stop)
        else:
            LOG.warning("Unknown SyncPlay command: %s", cmd)

    def on_group_update(self, update_type, payload, raw):
        """Server pushed a SyncPlayGroupUpdate."""
        if update_type == UPDATE_GROUP_JOINED:
            self.start()
        elif update_type == UPDATE_PLAY_QUEUE:
            self._handle_play_queue_update(payload or {})

    def _handle_play_queue_update(self, payload):
        playlist = payload.get("Playlist") or []
        idx = payload.get("PlayingItemIndex")
        if idx is None or idx < 0 or idx >= len(playlist):
            return
        item = playlist[idx] or {}
        item_id = item.get("ItemId")
        playlist_item_id = item.get("PlaylistItemId")
        start_ticks = payload.get("StartPositionTicks") or 0
        is_playing = bool(payload.get("IsPlaying"))

        with self._lock:
            self._current_playlist_item_id = playlist_item_id
            self._target_position_ticks = start_ticks
            self._target_anchor_monotonic = self._monotonic()
            self._target_is_playing = is_playing

        if not item_id:
            return

        # Skip the handoff if Kodi is already playing this exact item.
        currently_playing = self._player.get_playing_item_id()
        if currently_playing == item_id:
            with self._lock:
                self._current_item_id = item_id
            return

        with self._lock:
            self._current_item_id = item_id

        if self._item_loader is not None:
            try:
                self._item_loader(item_id, start_ticks)
            except Exception as error:
                LOG.exception("item_loader failed for %s: %s", item_id, error)

    # ------------------------------------------------------------------
    # Inbound: local Player events
    # ------------------------------------------------------------------

    def on_local_play_started(self, item):
        """Kodi just loaded a media file. Send Buffering -> Ready to the server."""
        item_id = item.get("Id")
        if item_id is None:
            return
        with self._lock:
            self._current_item_id = item_id
            self._is_playing_locally = True
        self._post_state(is_playing=True, position_seconds=0.0, kind="buffer")
        # Once the player reports a non-zero position the controller can post
        # Ready. For Phase 3 we post Ready immediately after Buffer; the server
        # gates the actual unpause via SendCommand.When.
        self._post_state(is_playing=True, position_seconds=0.0, kind="ready")

    def on_local_pause(self):
        if self._echo_suppressed():
            return
        if not self._in_group():
            return
        try:
            self._controller.api.pause()
        except Exception as error:
            LOG.warning("pause echo failed: %s", error)

    def on_local_resume(self):
        if self._echo_suppressed():
            return
        if not self._in_group():
            return
        try:
            self._controller.api.unpause()
        except Exception as error:
            LOG.warning("unpause echo failed: %s", error)

    def on_local_seek(self, position_seconds):
        if self._echo_suppressed():
            return
        if not self._in_group():
            return
        try:
            self._controller.api.seek(int(position_seconds * TICKS_PER_SECOND))
        except Exception as error:
            LOG.warning("seek echo failed: %s", error)

    def on_local_stopped(self):
        with self._lock:
            self._is_playing_locally = False

    # ------------------------------------------------------------------
    # Internal: command execution
    # ------------------------------------------------------------------

    def _fire_pause(self):
        self._with_suppressed_echo(self._player.pause)
        with self._lock:
            self._target_is_playing = False
            self._target_anchor_monotonic = self._monotonic()

    def _fire_unpause(self, position_ticks):
        if position_ticks is not None:
            self._with_suppressed_echo(
                lambda: self._player.seek_seconds(position_ticks / TICKS_PER_SECOND)
            )
        self._with_suppressed_echo(self._player.unpause)
        with self._lock:
            self._target_is_playing = True
            self._target_position_ticks = position_ticks
            self._target_anchor_monotonic = self._monotonic()

    def _fire_seek(self, position_ticks):
        if position_ticks is None:
            return
        self._with_suppressed_echo(
            lambda: self._player.seek_seconds(position_ticks / TICKS_PER_SECOND)
        )
        with self._lock:
            self._target_position_ticks = position_ticks
            self._target_anchor_monotonic = self._monotonic()

    def _fire_stop(self):
        self._with_suppressed_echo(self._player.stop)
        with self._lock:
            self._is_playing_locally = False
            self._target_is_playing = False

    # ------------------------------------------------------------------
    # Drift tick loop
    # ------------------------------------------------------------------

    def _drift_loop(self):
        try:
            import xbmc

            monitor = xbmc.Monitor()
        except Exception:
            monitor = None

        while not self._stop_event.is_set() and self._in_group():
            self._tick()
            if monitor is not None:
                if monitor.waitForAbort(DRIFT_TICK_S):
                    break
            else:
                if self._stop_event.wait(DRIFT_TICK_S):
                    break

    def _tick(self):
        if not self._player.is_playing():
            return
        with self._lock:
            target_ticks = self._target_position_ticks
            target_anchor = self._target_anchor_monotonic
            target_is_playing = self._target_is_playing
        if target_ticks is None or target_anchor is None or not target_is_playing:
            return

        elapsed = self._monotonic() - target_anchor
        expected_seconds = (target_ticks / TICKS_PER_SECOND) + elapsed
        actual_seconds = self._player.get_time()
        drift = actual_seconds - expected_seconds

        if abs(drift) < SYNC_TOLERANCE_S:
            return
        if abs(drift) >= DRIFT_SEEK_S:
            LOG.info(
                "Drift %.3fs exceeds %.1fs; corrective seek to %.3f",
                drift,
                DRIFT_SEEK_S,
                expected_seconds,
            )
            self._with_suppressed_echo(
                lambda: self._player.seek_seconds(expected_seconds)
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _post_state(self, is_playing, position_seconds, kind):
        """Post Buffering or Ready to the server."""
        if not self._in_group():
            return
        position_ticks = int(position_seconds * TICKS_PER_SECOND)
        playlist_item_id = self._current_playlist_item_id
        when_iso = format_utc(self._controller.clock.now_utc())
        try:
            if kind == "buffer":
                self._controller.api.buffering(
                    when_iso=when_iso,
                    position_ticks=position_ticks,
                    is_playing=is_playing,
                    playlist_item_id=playlist_item_id,
                )
            elif kind == "ready":
                self._controller.api.ready(
                    when_iso=when_iso,
                    position_ticks=position_ticks,
                    is_playing=is_playing,
                    playlist_item_id=playlist_item_id,
                )
        except Exception as error:
            LOG.warning("%s post failed: %s", kind, error)

    def _with_suppressed_echo(self, action):
        """Run an engine-driven player action while suppressing the
        local→server echo (the resulting onPlayBackPaused/Seek will be
        ignored by on_local_*)."""
        with self._lock:
            self._suppress_echo_until = self._monotonic() + 1.0
        try:
            action()
        except Exception as error:
            LOG.warning("Engine action failed: %s", error)

    def _echo_suppressed(self):
        with self._lock:
            return self._monotonic() < self._suppress_echo_until

    def _in_group(self):
        return self._controller.in_group
