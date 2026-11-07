package com.powerpos.android.ui

import android.annotation.SuppressLint
import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup
import android.webkit.WebSettings
import android.webkit.WebView
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.powerpos.android.hardware.cashdrawer.CashDrawerManager
import com.powerpos.android.hardware.cashdrawer.DrawerAuthorizationGrant
import com.powerpos.android.hardware.printer.BirComplianceBlock
import com.powerpos.android.hardware.printer.ReceiptLineItem
import com.powerpos.android.hardware.printer.ReceiptPrintPayload
import com.powerpos.android.hardware.printer.SunmiPrinterManager
import com.powerpos.android.monitoring.HardwareMonitorService
import com.powerpos.android.network.DeviceCredentialStore
import com.powerpos.android.network.PowerPosSyncClient
import com.powerpos.android.persistence.OfflineHardwareDatabase
import com.powerpos.android.pos.PosHardwareViewModel
import com.powerpos.android.pos.SecurePosWebViewBridge
import com.powerpos.hardware.sunmi.SunmiWoyouServiceAdapter
import kotlinx.coroutines.launch
import java.util.UUID

/**
 * Production POS Terminal Activity & Hardware Diagnostics Console.
 * Hosts the hardened Odoo 18 POS WebView alongside a collapsible SUNMI Hardware Diagnostics Drawer
 * for verifying cash drawer sensor polarity, BIR test receipts, and offline Room sync queues.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var viewModel: PosHardwareViewModel
    private lateinit var statusBannerText: TextView
    private lateinit var auditLogText: TextView
    private var posWebView: WebView? = null
    private val ephemeralSessionToken: String = UUID.randomUUID().toString()

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Wire production hardware dependency graph
        val serviceAdapter = SunmiWoyouServiceAdapter(applicationContext)
        val cashDrawerManager = CashDrawerManager(serviceAdapter)
        val printerManager = SunmiPrinterManager(serviceAdapter)
        val monitorService = HardwareMonitorService(serviceAdapter, cashDrawerManager, printerManager)
        val database = OfflineHardwareDatabase.getInstance(applicationContext)
        val credentialStore = DeviceCredentialStore(applicationContext)
        val syncClient = PowerPosSyncClient(credentialStore, database.hardwareEventDao())

        viewModel = PosHardwareViewModel(
            terminalId = "SUNMI-T2-POS01",
            cashDrawerManager = cashDrawerManager,
            printerManager = printerManager,
            monitorService = monitorService,
            eventDao = database.hardwareEventDao(),
            syncClient = syncClient
        )

        val rootLayout = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        }

        // Left Pane (70% width): Hardened Odoo 18 WebView POS
        val webView = WebView(this).apply {
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 0.7f)
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
        }
        posWebView = webView

        val bridge = SecurePosWebViewBridge(
            webViewProvider = { posWebView },
            viewModel = viewModel,
            allowedHttpsOrigins = setOf("https://pos.powerpos.local", "https://erp.powerpos.ph"),
            activeSessionTokenProvider = { ephemeralSessionToken },
            mainScope = lifecycleScope
        )
        webView.addJavascriptInterface(bridge, "PowerPosHardwareBridge")

        // Right Pane (30% width): Native SUNMI Hardware Diagnostics & Audit Panel
        val diagnosticsPane = buildDiagnosticsPane()

        rootLayout.addView(webView)
        rootLayout.addView(diagnosticsPane)
        setContentView(rootLayout)

        observeHardwareStates()
    }

    private fun buildDiagnosticsPane(): LinearLayout {
        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.parseColor("#1E293B"))
            setPadding(24, 24, 24, 24)
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 0.3f)

            val title = TextView(context).apply {
                text = "SUNMI Hardware Diagnostics"
                setTextColor(Color.WHITE)
                textSize = 18f
                setTypeface(typeface, Typeface.BOLD)
            }
            addView(title)

            statusBannerText = TextView(context).apply {
                text = "Initializing Woyou AIDL..."
                setTextColor(Color.parseColor("#38BDF8"))
                textSize = 13f
                setPadding(0, 16, 0, 16)
            }
            addView(statusBannerText)

            val openDrawerBtn = Button(context).apply {
                text = "Test Authorized Drawer Pulse"
                setOnClickListener {
                    lifecycleScope.launch {
                        val now = System.currentTimeMillis()
                        val grant = DrawerAuthorizationGrant(
                            authorizationId = "DIAG-AUTH-${now % 100000}",
                            idempotencyKey = UUID.randomUUID().toString(),
                            authToken = "SIGNED-TOKEN-${UUID.randomUUID()}",
                            reason = "DIAGNOSTIC_TEST_OPEN",
                            operatorId = "ADMIN",
                            posOrderId = null,
                            issuedAtEpochMs = now,
                            expiresAtEpochMs = now + 30_000L
                        )
                        viewModel.executeAuthorizedDrawerOpen(grant)
                    }
                }
            }
            addView(openDrawerBtn)

            val printTestBtn = Button(context).apply {
                text = "Print BIR Compliance Receipt"
                setOnClickListener {
                    lifecycleScope.launch {
                        viewModel.printPosReceipt(buildSampleBirReceipt())
                    }
                }
            }
            addView(printTestBtn)

            val syncNowBtn = Button(context).apply {
                text = "Flush Offline Event Queue"
                setOnClickListener {
                    viewModel.triggerManualSync()
                }
            }
            addView(syncNowBtn)

            val scroll = ScrollView(context).apply {
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    0,
                    1f
                )
            }
            auditLogText = TextView(context).apply {
                text = "No transitions recorded yet."
                setTextColor(Color.parseColor("#E2E8F0"))
                textSize = 11f
                gravity = Gravity.TOP
            }
            scroll.addView(auditLogText)
            addView(scroll)
        }
    }

    private fun observeHardwareStates() {
        lifecycleScope.launch {
            viewModel.uiState.collect { state ->
                val model = state.capabilities?.deviceModel ?: "Detecting..."
                val drawerCap = state.capabilities?.drawerSensorCapability?.name ?: "UNVERIFIED"
                statusBannerText.text = buildString {
                    appendLine("Terminal: ${state.terminalId} ($model)")
                    appendLine("Drawer Sensor Cap: $drawerCap")
                    appendLine("Physical Drawer State: ${state.drawerState}")
                    appendLine("Printer: ${state.printerStatusText}")
                    appendLine("Unsynced Room Events: ${state.pendingOfflineEventsCount}")
                    state.lastPulseResultMessage?.let { appendLine("Last Pulse: $it") }
                    state.activeSecurityAlert?.let { appendLine("ALERT: $it") }
                }
            }
        }

        lifecycleScope.launch {
            viewModel.recentAuditEvents.collect { events ->
                if (events.isEmpty()) return@collect
                auditLogText.text = events.joinToString("\n----------------\n") { ev ->
                    "${ev.transitionType}: ${ev.previousState} -> ${ev.newState}\n" +
                        "Corr: ${ev.correlationClass} | Auth: ${ev.matchedAuthorizationId ?: "NONE"}\n" +
                        "Dur: ${ev.openDurationMs ?: "-"}ms (incomplete=${ev.durationIncomplete})\n" +
                        "Sync: ${ev.syncStatus} (ack=${ev.serverAckToken ?: "NONE"})"
                }
            }
        }
    }

    private fun buildSampleBirReceipt(): ReceiptPrintPayload {
        return ReceiptPrintPayload(
            jobId = UUID.randomUUID().toString(),
            merchantStoreName = "POWERPOS MANILA FLAGSHIP",
            merchantCustomHeaderLines = listOf("Unit 101 Ayala Ave, Makati City", "Tel: (02) 8888-0000"),
            birCompliance = BirComplianceBlock(
                taxpayerName = "POWERPOS RETAIL CORP.",
                vatRegTin = "009-123-456-00000",
                machineIdentificationNumber = "24010112345678901",
                permitToUseNumber = "FP012026-123-0000001-00000",
                serialNumber = "SUNMIT2-SN998877",
                accreditationNumber = "048-123456789-000001",
                dateIssued = "2026-01-15",
                validUntil = "2031-01-15",
                officialReceiptNumber = "SI-000004821",
                vatableSales = "1,000.00",
                vatAmount = "120.00",
                vatExemptSales = "0.00",
                zeroRatedSales = "0.00",
                isReprint = false
            ),
            cashierName = "Maria Santos",
            terminalId = "SUNMI-T2-POS01",
            transactionTimestamp = "2026-11-07 21:15:00",
            items = listOf(
                ReceiptLineItem("Barako Coffee Beans 500g", "2", "560.00", "1,120.00")
            ),
            totalAmount = "1,120.00",
            paymentMethod = "CASH",
            amountTendered = "1,200.00",
            changeDue = "80.00",
            qrVerificationUrl = "https://pos.powerpos.local/verify/SI-000004821",
            barcodeData = "SI000004821",
            merchantCustomFooterLines = listOf("Thank you for shopping at PowerPOS!"),
            autoCutPaper = true
        )
    }
}
