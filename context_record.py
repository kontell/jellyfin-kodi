# -*- coding: utf-8 -*-
from __future__ import division, absolute_import, print_function, unicode_literals

#################################################################################################

from jellyfin_kodi.entrypoint.context_record import ContextRecord
from jellyfin_kodi.helper import LazyLogger

#################################################################################################

LOG = LazyLogger(__name__)

#################################################################################################


if __name__ == "__main__":

    LOG.debug("--->[ context_record ]")

    try:
        ContextRecord()
    except Exception as error:
        LOG.exception(error)

    LOG.info("---<[ context_record ]")
