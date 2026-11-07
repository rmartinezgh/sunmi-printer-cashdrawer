import uuid
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PowerPosSunmiPrintJob(models.Model):
    _name = 'powerpos.sunmi.print.job'
    _description = 'SUNMI Idempotent Print Job & Queue Record'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'

    job_uuid = fields.Char(
        string='Job UUID (trade_no)',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: uuid.uuid4().hex[:32],
        index=True,
        tracking=True,
    )
    device_id = fields.Many2one(
        'powerpos.sunmi.device',
        string='Target SUNMI Printer',
        required=True,
        index=True,
        tracking=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        related='device_id.company_id',
        store=True,
        readonly=True,
        index=True,
    )
    branch_id = fields.Many2one(
        'powerpos.branch',
        string='Branch',
        related='device_id.branch_id',
        store=True,
        readonly=True,
        index=True,
    )
    pos_order_id = fields.Many2one('pos.order', string='POS Order', index=True)
    pos_session_id = fields.Many2one('pos.session', string='POS Session', index=True)
    receipt_type = fields.Selection(
        selection=[
            ('official_sales', '1. Official Sales Receipt / Invoice'),
            ('cash_receipt', '2. Cash Payment Receipt'),
            ('card_receipt', '3. Card / Terminal Payment Slip'),
            ('qr_receipt', '4. QR Ph / E-Wallet Payment Slip'),
            ('refund_receipt', '5. Refund / Return Credit Note'),
            ('void_record', '6. Voided Transaction Slip'),
            ('reprint_receipt', '7. Duplicate / Reprint Receipt'),
            ('cash_in', '8. Cash-In / Float Deposit Slip'),
            ('cash_out', '9. Cash-Out / Safe Drop Slip'),
            ('x_report', '10. Mid-Shift X-Reading Report'),
            ('z_report', '11. End-of-Day Z-Reading Report'),
            ('eod_summary', '12. End-of-Day Settlement Summary'),
            ('kot', '13. Kitchen Order Ticket (KOT)'),
            ('customer_order_number', '14. Customer Queue / Order Number Slip'),
        ],
        string='Receipt Type',
        required=True,
        default='official_sales',
        tracking=True,
    )
    state = fields.Selection(
        selection=[
            ('pending', 'Pending'),
            ('submitted', 'Submitted to Transport'),
            ('accepted', 'Accepted by Printer Queue'),
            ('printed', 'Printed & Verified'),
            ('failed', 'Failed'),
            ('unknown', 'Unknown / Ambiguous Timeout'),
            ('cancelled', 'Cancelled'),
        ],
        string='Print Job State',
        default='pending',
        required=True,
        index=True,
        tracking=True,
    )
    payload_hex = fields.Text(string='ESC/POS Hex Payload', required=True)
    retry_count = fields.Integer(string='Retry Count', default=0, readonly=True)
    error_message = fields.Text(string='Error / Reconciliation Notes', readonly=True)
    submitted_at = fields.Datetime(string='Submitted At', readonly=True)
    completed_at = fields.Datetime(string='Completed At', readonly=True)

    _sql_constraints = [
        ('job_uuid_uniq', 'unique(job_uuid)', 'Print Job UUID (trade_no) must be globally unique for idempotency!'),
    ]

    def _get_cloud_account(self):
        self.ensure_one()
        account = self.env['powerpos.sunmi.cloud.account'].search([
            ('company_id', '=', self.company_id.id),
            ('active', '=', True),
        ], limit=1)
        if not account:
            raise UserError(_('No active SUNMI Cloud Account configured for company %s.') % self.company_id.name)
        return account

    def action_submit_job(self):
        """Submits print job idempotently using job_uuid as trade_no."""
        client = self.env['powerpos.sunmi.cloud.client']
        for job in self:
            if job.state in ('printed', 'accepted'):
                continue
            if job.state == 'unknown':
                # Must reconcile before re-submitting so accepted jobs never double print!
                job.action_reconcile_ambiguous_job()
                if job.state in ('printed', 'accepted'):
                    continue

            now = fields.Datetime.now()
            job.write({
                'state': 'submitted',
                'submitted_at': now,
            })

            if job.device_id.connection_type in ('sunmi_cloud', 'hybrid_local_cloud'):
                account = job._get_cloud_account()
                sn = job.device_id.cloud_printer_sn or job.device_id.serial_number
                res = client.push_content(
                    account=account,
                    sn=sn,
                    trade_no=job.job_uuid,
                    content_hex=job.payload_hex,
                )
                if res.get('ok'):
                    job.write({
                        'state': 'accepted',
                        'error_message': False,
                    })
                elif res.get('timeout_or_ambiguous'):
                    job.write({
                        'state': 'unknown',
                        'error_message': _('Ambiguous transport timeout; run printStatus reconciliation before retrying.'),
                    })
                else:
                    job.write({
                        'state': 'failed',
                        'retry_count': job.retry_count + 1,
                        'error_message': res.get('error', _('Cloud pushContent rejected.')),
                    })
            else:
                # Local Android SDK / LAN TCP queue picked up by local bridge or WebSocket
                job.write({
                    'state': 'accepted',
                    'error_message': _('Queued for local SUNMI Android Bridge execution.'),
                })
        return True

    def action_reconcile_ambiguous_job(self):
        """
        Queries SUNMI Cloud printStatus(trade_no) before retrying a timed-out job
        so jobs already accepted or printed by the printer never print twice.
        """
        client = self.env['powerpos.sunmi.cloud.client']
        for job in self:
            if job.device_id.connection_type not in ('sunmi_cloud', 'hybrid_local_cloud'):
                continue
            account = job._get_cloud_account()
            res = client.print_status(account, job.job_uuid)
            if res.get('ok'):
                data = res.get('data') or {}
                ticket_status = str(data.get('status', '')).lower()
                if ticket_status in ('1', 'printed', 'success', 'completed'):
                    job.write({
                        'state': 'printed',
                        'completed_at': fields.Datetime.now(),
                        'error_message': _('Reconciled via printStatus(): confirmed printed on device.'),
                    })
                elif ticket_status in ('0', 'queued', 'accepted', 'waiting'):
                    job.write({
                        'state': 'accepted',
                        'error_message': _('Reconciled via printStatus(): job already queued on printer; duplicate retry prevented.'),
                    })
                elif ticket_status in ('2', 'failed', 'error'):
                    job.write({
                        'state': 'failed',
                        'error_message': _('Reconciled via printStatus(): printer reported failure; safe to retry with new trade_no if needed.'),
                    })
        return True

    def action_clear_queue_authorized(self):
        """Clears the hardware print queue for the assigned printer (requires Supervisor or above)."""
        self.ensure_one()
        if not self.env.user.has_group('powerpos_sunmi_core.group_powerpos_supervisor'):
            raise UserError(_('Only Supervisors, Store Managers, or System Administrators can clear hardware print queues.'))
        account = self._get_cloud_account()
        sn = self.device_id.cloud_printer_sn or self.device_id.serial_number
        res = self.env['powerpos.sunmi.cloud.client'].clear_print_job(account, sn)
        if res.get('ok'):
            self.write({
                'state': 'cancelled',
                'error_message': _('Hardware queue cleared by %s.') % self.env.user.name,
            })
        return res
