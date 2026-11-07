"""Odoo 20.0.1.0.0 post-migration script for powerpos_sunmi_cashdrawer."""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    _logger.info("Upgrading powerpos_sunmi_cashdrawer from %s to 20.0.1.0.0", version)
    # Enforce AC-15: drawers without sensors must report UNSUPPORTED rather than OPEN/CLOSED
    cr.execute(
        """
        UPDATE powerpos_cashdrawer_device
        SET physical_state = 'UNSUPPORTED',
            physical_status_capability = 'UNSUPPORTED'
        WHERE sensor_type = 'none'
          AND physical_state IN ('OPEN', 'CLOSED')
        """
    )

