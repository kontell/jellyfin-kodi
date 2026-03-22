# -*- coding: utf-8 -*-
"""Context menu handler for scheduling Live TV recordings via Jellyfin."""
from __future__ import division, absolute_import, print_function, unicode_literals

import json
from datetime import datetime, timezone, timedelta

import xbmc
import xbmcgui

from ..helper import translate, dialog, LazyLogger
from ..helper.utils import translate_path
from ..jellyfin import Jellyfin
from ..livetv.livetv import LiveTV

LOG = LazyLogger(__name__)


class ContextRecord:
    """Launched from the PVR EPG context menu.

    Reads the selected programme's channel name and start time from Kodi's
    ListItem info labels, looks up the matching Jellyfin programme, and
    offers to create a one-time or series recording timer.
    """

    def __init__(self):
        channel_name = xbmc.getInfoLabel("ListItem.ChannelName")
        title = xbmc.getInfoLabel("ListItem.Title")
        start_date = xbmc.getInfoLabel("ListItem.StartDate")
        start_time = xbmc.getInfoLabel("ListItem.StartTime")

        LOG.info(
            "context_record: channel=%r title=%r start_date=%r start_time=%r",
            channel_name, title, start_date, start_time,
        )

        if not channel_name:
            LOG.warning("context_record: no channel name available")
            return

        # Initialise Jellyfin client
        jellyfin_client = Jellyfin(None).get_client()
        api_client = jellyfin_client.jellyfin

        addon_data = translate_path(
            "special://profile/addon_data/plugin.video.jellyfin/data.json"
        )
        try:
            with open(addon_data, "rb") as infile:
                data = json.load(infile)
            server_data = data["Servers"][0]
            api_client.config.data["auth.server"] = server_data.get("address")
            api_client.config.data["auth.server-name"] = server_data.get("Name")
            api_client.config.data["auth.user_id"] = server_data.get("UserId")
            api_client.config.data["auth.token"] = server_data.get("AccessToken")
        except Exception as e:
            LOG.warning("context_record: not configured – %s", e)
            return

        livetv = LiveTV(jellyfin_client)

        # --- Step 1: find the Jellyfin channel by name ---
        channel = livetv.find_channel_by_name(channel_name)
        if not channel:
            dialog(
                "notification",
                heading="{jellyfin}",
                message="%s is not a Jellyfin channel" % channel_name,
                icon="{jellyfin}",
                time=3000,
                sound=False,
            )
            return

        # --- Step 2: find the programme at the selected time ---
        start_iso = self._build_iso_time(start_date, start_time)
        programme = livetv.find_programme(channel["Id"], start_iso) if start_iso else None

        if not programme:
            dialog(
                "notification",
                heading="{jellyfin}",
                message="Could not find programme on %s" % channel_name,
                icon="{jellyfin}",
                time=3000,
                sound=False,
            )
            return

        prog_title = programme.get("Name", "Unknown")

        # --- Step 3: confirmation dialog ---
        options = [
            translate(33276),  # "Record"
            translate(33277),  # "Record Series"
        ]
        heading = "%s – %s" % (prog_title, channel_name)
        choice = dialog("select", heading, options)

        if choice < 0:
            return

        programme_id = programme["Id"]

        if choice == 0:
            ok = livetv.create_timer(programme_id)
            msg = "Recording scheduled" if ok else "Failed to schedule recording"
        else:
            ok = livetv.create_series_timer(programme_id)
            msg = "Series recording scheduled" if ok else "Failed to schedule series"

        dialog(
            "notification",
            heading="{jellyfin}",
            message=msg,
            icon="{jellyfin}",
            time=3000,
            sound=False,
        )

    @staticmethod
    def _build_iso_time(date_str: str, time_str: str) -> str | None:
        """Best-effort conversion of Kodi's ListItem date/time labels to ISO-8601.

        Kodi exposes ListItem.StartDate and ListItem.StartTime as
        locale-formatted strings.  We try a handful of common patterns.
        If nothing works we fall back to "now" so the channel lookup still
        has a chance of returning the current programme.
        """
        if date_str and time_str:
            combined = "%s %s" % (date_str, time_str)
            for fmt in (
                "%A, %d %B %Y %H:%M",
                "%A, %B %d, %Y %H:%M",
                "%d/%m/%Y %H:%M",
                "%m/%d/%Y %H:%M",
                "%Y-%m-%d %H:%M",
                "%d/%m/%Y %H:%M:%S",
                "%m/%d/%Y %H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%d.%m.%Y %H:%M",
                "%d-%m-%Y %H:%M",
                "%A, %d %B %Y %H:%M:%S",
                "%A, %B %d, %Y %H:%M:%S",
            ):
                try:
                    dt = datetime.strptime(combined, fmt)
                    # Kodi displays local time; convert to UTC for Jellyfin API
                    local_dt = dt.astimezone(timezone.utc)
                    return local_dt.isoformat()
                except ValueError:
                    continue

        # Fallback: use current UTC time (will find whatever is on-air now).
        return datetime.now(timezone.utc).isoformat()
