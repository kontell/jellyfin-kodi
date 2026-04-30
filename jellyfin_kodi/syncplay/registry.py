# -*- coding: utf-8 -*-
from __future__ import division, absolute_import, print_function, unicode_literals

"""Module-level registry of active SyncEngine instances.

The Player runs in the service process and needs to broadcast playback
events to whichever engines are currently in a group. Engines self-register
when their controller joins a group and deregister on leave/shutdown.

Held as a module-level set so the Player can import it without taking a
dependency on Monitor or Controller construction order.
"""

import threading

from ..helper import LazyLogger

LOG = LazyLogger(__name__)


_engines = set()
_lock = threading.Lock()


def register(engine):
    with _lock:
        _engines.add(engine)


def deregister(engine):
    with _lock:
        _engines.discard(engine)


def all_engines():
    with _lock:
        return list(_engines)


def broadcast(method_name, *args, **kwargs):
    """Call ``method_name(*args, **kwargs)`` on every registered engine.

    Exceptions in any single engine are caught and logged so they don't
    take down sibling engines.
    """
    for engine in all_engines():
        method = getattr(engine, method_name, None)
        if method is None:
            continue
        try:
            method(*args, **kwargs)
        except Exception as error:
            LOG.exception("Engine %s.%s raised: %s", engine, method_name, error)
