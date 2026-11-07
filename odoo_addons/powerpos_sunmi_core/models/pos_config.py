from odoo import fields, models


class PosConfig(models.Model):
    _inherit = 'pos.config'

    powerpos_branch_id = fields.Many2one(
        'powerpos.branch',
        string='PowerPOS Store Branch',
        domain="[('company_id', '=', company_id)]",
    )
    sunmi_device_ids = fields.One2many(
        'powerpos.sunmi.device',
        'pos_config_id',
        string='Assigned SUNMI Devices',
    )
