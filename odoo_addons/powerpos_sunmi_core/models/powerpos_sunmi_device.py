import hashlib
import secrets
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class PowerPosSunmiDevice(models.Model):
    _name = 'powerpos.sunmi.device'
    _description = 'PowerPOS SUNMI Hardware Device'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'branch_id, name, id'

    device_code = fields.Char(
        string='Device ID',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('New'),
        index=True,
        tracking=True,
    )
    name = fields.Char(string='Device Name', required=True, tracking=True)
    serial_number = fields.Char(
        string='Hardware Serial Number (SN)',
        required=True,
        copy=False,
        index=True,
        tracking=True,
    )
    device_category = fields.Selection(
        selection=[
            ('terminal_builtin_printer', 'SUNMI Terminal with Built-in Printer'),
            ('standalone_cloud_printer', 'SUNMI Standalone Cloud / Network Printer'),
            ('usb_external_printer', 'SUNMI / ESC-POS USB External Printer'),
            ('bluetooth_printer', 'SUNMI Bluetooth Printer'),
            ('external_gpio_bridge', 'External GPIO Cash Drawer Sensor Bridge'),
        ],
        string='Device Category',
        required=True,
        default='terminal_builtin_printer',
        tracking=True,
    )
    device_model = fields.Selection(
        selection=[
            ('T2', 'SUNMI T2 / T2s'),
            ('T2_MINI', 'SUNMI T2 Mini'),
            ('T3_PRO', 'SUNMI T3 / T3 Pro'),
            ('D2', 'SUNMI D2 / D2s / D2s KDS'),
            ('D3_MINI', 'SUNMI D3 Mini / D3 Pro'),
            ('V2_PRO', 'SUNMI V2 / V2 Pro / V2s'),
            ('V3_MIX', 'SUNMI V3 / V3 Mix'),
            ('NT311', 'SUNMI 80mm Cloud Kitchen Printer (NT311)'),
            ('NT212', 'SUNMI 58mm Cloud Printer (NT212)'),
            ('OTHER', 'Other SUNMI / Compatible Device'),
        ],
        string='Device Model',
        required=True,
        default='T2',
        tracking=True,
    )
    manufacturer = fields.Char(string='Manufacturer', default='SUNMI Technology', required=True)
    firmware_version = fields.Char(string='Firmware / OS Version', tracking=True)
    sdk_version = fields.Char(string='SUNMI PrinterX / SDK Version', tracking=True)

    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
        tracking=True,
    )
    branch_id = fields.Many2one(
        'powerpos.branch',
        string='Assigned Branch / Store',
        required=True,
        domain="[('company_id', '=', company_id)]",
        index=True,
        tracking=True,
    )
    pos_config_id = fields.Many2one(
        'pos.config',
        string='Assigned POS Register',
        domain="[('company_id', '=', company_id)]",
        tracking=True,
    )
    assigned_terminal_name = fields.Char(
        string='Assigned POS Terminal Hostname / Label',
        tracking=True,
    )

    connection_type = fields.Selection(
        selection=[
            ('builtin_sdk', 'Built-in Android SUNMI PrinterX SDK'),
            ('lan_tcp', 'Local Network TCP/IP (Port 9100 / Local HTTP)'),
            ('usb_otg', 'USB Direct / OTG'),
            ('bluetooth', 'Bluetooth Classic / BLE'),
            ('sunmi_cloud', 'SUNMI Cloud OpenAPI'),
            ('hybrid_local_cloud', 'Hybrid (Local SDK + Cloud Fallback)'),
        ],
        string='Connection Type',
        required=True,
        default='builtin_sdk',
        tracking=True,
    )
    ip_address = fields.Char(string='IP Address', tracking=True)
    printer_port = fields.Integer(string='Printer Port', default=9100)
    cloud_printer_sn = fields.Char(
        string='SUNMI Cloud Printer SN',
        help='Serial number used for SUNMI OpenAPI cloud printing calls.',
        tracking=True,
    )
    auth_token_hash = fields.Char(
        string='Device Auth Token SHA-256 Hash',
        groups='powerpos_sunmi_core.group_powerpos_sysadmin',
        readonly=True,
        copy=False,
    )
    device_secret = fields.Char(
        string='Device HMAC Secret',
        groups='powerpos_sunmi_core.group_powerpos_sysadmin',
        readonly=True,
        copy=False,
    )
    paper_width = fields.Selection(
        selection=[
            ('58', '58 mm'),
            ('80', '80 mm'),
        ],
        string='Paper Width',
        default='80',
        required=True,
    )

    capability_ids = fields.One2many(
        'powerpos.device.capability',
        'device_id',
        string='Hardware Capabilities',
    )
    capability_classification = fields.Selection(
        selection=[
            ('full_physical_monitoring', 'Full Physical Monitoring (Pulse + Verified Pin-3 Feedback)'),
            ('opening_control_only', 'Opening Control Only (Kick Pulse Supported, No Sensor Readback)'),
            ('printer_connectivity_only', 'Printer Connectivity Only (No Cash Drawer Port)'),
            ('requires_external_sensor', 'Requires External GPIO / Microswitch Sensor'),
            ('unsupported', 'Unsupported Hardware'),
            ('not_yet_verified', 'Not Yet Verified'),
        ],
        string='Capability Classification',
        default='not_yet_verified',
        required=True,
        tracking=True,
    )
    cash_drawer_supported = fields.Boolean(
        string='RJ11 Cash Drawer Kick Supported',
        default=False,
        tracking=True,
    )
    physical_status_supported = fields.Boolean(
        string='Physical Open/Closed Status Feedback Supported',
        default=False,
        tracking=True,
    )
    physical_status_verified = fields.Boolean(
        string='Physical Sensor Hardware Verified',
        default=False,
        tracking=True,
        help='True only after 3-step physical calibration or live hardware telemetry verification.',
    )
    pin3_open_level = fields.Selection(
        selection=[
            ('high_is_open', 'Normally Closed Switch (Pin-3 HIGH = Drawer OPEN)'),
            ('low_is_open', 'Normally Open Switch (Pin-3 LOW = Drawer OPEN)'),
            ('uncalibrated', 'Uncalibrated'),
        ],
        string='Pin-3 Sensor Polarity',
        default='uncalibrated',
        required=True,
        tracking=True,
    )
    local_comm_supported = fields.Boolean(
        string='Local Bridge Communication Supported',
        default=True,
    )

    last_heartbeat = fields.Datetime(string='Last Heartbeat Timestamp', readonly=True)
    last_successful_connection = fields.Datetime(string='Last Successful Connection', readonly=True)
    device_status = fields.Selection(
        selection=[
            ('online', 'Online'),
            ('offline', 'Offline'),
            ('degraded', 'Degraded / Warning'),
            ('error', 'Hardware Error'),
            ('unpaired', 'Unpaired / Pending Setup'),
        ],
        string='Device Connectivity Status',
        default='unpaired',
        required=True,
        tracking=True,
    )
    printer_operational_status = fields.Selection(
        selection=[
            ('ready', 'Ready'),
            ('printing', 'Printing'),
            ('paper_near_end', 'Paper Near End'),
            ('paper_out', 'Out of Paper'),
            ('cover_open', 'Cover Open'),
            ('cutter_error', 'Cutter Jam / Error'),
            ('overheated', 'Printhead Overheated'),
            ('offline', 'Printer Offline'),
            ('unknown', 'Unknown'),
        ],
        string='Printer Operational Status',
        default='unknown',
        required=True,
        tracking=True,
    )
    last_error = fields.Text(string='Last Diagnostic / Error Message', readonly=True)
    date_registered = fields.Datetime(
        string='Date Registered',
        default=fields.Datetime.now,
        readonly=True,
    )
    last_modified = fields.Datetime(
        string='Last Modified',
         compute='_compute_last_modified',
        store=True,
    )
    active = fields.Boolean(string='Active', default=True, tracking=True)

    _sql_constraints = [
        ('serial_company_uniq', 'unique(serial_number, company_id)', 'A device with this Serial Number is already registered in this company!'),
        ('device_code_uniq', 'unique(device_code)', 'Device ID must be globally unique!'),
    ]

    @api.depends('write_date', 'create_date')
    def _compute_last_modified(self):
        for rec in self:
            rec.last_modified = rec.write_date or rec.create_date or fields.Datetime.now()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('device_code', _('New')) == _('New'):
                vals['device_code'] = self.env['ir.sequence'].next_by_code('powerpos.sunmi.device') or _('SUNMI-DEV-00001')
            if not vals.get('device_secret'):
                raw_secret = secrets.token_hex(32)
                vals['device_secret'] = raw_secret
                vals['auth_token_hash'] = hashlib.sha256(raw_secret.encode('utf-8')).hexdigest()
        records = super().create(vals_list)
        for rec in records:
            rec._initialize_default_capability_matrix()
        return records

    def _initialize_default_capability_matrix(self):
        """Seeds the standard capability rows per device based on known SUNMI model profiles without claiming hardware verification until tested."""
        self.ensure_one()
        existing_keys = set(self.capability_ids.mapped('feature_key'))
        model_profiles = {
            'T2': {'drawer_pulse': True, 'drawer_sensor': True, 'cutter': True, 'cloud': False},
            'T2_MINI': {'drawer_pulse': True, 'drawer_sensor': True, 'cutter': True, 'cloud': False},
            'T3_PRO': {'drawer_pulse': True, 'drawer_sensor': True, 'cutter': True, 'cloud': False},
            'D2': {'drawer_pulse': True, 'drawer_sensor': True, 'cutter': False, 'cloud': False},
            'D3_MINI': {'drawer_pulse': True, 'drawer_sensor': True, 'cutter': False, 'cloud': False},
            'V2_PRO': {'drawer_pulse': False, 'drawer_sensor': False, 'cutter': False, 'cloud': False},
            'V3_MIX': {'drawer_pulse': True, 'drawer_sensor': True, 'cutter': False, 'cloud': False},
            'NT311': {'drawer_pulse': True, 'drawer_sensor': False, 'cutter': True, 'cloud': True},
            'NT212': {'drawer_pulse': False, 'drawer_sensor': False, 'cutter': False, 'cloud': True},
            'OTHER': {'drawer_pulse': True, 'drawer_sensor': False, 'cutter': False, 'cloud': False},
        }
        profile = model_profiles.get(self.device_model, model_profiles['OTHER'])
        specs = [
            ('thermal_printing', 'Thermal Receipt Printing', True),
            ('auto_cutter', 'Automatic Paper Cutter', profile['cutter']),
            ('cash_drawer_pulse', 'RJ11 Cash Drawer Kick Pulse (24V)', profile['drawer_pulse']),
            ('cash_drawer_sensor_read', 'RJ11 Pin-3 Physical Open/Closed Sensor Readback', profile['drawer_sensor']),
            ('sunmi_cloud_openapi', 'SUNMI Cloud OpenAPI Transport', profile['cloud'] or self.connection_type in ('sunmi_cloud', 'hybrid_local_cloud')),
            ('local_android_bridge', 'Local Android PrinterX Bridge Telemetry', self.connection_type in ('builtin_sdk', 'hybrid_local_cloud')),
        ]
        cap_vals = []
        for key, label, declared in specs:
            if key not in existing_keys:
                cap_vals.append({
                    'device_id': self.id,
                    'feature_key': key,
                    'feature_name': label,
                    'vendor_declared': declared,
                    'hardware_verified': False,
                    'status': 'UNVERIFIED' if declared else 'UNSUPPORTED',
                    'diagnostic_notes': _('Initialized from SUNMI %s hardware profile; awaiting runtime verification.') % self.device_model,
                })
        if cap_vals:
            self.env['powerpos.device.capability'].sudo().create(cap_vals)

    def action_pair_device(self):
        """Generates or rotates the cryptographic device secret and marks the device ready for bridge handshake."""
        for device in self:
            if not self.env.user.has_group('powerpos_sunmi_core.group_powerpos_store_manager'):
                raise UserError(_('Only Store Managers or System Administrators can pair or rotate device tokens.'))
            new_secret = secrets.token_hex(32)
            token_hash = hashlib.sha256(new_secret.encode('utf-8')).hexdigest()
            device.sudo().write({
                'device_secret': new_secret,
                'auth_token_hash': token_hash,
                'device_status': 'online' if device.last_heartbeat else 'unpaired',
            })
            device.message_post(
                body=_('Device cryptographic credentials generated/rotated by %s. Token SHA-256 fingerprint: %s...')
                % (self.env.user.name, token_hash[:12])
            )
        return True

    def action_test_connection(self):
        """Performs connectivity verification without fabricating hardware sensor states."""
        self.ensure_one()
        now = fields.Datetime.now()
        if self.connection_type in ('sunmi_cloud', 'hybrid_local_cloud') and 'powerpos.sunmi.cloud.client' in self.env:
            client = self.env['powerpos.sunmi.cloud.client']
            account = self.env['powerpos.sunmi.cloud.account'].search([
                ('company_id', '=', self.company_id.id),
                ('active', '=', True),
            ], limit=1)
            if account and (self.cloud_printer_sn or self.serial_number):
                res = client.online_status(account, self.cloud_printer_sn or self.serial_number)
                is_online = res.get('online', False)
                self.write({
                    'device_status': 'online' if is_online else 'offline',
                    'printer_operational_status': 'ready' if is_online else 'offline',
                    'last_successful_connection': now if is_online else self.last_successful_connection,
                    'last_error': False if is_online else res.get('error', _('Cloud printer reported offline')),
                })
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('SUNMI Cloud Connectivity Check'),
                        'message': _('Device %s is %s.') % (self.name, _('ONLINE') if is_online else _('OFFLINE')),
                        'type': 'success' if is_online else 'warning',
                        'sticky': False,
                    }
                }

        # For local Android SDK / LAN devices, verify freshness of heartbeat
        if self.last_heartbeat:
            delta = (now - self.last_heartbeat).total_seconds()
            if delta <= 90:
                self.write({
                    'device_status': 'online',
                    'last_successful_connection': now,
                    'last_error': False,
                })
                msg = _('Local bridge heartbeat verified (%d seconds ago).') % int(delta)
                notif_type = 'success'
            else:
                self.write({
                    'device_status': 'offline',
                    'last_error': _('Heartbeat stale (%d seconds since last contact).') % int(delta),
                })
                msg = _('No recent heartbeat from Android Bridge (%d seconds ago).') % int(delta)
                notif_type = 'warning'
        else:
            self.write({
                'device_status': 'unpaired',
                'last_error': _('Awaiting initial Android Companion / Bridge registration.'),
            })
            msg = _('Device has not yet sent an initial hardware heartbeat.')
            notif_type = 'warning'

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Connection Diagnostic'),
                'message': msg,
                'type': notif_type,
                'sticky': False,
            }
        }

    def action_run_capability_discovery(self):
        """Evaluates device capabilities based on verified telemetry and updates capability_classification."""
        for device in self:
            if not device.capability_ids:
                device._initialize_default_capability_matrix()

            pulse_cap = device.capability_ids.filtered(lambda c: c.feature_key == 'cash_drawer_pulse')
            sensor_cap = device.capability_ids.filtered(lambda c: c.feature_key == 'cash_drawer_sensor_read')

            # Never claim physical_status_supported on pure cloud connections!
            if device.connection_type == 'sunmi_cloud':
                if sensor_cap:
                    sensor_cap.write({
                        'hardware_verified': False,
                        'status': 'UNSUPPORTED',
                        'last_checked': fields.Datetime.now(),
                        'diagnostic_notes': _('SUNMI Cloud OpenAPI does not expose real-time RJ11 Pin-3 physical state feedback.'),
                    })
                device.write({
                    'cash_drawer_supported': bool(pulse_cap and pulse_cap.vendor_declared),
                    'physical_status_supported': False,
                    'physical_status_verified': False,
                    'capability_classification': 'opening_control_only' if (pulse_cap and pulse_cap.vendor_declared) else 'printer_connectivity_only',
                })
            else:
                has_pulse = bool(pulse_cap and (pulse_cap.hardware_verified or pulse_cap.vendor_declared))
                has_verified_sensor = bool(
                    sensor_cap
                    and sensor_cap.hardware_verified
                    and sensor_cap.status == 'SUPPORTED'
                    and device.pin3_open_level in ('high_is_open', 'low_is_open')
                )
                has_declared_sensor = bool(sensor_cap and sensor_cap.vendor_declared)

                if has_pulse and has_verified_sensor:
                    classification = 'full_physical_monitoring'
                elif has_pulse and has_declared_sensor and not has_verified_sensor:
                    classification = 'not_yet_verified'
                elif has_pulse and not has_declared_sensor:
                    classification = 'opening_control_only'
                elif not has_pulse:
                    classification = 'printer_connectivity_only'
                else:
                    classification = 'requires_external_sensor'

                device.write({
                    'cash_drawer_supported': has_pulse,
                    'physical_status_supported': has_declared_sensor,
                    'physical_status_verified': has_verified_sensor,
                    'capability_classification': classification,
                })
        return True

    def action_calibrate_physical_sensor(self):
        """Launches the 3-step physical cash drawer sensor calibration wizard."""
        self.ensure_one()
        return {
            'name': _('3-Step Cash Drawer Sensor Calibration'),
            'type': 'ir.actions.act_window',
            'res_model': 'powerpos.hardware.calibration.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_device_id': self.id,
            },
        }

    def register_heartbeat(self, payload=None):
        """Updates device heartbeat, operational status, and capabilities from authenticated bridge telemetry."""
        self.ensure_one()
        payload = payload or {}
        now = fields.Datetime.now()
        vals = {
            'last_heartbeat': now,
            'last_successful_connection': now,
            'device_status': payload.get('device_status', 'online'),
            'printer_operational_status': payload.get('printer_operational_status', 'ready'),
        }
        if payload.get('firmware_version'):
            vals['firmware_version'] = payload['firmware_version']
        if payload.get('sdk_version'):
            vals['sdk_version'] = payload['sdk_version']
        if payload.get('ip_address'):
            vals['ip_address'] = payload['ip_address']
        if 'last_error' in payload:
            vals['last_error'] = payload['last_error']
        self.sudo().write(vals)

        capabilities = payload.get('capabilities')
        if isinstance(capabilities, list):
            for cap_item in capabilities:
                fkey = cap_item.get('feature_key')
                if not fkey:
                    continue
                existing = self.capability_ids.filtered(lambda c: c.feature_key == fkey)
                cap_vals = {
                    'feature_name': cap_item.get('feature_name', fkey),
                    'vendor_declared': bool(cap_item.get('vendor_declared', True)),
                    'hardware_verified': bool(cap_item.get('hardware_verified', False)),
                    'status': cap_item.get('status', 'UNVERIFIED'),
                    'diagnostic_notes': cap_item.get('diagnostic_notes', ''),
                    'last_checked': now,
                }
                if existing:
                    existing.sudo().write(cap_vals)
                else:
                    cap_vals['device_id'] = self.id
                    cap_vals['feature_key'] = fkey
                    self.env['powerpos.device.capability'].sudo().create(cap_vals)
            self.sudo().action_run_capability_discovery()
        return True

    def get_pos_safe_metadata(self):
        """Returns sanitized dictionary for POS browser frontend — strictly excludes secrets and hashes."""
        self.ensure_one()
        return {
            'id': self.id,
            'device_code': self.device_code,
            'name': self.name,
            'serial_number': self.serial_number,
            'device_category': self.device_category,
            'device_model': self.device_model,
            'connection_type': self.connection_type,
            'ip_address': self.ip_address,
            'printer_port': self.printer_port,
            'paper_width': self.paper_width,
            'capability_classification': self.capability_classification,
            'cash_drawer_supported': self.cash_drawer_supported,
            'physical_status_supported': self.physical_status_supported,
            'physical_status_verified': self.physical_status_verified,
            'pin3_open_level': self.pin3_open_level,
            'device_status': self.device_status,
            'printer_operational_status': self.printer_operational_status,
            'last_heartbeat': fields.Datetime.to_string(self.last_heartbeat) if self.last_heartbeat else False,
        }
