# -*- coding: utf-8 -*-
from __future__ import division, absolute_import, print_function, unicode_literals

import threading
import time

import xbmc

from ..helper import LazyLogger, window
from .api import SyncPlayApi
from .clock import ServerClock, parse_utc

LOG = LazyLogger(__name__)


# Window property keys exposed to the entrypoint process.
WINDOW_GROUP_ID = "jellyfin.syncplay.groupId"
WINDOW_GROUP_STATE = "jellyfin.syncplay.state.json"


# Inbound WebSocket message types the controller cares about.
MSG_COMMAND = "SyncPlayCommand"
MSG_GROUP_UPDATE = "SyncPlayGroupUpdate"

# GroupUpdateType string values (Jellyfin serializes enums as strings).
UPDATE_USER_JOINED = "UserJoined"
UPDATE_USER_LEFT = "UserLeft"
UPDATE_GROUP_JOINED = "GroupJoined"
UPDATE_GROUP_LEFT = "GroupLeft"
UPDATE_STATE_UPDATE = "StateUpdate"
UPDATE_PLAY_QUEUE = "PlayQueue"
UPDATE_NOT_IN_GROUP = "NotInGroup"
UPDATE_GROUP_DOES_NOT_EXIST = "GroupDoesNotExist"
UPDATE_LIBRARY_ACCESS_DENIED = "LibraryAccessDenied"

UPDATE_ERRORS = (
    UPDATE_NOT_IN_GROUP,
    UPDATE_GROUP_DOES_NOT_EXIST,
    UPDATE_LIBRARY_ACCESS_DENIED,
)

# SendCommandType string values.
CMD_UNPAUSE = "Unpause"
CMD_PAUSE = "Pause"
CMD_STOP = "Stop"
CMD_SEEK = "Seek"

# GroupStateType string values.
STATE_IDLE = "Idle"
STATE_WAITING = "Waiting"
STATE_PAUSED = "Paused"
STATE_PLAYING = "Playing"

# Cadence of the background ping loop.
PING_INTERVAL_S = 5.0


class SyncPlayController(object):
    """Owns SyncPlay state for a single Jellyfin session.

    Phase 1 scope: REST round-trips, WebSocket inbound dispatch, group state
    cache, and the ping loop that keeps ``ServerClock`` warm. Drift detection
    and the player-event bridge live in ``engine.py`` (Phase 3) and attach via
    :meth:`attach_engine`.
    """

    def __init__(self, jellyfin_client, engine=None, monotonic=None):
        self._client = jellyfin_client
        self.api = SyncPlayApi(jellyfin_client.jellyfin)
        self.clock = ServerClock(monotonic=monotonic)
        self._engine = engine
        self._monotonic = monotonic or time.monotonic

        self._lock = threading.Lock()
        self._group_id = None
        self._group_info = None
        self._play_queue = None
        self._state = None
        self._last_command = None

        self._stop_event = threading.Event()
        self._ping_thread = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def group_id(self):
        return self._group_id

    @property
    def in_group(self):
        return self._group_id is not None

    @property
    def state(self):
        return self._state

    @property
    def group_info(self):
        return self._group_info

    @property
    def play_queue(self):
        return self._play_queue

    @property
    def last_command(self):
        return self._last_command

    # ------------------------------------------------------------------
    # Engine hookup (Phase 3)
    # ------------------------------------------------------------------

    def attach_engine(self, engine):
        from . import registry

        if self._engine is engine:
            return
        if self._engine is not None:
            registry.deregister(self._engine)
            try:
                self._engine.stop()
            except Exception as error:
                LOG.debug("Previous engine stop failed: %s", error)
        self._engine = engine
        if engine is not None:
            registry.register(engine)

    def _teardown_engine(self):
        from . import registry

        if self._engine is None:
            return
        registry.deregister(self._engine)
        try:
            self._engine.stop()
        except Exception as error:
            LOG.debug("Engine stop failed: %s", error)
        self._engine = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def shutdown(self):
        if self.in_group:
            try:
                self.api.leave_group()
            except Exception as error:
                LOG.warning("Failed to leave group during shutdown: %s", error)
        self._reset_group_state()
        self._teardown_engine()

    # ------------------------------------------------------------------
    # User-facing actions (called from UI / context menu)
    # ------------------------------------------------------------------

    def list_groups(self):
        try:
            return self.api.list_groups() or []
        except Exception as error:
            LOG.warning("list_groups failed: %s", error)
            return []

    def create_group(self, name):
        LOG.info("Creating SyncPlay group: %s", name)
        return self.api.create_group(name)

    def join_group(self, group_id):
        LOG.info("Joining SyncPlay group: %s", group_id)
        return self.api.join_group(group_id)

    def leave_group(self):
        if not self.in_group:
            return
        LOG.info("Leaving SyncPlay group: %s", self._group_id)
        try:
            self.api.leave_group()
        except Exception as error:
            LOG.warning("leave_group REST call failed: %s", error)
        self._reset_group_state()

    # ------------------------------------------------------------------
    # WebSocket inbound dispatch
    # ------------------------------------------------------------------

    def on_message(self, message_type, data):
        """Called by Monitor for SyncPlay-prefixed WebSocket messages."""
        if data is None:
            data = {}
        if message_type == MSG_COMMAND:
            self._on_command(data)
        elif message_type == MSG_GROUP_UPDATE:
            self._on_group_update(data)
        else:
            LOG.debug("Unhandled SyncPlay message type: %s", message_type)

    def _on_command(self, data):
        with self._lock:
            self._last_command = data
        cmd = data.get("Command")
        when_str = data.get("When")
        position_ticks = data.get("PositionTicks")
        LOG.info("SyncPlayCommand cmd=%s when=%s pos=%s", cmd, when_str, position_ticks)

        if self._engine is None:
            return

        deadline = None
        if when_str:
            try:
                deadline = self.clock.local_execute_at(parse_utc(when_str))
            except Exception as error:
                LOG.warning("Failed to parse SendCommand.When=%s: %s", when_str, error)
        try:
            self._engine.on_command(cmd, deadline, position_ticks, data)
        except Exception as error:
            LOG.exception("Engine.on_command failed: %s", error)

    def _on_group_update(self, data):
        update_type = data.get("Type")
        payload = data.get("Data")
        LOG.info("SyncPlayGroupUpdate type=%s", update_type)

        if update_type == UPDATE_GROUP_JOINED:
            with self._lock:
                if isinstance(payload, dict):
                    self._group_info = payload
                    self._group_id = payload.get("GroupId") or data.get("GroupId")
                    self._state = payload.get("State")
                else:
                    self._group_id = data.get("GroupId")
            self._start_ping_loop()
        elif update_type == UPDATE_GROUP_LEFT:
            self._reset_group_state()
        elif update_type == UPDATE_STATE_UPDATE:
            with self._lock:
                if isinstance(payload, dict):
                    self._state = payload.get("State")
        elif update_type == UPDATE_PLAY_QUEUE:
            with self._lock:
                if isinstance(payload, dict):
                    self._play_queue = payload
        elif update_type == UPDATE_USER_JOINED:
            self._mutate_participants(payload, add=True)
        elif update_type == UPDATE_USER_LEFT:
            self._mutate_participants(payload, add=False)
        elif update_type in UPDATE_ERRORS:
            LOG.warning("SyncPlay error update: %s", update_type)
            self._reset_group_state()

        self._publish_state()

        if self._engine is not None:
            try:
                self._engine.on_group_update(update_type, payload, data)
            except Exception as error:
                LOG.exception("Engine.on_group_update failed: %s", error)

    def _mutate_participants(self, payload, add):
        if not isinstance(payload, str):
            return
        with self._lock:
            if not isinstance(self._group_info, dict):
                return
            participants = list(self._group_info.get("Participants") or [])
            if add and payload not in participants:
                participants.append(payload)
            elif not add and payload in participants:
                participants.remove(payload)
            self._group_info["Participants"] = participants

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reset_group_state(self):
        self._stop_event.set()
        with self._lock:
            self._group_id = None
            self._group_info = None
            self._play_queue = None
            self._state = None
            self._last_command = None
        self.clock.reset()
        self._publish_state()

    def _publish_state(self):
        """Mirror group state into Kodi window properties for the UI process."""
        try:
            if not self.in_group:
                window(WINDOW_GROUP_ID, clear=True)
                window(WINDOW_GROUP_STATE, clear=True)
                return
            window(WINDOW_GROUP_ID, self._group_id)
            info = self._group_info or {}
            payload = {
                "GroupId": self._group_id,
                "GroupName": info.get("GroupName"),
                "State": self._state,
                "Participants": list(info.get("Participants") or []),
            }
            window(WINDOW_GROUP_STATE, payload)
        except Exception as error:
            LOG.debug("Failed to publish SyncPlay state: %s", error)

    def _start_ping_loop(self):
        if self._ping_thread is not None and self._ping_thread.is_alive():
            return
        self._stop_event.clear()
        self._ping_thread = threading.Thread(
            target=self._ping_loop, name="SyncPlayPing"
        )
        self._ping_thread.daemon = True
        self._ping_thread.start()

    def _ping_loop(self):
        monitor = xbmc.Monitor()
        last_rtt_ms = 0
        while not self._stop_event.is_set() and self.in_group:
            try:
                send_t = self._monotonic()
                self.api.ping(int(last_rtt_ms))
                recv_t = self._monotonic()
                self.clock.update_from_ping(send_t, recv_t)
                if self.clock.rtt_ms is not None:
                    last_rtt_ms = self.clock.rtt_ms
            except Exception as error:
                LOG.debug("Ping failed: %s", error)
            if monitor.waitForAbort(PING_INTERVAL_S):
                break
