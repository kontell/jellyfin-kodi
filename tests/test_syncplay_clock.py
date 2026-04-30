# -*- coding: utf-8 -*-
from __future__ import division, absolute_import, print_function, unicode_literals

from datetime import datetime, timedelta, timezone

import pytest

from jellyfin_kodi.syncplay.clock import ServerClock, format_utc, parse_utc

# ----------------------------------------------------------------------
# format_utc / parse_utc
# ----------------------------------------------------------------------


def test_format_utc_uses_z_suffix():
    dt = datetime(2024, 1, 1, 12, 34, 56, 789000, tzinfo=timezone.utc)
    assert format_utc(dt) == "2024-01-01T12:34:56.789000Z"


def test_format_utc_naive_assumed_utc():
    dt = datetime(2024, 1, 1, 0, 0, 0)
    assert format_utc(dt).endswith("Z")


def test_parse_utc_accepts_z_and_offset():
    a = parse_utc("2024-01-01T12:34:56.789Z")
    b = parse_utc("2024-01-01T12:34:56.789+00:00")
    assert a == b
    assert a.tzinfo is not None


def test_format_parse_round_trip():
    dt = datetime(2024, 6, 15, 10, 0, 0, 500000, tzinfo=timezone.utc)
    assert parse_utc(format_utc(dt)) == dt


# ----------------------------------------------------------------------
# ServerClock
# ----------------------------------------------------------------------


class FakeClock(object):
    """Deterministic monotonic + UTC source for tests."""

    def __init__(self, monotonic_start=1000.0, utc_start=None):
        self._monotonic = monotonic_start
        self._utc = utc_start or datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    def advance(self, seconds):
        self._monotonic += seconds
        self._utc += timedelta(seconds=seconds)

    def monotonic(self):
        return self._monotonic

    def utc_now(self):
        return self._utc


@pytest.fixture
def fake_clock():
    return FakeClock()


@pytest.fixture
def server_clock(fake_clock):
    return ServerClock(monotonic=fake_clock.monotonic, utc_now=fake_clock.utc_now)


def test_initial_state(server_clock):
    assert not server_clock.is_synced
    assert server_clock.rtt_ms is None
    assert server_clock.offset_seconds == 0.0


def test_update_from_ping_records_rtt_only(fake_clock, server_clock):
    send = fake_clock.monotonic()
    fake_clock.advance(0.040)  # 40 ms RTT
    recv = fake_clock.monotonic()
    server_clock.update_from_ping(send, recv)

    assert server_clock.rtt_ms == pytest.approx(40.0)
    assert not server_clock.is_synced
    assert server_clock.offset_seconds == 0.0


def test_update_from_response_with_zero_offset(fake_clock, server_clock):
    """If the server's clock matches local UTC exactly, offset should be ~0."""
    send = fake_clock.monotonic()
    fake_clock.advance(0.040)
    midpoint_local_utc = fake_clock.utc_now()
    fake_clock.advance(0.040)
    recv = fake_clock.monotonic()

    server_clock.update_from_response(midpoint_local_utc, send, recv)

    assert server_clock.is_synced
    assert server_clock.offset_seconds == pytest.approx(0.0, abs=1e-6)
    assert server_clock.rtt_ms == pytest.approx(80.0)


def test_update_from_response_with_positive_offset(fake_clock, server_clock):
    """Server is 5 seconds ahead of local."""
    send = fake_clock.monotonic()
    fake_clock.advance(0.020)
    midpoint_local_utc = fake_clock.utc_now()
    server_when = midpoint_local_utc + timedelta(seconds=5.0)
    fake_clock.advance(0.020)
    recv = fake_clock.monotonic()

    server_clock.update_from_response(server_when, send, recv)
    assert server_clock.offset_seconds == pytest.approx(5.0, abs=1e-6)


def test_local_execute_at_with_known_offset(fake_clock, server_clock):
    """A server time 1 s in the future maps to local monotonic + 1 s when offset=0."""
    send = fake_clock.monotonic()
    fake_clock.advance(0.020)
    midpoint_local_utc = fake_clock.utc_now()
    fake_clock.advance(0.020)
    recv = fake_clock.monotonic()
    server_clock.update_from_response(midpoint_local_utc, send, recv)

    target_server_utc = fake_clock.utc_now() + timedelta(seconds=1.0)
    deadline = server_clock.local_execute_at(target_server_utc)
    assert deadline == pytest.approx(fake_clock.monotonic() + 1.0, abs=1e-6)


def test_local_execute_at_compensates_offset(fake_clock, server_clock):
    """If server is 5 s ahead, a server_when 1 s in the server future is
    actually 6 s in the local future."""
    send = fake_clock.monotonic()
    fake_clock.advance(0.020)
    midpoint_local_utc = fake_clock.utc_now()
    server_when_now = midpoint_local_utc + timedelta(seconds=5.0)
    fake_clock.advance(0.020)
    recv = fake_clock.monotonic()
    server_clock.update_from_response(server_when_now, send, recv)

    target_server_utc = server_clock.now_utc() + timedelta(seconds=1.0)
    deadline = server_clock.local_execute_at(target_server_utc)
    assert deadline == pytest.approx(fake_clock.monotonic() + 1.0, abs=1e-3)


def test_ema_smoothing(fake_clock, server_clock):
    """Two samples blend toward, but don't snap to, the second value."""
    # Sample 1: offset = 0.0
    send = fake_clock.monotonic()
    fake_clock.advance(0.010)
    midpoint = fake_clock.utc_now()
    fake_clock.advance(0.010)
    recv = fake_clock.monotonic()
    server_clock.update_from_response(midpoint, send, recv)
    assert server_clock.offset_seconds == pytest.approx(0.0, abs=1e-6)

    # Sample 2: offset = 1.0
    send = fake_clock.monotonic()
    fake_clock.advance(0.010)
    midpoint = fake_clock.utc_now()
    server_when = midpoint + timedelta(seconds=1.0)
    fake_clock.advance(0.010)
    recv = fake_clock.monotonic()
    server_clock.update_from_response(server_when, send, recv)

    # Default EMA alpha is 0.25, so blended offset = 0.0 * 0.75 + 1.0 * 0.25 = 0.25
    assert server_clock.offset_seconds == pytest.approx(0.25, abs=1e-6)


def test_reset_clears_state(fake_clock, server_clock):
    send = fake_clock.monotonic()
    fake_clock.advance(0.020)
    server_clock.update_from_response(
        fake_clock.utc_now(), send, fake_clock.monotonic()
    )
    assert server_clock.is_synced

    server_clock.reset()
    assert not server_clock.is_synced
    assert server_clock.rtt_ms is None
    assert server_clock.offset_seconds == 0.0
