# -*- coding: utf-8 -*-
from __future__ import division, absolute_import, print_function, unicode_literals

import pytest

from jellyfin_kodi.syncplay import notifications as notif_mod

_TRANSLATIONS = {
    33525: "Joined %s",
    33526: "Left watch group",
    33540: "%s joined the group",
    33541: "%s left the group",
    33542: "Group paused",
    33543: "Group resumed",
    33544: "Group seeked",
    33545: "You are not in a watch group",
    33546: "Watch group no longer exists",
    33547: "You don't have access to this content",
    33548: "SyncPlay error",
}


@pytest.fixture(autouse=True)
def patch_translate(monkeypatch):
    monkeypatch.setattr(
        notif_mod, "translate", lambda code: _TRANSLATIONS.get(code, "")
    )


# ----------------------------------------------------------------------
# Pure rendering helpers
# ----------------------------------------------------------------------


def test_render_group_joined():
    assert notif_mod.render_group_joined("Movie night") == "Joined Movie night"


def test_render_group_joined_handles_empty_name():
    assert notif_mod.render_group_joined(None) == "Joined "


def test_render_group_left():
    assert notif_mod.render_group_left() == "Left watch group"


def test_render_user_joined():
    assert notif_mod.render_user_joined("alice") == "alice joined the group"


def test_render_user_joined_handles_missing_name():
    assert notif_mod.render_user_joined(None) == "? joined the group"


def test_render_user_left():
    assert notif_mod.render_user_left("alice") == "alice left the group"


def test_render_paused_resumed_seeked():
    assert notif_mod.render_paused_by_other() == "Group paused"
    assert notif_mod.render_resumed_by_other() == "Group resumed"
    assert notif_mod.render_seeked_by_other() == "Group seeked"


def test_render_error_known_codes():
    assert notif_mod.render_error("NotInGroup") == "You are not in a watch group"
    assert notif_mod.render_error("GroupDoesNotExist") == "Watch group no longer exists"
    assert (
        notif_mod.render_error("LibraryAccessDenied")
        == "You don't have access to this content"
    )


def test_render_error_unknown_falls_back():
    assert notif_mod.render_error("BogusKind") == "SyncPlay error"


# ----------------------------------------------------------------------
# Side-effecting helpers go through the addon's dialog() helper
# ----------------------------------------------------------------------


def test_notify_group_joined_uses_dialog(monkeypatch):
    calls = []

    def fake_dialog(kind, **kwargs):
        calls.append((kind, kwargs.get("message")))

    monkeypatch.setattr(notif_mod, "dialog", fake_dialog)
    notif_mod.notify_group_joined("Movie night")
    assert calls == [("notification", "Joined Movie night")]


def test_notify_swallows_dialog_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kodi not initialized")

    monkeypatch.setattr(notif_mod, "dialog", boom)
    # Should not raise.
    notif_mod.notify_group_joined("g")


def test_notify_skips_empty_message(monkeypatch):
    calls = []

    monkeypatch.setattr(notif_mod, "dialog", lambda *a, **k: calls.append(k))
    monkeypatch.setattr(notif_mod, "render_group_left", lambda: "")
    notif_mod.notify_group_left()
    assert calls == []
