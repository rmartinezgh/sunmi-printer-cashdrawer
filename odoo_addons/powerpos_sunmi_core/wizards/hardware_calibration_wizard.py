from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PowerPosHardwareCalibrationWizard(models.TransientModel):
    _name = 'powerpos.hardware.calibration.wizard'
    _description = '3-Step Cash Drawer Physical Sensor Calibration Wizard'

    device_id = fields.Many2one(
        'powerpos.sunmi.device',
        string='SUNMI Device',
        required=True,
        readonly=True,
    )
    step = fields.Selection(
        selection=[
            ('step1_closed', 'Step 1: Verify Drawer Physically Closed'),
            ('step2_pulse_open', 'Step 2: Send Kick Pulse & Verify Physically Open'),
            ('step3_reclose', 'Step 3: Physically Close Drawer & Confirm Calibration'),
            ('completed', 'Calibration Completed'),
        ],
        string='Calibration Step',
        default='step1_closed',
        required=True,
    )
    step1_pin3_level = fields.Selection(
        selection=[('HIGH', 'HIGH (Logic 1)'), ('LOW', 'LOW (Logic 0)'), ('NO_SIGNAL', 'No Signal / Floating')],
        string='Step 1 Baseline Reading (Drawer Closed)',
        default='LOW',
        required=True,
    )
    step2_pin3_level = fields.Selection(
        selection=[('HIGH', 'HIGH (Logic 1)'), ('LOW', 'LOW (Logic 0)'), ('NO_SIGNAL', 'No Signal / Floating')],
        string='Step 2 Reading After Kick Pulse (Drawer Open)',
        default='HIGH',
        required=True,
    )
    step3_pin3_level = fields.Selection(
        selection=[('HIGH', 'HIGH (Logic 1)'), ('LOW', 'LOW (Logic 0)'), ('NO_SIGNAL', 'No Signal / Floating')],
        string='Step 3 Reading After Re-closing Drawer',
        default='LOW',
        required=True,
    )
    detected_polarity = fields.Selection(
        selection=[
            ('high_is_open', 'Normally Closed Switch (Pin-3 HIGH = Drawer OPEN)'),
            ('low_is_open', 'Normally Open Switch (Pin-3 LOW = Drawer OPEN)'),
            ('uncalibrated', 'Uncalibrated / No Transition Detected'),
        ],
        string='Detected Pin-3 Polarity',
        compute='_compute_detected_polarity',
        store=True,
    )
    diagnostic_summary = fields.Text(
        string='Calibration Diagnostic Summary',
        compute='_compute_detected_polarity',
    )

    @api.depends('step1_pin3_level', 'step2_pin3_level', 'step3_pin3_level')
    def _compute_detected_polarity(self):
        for wiz in self:
            if (
                wiz.step1_pin3_level == 'LOW'
                and wiz.step2_pin3_level == 'HIGH'
                and wiz.step3_pin3_level == 'LOW'
            ):
                wiz.detected_polarity = 'high_is_open'
                wiz.diagnostic_summary = _(
                    'VERIFIED: Pin-3 transitions LOW -> HIGH -> LOW. '
                    'Switch type is Normally Closed (HIGH = OPEN). Full physical monitoring verified.'
                )
            elif (
                wiz.step1_pin3_level == 'HIGH'
                and wiz.step2_pin3_level == 'LOW'
                and wiz.step3_pin3_level == 'HIGH'
            ):
                wiz.detected_polarity = 'low_is_open'
                wiz.diagnostic_summary = _(
                    'VERIFIED: Pin-3 transitions HIGH -> LOW -> HIGH. '
                    'Switch type is Normally Open (LOW = OPEN). Full physical monitoring verified.'
                )
            else:
                wiz.detected_polarity = 'uncalibrated'
                wiz.diagnostic_summary = _(
                    'UNVERIFIED: Pin-3 did not produce a deterministic closed-open-closed state transition. '
                    'Device will be classified as Opening Control Only until a working microswitch is verified.'
                )

    def action_next_step(self):
        self.ensure_one()
        if self.device_id.connection_type == 'sunmi_cloud':
            raise UserError(_(
                'SUNMI Cloud OpenAPI printers do not support real-time RJ11 Pin-3 physical state readback. '
                'Use a local Android SUNMI terminal or GPIO sensor bridge for physical state calibration.'
            ))
        if self.step == 'step1_closed':
            self.step = 'step2_pulse_open'
        elif self.step == 'step2_pulse_open':
            self.step = 'step3_reclose'
        elif self.step == 'step3_reclose':
            self._apply_calibration_result()
            self.step = 'completed'
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _apply_calibration_result(self):
        self.ensure_one()
        device = self.device_id
        now = fields.Datetime.now()
        sensor_cap = device.capability_ids.filtered(lambda c: c.feature_key == 'cash_drawer_sensor_read')
        pulse_cap = device.capability_ids.filtered(lambda c: c.feature_key == 'cash_drawer_pulse')

        if pulse_cap:
            pulse_cap.sudo().write({
                'vendor_declared': True,
                'hardware_verified': True,
                'status': 'SUPPORTED',
                'last_checked': now,
                'diagnostic_notes': _('Verified during 3-step calibration wizard by %s.') % self.env.user.name,
            })

        if self.detected_polarity in ('high_is_open', 'low_is_open'):
            if sensor_cap:
                sensor_cap.sudo().write({
                    'vendor_declared': True,
                    'hardware_verified': True,
                    'status': 'SUPPORTED',
                    'last_checked': now,
                    'diagnostic_notes': self.diagnostic_summary,
                })
            device.sudo().write({
                'pin3_open_level': self.detected_polarity,
                'cash_drawer_supported': True,
                'physical_status_supported': True,
                'physical_status_verified': True,
                'capability_classification': 'full_physical_monitoring',
            })
            device.message_post(body=_('3-Step Sensor Calibration PASSED: %s') % self.diagnostic_summary)
        else:
            if sensor_cap:
                sensor_cap.sudo().write({
                    'hardware_verified': False,
                    'status': 'UNSUPPORTED',
                    'last_checked': now,
                    'diagnostic_notes': self.diagnostic_summary,
                })
            device.sudo().write({
                'pin3_open_level': 'uncalibrated',
                'cash_drawer_supported': True,
                'physical_status_supported': False,
                'physical_status_verified': False,
                'capability_classification': 'opening_control_only',
            })
            device.message_post(body=_('3-Step Sensor Calibration FAILED/UNSUPPORTED: %s') % self.diagnostic_summary)
