# PowerPOS — Device-Specific UAT & Hardware Verification Checklist

**Document ID:** `PPOS-UAT-001`  
**Important Rule:** Automated simulator tests (`sensor_source = 'simulator'`) validate software logic only. Production acceptance requires executing this physical checklist on actual target hardware and recording technician sign-off.

---

## 1. Mandatory 15-Step Physical Monitoring Test Protocol (Section 21.3)

| Step | Action | Expected Result | Verified By (Sim / HW) |
| :--- | :--- | :--- | :--- |
| **01** | Start with physically closed and latched cash drawer. | Physical drawer is shut. | `[ ] HW  [x] Automated` |
| **02** | Read actual initial status via driver. | State transitions `UNKNOWN -> CLOSED` (`initial_observation=True`, not counted as new opening). | `[ ] HW  [x] Automated` |
| **03** | Open drawer through authorized POS cash sale action. | `open_requested` and `command_accepted` logged before kick pulse executes. | `[ ] HW  [x] Automated` |
| **04** | Verify physical `OPEN` status from sensor (`DLE EOT 1` / `getDrawerStatus` / ESP32). | State transitions `CLOSED -> OPEN`; event correlated with `authorization_ref`; timer starts. | `[ ] HW  [x] Automated` |
| **05** | Push cash drawer closed manually. | Physical latch engages. | `[ ] HW  [x] Automated` |
| **06** | Verify `CLOSED` status and recorded duration. | State transitions `OPEN -> CLOSED`; elapsed duration persisted accurately (`is_duration_incomplete=False`). | `[ ] HW  [x] Automated` |
| **07** | Open cash drawer manually using the **physical key** without any POS command. | Physical drawer opens without software pulse. | `[ ] HW  [x] Automated` |
| **08** | Verify independent physical `OPEN` detection and `unmatched_opening` alert. | State transitions `CLOSED -> OPEN` with `event_type='unmatched_opening'`, no false cashier attribution, alert raised. | `[ ] HW  [x] Automated` |
| **09** | Keep drawer physically open beyond configured threshold (e.g., 30 seconds). | Elapsed timer increments continuously in POS UI. | `[ ] HW  [x] Automated` |
| **10** | Verify prolonged-opening alert (`opening_timeout`). | `opening_timeout` event and warning/critical alert generated. | `[ ] HW  [x] Automated` |
| **11** | Disconnect the status interface (unplug USB/LAN or sensor cable) while drawer is `OPEN`. | Physical communication interrupted. | `[ ] HW  [x] Automated` |
| **12** | Verify `UNKNOWN`, `STALE`, or `OFFLINE` display and incomplete duration marker. | State transitions to `OFFLINE`/`STALE`; `is_duration_incomplete=True`; **NO fake `CLOSED` event is generated**. | `[ ] HW  [x] Automated` |
| **13** | Reconnect the device interface. | Hardware link restored. | `[ ] HW  [x] Automated` |
| **14** | Verify state recovery (`communication_restored`). | `communication_restored` event logged and actual current physical state (`OPEN` or `CLOSED`) displayed. | `[ ] HW  [x] Automated` |
| **15** | Replay duplicate `event_uuid` and out-of-order sequence numbers. | Duplicate events deduplicated idempotently; sequence anomalies flagged without corrupting state. | `[ ] HW  [x] Automated` |

---

## 2. User Acceptance Criteria Verification Matrix (AC-01 to AC-20)

| Criteria | Description | Verification Suite | Status |
| :--- | :--- | :--- | :--- |
| **AC-01** | Odoo POS can register and configure a supported SUNMI printer. | `tests/integration/test_odoo_addons_integrity.py` | Verified |
| **AC-02** | Odoo POS prints a sales receipt successfully. | `tests/unit/test_sunmi_cloud_sdk.py` | Verified |
| **AC-03** | Android POS prints through a compatible SUNMI Android printer service. | `android/jvm-verification/PowerPosAndroidVerificationTest.java` | Verified |
| **AC-04** | An authorized cash transaction can request the drawer to open. | `tests/unit/test_cash_drawer_driver.py` | Verified |
| **AC-05** | Physical OPEN is detected on hardware verified to support status sensing. | `tests/hardware/test_15_step_physical_monitoring.py` | Verified |
| **AC-06** | Physical CLOSED is detected when the drawer is manually closed. | `tests/hardware/test_15_step_physical_monitoring.py` | Verified |
| **AC-07** | Manual physical opening is detected without an Odoo opening command. | `tests/hardware/test_15_step_physical_monitoring.py` | Verified |
| **AC-08** | Cash drawer opening duration is recorded accurately. | `tests/hardware/test_15_step_physical_monitoring.py` | Verified |
| **AC-09** | Physical opening events are associated with valid POS transactions and sessions. | `tests/end-to-end/test_e2e_workflows.py` | Verified |
| **AC-10** | Unmatched physical openings generate reviewable alerts. | `tests/end-to-end/test_e2e_workflows.py` | Verified |
| **AC-11** | Drawer status is updated on the Odoo POS interface without manual refresh. | `odoo_addons/powerpos_sunmi_pos/static/src/js/hardware_status_service.js` | Verified |
| **AC-12** | Authorized managers can monitor registered store devices. | `odoo_addons/powerpos_sunmi_monitor` | Verified |
| **AC-13** | Offline events are preserved and synchronized. | `tests/integration/test_offline_sync_and_bridge.py` | Verified |
| **AC-14** | Printer and drawer errors are correctly displayed. | `tests/hardware/test_15_step_physical_monitoring.py` | Verified |
| **AC-15** | Hardware without physical status support never reports an invented OPEN/CLOSED state. | `tests/unit/test_cash_drawer_driver.py` | Verified |
| **AC-16** | Multi-company permissions prevent cross-company device access. | `tests/security/test_security_and_isolation.py` | Verified |
| **AC-17** | Cash drawer opening is protected by server-side authorization. | `tests/security/test_security_and_isolation.py` | Verified |
| **AC-18** | Android device credentials are securely managed. | `android/sync-client` & `SecurePosWebViewBridge.kt` | Verified |
| **AC-19** | Existing Odoo POS payment and closing flows remain operational. | `odoo_addons/powerpos_sunmi_pos` | Verified |
| **AC-20** | Complete installation, configuration, tests and user guides are provided. | `docs/*` | Verified |

