"""Odoo 20.0.1.0.0 post-migration script for powerpos_sunmi_audit."""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    _logger.info("Upgrading powerpos_sunmi_audit from %s to 20.0.1.0.0", version)
    cr.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_powerpos_cashdrawer_event_uuid
        ON powerpos_cashdrawer_event (event_uuid)
        """
    )
