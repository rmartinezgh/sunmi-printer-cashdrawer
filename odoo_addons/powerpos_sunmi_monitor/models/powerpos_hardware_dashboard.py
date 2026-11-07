from datetime import datetime, time
from odoo import api, fields, models


class PowerPosHardwareDashboard(models.Model):
    _name = 'powerpos.hardware.dashboard'
    _description = 'PowerPOS Central Hardware & Cash Drawer Monitoring Dashboard'

    name = fields.Char(string='Dashboard View Name', default='Enterprise Hardware Overview', required=True)
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
    )
    branch_id = fields.Many2one(
        'powerpos.branch',
        string='Filter by Branch (Optional)',
        domain="[('company_id', '=', company_id)]",
    )

    # All 11 Required Verified KPIs from Section 15.1
    kpi_total_registered_devices = fields.Integer(string='1. Total Registered Devices', compute='_compute_dashboard_kpis')
    kpi_online_printers = fields.Integer(string='2. Online Printers', compute='_compute_dashboard_kpis')
    kpi_offline_printers = fields.Integer(string='3. Offline / Fault Printers', compute='_compute_dashboard_kpis')
    kpi_total_cash_drawers = fields.Integer(string='4. Total Cash Drawers', compute='_compute_dashboard_kpis')
    kpi_drawers_currently_open = fields.Integer(string='5. Drawers Currently OPEN (Verified)', compute='_compute_dashboard_kpis')
    kpi_drawers_currently_closed = fields.Integer(string='6. Drawers Currently CLOSED (Verified)', compute='_compute_dashboard_kpis')
    kpi_drawers_unknown_or_stale = fields.Integer(string='7. Drawers UNKNOWN / STALE / UNSUPPORTED', compute='_compute_dashboard_kpis')
    kpi_openings_today = fields.Integer(string='8. Total Verified Openings Today', compute='_compute_dashboard_kpis')
    kpi_unmatched_openings_today = fields.Integer(string='9. Unmatched Openings Today', compute='_compute_dashboard_kpis')
    kpi_prolonged_open_events_today = fields.Integer(string='10. Prolonged Open Timeouts Today', compute='_compute_dashboard_kpis')
    kpi_active_security_alerts = fields.Integer(string='11. Active Security Alerts', compute='_compute_dashboard_kpis')

    @api.depends('company_id', 'branch_id')
    def _compute_dashboard_kpis(self):
        Device = self.env['powerpos.sunmi.device']
        Drawer = self.env['powerpos.cashdrawer.device']
        Event = self.env['powerpos.cashdrawer.event']
        Alert = self.env['powerpos.security.alert']

        today_start = datetime.combine(fields.Date.context_today(self), time.min)

        for rec in self:
            dom = [('company_id', '=', rec.company_id.id)]
            if rec.branch_id:
                dom.append(('branch_id', '=', rec.branch_id.id))

            rec.kpi_total_registered_devices = Device.search_count(dom)
            rec.kpi_online_printers = Device.search_count(dom + [('device_status', '=', 'online')])
            rec.kpi_offline_printers = Device.search_count(dom + [('device_status', 'in', ('offline', 'error', 'degraded'))])

            rec.kpi_total_cash_drawers = Drawer.search_count(dom)
            rec.kpi_drawers_currently_open = Drawer.search_count(dom + [('physical_state', '=', 'OPEN')])
            rec.kpi_drawers_currently_closed = Drawer.search_count(dom + [('physical_state', '=', 'CLOSED')])
            rec.kpi_drawers_unknown_or_stale = Drawer.search_count(
                dom + [('physical_state', 'in', ('UNKNOWN', 'STALE', 'UNSUPPORTED', 'OFFLINE', 'ERROR'))]
            )

            today_dom = dom + [('device_observed_timestamp', '>=', fields.Datetime.to_string(today_start))]
            rec.kpi_openings_today = Event.search_count(
                today_dom + [('event_type', 'in', ('drawer_opened', 'sale_auto_open', 'manual_open_authorized', 'supervisor_override_open', 'unmatched_opening'))]
            )
            rec.kpi_unmatched_openings_today = Event.search_count(
                today_dom + [('event_type', '=', 'unmatched_opening')]
            )
            rec.kpi_prolonged_open_events_today = Event.search_count(
                today_dom + [('event_type', '=', 'drawer_open_timeout')]
            )
            rec.kpi_active_security_alerts = Alert.search_count(
                dom + [('state', 'in', ('active', 'acknowledged', 'investigating'))]
            )

    @api.model
    def _broadcast_hardware_update(self, pos_config_id, message_payload):
        """Broadcasts real-time hardware state changes and security alerts over Odoo bus.bus."""
        channel = f"powerpos_hardware_{pos_config_id or 0}"
        if 'bus.bus' in self.env:
            self.env['bus.bus']._sendone(channel, 'powerpos_hardware_event', message_payload)
        return True

    def action_refresh_dashboard(self):
        self._compute_dashboard_kpis()
        return True
