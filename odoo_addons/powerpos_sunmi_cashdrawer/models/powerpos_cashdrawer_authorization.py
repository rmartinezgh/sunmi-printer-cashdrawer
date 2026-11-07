import uuid
from datetime import timedelta
from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError


class PowerPosCashDrawerAuthorization(models.Model):
    _name = 'powerpos.cashdrawer.authorization'
    _description = 'Pre-Execution Cash Drawer Opening Authorization Token'
    _order = 'requested_at desc, id desc'

    auth_uuid = fields.Char(
        string='Authorization Token UUID',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: str(uuid.uuid4()),
        index=True,
    )
    idempotency_key = fields.Char(
        string='Client Idempotency Key',
        required=True,
        copy=False,
        index=True,
    )
    drawer_id = fields.Many2one(
        'powerpos.cashdrawer.device',
        string='Cash Drawer',
        required=True,
        index=True,
    )
    printer_id = fields.Many2one(
        'powerpos.sunmi.device',
        string='Controlling Printer',
        related='drawer_id.printer_device_id',
        store=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        related='drawer_id.company_id',
        store=True,
        readonly=True,
        index=True,
    )
    branch_id = fields.Many2one(
        'powerpos.branch',
        string='Branch',
        related='drawer_id.branch_id',
        store=True,
        readonly=True,
        index=True,
    )
    pos_session_id = fields.Many2one('pos.session', string='POS Session', index=True)
    pos_order_id = fields.Many2one('pos.order', string='POS Order', index=True)
    cashier_id = fields.Many2one('hr.employee', string='Cashier Employee', index=True)
    user_id = fields.Many2one(
        'res.users',
        string='Requesting User',
        required=True,
        default=lambda self: self.env.user,
        index=True,
    )
    supervisor_id = fields.Many2one('res.users', string='Approving Supervisor', index=True)
    reason = fields.Selection(
        selection=[
            ('cash_sale', 'Cash Sale Transaction'),
            ('cash_refund', 'Cash Refund / Return'),
            ('cash_in', 'Cash-In / Float Top-Up'),
            ('cash_out', 'Cash-Out / Safe Drop'),
            ('starting_cash_count', 'Shift Opening Cash Count'),
            ('closing_cash_count', 'Shift Closing Cash Count'),
            ('supervisor_no_sale', 'Supervisor Authorized No-Sale'),
            ('maintenance_test', 'Hardware Diagnostic / Calibration Test'),
        ],
        string='Opening Reason',
        required=True,
        index=True,
    )
    notes = fields.Char(string='Justification Notes')
    state = fields.Selection(
        selection=[
            ('authorized', 'Authorized (Awaiting Pulse/Open)'),
            ('rejected', 'Rejected by Policy'),
            ('executed', 'Kick Pulse Sent (Awaiting Sensor)'),
            ('matched_physical_open', 'Correlated with Verified Physical Opening'),
            ('expired', 'Expired Unmatched'),
        ],
        string='Authorization State',
        default='authorized',
        required=True,
        index=True,
    )
    requested_at = fields.Datetime(string='Requested At', default=fields.Datetime.now, required=True, index=True)
    expires_at = fields.Datetime(string='Expires At', required=True, index=True)

    _sql_constraints = [
        ('auth_uuid_uniq', 'unique(auth_uuid)', 'Authorization UUID must be globally unique!'),
        ('idempotency_key_company_uniq', 'unique(idempotency_key, company_id)', 'Idempotency key already used!'),
    ]

    @api.model
    def request_authorization(
        self,
        drawer,
        reason,
        idempotency_key=None,
        pos_session_id=False,
        pos_order_id=False,
        cashier_id=False,
        supervisor_id=False,
        notes='',
    ):
        """
        Validates policy rules, cooldown lock, and role permissions before issuing
        an opening authorization token.
        """
        drawer.ensure_one()
        idem_key = idempotency_key or str(uuid.uuid4())
        existing = self.search([
            ('idempotency_key', '=', idem_key),
            ('company_id', '=', drawer.company_id.id),
        ], limit=1)
        if existing:
            return existing

        policy = drawer._get_effective_policy()
        now = fields.Datetime.now()
        window_sec = policy.correlation_window_seconds if policy else 5
        cooldown_sec = policy.cooldown_lock_seconds if policy else 3

        # Check duplicate pulse cooldown
        recent_auth = self.search([
            ('drawer_id', '=', drawer.id),
            ('state', 'in', ('authorized', 'executed', 'matched_physical_open')),
            ('requested_at', '>=', now - timedelta(seconds=cooldown_sec)),
        ], limit=1)
        if recent_auth:
            raise UserError(_('Drawer kick pulse cooldown active (%d seconds). Please wait before re-triggering.') % cooldown_sec)

        # Enforce supervisor requirement for No-Sale and Refund policies
        is_supervisor = (
            bool(supervisor_id)
            or self.env.user.has_group('powerpos_sunmi_core.group_powerpos_supervisor')
        )
        rejected = False
        reject_note = ''

        if reason == 'supervisor_no_sale' and (not policy or policy.no_sale_requires_supervisor) and not is_supervisor:
            rejected = True
            reject_note = _('Supervisor override is required for No-Sale cash drawer openings.')
        elif reason == 'cash_refund' and policy and policy.refund_open_policy == 'supervisor_required' and not is_supervisor:
            rejected = True
            reject_note = _('Supervisor approval is required for Cash Refund drawer openings.')
        elif reason == 'cash_refund' and policy and policy.refund_open_policy == 'disabled':
            rejected = True
            reject_note = _('Cash drawer opening on refund is disabled by store policy.')
        elif reason in ('supervisor_no_sale', 'maintenance_test') and (not policy or policy.manual_open_requires_reason) and not (notes or '').strip():
            rejected = True
            reject_note = _('A justification note is required for Manual / No-Sale drawer openings.')

        auth_rec = self.sudo().create({
            'idempotency_key': idem_key,
            'drawer_id': drawer.id,
            'pos_session_id': pos_session_id or (drawer.current_pos_session_id.id if drawer.current_pos_session_id else False),
            'pos_order_id': pos_order_id or False,
            'cashier_id': cashier_id or (drawer.current_cashier_id.id if drawer.current_cashier_id else False),
            'user_id': self.env.user.id,
            'supervisor_id': supervisor_id or (self.env.user.id if is_supervisor else False),
            'reason': reason,
            'notes': reject_note if rejected else notes,
            'state': 'rejected' if rejected else 'authorized',
            'requested_at': now,
            'expires_at': now + timedelta(seconds=window_sec),
        })

        if rejected:
            raise AccessError(reject_note)
        return auth_rec
