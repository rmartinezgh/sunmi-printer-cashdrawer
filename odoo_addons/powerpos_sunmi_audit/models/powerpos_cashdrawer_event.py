import hashlib
import uuid
from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError


class PowerPosCashDrawerEvent(models.Model):
    _name = 'powerpos.cashdrawer.event'
    _description = 'Immutable Cryptographic Cash Drawer Event Audit Ledger'
    _order = 'server_received_timestamp desc, id desc'

    event_uuid = fields.Char(
        string='Event UUID',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: str(uuid.uuid4()),
        index=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        readonly=True,
        index=True,
    )
    branch_id = fields.Many2one(
        'powerpos.branch',
        string='Branch',
        required=True,
        readonly=True,
        index=True,
    )
    pos_config_id = fields.Many2one(
        'pos.config',
        string='POS Register',
        readonly=True,
        index=True,
    )
    cash_drawer_id = fields.Many2one(
        'powerpos.cashdrawer.device',
        string='Cash Drawer',
        required=True,
        readonly=True,
        index=True,
    )
    printer_id = fields.Many2one(
        'powerpos.sunmi.device',
        string='SUNMI Printer / Terminal',
        required=True,
        readonly=True,
        index=True,
    )
    cashier_id = fields.Many2one('hr.employee', string='Cashier Employee', readonly=True, index=True)
    employee_id = fields.Many2one('hr.employee', string='Operator Employee', readonly=True)
    user_id = fields.Many2one('res.users', string='Logged-In User', readonly=True, index=True)
    pos_session_id = fields.Many2one('pos.session', string='POS Session', readonly=True, index=True)
    pos_order_id = fields.Many2one('pos.order', string='POS Order', readonly=True, index=True)
    authorization_id = fields.Many2one(
        'powerpos.cashdrawer.authorization',
        string='Matched Authorization Record',
        readonly=True,
        index=True,
    )
    authorization_ref = fields.Char(string='Authorization UUID Reference', readonly=True)

    event_type = fields.Selection(
        selection=[
            ('drawer_open_requested', '1. Drawer Open Requested'),
            ('drawer_open_command_sent', '2. Drawer Kick Command Sent'),
            ('drawer_opened', '3. Drawer Physically Opened'),
            ('drawer_closed', '4. Drawer Physically Closed'),
            ('drawer_open_timeout', '5. Drawer Left Open Beyond Threshold'),
            ('manual_open_authorized', '6. Manual Authorized Opening'),
            ('supervisor_override_open', '7. Supervisor Override Opening'),
            ('sale_auto_open', '8. Automatic Cash Sale Opening'),
            ('unmatched_opening', '9. Unmatched Physical Opening (Key / Tamper / Unauthorized)'),
            ('drawer_open_failed', '10. Drawer Failed to Open After Command'),
            ('sensor_error', '11. Drawer Physical Sensor Fault'),
            ('device_offline', '12. Device / Telemetry Offline'),
        ],
        string='Event Classification',
        required=True,
        readonly=True,
        index=True,
    )
    previous_physical_state = fields.Char(string='Previous Physical State', readonly=True)
    new_physical_state = fields.Char(string='New Physical State', readonly=True)
    opening_reason = fields.Char(string='Opening Reason / Trigger', readonly=True, index=True)

    opening_request_timestamp = fields.Datetime(string='Authorization Request Timestamp', readonly=True)
    device_observed_timestamp = fields.Datetime(string='Device Observed Timestamp', required=True, readonly=True, index=True)
    server_received_timestamp = fields.Datetime(
        string='Server Received Timestamp',
        required=True,
        default=fields.Datetime.now,
        readonly=True,
        index=True,
    )
    clock_drift_seconds = fields.Float(
        string='Device-to-Server Clock Drift (s)',
        readonly=True,
    )
    opening_duration = fields.Float(
        string='Verified Open Duration (s)',
        default=0.0,
        readonly=True,
    )
    is_duration_incomplete = fields.Boolean(
        string='Duration Incomplete (Telemetry Interrupted)',
        default=False,
        readonly=True,
    )
    event_source = fields.Selection(
        selection=[
            ('android_bridge', 'SUNMI Android Bridge'),
            ('local_companion', 'Local Companion Service'),
            ('pos_frontend', 'Odoo POS UI'),
            ('staleness_watchdog', 'Server Staleness Watchdog'),
            ('cloud_webhook', 'SUNMI Cloud Webhook'),
        ],
        string='Event Source',
        default='android_bridge',
        required=True,
        readonly=True,
    )
    sequence_number = fields.Integer(string='Device Monotonic Sequence No.', default=0, readonly=True)
    sensor_quality = fields.Char(string='Sensor Signal Quality', default='verified', readonly=True)
    hardware_firmware = fields.Char(string='Device Firmware Version', readonly=True)

    previous_event_hash = fields.Char(
        string='Previous Event SHA-256 Hash',
        required=True,
        readonly=True,
        default='GENESIS',
    )
    event_hash = fields.Char(
        string='Event SHA-256 Cryptographic Hash',
        required=True,
        readonly=True,
        index=True,
    )

    # Supervisor Exception Review Fields (Only mutable fields after creation)
    review_status = fields.Selection(
        selection=[
            ('not_required', 'Review Not Required'),
            ('pending_review', 'Pending Supervisor Review'),
            ('under_investigation', 'Under Investigation'),
            ('resolved_justified', 'Resolved - Justified Operation'),
            ('resolved_exception', 'Resolved - Confirmed Security Exception'),
        ],
        string='Supervisor Review Status',
        default='not_required',
        required=True,
    )
    supervisor_id = fields.Many2one('res.users', string='Reviewing Supervisor')
    review_notes = fields.Text(string='Supervisor Investigation Notes')

    _sql_constraints = [
        ('event_uuid_uniq', 'unique(event_uuid)', 'Event UUID must be globally unique (replay/duplicate protection)!'),
    ]

    _ALLOWED_MUTABLE_FIELDS = {
        'review_status',
        'supervisor_id',
        'review_notes',
        'opening_duration',
        'is_duration_incomplete',
    }

    @api.model
    def compute_event_hash(self, vals, previous_hash):
        """
        Computes deterministic SHA-256 digest linking previous_event_hash with core immutable telemetry fields.
        """
        canonical_parts = [
            str(previous_hash or 'GENESIS'),
            str(vals.get('event_uuid', '')),
            str(vals.get('cash_drawer_id', '')),
            str(vals.get('printer_id', '')),
            str(vals.get('event_type', '')),
            str(vals.get('previous_physical_state', '')),
            str(vals.get('new_physical_state', '')),
            str(vals.get('opening_reason', '')),
            str(vals.get('device_observed_timestamp', '')),
            str(vals.get('sequence_number', 0)),
        ]
        canonical_str = '|'.join(canonical_parts)
        return hashlib.sha256(canonical_str.encode('utf-8')).hexdigest()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('event_uuid'):
                vals['event_uuid'] = str(uuid.uuid4())
            drawer_id = vals.get('cash_drawer_id')
            prev_event = self.sudo().search(
                [('cash_drawer_id', '=', drawer_id)],
                order='id desc',
                limit=1,
            ) if drawer_id else False
            prev_hash = prev_event.event_hash if prev_event else 'GENESIS'
            vals['previous_event_hash'] = prev_hash

            obs_ts = fields.Datetime.to_datetime(vals.get('device_observed_timestamp')) or fields.Datetime.now()
            srv_ts = fields.Datetime.to_datetime(vals.get('server_received_timestamp')) or fields.Datetime.now()
            vals['device_observed_timestamp'] = obs_ts
            vals['server_received_timestamp'] = srv_ts
            vals['clock_drift_seconds'] = round((srv_ts - obs_ts).total_seconds(), 3)

            if vals.get('event_type') in ('unmatched_opening', 'drawer_open_timeout', 'drawer_open_failed', 'sensor_error'):
                vals.setdefault('review_status', 'pending_review')
            else:
                vals.setdefault('review_status', 'not_required')

            vals['event_hash'] = self.compute_event_hash(vals, prev_hash)

        records = super().create(vals_list)
        if 'powerpos.monitoring.rule' in self.env:
            for rec in records:
                self.env['powerpos.monitoring.rule'].sudo().evaluate_event_for_alerts(rec)
        return records

    def write(self, vals):
        """Append-only enforcement: blocks modification of any core telemetry or hash field."""
        disallowed = set(vals.keys()) - self._ALLOWED_MUTABLE_FIELDS
        if disallowed:
            raise UserError(_(
                'Immutable Audit Log Violation: Cannot modify protected telemetry fields (%s) on powerpos.cashdrawer.event.'
            ) % ', '.join(sorted(disallowed)))
        if {'review_status', 'supervisor_id', 'review_notes'}.intersection(vals.keys()):
            if not self.env.su and not self.env.user.has_group('powerpos_sunmi_core.group_powerpos_supervisor'):
                raise AccessError(_('Only Supervisors, Store Managers, or System Administrators can update event review status.'))
        return super().write(vals)

    def unlink(self):
        raise UserError(_('Immutable Audit Log Violation: Cash drawer audit events cannot be deleted under any circumstance!'))

    @api.model
    def record_hardware_event(
        self,
        drawer,
        event_type,
        previous_physical_state,
        new_physical_state,
        opening_reason='',
        device_observed_timestamp=None,
        opening_duration=0.0,
        is_duration_incomplete=False,
        event_source='android_bridge',
        sequence_number=0,
        sensor_quality='verified',
        event_uuid=None,
        authorization=False,
        pos_session_id=False,
        pos_order_id=False,
        cashier_id=False,
    ):
        drawer.ensure_one()
        if event_uuid:
            existing = self.sudo().search([('event_uuid', '=', event_uuid)], limit=1)
            if existing:
                return existing

        vals = {
            'event_uuid': event_uuid or str(uuid.uuid4()),
            'company_id': drawer.company_id.id,
            'branch_id': drawer.branch_id.id,
            'pos_config_id': drawer.pos_config_id.id if drawer.pos_config_id else False,
            'cash_drawer_id': drawer.id,
            'printer_id': drawer.printer_device_id.id,
            'cashier_id': cashier_id or (drawer.current_cashier_id.id if drawer.current_cashier_id else False),
            'employee_id': cashier_id or (drawer.current_cashier_id.id if drawer.current_cashier_id else False),
            'user_id': (authorization.user_id.id if authorization and authorization.user_id else self.env.user.id),
            'pos_session_id': pos_session_id or (drawer.current_pos_session_id.id if drawer.current_pos_session_id else False),
            'pos_order_id': pos_order_id or (authorization.pos_order_id.id if authorization and authorization.pos_order_id else False),
            'authorization_id': authorization.id if authorization else False,
            'authorization_ref': authorization.auth_uuid if authorization else False,
            'event_type': event_type,
            'previous_physical_state': previous_physical_state,
            'new_physical_state': new_physical_state,
            'opening_reason': opening_reason,
            'opening_request_timestamp': authorization.requested_at if authorization else False,
            'device_observed_timestamp': device_observed_timestamp or fields.Datetime.now(),
            'server_received_timestamp': fields.Datetime.now(),
            'opening_duration': opening_duration,
            'is_duration_incomplete': is_duration_incomplete,
            'event_source': event_source,
            'sequence_number': sequence_number,
            'sensor_quality': sensor_quality,
            'hardware_firmware': drawer.printer_device_id.firmware_version or '',
        }
        return self.sudo().create(vals)

    @api.model
    def verify_audit_chain_integrity(self, drawer_id=None):
        """Verifies the cryptographic SHA-256 hash chain across all events for a cash drawer."""
        domain = [('cash_drawer_id', '=', drawer_id)] if drawer_id else []
        drawers = self.env['powerpos.cashdrawer.device'].sudo().search(
            [('id', '=', drawer_id)] if drawer_id else []
        )
        broken_events = []
        total_checked = 0
        for drawer in drawers:
            events = self.sudo().search([('cash_drawer_id', '=', drawer.id)], order='id asc')
            expected_prev = 'GENESIS'
            for ev in events:
                total_checked += 1
                recomputed = self.compute_event_hash({
                    'event_uuid': ev.event_uuid,
                    'cash_drawer_id': ev.cash_drawer_id.id,
                    'printer_id': ev.printer_id.id,
                    'event_type': ev.event_type,
                    'previous_physical_state': ev.previous_physical_state or '',
                    'new_physical_state': ev.new_physical_state or '',
                    'opening_reason': ev.opening_reason or '',
                    'device_observed_timestamp': ev.device_observed_timestamp,
                    'sequence_number': ev.sequence_number,
                }, expected_prev)
                if ev.previous_event_hash != expected_prev or ev.event_hash != recomputed:
                    broken_events.append(ev.event_uuid)
                expected_prev = ev.event_hash

        return {
            'verified': len(broken_events) == 0,
            'total_events_checked': total_checked,
            'broken_event_uuids': broken_events,
        }


class PowerPosCashDrawerAuthorizationAudit(models.Model):
    _inherit = 'powerpos.cashdrawer.authorization'

    matched_event_id = fields.Many2one(
        'powerpos.cashdrawer.event',
        string='Matched Audit Event',
        readonly=True,
    )

