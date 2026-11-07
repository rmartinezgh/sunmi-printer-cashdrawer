import hashlib
import hmac
import json
import time
from odoo import fields, http
from odoo.http import request


class PowerPosHardwareAuditController(http.Controller):

    def _authenticate_device_hmac(self, raw_body_str):
        """
        Verifies X-PowerPOS-Device-ID, X-PowerPOS-Timestamp, X-PowerPOS-Nonce, X-PowerPOS-Signature
        using the device's secret (or falls back to authenticated Odoo session for POS UI calls).
        """
        headers = request.httprequest.headers
        device_code = headers.get('X-PowerPOS-Device-ID')
        signature = headers.get('X-PowerPOS-Signature')
        timestamp = headers.get('X-PowerPOS-Timestamp')
        nonce = headers.get('X-PowerPOS-Nonce', '')

        if device_code and signature and timestamp:
            try:
                if abs(int(time.time()) - int(timestamp)) > 300:
                    return None, 'Timestamp outside 300s replay window'
            except ValueError:
                return None, 'Invalid timestamp header'

            device = request.env['powerpos.sunmi.device'].sudo().search([
                '|', ('device_code', '=', device_code), ('serial_number', '=', device_code)
            ], limit=1)
            if not device or not device.device_secret:
                return None, 'Unknown or unpaired hardware device'

            msg = f"{raw_body_str}{device_code}{timestamp}{nonce}"
            expected = hmac.new(
                device.device_secret.encode('utf-8'),
                msg.encode('utf-8'),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(expected.lower(), signature.lower()):
                return None, 'Invalid HMAC-SHA256 signature'
            return device, None

        if request.env.user and not request.env.user._is_public():
            return True, None
        return None, 'Missing hardware HMAC authentication headers'

    @http.route('/api/v1/hardware/register', type='http', auth='user', methods=['POST'], csrf=False)
    def hardware_register(self, **kwargs):
        try:
            payload = json.loads(request.httprequest.get_data(as_text=True) or '{}')
        except Exception:
            return request.make_json_response({'error': 'Invalid JSON'}, status=400)

        sn = payload.get('serial_number')
        if not sn:
            return request.make_json_response({'error': 'serial_number is required'}, status=400)

        Device = request.env['powerpos.sunmi.device'].sudo()
        device = Device.search([
            ('serial_number', '=', sn),
            ('company_id', '=', request.env.company.id),
        ], limit=1)
        if not device:
            branch_id = payload.get('branch_id')
            if not branch_id:
                branch = request.env['powerpos.branch'].sudo().search([('company_id', '=', request.env.company.id)], limit=1)
                branch_id = branch.id if branch else False
            device = Device.create({
                'name': payload.get('name', f'SUNMI {sn}'),
                'serial_number': sn,
                'device_model': payload.get('device_model', 'T2'),
                'device_category': payload.get('device_category', 'terminal_builtin_printer'),
                'connection_type': payload.get('connection_type', 'builtin_sdk'),
                'branch_id': branch_id,
                'company_id': request.env.company.id,
            })

        device.register_heartbeat(payload)
        return request.make_json_response({
            'status': 'registered',
            'device': device.get_pos_safe_metadata(),
        })

    @http.route('/api/v1/hardware/heartbeat', type='http', auth='none', methods=['POST'], csrf=False)
    def hardware_heartbeat(self, **kwargs):
        raw_body = request.httprequest.get_data(as_text=True) or '{}'
        auth_obj, err = self._authenticate_device_hmac(raw_body)
        if err:
            return request.make_json_response({'error': err}, status=401)

        payload = json.loads(raw_body)
        device = auth_obj if hasattr(auth_obj, 'register_heartbeat') else request.env['powerpos.sunmi.device'].sudo().browse(int(payload.get('device_id', 0)))
        if not device or not device.exists():
            return request.make_json_response({'error': 'Device not found'}, status=404)

        device.register_heartbeat(payload)
        if 'powerpos.hardware.dashboard' in request.env:
            request.env['powerpos.hardware.dashboard'].sudo()._broadcast_hardware_update(device.pos_config_id.id if device.pos_config_id else 0, {
                'type': 'heartbeat',
                'device': device.get_pos_safe_metadata(),
            })
        return request.make_json_response({'status': 'ok', 'server_time': fields.Datetime.to_string(fields.Datetime.now())})

    @http.route('/api/v1/hardware/capabilities', type='http', auth='none', methods=['POST'], csrf=False)
    def hardware_capabilities(self, **kwargs):
        raw_body = request.httprequest.get_data(as_text=True) or '{}'
        auth_obj, err = self._authenticate_device_hmac(raw_body)
        if err:
            return request.make_json_response({'error': err}, status=401)

        payload = json.loads(raw_body)
        device = auth_obj if hasattr(auth_obj, 'register_heartbeat') else request.env['powerpos.sunmi.device'].sudo().browse(int(payload.get('device_id', 0)))
        if not device or not device.exists():
            return request.make_json_response({'error': 'Device not found'}, status=404)

        device.register_heartbeat(payload)
        return request.make_json_response({
            'status': 'ok',
            'capability_classification': device.capability_classification,
            'physical_status_verified': device.physical_status_verified,
        })

    @http.route('/api/v1/hardware/health', type='http', auth='user', methods=['GET'], csrf=False)
    def hardware_health(self, branch_id=None, **kwargs):
        domain = [('company_id', '=', request.env.company.id)]
        if branch_id:
            domain.append(('branch_id', '=', int(branch_id)))
        devices = request.env['powerpos.sunmi.device'].search(domain)
        drawers = request.env['powerpos.cashdrawer.device'].search(domain)
        return request.make_json_response({
            'status': 'ok',
            'devices': [d.get_pos_safe_metadata() for d in devices],
            'drawers': [dr.get_pos_safe_metadata() for dr in drawers],
        })

    @http.route('/api/v1/drawer/authorize', type='http', auth='user', methods=['POST'], csrf=False)
    def drawer_authorize(self, **kwargs):
        try:
            payload = json.loads(request.httprequest.get_data(as_text=True) or '{}')
        except Exception:
            return request.make_json_response({'error': 'Invalid JSON'}, status=400)

        drawer_id = payload.get('drawer_id')
        drawer = request.env['powerpos.cashdrawer.device'].browse(int(drawer_id)) if drawer_id else False
        if not drawer or not drawer.exists():
            return request.make_json_response({'error': 'Cash drawer not found'}, status=404)

        try:
            auth_rec = request.env['powerpos.cashdrawer.authorization'].request_authorization(
                drawer=drawer,
                reason=payload.get('reason', 'cash_sale'),
                idempotency_key=payload.get('idempotency_key'),
                pos_session_id=payload.get('pos_session_id', False),
                pos_order_id=payload.get('pos_order_id', False),
                cashier_id=payload.get('cashier_id', False),
                supervisor_id=payload.get('supervisor_id', False),
                notes=payload.get('notes', ''),
            )
            return request.make_json_response({
                'status': 'authorized',
                'auth_uuid': auth_rec.auth_uuid,
                'expires_at': fields.Datetime.to_string(auth_rec.expires_at),
            })
        except Exception as exc:
            return request.make_json_response({'status': 'rejected', 'error': str(exc)}, status=403)

    @http.route('/api/v1/drawer/events', type='http', auth='none', methods=['POST'], csrf=False)
    def drawer_events_ingest(self, **kwargs):
        raw_body = request.httprequest.get_data(as_text=True) or '{}'
        auth_obj, err = self._authenticate_device_hmac(raw_body)
        if err:
            return request.make_json_response({'error': err}, status=401)

        payload = json.loads(raw_body)
        events_list = payload.get('events') if isinstance(payload.get('events'), list) else [payload]
        processed_uuids = []

        for item in events_list:
            drawer_id = item.get('drawer_id')
            drawer_code = item.get('drawer_code')
            Drawer = request.env['powerpos.cashdrawer.device'].sudo()
            drawer = Drawer.browse(int(drawer_id)) if drawer_id else Drawer.search([('drawer_code', '=', drawer_code)], limit=1)
            if not drawer or not drawer.exists():
                continue

            obs_ts = fields.Datetime.to_datetime(item.get('device_observed_timestamp')) if item.get('device_observed_timestamp') else fields.Datetime.now()
            ev = drawer.process_physical_state_reading(
                new_state=item.get('new_physical_state', 'OPEN'),
                device_timestamp=obs_ts,
                event_source=item.get('event_source', 'android_bridge'),
                sequence_number=int(item.get('sequence_number', 0)),
                sensor_quality=item.get('sensor_quality', 'verified'),
                event_uuid=item.get('event_uuid'),
                authorization_uuid=item.get('authorization_uuid'),
                pos_session_id=item.get('pos_session_id', False),
                pos_order_id=item.get('pos_order_id', False),
                cashier_id=item.get('cashier_id', False),
            )
            if ev:
                processed_uuids.append(ev.event_uuid)
            if 'powerpos.hardware.dashboard' in request.env:
                request.env['powerpos.hardware.dashboard'].sudo()._broadcast_hardware_update(
                    drawer.pos_config_id.id if drawer.pos_config_id else 0,
                    {
                        'type': 'drawer_state_change',
                        'drawer': drawer.get_pos_safe_metadata(),
                        'event_uuid': ev.event_uuid if ev else False,
                    }
                )

        return request.make_json_response({
            'status': 'ok',
            'processed_event_uuids': processed_uuids,
        })

    @http.route('/api/v1/drawer/status', type='http', auth='user', methods=['GET'], csrf=False)
    def drawer_status(self, drawer_id=None, pos_config_id=None, **kwargs):
        domain = []
        if drawer_id:
            domain.append(('id', '=', int(drawer_id)))
        if pos_config_id:
            domain.append(('pos_config_id', '=', int(pos_config_id)))
        drawers = request.env['powerpos.cashdrawer.device'].search(domain)
        return request.make_json_response({
            'status': 'ok',
            'drawers': [d.get_pos_safe_metadata() for d in drawers],
        })

    @http.route('/api/v1/drawer/history', type='http', auth='user', methods=['GET'], csrf=False)
    def drawer_history(self, drawer_id=None, pos_config_id=None, limit=50, **kwargs):
        domain = []
        if drawer_id:
            domain.append(('cash_drawer_id', '=', int(drawer_id)))
        if pos_config_id:
            domain.append(('pos_config_id', '=', int(pos_config_id)))
        events = request.env['powerpos.cashdrawer.event'].search(domain, limit=min(int(limit), 200))
        return request.make_json_response({
            'status': 'ok',
            'events': [{
                'event_uuid': ev.event_uuid,
                'event_type': ev.event_type,
                'previous_physical_state': ev.previous_physical_state,
                'new_physical_state': ev.new_physical_state,
                'opening_reason': ev.opening_reason,
                'opening_duration': ev.opening_duration,
                'is_duration_incomplete': ev.is_duration_incomplete,
                'device_observed_timestamp': fields.Datetime.to_string(ev.device_observed_timestamp),
                'cashier_name': ev.cashier_id.name if ev.cashier_id else (ev.user_id.name if ev.user_id else ''),
                'review_status': ev.review_status,
                'event_hash': ev.event_hash[:16],
            } for ev in events],
        })
