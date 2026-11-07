import json
import logging
from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)


class PowerPosSunmiCloudController(http.Controller):

    @http.route('/api/v1/sunmi/webhook', type='http', auth='none', methods=['POST'], csrf=False)
    def sunmi_new_ticket_notify(self, **kwargs):
        """Receives SUNMI Cloud newTicketNotify callbacks, verifies HMAC-SHA256 signature, and updates print job state."""
        raw_body = request.httprequest.get_data(as_text=True) or ''
        headers = request.httprequest.headers
        app_id = headers.get('Sunmi-Appid') or headers.get('SUNMI-APPID') or ''

        account = request.env['powerpos.sunmi.cloud.account'].sudo().search([
            ('app_id', '=', app_id),
            ('active', '=', True),
        ], limit=1)

        if not account:
            return request.make_json_response({'code': 401, 'msg': 'Unknown Sunmi-Appid'}, status=401)

        verified = request.env['powerpos.sunmi.cloud.client'].sudo().verify_new_ticket_notify(
            account, raw_body, headers
        )
        if not verified:
            return request.make_json_response({'code': 401, 'msg': 'Invalid Sunmi-Sign HMAC signature'}, status=401)

        try:
            payload = json.loads(raw_body) if raw_body else {}
        except Exception:
            return request.make_json_response({'code': 400, 'msg': 'Malformed JSON payload'}, status=400)

        trade_no = payload.get('trade_no') or (payload.get('data') or {}).get('trade_no')
        status_code = str(payload.get('status', (payload.get('data') or {}).get('status', '1')))

        if trade_no:
            job = request.env['powerpos.sunmi.print.job'].sudo().search([('job_uuid', '=', trade_no)], limit=1)
            if job:
                if status_code in ('1', 'printed', 'success'):
                    job.write({
                        'state': 'printed',
                        'completed_at': fields.Datetime.now(),
                        'error_message': False,
                    })
                elif status_code in ('2', 'failed', 'error'):
                    job.write({
                        'state': 'failed',
                        'error_message': payload.get('msg', 'Printer webhook reported print failure'),
                    })

        return request.make_json_response({'code': 1, 'msg': 'ok'})

    @http.route('/api/v1/printer/status', type='http', auth='user', methods=['GET'], csrf=False)
    def get_printer_status(self, device_id=None, **kwargs):
        domain = []
        if device_id:
            domain.append(('id', '=', int(device_id)))
        devices = request.env['powerpos.sunmi.device'].search(domain)
        return request.make_json_response({
            'status': 'ok',
            'printers': [d.get_pos_safe_metadata() for d in devices],
        })

    @http.route('/api/v1/printer/print', type='http', auth='user', methods=['POST'], csrf=False)
    def submit_print_job(self, **kwargs):
        try:
            payload = json.loads(request.httprequest.get_data(as_text=True) or '{}')
        except Exception:
            return request.make_json_response({'error': 'Invalid JSON body'}, status=400)

        device_id = payload.get('device_id')
        device = request.env['powerpos.sunmi.device'].browse(int(device_id)) if device_id else False
        if not device or not device.exists():
            return request.make_json_response({'error': 'Target printer device not found'}, status=404)

        job_uuid = payload.get('job_uuid')
        existing_job = False
        if job_uuid:
            existing_job = request.env['powerpos.sunmi.print.job'].search([('job_uuid', '=', job_uuid)], limit=1)
        if existing_job:
            return request.make_json_response({
                'status': 'idempotent_existing',
                'job_uuid': existing_job.job_uuid,
                'state': existing_job.state,
            })

        payload_hex = payload.get('payload_hex')
        receipt_type = payload.get('receipt_type', 'official_sales')
        if not payload_hex:
            template = request.env['powerpos.sunmi.receipt.template'].search([
                ('company_id', '=', device.company_id.id),
                ('active', '=', True),
            ], limit=1)
            if template:
                payload_hex = template.render_receipt_escpos_hex(
                    receipt_type=receipt_type,
                    order_data=payload.get('order_data', {}),
                )
            else:
                payload_hex = request.env['powerpos.sunmi.receipt.template']._text_to_escpos_hex(
                    payload.get('raw_text', 'POWERPOS RECEIPT')
                )

        create_vals = {
            'device_id': device.id,
            'receipt_type': receipt_type,
            'payload_hex': payload_hex,
        }
        if job_uuid:
            create_vals['job_uuid'] = job_uuid
        if payload.get('pos_order_id'):
            create_vals['pos_order_id'] = int(payload['pos_order_id'])
        if payload.get('pos_session_id'):
            create_vals['pos_session_id'] = int(payload['pos_session_id'])

        job = request.env['powerpos.sunmi.print.job'].create(create_vals)
        job.action_submit_job()
        return request.make_json_response({
            'status': 'ok',
            'job_uuid': job.job_uuid,
            'state': job.state,
            'cash_drawer_physical_state': 'UNSUPPORTED',
        })

    @http.route('/api/v1/printer/jobs/<string:job_uuid>', type='http', auth='user', methods=['GET'], csrf=False)
    def get_print_job_status(self, job_uuid, **kwargs):
        job = request.env['powerpos.sunmi.print.job'].search([('job_uuid', '=', job_uuid)], limit=1)
        if not job:
            return request.make_json_response({'error': 'Print job not found'}, status=404)
        return request.make_json_response({
            'job_uuid': job.job_uuid,
            'device_id': job.device_id.id,
            'receipt_type': job.receipt_type,
            'state': job.state,
            'retry_count': job.retry_count,
            'error_message': job.error_message or '',
            'submitted_at': fields.Datetime.to_string(job.submitted_at) if job.submitted_at else False,
            'completed_at': fields.Datetime.to_string(job.completed_at) if job.completed_at else False,
        })
