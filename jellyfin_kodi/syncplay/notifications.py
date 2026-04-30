# -*- coding: utf-8 -*-
from __future__ import division, absolute_import, print_function, unicode_literals

"""Toast notifications for SyncPlay state transitions.

Renders user-facing strings for join/leave, member changes, server-driven
pause/seek, and error states. The text-rendering functions are pure so
they can be unit-tested without xbmcgui; the ``notify_*`` helpers wrap
them in calls to the addon's existing ``dialog("notification", ...)``
helper.
"""

from ..helper import LazyLogger, dialog, translate

LOG = LazyLogger(__name__)


# Notification dwell time in ms.
DEFAULT_TIME_MS = 2500


# ----------------------------------------------------------------------
# Pure rendering helpers (testable without xbmc)
# ----------------------------------------------------------------------


def render_group_joined(group_name):
    return translate(33525) % (group_name or "")


def render_group_left():
    return translate(33526)


def render_user_joined(user_name):
    return translate(33540) % (user_name or "?")


def render_user_left(user_name):
    return translate(33541) % (user_name or "?")


def render_paused_by_other():
    return translate(33542)


def render_resumed_by_other():
    return translate(33543)


def render_seeked_by_other():
    return translate(33544)


def render_error(update_type):
    """Map a GroupUpdateType error string to a notification message."""
    mapping = {
        "NotInGroup": 33545,
        "GroupDoesNotExist": 33546,
        "LibraryAccessDenied": 33547,
    }
    code = mapping.get(update_type)
    if code is None:
        return translate(33548)
    return translate(code)


# ----------------------------------------------------------------------
# Side-effecting notification helpers
# ----------------------------------------------------------------------


def _notify(message):
    if not message:
        return
    try:
        dialog(
            "notification",
            heading="{jellyfin}",
            message=message,
            time=DEFAULT_TIME_MS,
            sound=False,
        )
    except Exception as error:
        LOG.debug("Notification failed: %s", error)


def notify_group_joined(group_name):
    _notify(render_group_joined(group_name))


def notify_group_left():
    _notify(render_group_left())


def notify_user_joined(user_name):
    _notify(render_user_joined(user_name))


def notify_user_left(user_name):
    _notify(render_user_left(user_name))


def notify_paused_by_other():
    _notify(render_paused_by_other())


def notify_resumed_by_other():
    _notify(render_resumed_by_other())


def notify_seeked_by_other():
    _notify(render_seeked_by_other())


def notify_error(update_type):
    _notify(render_error(update_type))
