# -*- coding: utf-8 -*-
from __future__ import division, absolute_import, print_function, unicode_literals

import pytest

from jellyfin_kodi.syncplay.controller import SyncPlayController

# ----------------------------------------------------------------------
# Test doubles
# ----------------------------------------------------------------------


class _StubApi(object):
    """Minimal SyncPlayApi stand-in used by the controller's _validate_*
    paths. Only the methods exercised by these tests are implemented.
    """

    def __init__(self, list_response=None, list_raises=False):
        self._list_response = list_response or []
        self._list_raises = list_raises
        self.calls = []

    def list_groups(self):
        self.calls.append(("list_groups",))
        if self._list_raises:
            raise RuntimeError("boom")
        return self._list_response


class _StubJellyfin(object):
    """A controller construction needs ``client.jellyfin``; we never let
    the controller actually call out to it in these tests because we
    replace ``self.api`` directly after construction.
    """

    jellyfin = object()


@pytest.fixture
def controller():
    c = SyncPlayController(_StubJellyfin())
    return c


# ----------------------------------------------------------------------
# Group-membership validation (Phase 5 reconnect path)
# ----------------------------------------------------------------------


def test_validate_no_op_when_not_in_group(controller):
    api = _StubApi(list_response=[{"GroupId": "anything"}])
    controller.api = api

    controller._validate_group_membership()

    assert api.calls == []


def test_validate_keeps_state_when_group_still_listed(controller):
    api = _StubApi(list_response=[{"GroupId": "g1"}, {"GroupId": "g2"}])
    controller.api = api
    controller._group_id = "g1"

    controller._validate_group_membership()

    assert controller.in_group is True
    assert controller._group_id == "g1"
    assert api.calls == [("list_groups",)]


def test_validate_resets_when_group_missing(controller):
    api = _StubApi(list_response=[{"GroupId": "other"}])
    controller.api = api
    controller._group_id = "g1"
    controller._group_info = {"GroupName": "Movie night"}

    controller._validate_group_membership()

    assert controller.in_group is False
    assert controller._group_id is None
    assert controller._group_info is None


def test_validate_swallows_list_failure(controller):
    api = _StubApi(list_raises=True)
    controller.api = api
    controller._group_id = "g1"

    # Should not raise; state preserved (we can't tell either way after a failure).
    controller._validate_group_membership()
    assert controller._group_id == "g1"


def test_validate_handles_missing_groupid_in_response(controller):
    api = _StubApi(list_response=[None, {"GroupName": "no-id"}, {"GroupId": "other"}])
    controller.api = api
    controller._group_id = "g1"

    controller._validate_group_membership()

    assert controller._group_id is None
