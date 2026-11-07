# PowerPOS — SUNMI Hardware Capability Assessment & Compatibility Matrix

**Document ID:** `PPOS-HW-MAT-001`  
**Version:** `1.0.0`  
**Status:** Production Baseline (Hardware-Gated Verification Policy)

---

## 1. Executive Technical Assessment

Before enabling physical cash drawer monitoring in production, PowerPOS enforces a strict **Hardware-Verified Capability Gate**:

1. **Opening Command != Physical Opening:** Sending `ESC p m t1 t2` (`1B 70 ...`) or calling `woyouService.openDrawer(callback)` only energizes the RJ11/RJ12 solenoid kick coil (typically 12V or 24V pulse on Pin 2 or Pin 4). It **never** proves the drawer physically opened (e.g., jammed latch, disconnected RJ11 cable, locked keyシリンダー, or empty pulse).
2. **Receipt Print != Drawer State:** Successful completion of a print job via SUNMI Cloud OpenAPI (`pushContent` / `printStatus`) or local USB/LAN printing provides zero information about cash drawer position.
3. **No Fabricated States:** When a printer or interface lacks a verified bidirectional drawer status input or when the connected cash drawer lacks a physical microswitch on the status return pin (RJ11 Pin 3), PowerPOS reports physical state strictly as `UNSUPPORTED` or `UNKNOWN`—never a synthetic `OPEN` or `CLOSED`.

---

## 2. Protocol & SDK Capability Analysis

### 2.1 SUNMI Cloud Printer OpenAPI (`https://openapi.sunmi.com`)

The official SUNMI Cloud Printer SDK exposes the following server-to-cloud endpoints:

| SDK Function | Endpoint Path | Direction | Cash Drawer Capability |
| :--- | :--- | :--- | :--- |
| `bindShop()` | `/v1/printer/bindShop` | Cloud Request | None (binds printer SN to merchant shop ID) |
| `unbindShop()` | `/v1/printer/unbindShop` | Cloud Request | None |
| `onlineStatus()` | `/v1/printer/onlineStatus` | Cloud Query | Returns printer online/offline status only; **no drawer state** |
| `clearPrintJob()` | `/v1/printer/clearPrintJob` | Cloud Command | Clears pending print buffer; **no drawer state** |
| `pushVoice()` | `/v1/printer/pushVoice` | Cloud Command | Audio broadcast only |
| `pushContent()` | `/v1/printer/pushContent` | Cloud Command | Can embed ESC/POS drawer kick (`1B 70 00 32 96`) inside hex payload; **does not return raw ESC/POS read bytes** |
| `printStatus()` | `/v1/printer/printStatus` | Cloud Query | Queries ticket print job lifecycle (`pending`/`printed`/`failed`); **no drawer state** |
| `newTicketNotify()`| Webhook Callback | Cloud -> Server | Notifies print completion or printer status; **does not stream RJ11 Pin 3 state transitions** |

**Conclusion for Cloud OpenAPI Only:**
- **Classification:** `Opening control only` + `Printer connectivity monitoring only`.
- **Limitation:** `pushContent()` is a unidirectional cloud spooler. Embedding `DLE EOT 1` (`10 04 01`) in a cloud print payload is discarded or unreturned by the cloud API. Physical status monitoring over Cloud-only transport is **UNSUPPORTED**. To monitor drawer status on a Cloud Printer, either a **Local Printer Bridge** (if local LAN/USB raw socket is enabled alongside cloud mode) or an **External Sensor Gateway** (ESP32 reed/microswitch sensor) is mandatory.

---

### 2.2 ESC/POS Real-Time Status Transmission (`DLE EOT n` / `0x10 0x04 n`)

In the ESC/POS specification (`DLE EOT 1`, hex `10 04 01`):
- **Command:** `0x10 0x04 0x01` (Transmit real-time printer status).
- **Expected 1-Byte Response Format:**
  - Bit 0 (`0x01`): Fixed `0`
  - Bit 1 (`0x02`): Fixed `1`
  - **Bit 2 (`0x04`): Cash drawer kick-out connector Pin 3 level (`0` = Low, `1` = High)**
  - Bit 3 (`0x08`): Online/Offline (`0` = Online, `1` = Offline)
  - Bit 4 (`0x10`): Fixed `1`
  - Bit 5, 6: Undefined / Reserved
  - Bit 7 (`0x80`): Fixed `0`
- **Validation Mask:** `(byte & 0x93) == 0x12` confirms a valid `DLE EOT 1` status frame (`bit 7 = 0`, `bit 4 = 1`, `bit 1 = 1`, `bit 0 = 0`).

**Critical Hardware Caveats:**
1. **Electrical Polarity Inversion (`active_high` vs `active_low`):**
   - Standard 24V cash drawers with a microswitch on RJ11 Pin 3 may wire the switch as **Normally Open (NO)** or **Normally Closed (NC)** when the drawer is shut.
   - Therefore, Bit 2 = `1` means `OPEN` on some drawer models and `CLOSED` on others. Each device profile in PowerPOS requires a verified `pin3_open_level` (`0` or `1`) calibrated during the 2-step Hardware Diagnostic Wizard (Check Closed -> Open -> Check Open).
2. **Simultaneous Cloud + Local Port Contention:**
   - On SUNMI NT311 (80mm Cloud Kitchen Printer), TCP Port `9100` (raw ESC/POS) is only accessible on the local LAN if enabled in firmware, and some firmware revisions do not flush `DLE EOT 1` responses while a cloud job is actively spooling.
3. **58mm Printers Without RJ11 Status Pin:**
   - Entry-level 58mm printers either lack an RJ11 port entirely or wire only the solenoid drive pins without an optocoupler input on Pin 3.

---

### 2.3 SUNMI Android Built-in Printer / Woyou AIDL Service (`woyou.aidlservice.jiuiv5`)

On SUNMI Android POS terminals (T2, T2s, T3, D2s, D3 Pro):
- **Service Package:** `woyou.aidlservice.jiuiv5` / `com.sunmi.peripheral.printer` (`InnerPrinterManager`).
- **Methods:**
  - `openDrawer(ICallback callback)`: Triggers the integrated RJ11 cash drawer kick pulse.
  - `getDrawerStatus(): Int` (or `Boolean` on newer `SunmiPrinterService` wrappers): Queries the hardware GPIO connected to RJ11 Pin 3.
  - `getOpenDrawerTimes(): Int`: Returns the cumulative software-triggered pulse counter maintained by the printer microcontroller.

**Critical Hardware Caveats:**
1. `getOpenDrawerTimes()` only increments when `openDrawer()` or `ESC p` is executed by software. It **does not** increment when a cashier or intruder opens the drawer with a physical key! Therefore, `getOpenDrawerTimes()` is strictly recorded as a diagnostic counter (`drawer_pulse_count`), **never** as physical open/close detection.
2. `getDrawerStatus()` only works on desktop terminals equipped with a full 6-pin RJ11/RJ12 cash drawer interface that includes the Pin 3 sense circuit **and** when paired with a 5-wire/6-wire sensor-equipped cash drawer (e.g., SUNMI Max Cash Drawer). Handheld terminals (V2, V2 Pro, V2s) have no RJ11 port and must return `UNSUPPORTED`.

---

## 3. Model-by-Model Hardware Capability Matrix

| Device Model | Form Factor | Primary Interface | Receipt Printing | Drawer Kick (`openDrawer` / `ESC p`) | Physical Status via SDK (`getDrawerStatus`) | Physical Status via Local `DLE EOT 1` | Concurrent Cloud + Local Status | Recommended Production Classification |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **SUNMI T2 / T2s** (with Sensor Drawer) | Desktop Android POS (80mm) | Android AIDL (`InnerPrinter`) | `SUPPORTED` | `SUPPORTED` | `VERIFIED_REQUIRED` (Hardware Pin 3 supported) | N/A (Uses AIDL) | N/A | **Full physical status monitoring supported** (after calibration) |
| **SUNMI T3 / T3 Pro** (with Sensor Drawer) | Desktop Android POS (80mm) | Android AIDL (`SunmiPrinterService`) | `SUPPORTED` | `SUPPORTED` | `VERIFIED_REQUIRED` (Hardware Pin 3 supported) | N/A (Uses AIDL) | N/A | **Full physical status monitoring supported** (after calibration) |
| **SUNMI D2s / D2s Plus / D3 Pro** | Desktop Android POS (58mm/80mm) | Android AIDL | `SUPPORTED` | `SUPPORTED` | Model/Revision Dependent (Requires Pin 3 check) | N/A | N/A | **Not yet verified** -> Upgrades to **Full** or **Opening control only** via Diagnostic Wizard |
| **SUNMI D2s KDS** | Kitchen Display System | LAN / Android | `UNSUPPORTED` (No built-in printer) | `UNSUPPORTED` | `UNSUPPORTED` | `UNSUPPORTED` | N/A | **Unsupported** (Requires external LAN printer or Sensor Gateway) |
| **SUNMI V2 / V2 Pro / V2s** | Handheld Android POS (58mm) | Android AIDL | `SUPPORTED` | `UNSUPPORTED` (No RJ11 port) | `UNSUPPORTED` | `UNSUPPORTED` | N/A | **Printer connectivity monitoring only** |
| **SUNMI NT311 (80mm Cloud Kitchen Printer)** — *Cloud Mode Only* | Standalone Cloud/LAN/USB (80mm) | SUNMI Cloud OpenAPI | `SUPPORTED` | `SUPPORTED` (via `ESC p` in `pushContent`) | `UNSUPPORTED` | `UNSUPPORTED` (Over Cloud HTTP) | `NO` | **Opening control only** + **Requires external sensor** for physical monitoring |
| **SUNMI NT311 (80mm Cloud Printer)** — *With Local Bridge (USB/LAN 9100)* | Standalone Cloud/LAN/USB (80mm) | Local Bridge (USB Virtual COM / TCP 9100) | `SUPPORTED` | `SUPPORTED` | N/A | `VERIFIED_REQUIRED` (`10 04 01` bit 2) | Firmware-dependent | **Not yet verified** (Requires Local Bridge calibration) or **Requires external sensor** |
| **SUNMI NT212 (58mm Cloud Printer)** | Standalone Cloud/USB (58mm) | SUNMI Cloud OpenAPI | `SUPPORTED` | Model-dependent | `UNSUPPORTED` | `UNSUPPORTED` | `NO` | **Printer connectivity monitoring only** / **Requires external sensor** |
| **Generic ESC/POS 80mm Printer (Epson TM-T88 / Star)** | Standalone LAN/USB/Serial | Local Bridge (`DLE EOT 1`) | `SUPPORTED` | `SUPPORTED` | N/A | `SUPPORTED` (Verified via calibration) | N/A | **Full physical status monitoring supported** (with 5-wire drawer) |
| **Any Printer + 2-Wire (Solenoid-Only) Cash Drawer** | Any | Any | `SUPPORTED` | `SUPPORTED` | `UNSUPPORTED` (No microswitch in drawer) | `UNSUPPORTED` (Pin 3 floating/static) | N/A | **Opening control only** / **Requires external sensor** |
| **Any Printer + PowerPOS ESP32 Sensor Gateway** | Independent IoT Reed/Microswitch | HTTPS / MQTT Sensor Gateway | Via paired printer | Via paired printer | **SUPPORTED** (Independent GPIO reed switch + tamper loop) | N/A | **YES** (100% independent of printer) | **Full physical status monitoring supported** |

---

## 4. Mandatory Two-Step Hardware Calibration Protocol

Before any device in `powerpos.sunmi.device` or `powerpos.cashdrawer.device` can transition its `physical_status_capability` field from `unverified` to `supported`, the administrator or technician must execute the **Interactive Hardware Verification Wizard**:

1. **Step 1 — Closed Baseline Check:**
   - Prompt technician to ensure the cash drawer is physically **CLOSED** and latched.
   - Read 3 consecutive status samples 200 ms apart.
   - Verify signal stability. Record `closed_raw_level` (`0` or `1`).
2. **Step 2 — Open & Manual Transition Check:**
   - Trigger software open (or prompt technician to open with physical key).
   - Poll for state transition within 10 seconds.
   - Verify that `open_raw_level != closed_raw_level` (if both read identical values, the drawer lacks a Pin 3 microswitch or the printer does not wire Pin 3; automatically classify as `Opening control only` / `Requires external sensor`).
3. **Step 3 — Manual Key Opening Verification (AC-07 Gate):**
   - Prompt technician to close the drawer, then open it using the **physical key** without pressing any POS button.
   - Verify that the status driver independently detects `CLOSED -> OPEN` within the configured polling/callback window.
   - Only when all 3 steps pass does PowerPOS set `capability_classification = 'full_physical_monitoring'` and `physical_status_verified = True`.

---

## 5. Risk Register & Mitigation Controls

| Risk ID | Risk Description | Likelihood | Impact | Mitigation in PowerPOS Architecture |
| :--- | :--- | :--- | :--- | :--- |
| **R-01** | **False Physical Status on 2-Wire Cash Drawers:** Many low-cost cash drawers only wire Pin 2 & Pin 4 (solenoid) and leave Pin 3 floating, causing `getDrawerStatus()` or `DLE EOT 1` to return a constant static value (`0` or `1`). | High | Critical | Mandatory 3-step Hardware Calibration Wizard checks that `open_raw_level != closed_raw_level`. Static signals are rejected and classified as `opening_only`. |
| **R-02** | **Cloud Print Timeout Causing Duplicate Receipts or Double Drawer Pulses:** If `openapi.sunmi.com` accepts `pushContent` but the HTTP response times out, naive retry logic prints twice or fires the drawer solenoid twice. | Medium | High | Idempotent `print_job_uuid` and `trade_no` tracking; before any retry, `printStatus()` reconciliation is executed. Drawer kick commands carry a unique `authorization_uuid` with a cooldown lock. |
| **R-03** | **False Attribution of Unmatched Openings to Last Cashier:** If a drawer is opened with a physical key during a break or between sessions, blaming the last cashier automatically creates unfair audit liability. | Medium | High | Strict correlation engine: physical `OPEN` events only link to an `authorization_reference` if occurring within `correlation_window_seconds` (default 8s) on the matching device. Unmatched openings leave `cashier_id` as contextual/nullable and set `event_type = 'unmatched_open'` with `review_status = 'pending_review'`. |
| **R-04** | **Sensor Disconnect While Drawer is Open:** If the USB/LAN cable or ESP32 sensor disconnects while the drawer is `OPEN`, naive systems might close the timer or leave it showing `OPEN` forever. | Medium | High | Heartbeat & staleness monitor transitions state to `OFFLINE` or `STALE`, marks the open duration record as `is_incomplete = True`, and never synthesizes a fake `CLOSED` event. |
| **R-05** | **Credential Leakage in POS Browser / WebView:** Exposing SUNMI `APP_KEY` in OWL JS or Android WebView allows attackers to forge print/open requests. | Low | Critical | SUNMI `APP_ID` and `APP_KEY` are stored encrypted/restricted server-side in Odoo (`ir.config_parameter` / device vault) and never serialized to POS frontend models or WebView JS bridges. |

