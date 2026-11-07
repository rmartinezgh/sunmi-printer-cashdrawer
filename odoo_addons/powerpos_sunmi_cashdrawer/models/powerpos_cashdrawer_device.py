from datetime import timedelta
from odoo import api, fields, models, _


class PowerPosCashDrawerDevice(models.Model):
    _name = 'powerpos.cashdrawer.device'
    _description = 'PowerPOS Cash Drawer Hardware Asset & Physical State Monitor'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'branch_id, name, id'

    drawer_code = fields.Char(
        string='Cash Drawer ID',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('New'),
        index=True,
        tracking=True,
    )
    name = fields.Char(string='Cash Drawer Name', required=True, tracking=True)
    drawer_model = fields.Char(
        string='Drawer Model',
        default='SUNMI Max / Standard RJ11 24V Cash Drawer',
        required=True,
    )
    printer_device_id = fields.Many2one(
        'powerpos.sunmi.device',
        string='Connected SUNMI Printer / Terminal',
        required=True,
        index=True,
        tracking=True,
    )
    pos_config_id = fields.Many2one(
        'pos.config',
        string='Assigned POS Register',
         index=True,
        tracking=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        related='printer_device_id.company_id',
        store=True,
        readonly=True,
        index=True,
    )
    branch_id = fields.Many2one(
        'powerpos.branch',
        string='Assigned Branch',
        related='printer_device_id.branch_id',
        store=True,
        readonly=True,
        index=True,
    )
    sensor_type = fields.Selection(
        selection=[
            ('rj11_pin3_microswitch', 'RJ11 Pin-3 Internal Latch Microswitch'),
            ('external_gpio_reed', 'External GPIO Magnetic Reed Sensor'),
            ('none', 'No Physical State Sensor (Pulse Only)'),
        ],
        string='Physical Sensor Type',
        default='rj11_pin3_microswitch',
        required=True,
        tracking=True,
    )
    monitoring_method = fields.Selection(
        selection=[
            ('android_printerx_bridge', 'SUNMI Android PrinterX Bridge (Event + Polling)'),
            ('local_companion_agent', 'Local Companion Hardware Agent'),
            ('command_only_no_feedback', 'Command Only (No Physical Feedback)'),
        ],
        string='Monitoring Method',
        default='android_printerx_bridge',
        required=True,
        tracking=True,
    )
    physical_status_capability = fields.Selection(
        selection=[
            ('SUPPORTED', 'Supported & Verified'),
            ('UNSUPPORTED', 'Unsupported'),
            ('UNVERIFIED', 'Unverified'),
            ('ERROR', 'Sensor Error'),
        ],
        string='Physical Status Capability',
        default='UNVERIFIED',
        required=True,
        tracking=True,
    )
    physical_state = fields.Selection(
        selection=[
            ('OPEN', 'OPEN (Physically Verified)'),
            ('CLOSED', 'CLOSED (Physically Verified)'),
            ('UNKNOWN', 'UNKNOWN (Awaiting Telemetry)'),
            ('UNSUPPORTED', 'UNSUPPORTED (No Physical Sensor)'),
            ('STALE', 'STALE (Heartbeat Timeout)'),
            ('ERROR', 'ERROR (Sensor Fault)'),
            ('OFFLINE', 'OFFLINE (Device Disconnected)'),
        ],
        string='Current Physical State',
        default='UNKNOWN',
        required=True,
        index=True,
        tracking=True,
    )
    last_verified_state = fields.Selection(
        selection=[
            ('OPEN', 'OPEN'),
            ('CLOSED', 'CLOSED'),
            ('UNKNOWN', 'UNKNOWN'),
        ],
        string='Last Verified Physical Reading',
        default='UNKNOWN',
        readonly=True,
    )
    last_sensor_timestamp = fields.Datetime(string='Last Sensor Reading Timestamp', readonly=True)
    last_heartbeat = fields.Datetime(string='Last Bridge Heartbeat', readonly=True)

    current_pos_session_id = fields.Many2one('pos.session', string='Active POS Session', index=True)
    current_cashier_id = fields.Many2one('hr.employee', string='Current Cashier', index=True)

    open_since_timestamp = fields.Datetime(
        string='Physically Open Since',
        readonly=True,
        help='Timestamp when the current verified CLOSED -> OPEN transition started.',
    )
    current_open_duration_seconds = fields.Integer(
        string='Current Open Duration (s)',
        compute='_compute_current_open_duration',
    )
    last_closed_duration_seconds = fields.Float(
        string='Last Completed Open Duration (s)',
        readonly=True,
    )
    is_current_open_incomplete = fields.Boolean(
        string='Open Duration Incomplete (Telemetry Lost While Open)',
        default=False,
        readonly=True,
    )
    sdk_open_counter = fields.Integer(
        string='Cumulative Verified Openings Count',
        default=0,
        readonly=True,
    )

    monitoring_enabled = fields.Boolean(string='Real-Time Monitoring Enabled', default=True, tracking=True)
    polling_interval_ms = fields.Integer(
        string='Bridge Sensor Polling Interval (ms)',
        default=250,
        required=True,
    )
    staleness_timeout_seconds = fields.Integer(
        string='Telemetry Staleness Timeout (s)',
        default=60,
        required=True,
    )
    alert_prolonged_open_seconds = fields.Integer(
        string='Prolonged Open Alert Threshold (s)',
        default=30,
        required=True,
    )
    device_status = fields.Selection(
        selection=[
            ('online', 'Online'),
            ('offline', 'Offline'),
            ('degraded', 'Degraded'),
            ('error', 'Error'),
        ],
        string='Drawer Operational Status',
        default='offline',
        required=True,
        tracking=True,
    )
    active = fields.Boolean(string='Active', default=True)

    _sql_constraints = [
        ('drawer_code_uniq', 'unique(drawer_code)', 'Cash Drawer ID must be unique!'),
    ]

    @api.depends('physical_state', 'open_since_timestamp')
    def _compute_current_open_duration(self):
        now = fields.Datetime.now()
        for drawer in self:
            if drawer.physical_state == 'OPEN' and drawer.open_since_timestamp:
                delta = (now - drawer.open_since_timestamp).total_seconds()
                drawer.current_open_duration_seconds = max(0, int(delta))
            else:
                drawer.current_open_duration_seconds = 0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('drawer_code', _('New')) == _('New'):
                vals['drawer_code'] = self.env['ir.sequence'].next_by_code('powerpos.cashdrawer.device') or _('CD-00001')
        return super().create(vals_list)

    def _get_effective_policy(self):
        self.ensure_one()
        Policy = self.env['powerpos.cashdrawer.policy']
        if self.pos_config_id:
            pol = Policy.search([
                ('pos_config_id', '=', self.pos_config_id.id),
                ('active', '=', True),
            ], limit=1)
            if pol:
                return pol
        if self.branch_id:
            pol = Policy.search([
                ('branch_id', '=', self.branch_id.id),
                ('pos_config_id', '=', False),
                ('active', '=', True),
            ], limit=1)
            if pol:
                return pol
        return Policy.search([
            ('company_id', '=', self.company_id.id),
            ('branch_id', '=', False),
            ('pos_config_id', '=', False),
            ('active', '=', True),
        ], limit=1)

    def process_physical_state_reading(
        self,
        new_state,
        device_timestamp=None,
        event_source='android_bridge',
        sequence_number=0,
        sensor_quality='verified',
        event_uuid=None,
        authorization_uuid=None,
        pos_session_id=False,
        pos_order_id=False,
        cashier_id=False,
    ):
        """
        Truthful Cash Drawer Physical State Machine (Section 10.2):
        - UNKNOWN -> OPEN: Initial state observation (does NOT increment opening counter)
        - UNKNOWN -> CLOSED: Initial state observation
        - CLOSED -> OPEN: Verified physical opening (starts duration timer, correlates with active authorization within 5s window or flags unmatched_opening)
        - OPEN -> CLOSED: Verified physical closing (stops timer, persists verified duration)
        - ANY -> STALE / OFFLINE / ERROR: If previously OPEN, marks duration as incomplete and NEVER synthesizes a fake CLOSED event!
        """
        self.ensure_one()
        now = fields.Datetime.now()
        observed_dt = device_timestamp or now
        prev_state = self.physical_state

        if self.sensor_type == 'none' or self.printer_device_id.connection_type == 'sunmi_cloud':
            new_state = 'UNSUPPORTED'

        update_vals = {
            'last_sensor_timestamp': observed_dt,
            'last_heartbeat': now,
            'device_status': 'online' if new_state in ('OPEN', 'CLOSED', 'UNSUPPORTED') else (
                'error' if new_state == 'ERROR' else 'offline'
            ),
        }
        if pos_session_id:
            update_vals['current_pos_session_id'] = pos_session_id
        if cashier_id:
            update_vals['current_cashier_id'] = cashier_id

        event_type_to_log = False
        opening_reason = False
        matched_auth = False
        computed_duration = 0.0
        duration_incomplete = False

        if prev_state == 'UNKNOWN' and new_state in ('OPEN', 'CLOSED'):
            # Initial state observation upon startup/reconnect
            update_vals['physical_state'] = new_state
            update_vals['last_verified_state'] = new_state
            update_vals['physical_status_capability'] = 'SUPPORTED'
            if new_state == 'OPEN':
                update_vals['open_since_timestamp'] = observed_dt
                update_vals['is_current_open_incomplete'] = True
            event_type_to_log = 'drawer_opened' if new_state == 'OPEN' else 'drawer_closed'
            opening_reason = 'initial_state_observation'

        elif prev_state in ('CLOSED', 'STALE', 'OFFLINE') and new_state == 'OPEN':
            # Verified physical opening transition!
            update_vals['physical_state'] = 'OPEN'
            update_vals['last_verified_state'] = 'OPEN'
            update_vals['physical_status_capability'] = 'SUPPORTED'
            update_vals['open_since_timestamp'] = observed_dt
            update_vals['is_current_open_incomplete'] = False
            update_vals['sdk_open_counter'] = self.sdk_open_counter + 1

            # Correlate with active pre-execution authorization within correlation window
            Auth = self.env['powerpos.cashdrawer.authorization'].sudo()
            if authorization_uuid:
                matched_auth = Auth.search([
                    ('auth_uuid', '=', authorization_uuid),
                    ('drawer_id', '=', self.id),
                ], limit=1)
            if not matched_auth:
                matched_auth = Auth.search([
                    ('drawer_id', '=', self.id),
                    ('state', 'in', ('authorized', 'executed')),
                    ('expires_at', '>=', now - timedelta(seconds=2)),
                ], order='requested_at desc', limit=1)

            if matched_auth:
                opening_reason = matched_auth.reason
                if matched_auth.reason == 'supervisor_no_sale':
                    event_type_to_log = 'supervisor_override_open'
                elif matched_auth.reason in ('cash_sale', 'cash_refund', 'cash_in', 'cash_out', 'starting_cash_count', 'closing_cash_count'):
                    event_type_to_log = 'sale_auto_open' if matched_auth.reason == 'cash_sale' else 'manual_open_authorized'
                else:
                    event_type_to_log = 'manual_open_authorized'
            else:
                event_type_to_log = 'unmatched_opening'
                opening_reason = 'unmatched_physical_open'

        elif prev_state == 'OPEN' and new_state == 'CLOSED':
            # Verified physical closing transition!
            if self.open_since_timestamp:
                computed_duration = max(0.0, (observed_dt - self.open_since_timestamp).total_seconds())
            duration_incomplete = self.is_current_open_incomplete
            update_vals.update({
                'physical_state': 'CLOSED',
                'last_verified_state': 'CLOSED',
                'open_since_timestamp': False,
                'last_closed_duration_seconds': computed_duration,
                'is_current_open_incomplete': False,
            })
            event_type_to_log = 'drawer_closed'

        elif new_state in ('STALE', 'OFFLINE', 'ERROR'):
            # Telemetry lost or errored — NEVER synthesize a fake CLOSED event!
            if prev_state == 'OPEN':
                duration_incomplete = True
                update_vals['is_current_open_incomplete'] = True
            update_vals['physical_state'] = new_state
            if prev_state != new_state:
                event_type_to_log = 'device_offline' if new_state in ('STALE', 'OFFLINE') else 'sensor_error'

        elif prev_state != new_state:
            update_vals['physical_state'] = new_state
            if new_state in ('OPEN', 'CLOSED'):
                update_vals['last_verified_state'] = new_state

        self.sudo().write(update_vals)

        created_event = False
        if event_type_to_log and 'powerpos.cashdrawer.event' in self.env:
            created_event = self.env['powerpos.cashdrawer.event'].sudo().record_hardware_event(
                drawer=self,
                event_type=event_type_to_log,
                previous_physical_state=prev_state,
                new_physical_state=new_state,
                opening_reason=opening_reason or 'telemetry_transition',
                device_observed_timestamp=observed_dt,
                opening_duration=computed_duration,
                is_duration_incomplete=duration_incomplete,
                event_source=event_source,
                sequence_number=sequence_number,
                sensor_quality=sensor_quality,
                event_uuid=event_uuid,
                authorization=matched_auth,
                pos_session_id=pos_session_id or (self.current_pos_session_id.id if self.current_pos_session_id else False),
                pos_order_id=pos_order_id or (matched_auth.pos_order_id.id if matched_auth and matched_auth.pos_order_id else False),
                cashier_id=cashier_id or (self.current_cashier_id.id if self.current_cashier_id else False),
            )
            if matched_auth and created_event:
                auth_write_vals = {'state': 'matched_physical_open'}
                if 'matched_event_id' in matched_auth._fields:
                    auth_write_vals['matched_event_id'] = created_event.id
                matched_auth.sudo().write(auth_write_vals)

        return created_event

    @api.model
    def _cron_evaluate_drawer_staleness(self):
        """Marks drawers STALE if heartbeat exceeds staleness_timeout_seconds and checks prolonged open thresholds."""
        now = fields.Datetime.now()
        drawers = self.sudo().search([('monitoring_enabled', '=', True), ('active', '=', True)])
        for drawer in drawers:
            if drawer.last_heartbeat:
                age_sec = (now - drawer.last_heartbeat).total_seconds()
                if age_sec > (drawer.staleness_timeout_seconds or 60) and drawer.physical_state not in ('STALE', 'OFFLINE', 'UNSUPPORTED'):
                    drawer.process_physical_state_reading(
                        new_state='STALE',
                        device_timestamp=now,
                        event_source='staleness_watchdog',
                        sensor_quality='stale_timeout',
                    )
            if drawer.physical_state == 'OPEN' and drawer.open_since_timestamp:
                open_sec = (now - drawer.open_since_timestamp).total_seconds()
                threshold = drawer.alert_prolonged_open_seconds or 30
                if open_sec >= threshold and 'powerpos.cashdrawer.event' in self.env:
                    # Check if drawer_open_timeout event was already logged for this opening
                    recent_timeout = self.env['powerpos.cashdrawer.event'].sudo().search([
                        ('cash_drawer_id', '=', drawer.id),
                        ('event_type', '=', 'drawer_open_timeout'),
                        ('server_received_timestamp', '>=', drawer.open_since_timestamp),
                    ], limit=1)
                    if not recent_timeout:
                        self.env['powerpos.cashdrawer.event'].sudo().record_hardware_event(
                            drawer=drawer,
                            event_type='drawer_open_timeout',
                            previous_physical_state='OPEN',
                            new_physical_state='OPEN',
                            opening_reason='prolonged_open_threshold_exceeded',
                            device_observed_timestamp=now,
                            opening_duration=open_sec,
                            is_duration_incomplete=False,
                            event_source='staleness_watchdog',
                        )

    def get_pos_safe_metadata(self):
        self.ensure_one()
        return {
            'id': self.id,
            'drawer_code': self.drawer_code,
            'name': self.name,
            'drawer_model': self.drawer_model,
            'printer_device_id': self.printer_device_id.id,
            'sensor_type': self.sensor_type,
            'monitoring_method': self.monitoring_method,
            'physical_status_capability': self.physical_status_capability,
            'physical_state': self.physical_state,
            'last_verified_state': self.last_verified_state,
            'last_sensor_timestamp': fields.Datetime.to_string(self.last_sensor_timestamp) if self.last_sensor_timestamp else False,
            'open_since_timestamp': fields.Datetime.to_string(self.open_since_timestamp) if self.open_since_timestamp else False,
            'current_open_duration_seconds': self.current_open_duration_seconds,
            'alert_prolonged_open_seconds': self.alert_prolonged_open_seconds,
            'polling_interval_ms': self.polling_interval_ms,
            'device_status': self.device_status,
        }

    @api.model
    def _load_pos_data_domain(self, data):
        """Odoo 20 pos.load.mixin domain hook restricting loaded cash drawers to the active POS config."""
        config_records = data.get('pos.config', {}).get('data', [])
        config_ids = [c['id'] for c in config_records if 'id' in c]
        if config_ids:
            return [('pos_config_id', 'in', config_ids), ('active', '=', True)]
        return [('active', '=', True)]

    @api.model
    def _load_pos_data_fields(self, config_id):
        """Odoo 20 pos.load.mixin field allowlist for POS frontend."""
        return [
            'id',
            'drawer_code',
            'name',
            'drawer_model',
            'printer_device_id',
            'pos_config_id',
            'branch_id',
            'sensor_type',
            'monitoring_method',
            'physical_status_capability',
            'physical_state',
            'last_verified_state',
            'last_sensor_timestamp',
            'open_since_timestamp',
            'current_open_duration_seconds',
            'alert_prolonged_open_seconds',
            'polling_interval_ms',
            'device_status',
        ]
