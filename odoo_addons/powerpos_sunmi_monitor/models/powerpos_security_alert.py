import uuid
from odoo import fields, models, _
from odoo.exceptions import AccessError, UserError


class PowerPosSecurityAlert(models.Model):
    _name = 'powerpos.security.alert'
    _description = 'Cash Drawer & Hardware Security Alert'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'

    alert_uuid = fields.Char(
        string='Alert UUID',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: str(uuid.uuid4()),
        index=True,
    )
    company_id = fields.Many2one('res.company', string='Company', required=True, index=True)
    branch_id = fields.Many2one('powerpos.branch', string='Branch', required=True, index=True)
    pos_config_id = fields.Many2one('pos.config', string='POS Register', index=True)
    cash_drawer_id = fields.Many2one('powerpos.cashdrawer.device', string='Cash Drawer', required=True, index=True)
    printer_id = fields.Many2one('powerpos.sunmi.device', string='Printer / Terminal', index=True)
    event_id = fields.Many2one('powerpos.cashdrawer.event', string='Triggering Audit Event', readonly=True, index=True)
    cashier_id = fields.Many2one('hr.employee', string='Cashier on Duty', index=True)
    pos_session_id = fields.Many2one('pos.session', string='POS Session', index=True)
    rule_id = fields.Many2one('powerpos.monitoring.rule', string='Triggered Rule', readonly=True)

    alert_category = fields.Selection(
        selection=[
            ('prolonged_open', 'Drawer Left Open Beyond Threshold'),
            ('unmatched_opening', 'Unmatched Physical Opening'),
            ('excessive_manual_open', 'Excessive Manual / No-Sale Openings'),
            ('outside_session_open', 'Opening Outside Active POS Session'),
            ('hardware_offline_or_error', 'Printer / Sensor Offline or Fault'),
        ],
        string='Alert Category',
        required=True,
        index=True,
    )
    severity = fields.Selection(
        selection=[
            ('info', 'Info'),
            ('warning', 'Warning'),
            ('critical', 'Critical'),
        ],
        string='Severity',
        default='warning',
        required=True,
        index=True,
        tracking=True,
    )
    title = fields.Char(string='Alert Summary', required=True)
    description = fields.Text(string='Detailed Description')

    state = fields.Selection(
        selection=[
            ('active', 'Active (Unacknowledged)'),
            ('acknowledged', 'Acknowledged by Supervisor'),
            ('investigating', 'Under Investigation'),
            ('resolved', 'Resolved & Closed'),
        ],
        string='Lifecycle State',
        default='active',
        required=True,
        index=True,
        tracking=True,
    )
    acknowledged_by = fields.Many2one('res.users', string='Acknowledged By', readonly=True)
    acknowledged_at = fields.Datetime(string='Acknowledged At', readonly=True)
    investigation_notes = fields.Text(string='Investigation Notes', tracking=True)
    resolution_summary = fields.Text(string='Resolution Summary', tracking=True)
    resolved_by = fields.Many2one('res.users', string='Resolved By', readonly=True)
    resolved_at = fields.Datetime(string='Resolved At', readonly=True)

    def _check_supervisor_access(self):
        if not self.env.su and not self.env.user.has_group('powerpos_sunmi_core.group_powerpos_supervisor'):
            raise AccessError(_('Only Supervisors, Store Managers, or System Administrators can acknowledge or resolve security alerts.'))

    def action_acknowledge(self):
        self._check_supervisor_access()
        now = fields.Datetime.now()
        for alert in self:
            if alert.state == 'active':
                alert.write({
                    'state': 'acknowledged',
                    'acknowledged_by': self.env.user.id,
                    'acknowledged_at': now,
                })
        return True

    def action_start_investigation(self):
        self._check_supervisor_access()
        now = fields.Datetime.now()
        for alert in self:
            vals = {'state': 'investigating'}
            if not alert.acknowledged_by:
                vals.update({
                    'acknowledged_by': self.env.user.id,
                    'acknowledged_at': now,
                })
            alert.write(vals)
            if alert.event_id:
                alert.event_id.write({
                    'review_status': 'under_investigation',
                    'supervisor_id': self.env.user.id,
                })
        return True

    def action_resolve_alert(self):
        self._check_supervisor_access()
        now = fields.Datetime.now()
        for alert in self:
            if not (alert.resolution_summary or '').strip():
                raise UserError(_('Please enter a Resolution Summary before closing security alert %s.') % alert.title)
            alert.write({
                'state': 'resolved',
                'resolved_by': self.env.user.id,
                'resolved_at': now,
            })
            if alert.event_id and alert.event_id.review_status in ('pending_review', 'under_investigation'):
                alert.event_id.write({
                    'review_status': 'resolved_justified',
                    'supervisor_id': self.env.user.id,
                    'review_notes': alert.resolution_summary,
                })
        return True
