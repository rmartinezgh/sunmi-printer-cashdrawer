from odoo import fields, models


class PowerPosSunmiCloudAccount(models.Model):
    _name = 'powerpos.sunmi.cloud.account'
    _description = 'SUNMI OpenAPI v2 Cloud Account Configuration'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Account Name', required=True, tracking=True)
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
        tracking=True,
    )
    app_id = fields.Char(
        string='SUNMI App ID',
        required=True,
        tracking=True,
    )
    app_key = fields.Char(
        string='SUNMI App Key (Secret)',
        required=True,
        groups='powerpos_sunmi_core.group_powerpos_sysadmin',
        copy=False,
        help='Restricted to System Administrators. Never transmitted to POS frontend browsers.',
    )
    base_url = fields.Char(
        string='SUNMI OpenAPI Base URL',
        required=True,
        default='https://openapi.sunmi.com',
        tracking=True,
    )
    webhook_secret = fields.Char(
        string='Webhook Verification Secret',
        groups='powerpos_sunmi_core.group_powerpos_sysadmin',
        copy=False,
    )
    timeout_seconds = fields.Integer(string='HTTP Request Timeout (s)', default=10, required=True)
    active = fields.Boolean(string='Active', default=True, tracking=True)

    _sql_constraints = [
        ('appid_company_uniq', 'unique(app_id, company_id)', 'SUNMI App ID is already configured for this company!'),
    ]
