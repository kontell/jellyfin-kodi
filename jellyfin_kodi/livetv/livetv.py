# -*- coding: utf-8 -*-
import contextlib
import logging
from datetime import datetime, timezone, timedelta

LOG = logging.getLogger("plugin.video.jellyfin.livetv")
_jf_log = logging.getLogger("JELLYFIN")

_CHANNEL_CHUNK = 50
_PAGE_SIZE = 500


@contextlib.contextmanager
def _quiet():
    old = _jf_log.level
    _jf_log.setLevel(logging.WARNING)
    try:
        yield
    finally:
        _jf_log.setLevel(old)


class LiveTV:

    def __init__(self, server_client):
        self._client     = server_client
        self._api        = server_client.jellyfin
        cfg              = server_client.config.data
        self._server_url = cfg.get("auth.server", "").rstrip("/")
        self._api_key    = cfg.get("auth.token", "")
        self.token       = self._api_key
        self.server_id   = cfg.get("auth.server-id", "")

    def get_channels(self) -> list[dict]:
        LOG.debug("LiveTV: fetching channel list")
        with _quiet():
            data = self._api.get_channels({
                "EnableImages": True,
                "SortBy":       "SortName",
                "SortOrder":    "Ascending",
                "Limit":        1000,
            })
        channels = (data or {}).get("Items", [])
        LOG.debug("LiveTV: %d channels received", len(channels))
        return channels

    def get_programmes(self, days: int = 3) -> list[dict]:
        with _quiet():
            channels = self.get_channels()
        if not channels:
            return []

        now  = datetime.now(timezone.utc)
        end  = now + timedelta(days=days)
        all_prg: list[dict] = []

        channel_ids = [ch["Id"] for ch in channels]
        chunks = [
            channel_ids[i:i + _CHANNEL_CHUNK]
            for i in range(0, len(channel_ids), _CHANNEL_CHUNK)
        ]

        for idx, chunk in enumerate(chunks, 1):
            LOG.debug("LiveTV: programmes chunk %d/%d (%d channels)", idx, len(chunks), len(chunk))
            start_index = 0
            while True:
                with _quiet():
                    data = self._api.get_programs({
                        "ChannelIds":   ",".join(chunk),
                        "MinStartDate": now.isoformat(),
                        "MaxEndDate":   end.isoformat(),
                        "EnableImages": True,
                        "Fields":       "Overview",
                        "SortBy":       "StartDate",
                        "Limit":        _PAGE_SIZE,
                        "StartIndex":   start_index,
                    })
                items = (data or {}).get("Items", [])
                total = (data or {}).get("TotalRecordCount", 0)
                all_prg.extend(items)
                start_index += len(items)
                if start_index >= total or not items:
                    break

        LOG.debug("LiveTV: %d programme entries fetched", len(all_prg))
        return all_prg

    def get_stream_url(self, channel_id: str) -> str:
        return (
            f"plugin://plugin.video.jellyfin/"
            f"?id={channel_id}&mode=play&server={self.server_id}"
        )

    def get_channel_logo_url(self, channel: dict) -> str | None:
        tag = (channel.get("ImageTags") or {}).get("Primary")
        if not tag:
            return None
        return (
            f"{self._server_url}/Items/{channel['Id']}/Images/Primary"
            f"?tag={tag}&ApiKey={self.token}"
        )

    def get_programme_image_url(self, programme: dict) -> str | None:
        tag = (programme.get("ImageTags") or {}).get("Primary")
        if not tag:
            return None
        return (
            f"{self._server_url}/Items/{programme['Id']}/Images/Primary"
            f"?tag={tag}&ApiKey={self.token}"
        )

    def channels_for_iptv_manager(self) -> dict:
        streams = []
        for ch in self.get_channels():
            cid    = ch["Id"]
            number = ch.get("ChannelNumber") or ch.get("Number") or ""
            entry  = {
                "id":     cid,
                "name":   ch.get("Name", "Unknown"),
                "stream": self.get_stream_url(cid),
            }
            logo = self.get_channel_logo_url(ch)
            if logo:
                entry["logo"] = logo
            try:
                entry["preset"] = int(number)
            except (TypeError, ValueError):
                pass
            streams.append(entry)
        return {"version": 1, "streams": streams}

    def epg_for_iptv_manager(self, days: int = 3) -> dict:
        epg: dict[str, list] = {}
        for prog in self.get_programmes(days=days):
            cid       = prog.get("ChannelId")
            start_raw = prog.get("StartDate")
            stop_raw  = prog.get("EndDate")
            if not cid or not start_raw or not stop_raw:
                continue

            entry: dict = {
                "start": _normalize_ts(start_raw),
                "stop":  _normalize_ts(stop_raw),
                "title": prog.get("Name") or "Unknown",
            }
            if prog.get("Overview"):
                entry["description"] = prog["Overview"]
            if prog.get("EpisodeTitle"):
                entry["subtitle"] = prog["EpisodeTitle"]
            season  = prog.get("ParentIndexNumber")
            episode = prog.get("IndexNumber")
            if season is not None and episode is not None:
                entry["episode"] = f"S{season:02d}E{episode:02d}"
            image = self.get_programme_image_url(prog)
            if image:
                entry["image"] = image
            if prog.get("Genres"):
                entry["genre"] = prog["Genres"]
            if prog.get("PremiereDate"):
                entry["date"] = prog["PremiereDate"][:10]

            epg.setdefault(cid, []).append(entry)
        return {"version": 1, "epg": epg}


def _normalize_ts(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.replace(microsecond=0).isoformat()
    except (ValueError, AttributeError):
        return ts
