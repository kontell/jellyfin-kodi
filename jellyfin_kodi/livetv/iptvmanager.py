# -*- coding: utf-8 -*-
"""
jellyfin_kodi/livetv/iptvmanager.py
------------------------------------
IPTV Manager integration: sends channel and EPG data back to IPTV Manager
via a local TCP socket callback.

How the protocol works
~~~~~~~~~~~~~~~~~~~~~~
IPTV Manager cannot receive a return value from a ``RunPlugin()`` or
``RunScript()`` Kodi call, so it instead:

  1. Binds to a free localhost port.
  2. Calls our endpoint with ``?port=<N>`` appended.
  3. Waits (with a timeout) for *us* to connect back and send JSON.

This module implements that callback side.  The caller (a route handler in
``default.py``) just does::

    from jellyfin_kodi.livetv.iptvmanager import IPTVManager
    IPTVManager(port, livetv).send_channels()

or::

    IPTVManager(port, livetv).send_epg(days=3)

References
~~~~~~~~~~
- IPTV Manager integration wiki:
  https://github.com/add-ons/service.iptv.manager/wiki/Integration
- JSON-STREAMS format:
  https://github.com/add-ons/service.iptv.manager/wiki/JSON-STREAMS-format
- JSON-EPG format:
  https://github.com/add-ons/service.iptv.manager/wiki/JSON-EPG-format
"""

import json
import logging
import socket

LOG = logging.getLogger("plugin.video.jellyfin.livetv.iptvmanager")


class IPTVManager:
    """
    Sends Live TV data to IPTV Manager via the socket callback protocol.

    Parameters
    ----------
    port : int
        The localhost port that IPTV Manager is listening on.  Passed in
        as the ``port`` query parameter by IPTV Manager itself.
    livetv : LiveTV
        An initialised ``LiveTV`` instance (from ``livetv.py``) bound to
        the active Jellyfin server.
    """

    def __init__(self, port: int, livetv):
        self.port   = port
        self.livetv = livetv

    # ------------------------------------------------------------------
    # Socket decorator
    # ------------------------------------------------------------------

    @staticmethod
    def _via_socket(func):
        """
        Decorator: opens the callback socket, serialises the return value of
        *func* as JSON, sends it, then closes the socket.

        The socket is opened *before* any data is fetched so that IPTV
        Manager's timeout countdown starts as late as possible.  The actual
        data fetch happens inside the wrapped function after the connection
        is established.
        """
        def send(self):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                LOG.debug("IPTVManager: connecting to callback port %d", self.port)
                sock.connect(("127.0.0.1", self.port))
                payload = json.dumps(func(self)).encode("utf-8")
                sock.sendall(payload)
                LOG.debug("IPTVManager: sent %d bytes", len(payload))
            except Exception as exc:  # noqa: BLE001
                LOG.error("IPTVManager: socket error – %s", exc)
                # Closing without sending signals failure to IPTV Manager.
            finally:
                sock.close()

        return send

    # ------------------------------------------------------------------
    # Data senders
    # ------------------------------------------------------------------

    @_via_socket
    def send_channels(self):
        """
        Fetch channels from Jellyfin and send JSON-STREAMS v1 data to IPTV
        Manager over the callback socket.
        """
        LOG.info("IPTVManager: building channel list for IPTV Manager")
        return self.livetv.channels_for_iptv_manager()

    @_via_socket
    def send_epg(self, days: int = 3):
        """
        Fetch EPG programme data from Jellyfin and send JSON-EPG v1 data to
        IPTV Manager over the callback socket.

        Parameters
        ----------
        days : int
            Number of days ahead to fetch.  Reads ``days`` from
            ``self._days`` if set by the caller (see ``send_epg_days``
            helper below).
        """
        LOG.info("IPTVManager: building EPG data for IPTV Manager")
        d = getattr(self, "_days", days)
        return self.livetv.epg_for_iptv_manager(days=d)

    def send_epg_days(self, days: int):
        """Convenience wrapper: set days then call send_epg via the decorator."""
        self._days = days
        self.send_epg()
