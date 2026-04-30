# -*- coding: utf-8 -*-
from __future__ import division, absolute_import, print_function, unicode_literals

from ..helper import LazyLogger

LOG = LazyLogger(__name__)


class SyncPlayApi(object):
    """REST wrapper for the Jellyfin /SyncPlay/* endpoints.

    Wraps an existing ``jellyfin.api.API`` instance and routes calls through its
    ``_get`` / ``_post`` helpers so authentication, retries, and ``{UserId}``
    substitution behave the same as every other Jellyfin call the addon makes.

    All methods return what the server returned (parsed JSON dict, list, or
    ``None`` for 204 responses) and raise ``HTTPException`` on non-2xx replies.
    """

    def __init__(self, api_client):
        self._api = api_client

    # ------------------------------------------------------------------
    # Group lifecycle
    # ------------------------------------------------------------------

    def create_group(self, group_name):
        """POST /SyncPlay/New."""
        return self._api._post("SyncPlay/New", json={"GroupName": group_name})

    def join_group(self, group_id):
        """POST /SyncPlay/Join."""
        return self._api._post("SyncPlay/Join", json={"GroupId": group_id})

    def leave_group(self):
        """POST /SyncPlay/Leave."""
        return self._api._post("SyncPlay/Leave")

    def list_groups(self):
        """GET /SyncPlay/List."""
        return self._api._get("SyncPlay/List")

    def get_group(self, group_id):
        """GET /SyncPlay/{group_id}."""
        return self._api._get("SyncPlay/%s" % group_id)

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    def set_new_queue(self, item_ids, position=0, start_position_ticks=0):
        """POST /SyncPlay/SetNewQueue."""
        return self._api._post(
            "SyncPlay/SetNewQueue",
            json={
                "PlayingQueue": list(item_ids),
                "PlayingItemPosition": int(position),
                "StartPositionTicks": int(start_position_ticks),
            },
        )

    def set_playlist_item(self, playlist_item_id):
        """POST /SyncPlay/SetPlaylistItem."""
        return self._api._post(
            "SyncPlay/SetPlaylistItem",
            json={"PlaylistItemId": playlist_item_id},
        )

    def remove_from_playlist(
        self, playlist_item_ids, clear_playlist=False, clear_playing_item=False
    ):
        """POST /SyncPlay/RemoveFromPlaylist."""
        return self._api._post(
            "SyncPlay/RemoveFromPlaylist",
            json={
                "PlaylistItemIds": list(playlist_item_ids),
                "ClearPlaylist": bool(clear_playlist),
                "ClearPlayingItem": bool(clear_playing_item),
            },
        )

    def move_playlist_item(self, playlist_item_id, new_index):
        """POST /SyncPlay/MovePlaylistItem."""
        return self._api._post(
            "SyncPlay/MovePlaylistItem",
            json={
                "PlaylistItemId": playlist_item_id,
                "NewIndex": int(new_index),
            },
        )

    def queue(self, item_ids, mode="Default"):
        """POST /SyncPlay/Queue. ``mode`` is a GroupQueueMode string."""
        return self._api._post(
            "SyncPlay/Queue",
            json={"ItemIds": list(item_ids), "Mode": mode},
        )

    def next_item(self, playlist_item_id):
        """POST /SyncPlay/NextItem."""
        return self._api._post(
            "SyncPlay/NextItem", json={"PlaylistItemId": playlist_item_id}
        )

    def previous_item(self, playlist_item_id):
        """POST /SyncPlay/PreviousItem."""
        return self._api._post(
            "SyncPlay/PreviousItem", json={"PlaylistItemId": playlist_item_id}
        )

    def set_repeat_mode(self, mode):
        """POST /SyncPlay/SetRepeatMode. ``mode`` is a GroupRepeatMode string."""
        return self._api._post("SyncPlay/SetRepeatMode", json={"Mode": mode})

    def set_shuffle_mode(self, mode):
        """POST /SyncPlay/SetShuffleMode. ``mode`` is a GroupShuffleMode string."""
        return self._api._post("SyncPlay/SetShuffleMode", json={"Mode": mode})

    # ------------------------------------------------------------------
    # Playback control
    # ------------------------------------------------------------------

    def pause(self):
        """POST /SyncPlay/Pause."""
        return self._api._post("SyncPlay/Pause")

    def unpause(self):
        """POST /SyncPlay/Unpause."""
        return self._api._post("SyncPlay/Unpause")

    def stop(self):
        """POST /SyncPlay/Stop."""
        return self._api._post("SyncPlay/Stop")

    def seek(self, position_ticks):
        """POST /SyncPlay/Seek. Position is in 100-ns ticks."""
        return self._api._post(
            "SyncPlay/Seek", json={"PositionTicks": int(position_ticks)}
        )

    # ------------------------------------------------------------------
    # Client state reporting
    # ------------------------------------------------------------------

    def buffering(self, when_iso, position_ticks, is_playing, playlist_item_id):
        """POST /SyncPlay/Buffering. ``when_iso`` must be ISO-8601 UTC."""
        return self._api._post(
            "SyncPlay/Buffering",
            json={
                "When": when_iso,
                "PositionTicks": int(position_ticks),
                "IsPlaying": bool(is_playing),
                "PlaylistItemId": playlist_item_id,
            },
        )

    def ready(self, when_iso, position_ticks, is_playing, playlist_item_id):
        """POST /SyncPlay/Ready."""
        return self._api._post(
            "SyncPlay/Ready",
            json={
                "When": when_iso,
                "PositionTicks": int(position_ticks),
                "IsPlaying": bool(is_playing),
                "PlaylistItemId": playlist_item_id,
            },
        )

    def set_ignore_wait(self, ignore_wait):
        """POST /SyncPlay/SetIgnoreWait."""
        return self._api._post(
            "SyncPlay/SetIgnoreWait", json={"IgnoreWait": bool(ignore_wait)}
        )

    def ping(self, ping_ms):
        """POST /SyncPlay/Ping. ``ping_ms`` is the client's last observed RTT."""
        return self._api._post("SyncPlay/Ping", json={"Ping": int(ping_ms)})
