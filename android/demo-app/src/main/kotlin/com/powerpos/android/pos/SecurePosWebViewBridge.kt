package com.powerpos.android.pos

import android.net.Uri
import android.webkit.JavascriptInterface
import android.webkit.WebView
import com.powerpos.android.hardware.cashdrawer.DrawerAuthorizationGrant
import com.powerpos.android.hardware.printer.BirComplianceBlock
import com.powerpos.android.hardware.printer.ReceiptLineItem
import com.powerpos.android.hardware.printer.ReceiptPrintPayload
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import java.security.MessageDigest

/**
 * Hardened `@JavascriptInterface` Bridge for the Odoo 18 WebView POS Client.
 *
 * Security Invariants:
 * 1. Exposes ONLY the 4 approved high-level methods:
 *    - `printReceipt(payloadJson, sessionToken)`
 *    - `requestAuthorizedDrawerOpening(grantJson, sessionToken)`
 *    - `getCurrentState(sessionToken)`
 *    - `subscribeStateUpdates(jsCallbackName, sessionToken)`
 * 2. Enforces strict HTTPS Origin Allowlist validation against the active WebView URL on the main thread
 *    before executing any hardware operation.
 * 3. Verifies the ephemeral WebView session token using constant-time byte comparison.
 * 4. NEVER exposes raw AIDL commands (`sendRAWData`, `openDrawer` without grant) or `APP_KEY`/HMAC secrets.
 */
class SecurePosWebViewBridge(
    private val webViewProvider: () -> WebView?,
    private val viewModel: PosHardwareViewModel,
    private val allowedHttpsOrigins: Set<String>,
    private val activeSessionTokenProvider: () -> String,
    private val mainScope: CoroutineScope
) {
    private var subscriptionJob: Job? = null

    /**
     * Validates that an origin string is HTTPS and explicitly listed in `allowedHttpsOrigins`.
     */
    fun isOriginAllowed(currentPageUrl: String?): Boolean {
        if (currentPageUrl.isNullOrBlank()) return false
        return try {
            val uri = Uri.parse(currentPageUrl)
            val scheme = uri.scheme?.lowercase() ?: return false
            if (scheme != "https") return false
            val host = uri.host?.lowercase() ?: return false
            val port = if (uri.port != -1 && uri.port != 443) ":${uri.port}" else ""
            val normalizedOrigin = "https://$host$port"
            allowedHttpsOrigins.any { it.lowercase().trimEnd('/') == normalizedOrigin }
        } catch (_: Exception) {
            false
        }
    }

    private fun verifyCallerSecurity(sessionToken: String): Result<Unit> {
        val expectedToken = activeSessionTokenProvider()
        if (expectedToken.isBlank() || sessionToken.isBlank()) {
            return Result.failure(SecurityException("Missing WebView POS session token"))
        }
        val validToken = MessageDigest.isEqual(
            expectedToken.toByteArray(Charsets.UTF_8),
            sessionToken.toByteArray(Charsets.UTF_8)
        )
        if (!validToken) {
            return Result.failure(SecurityException("Invalid WebView POS session token"))
        }

        val currentUrl = runBlocking(Dispatchers.Main.immediate) {
            webViewProvider()?.url
        }
        if (!isOriginAllowed(currentUrl)) {
            return Result.failure(SecurityException("Untrusted WebView origin: $currentUrl"))
        }
        return Result.success(Unit)
    }

    @JavascriptInterface
    fun getCurrentState(sessionToken: String): String {
        val sec = verifyCallerSecurity(sessionToken)
        if (sec.isFailure) {
            return buildErrorJson("UNAUTHORIZED", sec.exceptionOrNull()?.message ?: "Access denied")
        }

        val snapshot = viewModel.hardwareSnapshot.value
        val ui = viewModel.uiState.value
        return JSONObject().apply {
            put("ok", true)
            put("terminal_id", ui.terminalId)
            put("drawer_state", snapshot.drawerReading.state.name)
            put("drawer_observed_at_ms", snapshot.drawerReading.observedAtEpochMs)
            put("printer_ready", snapshot.printerStatus.isReady)
            put("printer_status_code", snapshot.printerStatus.statusCode)
            put("printer_status_name", snapshot.printerStatus.statusName)
            put("pending_offline_events", ui.pendingOfflineEventsCount)
        }.toString()
    }

    @JavascriptInterface
    fun requestAuthorizedDrawerOpening(grantJson: String, sessionToken: String): String {
        val sec = verifyCallerSecurity(sessionToken)
        if (sec.isFailure) {
            return buildErrorJson("UNAUTHORIZED", sec.exceptionOrNull()?.message ?: "Access denied")
        }

        return try {
            val json = JSONObject(grantJson)
            val grant = DrawerAuthorizationGrant(
                authorizationId = json.getString("authorization_id"),
                idempotencyKey = json.getString("idempotency_key"),
                authToken = json.getString("auth_token"),
                reason = json.optString("reason", "CASH_SALE"),
                operatorId = json.getString("operator_id"),
                posOrderId = json.optString("pos_order_id", "").takeIf { it.isNotBlank() },
                issuedAtEpochMs = json.getLong("issued_at_epoch_ms"),
                expiresAtEpochMs = json.getLong("expires_at_epoch_ms")
            )

            val pulseResult = runBlocking(Dispatchers.IO) {
                viewModel.executeAuthorizedDrawerOpen(grant)
            }

            JSONObject().apply {
                put("ok", pulseResult.pulseDispatched)
                put("pulse_dispatched", pulseResult.pulseDispatched)
                put("timestamp_ms", pulseResult.timestampMs)
                put("note", "pulse_dispatched confirms solenoid pulse only; physical opening is reported via state stream")
                pulseResult.errorCode?.let { put("error_code", it) }
                pulseResult.errorMessage?.let { put("error_message", it) }
            }.toString()
        } catch (e: Exception) {
            buildErrorJson("INVALID_GRANT_PAYLOAD", e.message ?: "Malformed authorization grant JSON")
        }
    }

    @JavascriptInterface
    fun printReceipt(payloadJson: String, sessionToken: String): String {
        val sec = verifyCallerSecurity(sessionToken)
        if (sec.isFailure) {
            return buildErrorJson("UNAUTHORIZED", sec.exceptionOrNull()?.message ?: "Access denied")
        }

        return try {
            val json = JSONObject(payloadJson)
            val birObj = json.getJSONObject("bir_compliance")
            val bir = BirComplianceBlock(
                taxpayerName = birObj.getString("taxpayer_name"),
                vatRegTin = birObj.getString("vat_reg_tin"),
                machineIdentificationNumber = birObj.getString("min"),
                permitToUseNumber = birObj.getString("ptu_number"),
                serialNumber = birObj.getString("serial_number"),
                accreditationNumber = birObj.getString("accreditation_number"),
                dateIssued = birObj.getString("date_issued"),
                validUntil = birObj.getString("valid_until"),
                officialReceiptNumber = birObj.getString("official_receipt_number"),
                vatableSales = birObj.getString("vatable_sales"),
                vatAmount = birObj.getString("vat_amount"),
                vatExemptSales = birObj.getString("vat_exempt_sales"),
                zeroRatedSales = birObj.getString("zero_rated_sales"),
                isReprint = birObj.optBoolean("is_reprint", false)
            )

            val itemsArr = json.optJSONArray("items") ?: JSONArray()
            val items = mutableListOf<ReceiptLineItem>()
            for (i in 0 until itemsArr.length()) {
                val itemObj = itemsArr.getJSONObject(i)
                items.add(
                    ReceiptLineItem(
                        description = itemObj.getString("description"),
                        quantity = itemObj.getString("quantity"),
                        unitPrice = itemObj.getString("unit_price"),
                        lineTotal = itemObj.getString("line_total")
                    )
                )
            }

            val payload = ReceiptPrintPayload(
                jobId = json.getString("job_id"),
                merchantStoreName = json.getString("merchant_store_name"),
                merchantCustomHeaderLines = jsonArrayToStringList(json.optJSONArray("merchant_header_lines")),
                birCompliance = bir,
                cashierName = json.getString("cashier_name"),
                terminalId = json.getString("terminal_id"),
                transactionTimestamp = json.getString("transaction_timestamp"),
                items = items,
                totalAmount = json.getString("total_amount"),
                paymentMethod = json.getString("payment_method"),
                amountTendered = json.getString("amount_tendered"),
                changeDue = json.getString("change_due"),
                qrVerificationUrl = json.optString("qr_verification_url", "").takeIf { it.isNotBlank() },
                barcodeData = json.optString("barcode_data", "").takeIf { it.isNotBlank() },
                merchantCustomFooterLines = jsonArrayToStringList(json.optJSONArray("merchant_footer_lines")),
                autoCutPaper = json.optBoolean("auto_cut_paper", true)
            )

            val printResult = runBlocking(Dispatchers.IO) {
                viewModel.printPosReceipt(payload)
            }

            JSONObject().apply {
                put("ok", printResult.isSuccess)
                put("job_id", payload.jobId)
                printResult.exceptionOrNull()?.message?.let { put("error_message", it) }
            }.toString()
        } catch (e: Exception) {
            buildErrorJson("PRINT_FAILED", e.message ?: "Invalid print payload")
        }
    }

    @JavascriptInterface
    fun subscribeStateUpdates(jsCallbackName: String, sessionToken: String): String {
        val sec = verifyCallerSecurity(sessionToken)
        if (sec.isFailure) {
            return buildErrorJson("UNAUTHORIZED", sec.exceptionOrNull()?.message ?: "Access denied")
        }

        // Only allow safe identifier names to prevent JS injection via callback parameter
        if (!jsCallbackName.matches(Regex("^[a-zA-Z0-9_.]{1,64}$"))) {
            return buildErrorJson("INVALID_CALLBACK", "Callback name must be a valid JS identifier")
        }

        subscriptionJob?.cancel()
        subscriptionJob = mainScope.launch(Dispatchers.Main) {
            viewModel.hardwareSnapshot.collect { snapshot ->
                val webView = webViewProvider() ?: return@collect
                if (!isOriginAllowed(webView.url)) return@collect
                val stateJson = JSONObject().apply {
                    put("drawer_state", snapshot.drawerReading.state.name)
                    put("observed_at_ms", snapshot.drawerReading.observedAtEpochMs)
                    put("printer_ready", snapshot.printerStatus.isReady)
                    put("printer_status_code", snapshot.printerStatus.statusCode)
                    snapshot.activeOpenAlert?.let { alert ->
                        put("open_alert_severity", alert.severity.name)
                        put("open_duration_sec", alert.openDurationSeconds)
                    }
                }.toString()
                webView.evaluateJavascript("window.$jsCallbackName($stateJson);", null)
            }
        }

        return JSONObject().apply {
            put("ok", true)
            put("subscribed_callback", jsCallbackName)
        }.toString()
    }

    private fun jsonArrayToStringList(arr: JSONArray?): List<String> {
        if (arr == null) return emptyList()
        val list = mutableListOf<String>()
        for (i in 0 until arr.length()) {
            list.add(arr.optString(i, ""))
        }
        return list
    }

    private fun buildErrorJson(code: String, message: String): String {
        return JSONObject().apply {
            put("ok", false)
            put("error_code", code)
            put("error_message", message)
        }.toString()
    }
}
