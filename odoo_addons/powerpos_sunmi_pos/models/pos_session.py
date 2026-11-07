import uuid
from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError


class PosSession(models.Model):
    _inherit = 'pos.session'

    def _loader_params_pos_config(self):
        res = super()._loader_params_pos_config()
        if 'search_params' in res and 'fields' in res['search_params']:
            extra_fields = [
                'powerpos_branch_id',
                'powerpos_printer_id',
                'powerpos_cashdrawer_id',
                'powerpos_enable_hardware_bar',
            ]
            for fname in extra_fields:
                if fname not in res['search_params']['fields']:
                    res['search_params']['fields'].append(fname)
        return res

    @api.model
    def _pos_ui_models_to_load(self):
        models_to_load = super()._pos_ui_models_to_load()
        return models_to_load

    @api.model
    def _load_pos_data_models(self, config_id):
        """Odoo 20 pos.load.mixin hook registering sanitized PowerPOS hardware models in POS session load_data."""
        data_models = super()._load_pos_data_models(config_id) if hasattr(super(), '_load_pos_data_models') else []
        for model_name in ('powerpos.sunmi.device', 'powerpos.cashdrawer.device'):
            if model_name not in data_models:
                data_models.append(model_name)
        return data_models

    def powerpos_get_hardware_status(self):
        """
        RPC called by POS OWL frontend to fetch sanitized printer, cash drawer,
        and recent activity metadata. Strictly excludes app_key, device_secret, and auth_token_hash.
        """
        self.ensure_one()
        config = self.config_id
        printer = config.powerpos_printer_id or self.env['powerpos.sunmi.device'].search([
            ('pos_config_id', '=', config.id),
        ], limit=1)
        drawer = config.powerpos_cashdrawer_id or self.env['powerpos.cashdrawer.device'].search([
            ('pos_config_id', '=', config.id),
        ], limit=1)

        recent_events = []
        if drawer:
            evs = self.env['powerpos.cashdrawer.event'].search([
                ('cash_drawer_id', '=', drawer.id),
            ], limit=15)
            recent_events = [{
                'event_uuid': ev.event_uuid,
                'event_type': ev.event_type,
                'new_physical_state': ev.new_physical_state,
                'opening_reason': ev.opening_reason,
                'opening_duration': round(ev.opening_duration, 1),
                'timestamp': fields.Datetime.to_string(ev.device_observed_timestamp),
                'cashier': ev.cashier_id.name if ev.cashier_id else (ev.user_id.name if ev.user_id else ''),
            } for ev in evs]

        return {
            'pos_config_id': config.id,
            'pos_session_id': self.id,
            'printer': printer.get_pos_safe_metadata() if printer else None,
            'drawer': drawer.get_pos_safe_metadata() if drawer else None,
            'recent_events': recent_events,
        }

    def powerpos_verify_supervisor_pin(self, supervisor_user_id, pin_code):
        """Verifies a supervisor's POS security PIN server-side before issuing a supervisor override authorization."""
        self.ensure_one()
        sup_user = self.env['res.users'].sudo().browse(int(supervisor_user_id)) if supervisor_user_id else self.env.user
        if not sup_user.exists() or not sup_user.has_group('powerpos_sunmi_core.group_powerpos_supervisor'):
            raise AccessError(_('Selected user does not belong to the PowerPOS Supervisor role.'))

        emp = self.env['hr.employee'].sudo().search([
            ('user_id', '=', sup_user.id),
            ('company_id', '=', self.company_id.id),
        ], limit=1)
        if emp and emp.pin and str(emp.pin) != str(pin_code or ''):
            raise AccessError(_('Invalid Supervisor PIN.'))
        return {
            'verified': True,
            'supervisor_id': sup_user.id,
            'supervisor_name': sup_user.name,
        }

    def powerpos_request_drawer_open(self, reason='cash_sale', notes='', supervisor_id=False, pos_order_id=False, cashier_id=False, idempotency_key=None):
        """
        Pre-authorizes a cash drawer kick pulse from POS UI, records drawer_open_requested,
        and returns the short-lived authorization token for the local Android bridge.
        """
        self.ensure_one()
        config = self.config_id
        drawer = config.powerpos_cashdrawer_id or self.env['powerpos.cashdrawer.device'].search([
            ('pos_config_id', '=', config.id),
        ], limit=1)
        if not drawer:
            raise UserError(_('No Cash Drawer is assigned to POS Register %s.') % config.name)

        auth_rec = self.env['powerpos.cashdrawer.authorization'].request_authorization(
            drawer=drawer,
            reason=reason,
            idempotency_key=idempotency_key or str(uuid.uuid4()),
            pos_session_id=self.id,
            pos_order_id=pos_order_id,
            cashier_id=cashier_id,
            supervisor_id=supervisor_id,
            notes=notes,
        )

        self.env['powerpos.cashdrawer.event'].sudo().record_hardware_event(
            drawer=drawer,
            event_type='drawer_open_requested',
            previous_physical_state=drawer.physical_state,
            new_physical_state=drawer.physical_state,
            opening_reason=reason,
            event_source='pos_frontend',
            authorization=auth_rec,
            pos_session_id=self.id,
            pos_order_id=pos_order_id,
            cashier_id=cashier_id,
        )

        return {
            'authorized': True,
            'auth_uuid': auth_rec.auth_uuid,
            'drawer_id': drawer.id,
            'drawer_code': drawer.drawer_code,
            'expires_at': fields.Datetime.to_string(auth_rec.expires_at),
            'physical_status_capability': drawer.physical_status_capability,
        }

    def powerpos_print_receipt(self, receipt_type='official_sales', order_data=None, job_uuid=None, pos_order_id=False):
        """Submits an idempotent print job from the POS session."""
        self.ensure_one()
        config = self.config_id
        printer = config.powerpos_printer_id or self.env['powerpos.sunmi.device'].search([
            ('pos_config_id', '=', config.id),
        ], limit=1)
        if not printer:
            raise UserError(_('No SUNMI Printer is assigned to POS Register %s.') % config.name)

        trade_no = job_uuid or uuid.uuid4().hex[:32]
        existing = self.env['powerpos.sunmi.print.job'].search([('job_uuid', '=', trade_no)], limit=1)
        if existing:
            return {
                'job_uuid': existing.job_uuid,
                'state': existing.state,
                'idempotent_reused': True,
            }

        template = self.env['powerpos.sunmi.receipt.template'].search([
            ('company_id', '=', self.company_id.id),
            ('active', '=', True),
        ], limit=1)
        if template:
            payload_hex = template.render_receipt_escpos_hex(receipt_type=receipt_type, order_data=order_data or {})
        else:
            payload_hex = self.env['powerpos.sunmi.receipt.template']._text_to_escpos_hex('POWERPOS RECEIPT')

        job = self.env['powerpos.sunmi.print.job'].create({
            'job_uuid': trade_no,
            'device_id': printer.id,
            'pos_session_id': self.id,
            'pos_order_id': pos_order_id or False,
            'receipt_type': receipt_type,
            'payload_hex': payload_hex,
        })
        job.action_submit_job()
        return {
            'job_uuid': job.job_uuid,
            'state': job.state,
            'payload_hex': job.payload_hex,
        }
