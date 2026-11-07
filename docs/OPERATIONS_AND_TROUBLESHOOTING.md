# PowerPOS — Operations, Maintenance & Troubleshooting Manual

**Document ID:** `PPOS-OPS-001`

---

## 1. Common Operational Scenarios & Diagnostics

### 1.1 Cash Drawer Shows `UNSUPPORTED`
- **Cause:** Either the device is connected via SUNMI Cloud OpenAPI only (which lacks a bidirectional `DLE EOT 1` return channel), the terminal is a handheld model without an RJ11 port (e.g., SUNMI V2), or the 3-Step Hardware Calibration Wizard detected that RJ11 Pin 3 does not change state when opened (2-wire solenoid-only drawer).
- **Resolution:** If physical open/close monitoring is required on this register, install a 5-wire sensor-equipped cash drawer with the **Local Printer Bridge** or attach a **PowerPOS ESP32 External Sensor Gateway**, then re-run the Calibration Wizard.

### 1.2 Cash Drawer Shows `STALE` or `OFFLINE`
- **Cause:** No valid sensor sample or heartbeat has been received within `staleness_timeout_seconds`.
- **Important Behavior:** PowerPOS preserves the last verified timestamp and marks any in-progress open duration as `is_duration_incomplete = True`. It will **never** display a stale `CLOSED` state as current or synthesize a fake `CLOSED` event.
- **Resolution:** Check LAN/USB cable connection to the printer or power/Wi-Fi on the ESP32 sensor gateway. Once communication is restored, a `communication_restored` audit event is automatically logged.

### 1.3 Ambiguous Cloud Print Jobs (`Unknown` State)
- **Cause:** A network timeout occurred after `pushContent()` was sent to `https://openapi.sunmi.com`.
- **Resolution:** PowerPOS automatically calls `printStatus(trade_no)` before any retry. If the cloud API reports the ticket was already queued or printed, PowerPOS transitions the job to `Accepted`/`Printed` without re-sending `pushContent`, preventing duplicate receipts and duplicate drawer kick pulses.

### 1.4 Reviewing `Unmatched Opening` Alerts
- **Cause:** The physical drawer sensor reported `CLOSED -> OPEN` without a matching `powerpos.cashdrawer.authorization` within the correlation window (default 8 seconds).
- **Resolution:** Open **PowerPOS -> Hardware Management -> Security Alerts**. Inspect the event timestamp, active POS session, and CCTV if applicable, then record investigation notes and set review status to `Resolved (Justified)` or `Resolved (Exception)`.

