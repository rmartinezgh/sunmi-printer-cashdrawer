import hashlib
import hmac
import json
import logging
import secrets
import time
import urllib.error
import urllib.request
from odoo import api, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PowerPosSunmiCloudClient(models.AbstractModel):
    _name = 'powerpos.sunmi.cloud.client'
    _description = 'SUNMI Cloud OpenAPI v2 Cryptographic Client Service'

    @api.model
    def _compute_signature(self, json_body_str, app_id, timestamp_str, nonce_str, app_key):
        """
        SUNMI OpenAPI v2 HMAC-SHA256 Signing Specification:
        Sunmi-Sign = HMAC-SHA256(json_body + Sunmi-Appid + Sunmi-Timestamp + Sunmi-Nonce, app_key).hexdigest()
        """
        message = f"{json_body_str}{app_id}{timestamp_str}{nonce_str}"
        signature = hmac.new(
            app_key.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        return signature

    @api.model
    def _build_signed_headers(self, account, json_body_str, timestamp_str=None, nonce_str=None):
        sudo_acc = account.sudo()
        if not sudo_acc.app_id or not sudo_acc.app_key:
            raise UserError(_('SUNMI Cloud Account is missing App ID or App Key.'))
        ts = timestamp_str or str(int(time.time()))
        nonce = nonce_str or str(secrets.randbelow(899999) + 100000)
        sign = self._compute_signature(json_body_str, sudo_acc.app_id, ts, nonce, sudo_acc.app_key)
        return {
            'Content-Type': 'application/json; charset=utf-8',
            'Sunmi-Appid': sudo_acc.app_id,
            'Sunmi-Timestamp': ts,
            'Sunmi-Nonce': nonce,
            'Sunmi-Sign': sign,
            'Source': 'openapi',
        }

    @api.model
    def _post_openapi(self, account, endpoint_path, payload_dict):
        """Executes signed HTTPS POST to SUNMI OpenAPI v2 and never fabricates physical cash drawer states."""
        sudo_acc = account.sudo()
        json_body = json.dumps(payload_dict, separators=(',', ':'), ensure_ascii=False)
        headers = self._build_signed_headers(sudo_acc, json_body)
        url = f"{sudo_acc.base_url.rstrip('/')}/{endpoint_path.lstrip('/')}"
        req = urllib.request.Request(
            url=url,
            data=json_body.encode('utf-8'),
            headers=headers,
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=sudo_acc.timeout_seconds or 10) as resp:
                raw_resp = resp.read().decode('utf-8')
                parsed = json.loads(raw_resp) if raw_resp else {}
                return {
                    'ok': parsed.get('code') in (1, '1', 10000, '10000', 0, '0'),
                    'http_status': resp.status,
                    'data': parsed.get('data', {}),
                    'raw': parsed,
                    'cash_drawer_physical_state': 'UNSUPPORTED',  # Strictly prohibited from fabricating physical state from cloud
                }
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode('utf-8', errors='replace') if exc.fp else str(exc)
            _logger.warning("SUNMI Cloud HTTPError on %s: %s", endpoint_path, err_body)
            return {
                'ok': False,
                'http_status': exc.code,
                'error': err_body,
                'cash_drawer_physical_state': 'UNSUPPORTED',
            }
        except Exception as exc:
            _logger.warning("SUNMI Cloud Transport Error on %s: %s", endpoint_path, exc)
            return {
                'ok': False,
                'http_status': 0,
                'error': str(exc),
                'timeout_or_ambiguous': True,
                'cash_drawer_physical_state': 'UNSUPPORTED',
            }

    @api.model
    def bind_shop(self, account, sn, shop_id):
        return self._post_openapi(account, '/v2/printer/open/open/device/bindShop', {
            'sn': sn,
            'shop_id': int(shop_id),
        })

    @api.model
    def unbind_shop(self, account, sn, shop_id):
        return self._post_openapi(account, '/v2/printer/open/open/device/unbindShop', {
            'sn': sn,
            'shop_id': int(shop_id),
        })

    @api.model
    def online_status(self, account, sn):
        res = self._post_openapi(account, '/v2/printer/open/open/device/onlineStatus', {
            'sn': sn,
        })
        online_flag = False
        if res.get('ok'):
            data = res.get('data') or {}
            online_flag = str(data.get('status', data.get('online', '0'))) in ('1', 'online', 'true', 'True')
        res['online'] = online_flag
        return res

    @api.model
    def clear_print_job(self, account, sn):
        return self._post_openapi(account, '/v2/printer/open/open/device/clearPrintJob', {
            'sn': sn,
        })

    @api.model
    def push_voice(self, account, sn, content, cycle=1, interval=2, expire_in=300):
        return self._post_openapi(account, '/v2/printer/open/open/device/pushVoice', {
            'sn': sn,
            'content': content,
            'cycle': int(cycle),
            'interval': int(interval),
            'expire_in': int(expire_in),
        })

    @api.model
    def push_content(self, account, sn, trade_no, content_hex, order_type=1, count=1, media_text=''):
        payload = {
            'trade_no': trade_no,
            'sn': sn,
            'order_type': int(order_type),
            'content': content_hex,
            'count': int(count),
        }
        if media_text:
            payload['media_text'] = media_text
        return self._post_openapi(account, '/v2/printer/open/open/device/pushContent', payload)

    @api.model
    def print_status(self, account, trade_no):
        return self._post_openapi(account, '/v2/printer/open/open/ticket/printStatus', {
            'trade_no': trade_no,
        })

    @api.model
    def verify_new_ticket_notify(self, account, raw_body_str, headers):
        """Verifies incoming webhook signature from SUNMI Cloud newTicketNotify."""
        sudo_acc = account.sudo()
        app_id = headers.get('Sunmi-Appid') or headers.get('SUNMI-APPID') or ''
        timestamp = headers.get('Sunmi-Timestamp') or headers.get('SUNMI-TIMESTAMP') or ''
        nonce = headers.get('Sunmi-Nonce') or headers.get('SUNMI-NONCE') or ''
        received_sign = headers.get('Sunmi-Sign') or headers.get('SUNMI-SIGN') or ''

        if not (app_id and timestamp and nonce and received_sign):
            return False
        if app_id != sudo_acc.app_id:
            return False
        try:
            ts_int = int(timestamp)
            if abs(int(time.time()) - ts_int) > 300:
                return False
        except ValueError:
            return False

        expected_sign = self._compute_signature(raw_body_str, sudo_acc.app_id, timestamp, nonce, sudo_acc.app_key)
        return hmac.compare_digest(expected_sign.lower(), received_sign.lower())
