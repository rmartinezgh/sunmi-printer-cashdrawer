from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class PowerPosBranch(models.Model):
    _name = 'powerpos.branch'
    _description = 'PowerPOS Store / Branch'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name asc'

    name = fields.Char(string='Branch Name', required=True, tracking=True)
    code = fields.Char(
        string='Branch Code',
        required=True,
        copy=False,
        default=lambda self: _('New'),
        tracking=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
        tracking=True,
    )
    address = fields.Text(string='Physical Address')
    manager_id = fields.Many2one(
        'res.users',
        string='Store Manager',
        tracking=True,
        domain="[('company_ids', 'in', company_id)]",
    )
    pos_config_ids = fields.One2many(
        'pos.config',
        'powerpos_branch_id',
        string='POS Registers',
    )
    device_ids = fields.One2many(
        'powerpos.sunmi.device',
        'branch_id',
        string='Registered SUNMI Devices',
    )
    device_count = fields.Integer(
        string='Device Count',
        compute='_compute_device_count',
    )
    alert_prolonged_open_seconds = fields.Integer(
        string='Default Prolonged Open Alert (Seconds)',
        default=30,
        required=True,
        tracking=True,
        help='Default threshold in seconds before a physically open cash drawer triggers a prolonged-open alert.',
    )
    alert_excessive_open_count = fields.Integer(
        string='Excessive Manual Openings Threshold (per Shift)',
        default=5,
        required=True,
        tracking=True,
        help='Maximum number of No-Sale or manual openings allowed in a single POS session before alerting.',
    )
    active = fields.Boolean(string='Active', default=True, tracking=True)

    _sql_constraints = [
        ('branch_code_company_uniq', 'unique(code, company_id)', 'Branch code must be unique per company!'),
    ]

    @api.depends('device_ids')
    def _compute_device_count(self):
        for branch in self:
            branch.device_count = len(branch.device_ids)

    @api.constrains('alert_prolonged_open_seconds', 'alert_excessive_open_count')
    def _check_thresholds(self):
        for branch in self:
            if branch.alert_prolonged_open_seconds < 5:
                raise ValidationError(_('Prolonged open alert threshold must be at least 5 seconds.'))
            if branch.alert_excessive_open_count < 1:
                raise ValidationError(_('Excessive manual openings threshold must be at least 1.'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('code', _('New')) == _('New'):
                vals['code'] = self.env['ir.sequence'].next_by_code('powerpos.branch') or _('BR-0001')
        return super().create(vals_list)
