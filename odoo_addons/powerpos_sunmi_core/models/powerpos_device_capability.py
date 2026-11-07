from odoo import fields, models


class PowerPosDeviceCapability(models.Model):
    _name = 'powerpos.device.capability'
    _description = 'PowerPOS Hardware Device Capability Matrix Entry'
    _order = 'device_id, feature_key'

    device_id = fields.Many2one(
        'powerpos.sunmi.device',
        string='Device',
        required=True,
        ondelete='cascade',
        index=True,
    )
    feature_key = fields.Char(string='Feature Key', required=True, index=True)
    feature_name = fields.Char(string='Capability Name', required=True)
    vendor_declared = fields.Boolean(
        string='Vendor Declared',
        default=False,
        help='Whether the hardware model datasheet or SDK declares support for this feature.',
    )
    hardware_verified = fields.Boolean(
        string='Hardware Verified',
        default=False,
        help='True only when runtime probing or physical calibration confirmed live operation.',
    )
    status = fields.Selection(
        selection=[
            ('SUPPORTED', 'Supported & Verified'),
            ('UNSUPPORTED', 'Unsupported'),
            ('UNVERIFIED', 'Declared / Unverified'),
            ('ERROR', 'Hardware Probe Error'),
        ],
        string='Capability Status',
        default='UNVERIFIED',
        required=True,
    )
    diagnostic_notes = fields.Text(string='Diagnostic Notes')
    last_checked = fields.Datetime(string='Last Checked', default=fields.Datetime.now)

    _sql_constraints = [
        ('device_feature_uniq', 'unique(device_id, feature_key)', 'Capability key must be unique per device!'),
    ]
