"""Odoo 20.0.1.0.0 post-migration script for powerpos_sunmi_cloud."""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    _logger.info("Upgrading powerpos_sunmi_cloud from %s to 20.0.1.0.0", version)
    # Ensure indices on job_uuid for fast idempotent lookup in Odoo 20
    cr.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_powerpos_sunmi_print_job_uuid
        ON powerpos_sunmi_print_job (job_uuid)
        """
    )
