from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class PowerPosCashDrawerPolicy(models.Model):
    _name = 'powerpos.cashdrawer.policy'
    _description = 'Cash Drawer Opening & Security Policy'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'branch_id, name'

    name = fields.Char(string='Policy Name', required=True, tracking=True)
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    branch_id = fields.Many2one(
        'powerpos.branch',
        string='Branch Scope',
        domain="[('company_id', '=', company_id)]",
        index=True,
    )
    pos_config_id = fields.Many2one(
        'pos.config',
        string='POS Register Override (Optional)',
        domain="[('company_id', '=', company_id)]",
    )
    auto_open_cash_sale = fields.Boolean(
        string='Auto-Open on Cash Sale Settlement',
        default=True,
        tracking=True,
    )
    refund_open_policy = fields.Selection(
        selection=[
            ('cashier_allowed', 'Cashier Allowed on Validated Cash Refund'),
            ('supervisor_required', 'Supervisor Approval Always Required for Refund Open'),
            ('disabled', 'Disabled (Do Not Open on Refund)'),
        ],
        string='Cash Refund Opening Policy',
        default='supervisor_required',
        required=True,
        tracking=True,
    )
    manual_open_requires_reason = fields.Boolean(
        string='Require Reason Code for Manual / No-Sale Open',
        default=True,
        tracking=True,
    )
    no_sale_requires_supervisor = fields.Boolean(
        string='Require Supervisor Override for No-Sale Open',
        default=True,
        tracking=True,
    )
    allow_remote_open = fields.Boolean(
        string='Allow Remote Backoffice Drawer Kick',
        default=False,
        tracking=True,
    )
    offline_open_policy = fields.Selection(
        selection=[
            ('allow_cash_sales_and_supervisor', 'Allow Cash Sales & Local Supervisor PIN (Queue Events)'),
            ('supervisor_only_offline', 'Require Local Supervisor PIN for All Offline Openings'),
            ('strict_lockdown', 'Disable Software Kick When Offline'),
        ],
        string='Offline Opening Policy',
        default='allow_cash_sales_and_supervisor',
        required=True,
        tracking=True,
    )
    correlation_window_seconds = fields.Integer(
        string='Authorization-to-Physical Correlation Window (s)',
        default=5,
        required=True,
        tracking=True,
        help='Time window in seconds to correlate an authorized kick command with a verified physical CLOSED->OPEN transition.',
    )
    cooldown_lock_seconds = fields.Integer(
        string='Duplicate Pulse Cooldown Lock (s)',
        default=3,
        required=True,
        tracking=True,
        help='Minimum seconds between consecutive kick pulses on the same drawer.',
    )
    active = fields.Boolean(string='Active', default=True)

    @api.constrains('correlation_window_seconds', 'cooldown_lock_seconds')
    def _check_policy_timings(self):
        for policy in self:
            if policy.correlation_window_seconds < 2 or policy.correlation_window_seconds > 30:
                raise ValidationError(_('Correlation window must be between 2 and 30 seconds.'))
            if policy.cooldown_lock_seconds < 1:
                raise ValidationError(_('Cooldown lock must be at least 1 second.'))
