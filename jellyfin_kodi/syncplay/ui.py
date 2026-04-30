# -*- coding: utf-8 -*-
from __future__ import division, absolute_import, print_function, unicode_literals

from ..helper import LazyLogger, dialog, translate, window
from .api import SyncPlayApi
from .controller import (
    STATE_IDLE,
    STATE_PAUSED,
    STATE_PLAYING,
    STATE_WAITING,
    WINDOW_GROUP_ID,
    WINDOW_GROUP_STATE,
)

LOG = LazyLogger(__name__)


def open_groups_dialog(api_client):
    """Top-level entry point for the Watch Together menu.

    Branches between the idle and in-group views based on the current
    controller state, which the service publishes via window properties.
    """
    sp = SyncPlayApi(api_client)
    if window(WINDOW_GROUP_ID):
        _show_in_group_menu(sp)
    else:
        _show_idle_menu(sp)


# ----------------------------------------------------------------------
# Idle menu (not currently in a group)
# ----------------------------------------------------------------------


def _show_idle_menu(sp):
    groups = _list_groups(sp)
    if groups is None:  # error already surfaced
        return

    options = [translate(33520)]  # "Create new group..."
    for group in groups:
        options.append(format_group_label(group))

    if not groups:
        options.append("[I]" + translate(33527) + "[/I]")

    selection = dialog("select", translate(33500), options)
    if selection < 0:
        return
    if selection == 0:
        _create_group_flow(sp)
        return
    if selection - 1 < len(groups):
        _join_group_flow(sp, groups[selection - 1])


def _create_group_flow(sp):
    name = dialog("input", heading=translate(33521))
    if not name:
        return
    try:
        sp.create_group(name)
    except Exception as error:
        LOG.warning("create_group failed: %s", error)
        dialog("ok", "{jellyfin}", translate(33528) % error)
        return
    dialog(
        "notification",
        heading="{jellyfin}",
        message=translate(33525) % name,
        time=2000,
        sound=False,
    )


def _join_group_flow(sp, group):
    group_id = group.get("GroupId")
    name = group.get("GroupName") or ""
    if not group_id:
        return
    try:
        sp.join_group(group_id)
    except Exception as error:
        LOG.warning("join_group failed: %s", error)
        dialog("ok", "{jellyfin}", translate(33528) % error)
        return
    dialog(
        "notification",
        heading="{jellyfin}",
        message=translate(33525) % name,
        time=2000,
        sound=False,
    )


# ----------------------------------------------------------------------
# In-group menu
# ----------------------------------------------------------------------


def _show_in_group_menu(sp):
    state = window(WINDOW_GROUP_STATE) or {}
    if isinstance(state, dict):
        cached = state
    else:
        cached = {}

    # Refresh participant data from the server when possible.
    fresh = _list_groups(sp) or []
    group_id = window(WINDOW_GROUP_ID)
    current = next((g for g in fresh if g.get("GroupId") == group_id), None) or cached

    name = current.get("GroupName") or "?"
    participants = current.get("Participants") or []
    state_label = format_state(current.get("State"))

    info_lines = [
        translate(33529) % name,
        translate(33533) % len(participants),
    ]
    if state_label:
        info_lines.append(state_label)
    if participants:
        info_lines.append(", ".join(participants))

    options = info_lines + ["", translate(33530)]  # blank separator + Leave
    leave_index = len(options) - 1

    selection = dialog("select", translate(33500), options)
    if selection < 0:
        return
    if selection == leave_index:
        _leave_group_flow(sp)


def _leave_group_flow(sp):
    if not dialog("yesno", "{jellyfin}", translate(33523)):
        return
    try:
        sp.leave_group()
    except Exception as error:
        LOG.warning("leave_group failed: %s", error)
        dialog("ok", "{jellyfin}", translate(33528) % error)
        return
    dialog(
        "notification",
        heading="{jellyfin}",
        message=translate(33526),
        time=2000,
        sound=False,
    )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _list_groups(sp):
    try:
        return sp.list_groups() or []
    except Exception as error:
        LOG.warning("list_groups failed: %s", error)
        dialog("ok", "{jellyfin}", translate(33528) % error)
        return None


def format_group_label(group):
    """Render a single group as a list-item label.

    Pure function — exposed for testing.
    """
    name = group.get("GroupName") or "?"
    participants = group.get("Participants") or []
    base = "%s — %s" % (name, translate(33533) % len(participants))
    state = group.get("State")
    suffix = format_state(state)
    if suffix:
        base = "%s · %s" % (base, suffix)
    return base


def format_state(state):
    """Render a GroupStateType into a short display string. Pure function."""
    if state == STATE_IDLE:
        return translate(33532)
    if state == STATE_PLAYING:
        return translate(33535)
    if state == STATE_PAUSED:
        return translate(33536)
    if state == STATE_WAITING:
        return translate(33537)
    return ""
