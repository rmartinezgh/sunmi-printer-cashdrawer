from odoo import api, fields, models, _


class PowerPosMonitoringRule(models.Model):
    _name = 'powerpos.monitoring.rule'
    _description = 'Cash Drawer & Hardware Security Monitoring Rule'
    _order = 'sequence, id'

    name = fields.Char(string='Rule Name', required=True)
    sequence = fields.Integer(string='Sequence', default=10)
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    branch_id = fields.Many2one(
        'powerpos.branch',
        string='Branch Scope (Optional)',
        domain="[('company_id', '=', company_id)]",
    )
    pos_config_id = fields.Many2one(
        'pos.config',
        string='POS Register Scope (Optional)',
        domain="[('company_id', '=', company_id)]",
    )
    rule_type = fields.Selection(
        selection=[
            ('prolonged_open', '1. Drawer Left Open Beyond Threshold (15s / 30s / 60s / 120s)'),
            ('unmatched_opening', '2. Unmatched Physical Drawer Opening (Key / Tamper)'),
            ('excessive_manual_open', '3. Excessive Manual / No-Sale Openings in Shift'),
            ('outside_session_open', '4. Drawer Opened Outside Active POS Session / After Logout'),
            ('hardware_offline_or_error', '5. Printer or Drawer Sensor Offline / Stale / Fault'),
        ],
        string='Rule Category',
        required=True,
        default='prolonged_open',
        index=True,
    )
    prolonged_open_preset = fields.Selection(
        selection=[
            ('15', '15 Seconds (Strict High-Security)'),
            ('30', '30 Seconds (Standard Retail Default)'),
            ('60', '60 Seconds (Hospitality / Busy Counter)'),
            ('120', '120 Seconds (Extended Count Tolerance)'),
        ],
        string='Prolonged Open Duration Preset',
        default='30',
    )
    threshold_seconds = fields.Integer(string='Threshold Duration (Seconds)', default=30)
    max_manual_openings_per_shift = fields.Integer(
        string='Max Manual / No-Sale Openings per Shift',
        default=5,
    )
    severity = fields.Selection(
        selection=[
            ('info', 'Info'),
            ('warning', 'Warning'),
            ('critical', 'Critical Security Alert'),
        ],
        string='Alert Severity',
        default='warning',
        required=True,
    )
    notify_pos_screen = fields.Boolean(string='Push Real-Time Alert to POS Screen', default=True)
    notify_supervisor_channel = fields.Boolean(string='Notify Supervisor Dashboard', default=True)
    active = fields.Boolean(string='Active', default=True)

    @api.onchange('prolonged_open_preset')
    def _onchange_prolonged_open_preset(self):
        for rec in self:
            if rec.prolonged_open_preset:
                rec.threshold_seconds = int(rec.prolonged_open_preset)

    @api.model
    def evaluate_event_for_alerts(self, event):
        """Evaluates a newly created powerpos.cashdrawer.event against active monitoring rules."""
        rules = self.sudo().search([
            ('company_id', '=', event.company_id.id),
            ('active', '=', True),
        ])
        Alert = self.env['powerpos.security.alert'].sudo()
        created_alerts = self.env['powerpos.security.alert']

        for rule in rules:
            if rule.branch_id and rule.branch_id.id != event.branch_id.id:
                continue
            if rule.pos_config_id and rule.pos_config_id.id != event.pos_config_id.id:
                continue

            should_trigger = False
            title = ''
            desc = ''

            if rule.rule_type == 'unmatched_opening' and event.event_type == 'unmatched_opening':
                should_trigger = True
                title = _('CRITICAL: Unmatched Physical Cash Drawer Opening (%s)') % event.cash_drawer_id.name
                desc = _(
                    'Cash drawer %s transitioned to physical OPEN state without any matching POS authorization token '
                    'within the correlation window (potential physical key use or hardware tamper).'
                ) % event.cash_drawer_id.display_name

            elif rule.rule_type == 'prolonged_open' and (
                event.event_type == 'drawer_open_timeout'
                or (event.event_type == 'drawer_closed' and event.opening_duration >= (rule.threshold_seconds or 30))
            ):
                should_trigger = True
                title = _('Drawer Left Open Beyond %ds (%s)') % (rule.threshold_seconds or 30, event.cash_drawer_id.name)
                desc = _(
                    'Cash drawer %s remained physically open for %.1f seconds (threshold: %d seconds).'
                ) % (event.cash_drawer_id.display_name, event.opening_duration, rule.threshold_seconds or 30)

            elif rule.rule_type == 'outside_session_open' and event.new_physical_state == 'OPEN' and not event.pos_session_id:
                if event.opening_reason != 'initial_state_observation':
                    should_trigger = True
                    title = _('Drawer Opened Outside Active POS Session (%s)') % event.cash_drawer_id.name
                    desc = _(
                        'Cash drawer %s was physically opened while no active POS session was open on register %s.'
                    ) % (event.cash_drawer_id.display_name, event.pos_config_id.display_name or _('Unassigned'))

            elif rule.rule_type == 'excessive_manual_open' and event.event_type in ('manual_open_authorized', 'supervisor_override_open'):
                if event.pos_session_id:
                    manual_count = self.env['powerpos.cashdrawer.event'].sudo().search_count([
                        ('pos_session_id', '=', event.pos_session_id.id),
                        ('event_type', 'in', ('manual_open_authorized', 'supervisor_override_open')),
                    ])
                    limit_cnt = rule.max_manual_openings_per_shift or 5
                    if manual_count >= limit_cnt:
                        should_trigger = True
                        title = _('Excessive Manual / No-Sale Openings (%d in Shift)') % manual_count
                        desc = _(
                            'POS Session %s has recorded %d manual/supervisor openings, exceeding the shift threshold of %d.'
                        ) % (event.pos_session_id.display_name, manual_count, limit_cnt)

            elif rule.rule_type == 'hardware_offline_or_error' and event.event_type in ('device_offline', 'sensor_error', 'drawer_open_failed'):
                should_trigger = True
                title = _('Hardware Telemetry / Sensor Alert (%s)') % event.cash_drawer_id.name
                desc = _(
                    'Device %s reported %s (physical state: %s, incomplete duration: %s).'
                ) % (
                    event.cash_drawer_id.display_name,
                    event.event_type,
                    event.new_physical_state,
                    _('Yes') if event.is_duration_incomplete else _('No'),
                )

            if should_trigger:
                alert = Alert.create({
                    'company_id': event.company_id.id,
                    'branch_id': event.branch_id.id,
                    'pos_config_id': event.pos_config_id.id if event.pos_config_id else False,
                    'cash_drawer_id': event.cash_drawer_id.id,
                    'printer_id': event.printer_id.id,
                    'event_id': event.id,
                    'cashier_id': event.cashier_id.id if event.cashier_id else False,
                    'pos_session_id': event.pos_session_id.id if event.pos_session_id else False,
                    'rule_id': rule.id,
                    'alert_category': rule.rule_type,
                    'severity': rule.severity,
                    'title': title,
                    'description': desc,
                })
                created_alerts |= alert
                if rule.notify_pos_screen and 'powerpos.hardware.dashboard' in self.env:
                    self.env['powerpos.hardware.dashboard'].sudo()._broadcast_hardware_update(
                        event.pos_config_id.id if event.pos_config_id else 0,
                        {
                            'type': 'security_alert',
                            'alert_uuid': alert.alert_uuid,
                            'severity': alert.severity,
                            'title': alert.title,
                            'description': alert.description,
                            'drawer_id': event.cash_drawer_id.id,
                        }
                    )
        return created_alerts
