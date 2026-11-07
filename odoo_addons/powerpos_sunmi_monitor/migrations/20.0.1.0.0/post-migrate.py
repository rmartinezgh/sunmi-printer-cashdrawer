"""Odoo 20.0.1.0.0 post-migration script for powerpos_sunmi_monitor."""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    _logger.info("Upgrading powerpos_sunmi_monitor from %s to 20.0.1.0.0", version)

