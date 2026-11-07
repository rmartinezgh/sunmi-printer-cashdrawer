# PowerPOS — Authentication, Cryptography & REST API Integration Guide

**Document ID:** `PPOS-API-001`  
**Version:** `1.0.0`

---

## 1. Dual Cryptographic Authentication Model

PowerPOS separates external **SUNMI Cloud OpenAPI** credentials from internal **PowerPOS Edge Hardware** credentials.

### 1.1 SUNMI Cloud OpenAPI Signing (Server-to-Cloud Only)
- **Endpoint Base:** `https://openapi.sunmi.com`
- **Secret Storage:** Stored exclusively in Odoo (`powerpos.sunmi.cloud.account`), restricted to `powerpos_sunmi_core.group_powerpos_sysadmin`. Never sent to POS browsers or Android WebView JavaScript.
- **Headers:**
  - `Sunmi-Appid`: Merchant Application ID
  - `Sunmi-Timestamp`: Unix epoch seconds (string)
  - `Sunmi-Nonce`: 6-digit random cryptographic nonce
  - `Sunmi-Sign`: `HMAC-SHA256(json_body + app_id + timestamp + nonce, app_key).hexdigest().lower()`

### 1.2 PowerPOS Device-to-Odoo Signing (Edge-to-Server)
Every edge device (Android POS, Local Printer Bridge, ESP32 Sensor Gateway) is provisioned via a single-use pairing token (`POST /api/v1/hardware/register`) and receives a scoped `device_token` and `device_secret` bound to:
- `company_id`
- `branch_id`
- `pos_config_id`
- `device_id`

Every subsequent API call to `/api/v1/*` requires:
- `Authorization: Bearer <device_token>`
- `X-PowerPOS-Timestamp`: Current UTC epoch seconds (rejected if drift > 300s)
- `X-PowerPOS-Nonce`: Unique 12+ char nonce (replay cache blocks reuse)
- `X-PowerPOS-Signature`: `HMAC-SHA256(timestamp + "." + nonce + "." + raw_body, device_secret)`

---

## 2. REST API Endpoint Reference

| Method | Path | Purpose | Scope |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/hardware/register` | Exchange one-time pairing token for device credentials | Pairing Token |
| `POST` | `/api/v1/hardware/heartbeat` | Report printer status, verified drawer state, and firmware | Device Bearer + HMAC |
| `GET` | `/api/v1/printer/status` | Query live and cached printer connectivity and paper status | Device Bearer + HMAC |
| `POST` | `/api/v1/printer/print` | Submit idempotent print job (`job_uuid`) | Device Bearer + HMAC |
| `GET` | `/api/v1/printer/jobs/{id}` | Query print job lifecycle and trigger `printStatus` reconciliation | Device Bearer + HMAC |
| `POST` | `/api/v1/drawer/authorize` | Request pre-execution authorization token to open cash drawer | Device Bearer + HMAC |
| `POST` | `/api/v1/drawer/events` | Upload online or offline batch of physical drawer events (`event_uuid`) | Device Bearer + HMAC |
| `GET` | `/api/v1/drawer/status` | Retrieve verified physical state, timestamp, and elapsed open time | Device Bearer + HMAC |
| `GET` | `/api/v1/drawer/history` | Retrieve immutable hash-chained audit trail | Manager / Device Auth |
| `GET` | `/api/v1/hardware/capabilities` | Retrieve verified vs declared hardware capability matrix | Device Bearer + HMAC |
| `GET` | `/api/v1/hardware/health` | Retrieve connection, error backoff, and sensor health diagnostics | Device Bearer + HMAC |
| `POST` | `/api/v1/sunmi/webhook` | Receive `newTicketNotify` callbacks from SUNMI Cloud | `Sunmi-Sign` HMAC |

