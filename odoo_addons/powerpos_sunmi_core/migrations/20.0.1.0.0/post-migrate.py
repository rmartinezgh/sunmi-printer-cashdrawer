"""Odoo 20.0.1.0.0 post-migration script for powerpos_sunmi_core."""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    _logger.info("Upgrading powerpos_sunmi_core from %s to 20.0.1.0.0", version)
    # Ensure unverified devices never claim physical_status_verified = True without calibration
    cr.execute(
        """
        UPDATE powerpos_sunmi_device
        SET physical_status_verified = FALSE
        WHERE capability_classification IN ('not_yet_verified', 'opening_control_only', 'printer_connectivity_only', 'unsupported')
          AND physical_status_verified IS TRUE
        """
    )
