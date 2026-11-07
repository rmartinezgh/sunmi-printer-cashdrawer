# PowerPOS — Android SUNMI Hardware SDK Integration Guide

**Document ID:** `PPOS-ANDROID-001`  
**Package:** `com.powerpos.hardware.sunmi` / `com.powerpos.android`

---

## 1. Module Overview

The `android/` project provides 6 modular Kotlin libraries and 1 reference POS application:

- `:sunmi-sdk-adapter` — Binder IPC wrapper for `woyou.aidlservice.jiuiv5.IWoyouService`, handling service connection, `DeathRecipient` reconnection, `openDrawer(callback)`, `getDrawerStatus()`, and `getOpenDrawerTimes()`.
- `:printer-manager` — `SunmiPrinterManager` for receipt, text, bitmap, QR, and Code128 barcode printing.
- `:cashdrawer-manager` — `CashDrawerManager` enforcing server-signed opening authorizations, state machine transitions, and open duration calculation.
- `:hardware-monitor` — Coroutine + `StateFlow` background polling and callback monitor with debounce and prolonged-open alerts.
- `:offline-storage` — Room SQLite database (`OfflineHardwareDatabase`) storing events until server acknowledgment.
- `:sync-client` — `PowerPosSyncClient` implementing HMAC-SHA256 request signing and Android Keystore credential storage.
- `:demo-app` — Sample Android POS Activity and `SecurePosWebViewBridge` for hosting Odoo Web POS inside Android WebView.

## 2. WebView Security Rules

When embedding Odoo Web POS inside an Android `WebView`:
1. Attach `SecurePosWebViewBridge` as `window.PowerPosAndroidBridge`.
2. Configure `allowedHttpsOrigins` (e.g. `setOf("https://pos.powerpos.local")`).
3. The bridge validates the current WebView URL origin and active session token before executing `printReceipt` or `requestAuthorizedDrawerOpening`.
4. Raw ESC/POS commands, `woyouService` methods, and `APP_KEY` secrets are **never** exposed to JavaScript.

