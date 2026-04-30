# -*- coding: utf-8 -*-
from __future__ import division, absolute_import, print_function, unicode_literals

import pytest

from jellyfin_kodi.syncplay import controller as controller_mod
from jellyfin_kodi.syncplay import ui as ui_mod

# Translations the UI module looks up via translate(string_id). Provide a
# format-string-compatible map so the format helpers produce stable output
# regardless of the test runtime's xbmc localization stubs.
_TRANSLATIONS = {
    33500: "Watch together",
    33520: "Create new group...",
    33521: "Group name",
    33523: "Leave the watch group?",
    33525: "Joined %s",
    33526: "Left watch group",
    33527: "No watch groups available",
    33528: "Watch group action failed: %s",
    33529: "Group: %s",
    33530: "Leave group",
    33532: "Idle",
    33533: "%d watching",
    33535: "Playing",
    33536: "Paused",
    33537: "Waiting for members",
}


@pytest.fixture(autouse=True)
def patch_translate(monkeypatch):
    monkeypatch.setattr(ui_mod, "translate", lambda code: _TRANSLATIONS.get(code, ""))


# ----------------------------------------------------------------------
# Pure formatting helpers
# ----------------------------------------------------------------------


def test_format_state_known_values():
    assert ui_mod.format_state(controller_mod.STATE_IDLE) == "Idle"
    assert ui_mod.format_state(controller_mod.STATE_PLAYING) == "Playing"
    assert ui_mod.format_state(controller_mod.STATE_PAUSED) == "Paused"
    assert ui_mod.format_state(controller_mod.STATE_WAITING) == "Waiting for members"


def test_format_state_unknown_returns_empty():
    assert ui_mod.format_state(None) == ""
    assert ui_mod.format_state("Bogus") == ""


def test_format_group_label_idle():
    label = ui_mod.format_group_label(
        {"GroupName": "Movie night", "Participants": ["alice", "bob"], "State": "Idle"}
    )
    assert label == "Movie night — 2 watching · Idle"


def test_format_group_label_no_state():
    label = ui_mod.format_group_label({"GroupName": "Group", "Participants": ["solo"]})
    assert label == "Group — 1 watching"


def test_format_group_label_handles_missing_fields():
    label = ui_mod.format_group_label({})
    assert label.startswith("? — 0 watching")


# ----------------------------------------------------------------------
# Dispatch: idle vs. in-group routing
# ----------------------------------------------------------------------


class StubApi(object):
    def __init__(self, list_response=None):
        self._list_response = list_response or []
        self.created = []
        self.joined = []
        self.left = False

    def list_groups(self):
        return self._list_response

    def create_group(self, name):
        self.created.append(name)

    def join_group(self, gid):
        self.joined.append(gid)

    def leave_group(self):
        self.left = True


def test_dispatch_uses_idle_menu_when_window_property_empty(monkeypatch):
    called = {}

    def fake_idle(sp):
        called["idle"] = True

    def fake_in_group(sp):
        called["in_group"] = True

    monkeypatch.setattr(ui_mod, "_show_idle_menu", fake_idle)
    monkeypatch.setattr(ui_mod, "_show_in_group_menu", fake_in_group)
    monkeypatch.setattr(ui_mod, "window", lambda key: "")
    monkeypatch.setattr(ui_mod, "SyncPlayApi", lambda api: StubApi())

    ui_mod.open_groups_dialog(api_client=object())

    assert called == {"idle": True}


def test_dispatch_uses_in_group_menu_when_window_property_set(monkeypatch):
    called = {}

    monkeypatch.setattr(
        ui_mod, "_show_idle_menu", lambda sp: called.setdefault("idle", True)
    )
    monkeypatch.setattr(
        ui_mod, "_show_in_group_menu", lambda sp: called.setdefault("in_group", True)
    )
    monkeypatch.setattr(
        ui_mod,
        "window",
        lambda key: "abc-123" if key == controller_mod.WINDOW_GROUP_ID else "",
    )
    monkeypatch.setattr(ui_mod, "SyncPlayApi", lambda api: StubApi())

    ui_mod.open_groups_dialog(api_client=object())

    assert called == {"in_group": True}


# ----------------------------------------------------------------------
# Idle menu actions
# ----------------------------------------------------------------------


def test_idle_menu_create_action_invokes_create_flow(monkeypatch):
    api = StubApi(list_response=[])

    selections = iter([0])  # first option = "Create new group..."

    def fake_dialog(kind, *args, **kwargs):
        if kind == "select":
            return next(selections)
        if kind == "input":
            return "movie night"
        if kind == "notification":
            return None
        if kind == "ok":
            return None
        raise AssertionError("Unexpected dialog kind: %s" % kind)

    monkeypatch.setattr(ui_mod, "dialog", fake_dialog)

    ui_mod._show_idle_menu(api)

    assert api.created == ["movie night"]
    assert api.joined == []


def test_idle_menu_join_action_picks_correct_group(monkeypatch):
    groups = [
        {"GroupId": "g1", "GroupName": "Group 1", "Participants": []},
        {"GroupId": "g2", "GroupName": "Group 2", "Participants": ["alice"]},
    ]
    api = StubApi(list_response=groups)

    # Select the second listed group (index 2: 0=Create, 1=Group 1, 2=Group 2)
    selections = iter([2])

    def fake_dialog(kind, *args, **kwargs):
        if kind == "select":
            return next(selections)
        if kind == "notification":
            return None
        raise AssertionError("Unexpected dialog kind: %s" % kind)

    monkeypatch.setattr(ui_mod, "dialog", fake_dialog)

    ui_mod._show_idle_menu(api)

    assert api.joined == ["g2"]
    assert api.created == []


def test_idle_menu_cancel_does_nothing(monkeypatch):
    api = StubApi(list_response=[{"GroupId": "g1", "GroupName": "Group 1"}])

    monkeypatch.setattr(ui_mod, "dialog", lambda kind, *a, **k: -1)

    ui_mod._show_idle_menu(api)

    assert api.joined == []
    assert api.created == []


def test_create_flow_aborts_on_empty_name(monkeypatch):
    api = StubApi()

    monkeypatch.setattr(
        ui_mod, "dialog", lambda kind, *a, **k: "" if kind == "input" else None
    )

    ui_mod._create_group_flow(api)

    assert api.created == []


# ----------------------------------------------------------------------
# In-group menu / leave flow
# ----------------------------------------------------------------------


def test_leave_flow_requires_yesno_confirmation(monkeypatch):
    api = StubApi()

    monkeypatch.setattr(ui_mod, "dialog", lambda kind, *a, **k: False)

    ui_mod._leave_group_flow(api)

    assert api.left is False


def test_leave_flow_calls_api_when_confirmed(monkeypatch):
    api = StubApi()

    def fake_dialog(kind, *args, **kwargs):
        if kind == "yesno":
            return True
        if kind == "notification":
            return None
        raise AssertionError("Unexpected dialog kind: %s" % kind)

    monkeypatch.setattr(ui_mod, "dialog", fake_dialog)

    ui_mod._leave_group_flow(api)

    assert api.left is True
