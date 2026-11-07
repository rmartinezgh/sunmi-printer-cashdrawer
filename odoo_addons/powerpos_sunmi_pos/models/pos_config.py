from odoo import fields, models


class PosConfig(models.Model):
    _inherit = 'pos.config'

    powerpos_printer_id = fields.Many2one(
        'powerpos.sunmi.device',
        string='Primary SUNMI Printer / Terminal',
        domain="[('company_id', '=', company_id)]",
    )
    powerpos_cashdrawer_id = fields.Many2one(
        'powerpos.cashdrawer.device',
        string='Assigned Cash Drawer',
        domain="[('company_id', '=', company_id)]",
    )
    powerpos_enable_hardware_bar = fields.Boolean(
        string='Show Real-Time SUNMI & Cash Drawer Status in POS',
        default=True,
    )
