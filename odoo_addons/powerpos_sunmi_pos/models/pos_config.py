from odoo import api, fields, models


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

    @api.model
    def _load_pos_data_fields(self, config_id):
        """Odoo 20 pos.load.mixin hook appending PowerPOS hardware fields to pos.config."""
        fields_list = super()._load_pos_data_fields(config_id) if hasattr(super(), '_load_pos_data_fields') else []
        if fields_list:
            for fname in (
                'powerpos_branch_id',
                'powerpos_printer_id',
                'powerpos_cashdrawer_id',
                'powerpos_enable_hardware_bar',
            ):
                if fname not in fields_list:
                    fields_list.append(fname)
        return fields_list
