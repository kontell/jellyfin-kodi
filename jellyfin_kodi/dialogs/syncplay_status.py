# -*- coding: utf-8 -*-
from __future__ import division, absolute_import, print_function, unicode_literals

import xbmcgui

from ..helper import LazyLogger

LOG = LazyLogger(__name__)


# Action IDs.
ACTION_PARENT_DIR = 9
ACTION_PREVIOUS_MENU = 10
ACTION_BACK = 92
ACTION_NAV_BACK = 92


class SyncPlayStatusDialog(xbmcgui.WindowXMLDialog):
    """In-player overlay showing SyncPlay group state.

    Reads two window properties via the skin:

    - ``syncplay_top``: top line, e.g. ``"Sarah's group · 3 members"``
    - ``syncplay_state``: bottom line, e.g. ``"Synced ±42 ms"`` or
      ``"Paused by Sarah"``.

    The Player owns the lifecycle: opens on playback start while in a
    group and the ``syncplayShowOverlay`` setting is on, closes on
    playback stop or group leave. The :class:`SyncEngine` publishes the
    actual property strings; this class is just a thin wrapper around
    the skin so updates from any thread land on the next frame.
    """

    def __init__(self, *args, **kwargs):
        xbmcgui.WindowXMLDialog.__init__(self, *args)
        self._dismissed = False

    def set_lines(self, top, state):
        self.setProperty("syncplay_top", top or "")
        self.setProperty("syncplay_state", state or "")

    def onAction(self, action):
        action_id = action.getId()
        if action_id in (
            ACTION_BACK,
            ACTION_PARENT_DIR,
            ACTION_PREVIOUS_MENU,
            ACTION_NAV_BACK,
        ):
            self._dismissed = True
            self.close()

    def is_dismissed(self):
        return self._dismissed
