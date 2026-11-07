# PowerPOS — SUNMI Printer and Intelligent Cash Drawer Management System

Production-grade hardware integration platform connecting **SUNMI Cloud Printers**, **SUNMI Android POS Terminals**, **Standalone ESC/POS Printers**, and **Intelligent Cash Drawers** to **Odoo 18 / 20 POS** and **Native Android POS** applications across multi-store, multi-branch, and multi-company deployments.

---

## Core Architectural Principles

1. **Opening Command != Physical Opening:** Executing `woyouService.openDrawer(callback)` or sending ESC/POS `ESC p` (`1B 70 ...`) only energizes the drawer kick coil. Physical `OPEN` is never reported until verified sensor feedback (`DLE EOT 1` / `10 04 01`, `getDrawerStatus()`, or external ESP32 reed switch) confirms the transition.
2. **Receipt Printing != Drawer Status:** Cloud or local receipt completion never implies drawer state.
3. **Zero Fabricated States (`AC-15`):** Hardware without verified physical sensor support reports `UNSUPPORTED` or `UNKNOWN`—never an invented `OPEN` or `CLOSED`.
4. **Disconnect Safety (`Section 10.5`):** If sensor communication drops while a drawer is physically `OPEN`, the system transitions to `OFFLINE`/`STALE`/`ERROR` and marks the opening duration as `is_duration_incomplete = True` without generating an artificial `CLOSED` event.
5. **Strict Event Attribution (`Section 11.1`):** Manual physical openings without a matching pre-execution authorization within the correlation window are classified as `unmatched_opening` for supervisor review rather than falsely blaming the last cashier.

---

## Repository Structure

```text
powerpos-sunmi/
├── odoo_addons/
│   ├── powerpos_sunmi_core/        # Module A: Device Registry, Pairing, Capability Matrix
│   ├── powerpos_sunmi_cloud/       # Module B: SUNMI Cloud OpenAPI, Print Queue, BIR Templates
│   ├── powerpos_sunmi_cashdrawer/  # Module D & E: Cash Drawer Registry, Policy & State Machine
│   ├── powerpos_sunmi_audit/       # Module F & L: Immutable Hash-Chained Audit & REST API
│   ├── powerpos_sunmi_monitor/     # Module G & J: Security Alerts, Bus Push & 12-Menu Dashboard
│   ├── powerpos_sunmi_pos/         # Module H: Odoo POS OWL 2 Hardware Status Widget & Controls
│   └── powerpos_sunmi_reports/     # Module K: 8 Operational & Exception Reports (PDF/CSV/XLSX)
├── android/
│   ├── sunmi-sdk-adapter/          # Module C: Woyou AIDL Binder & Hardware Capabilities
│   ├── printer-manager/            # SunmiPrinterManager (Receipts, QR, Barcode, BIR Layout)
│   ├── cashdrawer-manager/         # CashDrawerManager & State Transition Engine
│   ├── hardware-monitor/           # Coroutines + StateFlow Polling, Debounce & Alerts
│   ├── offline-storage/            # Durable Room SQLite Event & Auth Persistence
│   ├── sync-client/                # HMAC-SHA256 Signed REST Sync & Keystore Credentials
│   ├── demo-app/                   # Android POS Sample App & SecurePosWebViewBridge
│   └── jvm-verification/           # Standalone Java Verification Suite
├── local-bridge/
│   ├── python/                     # CashDrawerDriver, DLE EOT 1 Adapter, Cloud SDK, SQLite Sync
│   ├── windows-service/            # Windows Service Daemon & PowerShell Installer
│   └── linux-service/              # Linux systemd Service Unit & Installer
├── sensor-gateway/
│   ├── esp32_firmware/             # ESP32 C++ (.ino) & MicroPython Reed + Tamper Firmware
│   └── gateway_service.py          # HMAC-Verified Sensor Gateway Coordinator
├── api-contracts/
│   └── openapi.yaml                # OpenAPI 3.1 Specification for `/api/v1/*`
├── tests/
│   ├── unit/                       # Cloud SDK & CashDrawerDriver Unit Tests
│   ├── hardware/                   # 15-Step Physical Monitoring Protocol Verification
│   ├── integration/                # Offline SQLite Durability & ESP32 Gateway Sync Tests
│   ├── security/                   # SHA-256 Audit Chain & Multi-Company Isolation Tests
│   └── end-to-end/                 # Addon Manifest, Syntax, XML & AC-01..AC-20 Verification
├── scripts/
│   └── run_all_tests.py            # Master Test Runner
├── deployment/                     # Docker Compose, Odoo Config & Nginx TLS Rate-Limiting
└── docs/                           # Capability Matrix, Architecture, API, UAT & Operations Guides
```

