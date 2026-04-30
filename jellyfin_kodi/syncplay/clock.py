# -*- coding: utf-8 -*-
from __future__ import division, absolute_import, print_function, unicode_literals

import threading
import time
from datetime import datetime, timedelta, timezone

from ..helper import LazyLogger

LOG = LazyLogger(__name__)


# Exponential moving-average factor for offset/RTT smoothing.
# 0.25 means each new sample contributes a quarter to the running estimate.
_EMA_ALPHA = 0.25


def _utc_now():
    return datetime.now(timezone.utc)


def format_utc(dt):
    """Format a datetime as the ISO-8601 string the server expects.

    Server-side .NET parses both ``2024-01-01T12:34:56.789Z`` and
    ``2024-01-01T12:34:56.789+00:00``. We emit the ``Z`` form for parity with
    jellyfin-web.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    s = dt.astimezone(timezone.utc).isoformat()
    if s.endswith("+00:00"):
        s = s[:-6] + "Z"
    return s


def parse_utc(s):
    """Parse an ISO-8601 UTC timestamp emitted by the server."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class ServerClock(object):
    """Tracks the offset between local time and the Jellyfin server's UTC clock.

    The offset estimate is fed by two signals:

    1. ``update_from_ping`` records a ``/SyncPlay/Ping`` round-trip. The Ping
       endpoint does not echo the server's clock, so this only refines the RTT
       estimate.
    2. ``update_from_response`` records a request whose response carries the
       server's ``DateTime.UtcNow`` (e.g. ``Buffer``/``Ready``). Assuming the
       server time was sampled at roughly the midpoint of the round trip, the
       offset is ``server_utc_at_midpoint - local_utc_at_midpoint``.

    Both signals feed an exponential moving average so transient network
    spikes don't whipsaw the estimate.
    """

    def __init__(self, monotonic=None, utc_now=None):
        self._monotonic = monotonic or time.monotonic
        self._utc_now = utc_now or _utc_now
        self._lock = threading.Lock()
        self._offset_seconds = 0.0  # server_utc - local_utc
        self._rtt_ms = None
        self._sample_count = 0

    @property
    def rtt_ms(self):
        return self._rtt_ms

    @property
    def offset_seconds(self):
        return self._offset_seconds

    @property
    def is_synced(self):
        return self._sample_count > 0

    def reset(self):
        with self._lock:
            self._offset_seconds = 0.0
            self._rtt_ms = None
            self._sample_count = 0

    def update_from_ping(self, send_local_monotonic, recv_local_monotonic):
        """Record a Ping round-trip. Updates RTT only."""
        rtt_ms = (recv_local_monotonic - send_local_monotonic) * 1000.0
        with self._lock:
            self._rtt_ms = self._blend(self._rtt_ms, rtt_ms)

    def update_from_response(
        self, server_utc_when, send_local_monotonic, recv_local_monotonic
    ):
        """Compute the server-vs-local offset from a timestamped response.

        ``server_utc_when`` is the server's reported UTC at the time it
        processed the request; we assume that's roughly the round-trip
        midpoint. ``send_local_monotonic`` and ``recv_local_monotonic`` are
        readings of ``time.monotonic()`` taken just before send and just after
        receive.
        """
        rtt_seconds = recv_local_monotonic - send_local_monotonic
        rtt_ms = rtt_seconds * 1000.0

        # Anchor the monotonic midpoint to wall-clock UTC.
        local_utc_now = self._utc_now()
        local_monotonic_now = self._monotonic()
        midpoint_local_monotonic = send_local_monotonic + rtt_seconds / 2.0
        midpoint_offset_from_now = local_monotonic_now - midpoint_local_monotonic
        midpoint_local_utc = local_utc_now - timedelta(seconds=midpoint_offset_from_now)

        offset_seconds = (server_utc_when - midpoint_local_utc).total_seconds()

        with self._lock:
            self._offset_seconds = self._blend(
                self._offset_seconds if self._sample_count else None, offset_seconds
            )
            self._rtt_ms = self._blend(self._rtt_ms, rtt_ms)
            self._sample_count += 1

    def now_utc(self):
        """Best estimate of the current server UTC time."""
        with self._lock:
            offset = self._offset_seconds
        return self._utc_now() + timedelta(seconds=offset)

    def local_execute_at(self, server_utc_when):
        """Convert a server UTC instant into a local monotonic deadline.

        Returns the value of ``time.monotonic()`` at which the caller should
        execute an action that the server scheduled for ``server_utc_when``.
        Negative values mean the deadline has already passed.
        """
        with self._lock:
            offset = self._offset_seconds
        local_utc_target = server_utc_when - timedelta(seconds=offset)
        delta = (local_utc_target - self._utc_now()).total_seconds()
        return self._monotonic() + delta

    @staticmethod
    def _blend(prev, sample):
        if prev is None:
            return float(sample)
        return float((1.0 - _EMA_ALPHA) * prev + _EMA_ALPHA * sample)
