package com.powerpos.android.hardware.printer

import com.powerpos.hardware.sunmi.HardwareCapabilities
import com.powerpos.hardware.sunmi.SunmiWoyouServiceAdapter
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/**
 * Mandatory Philippine BIR (Bureau of Internal Revenue) compliance block for POS receipts.
 * Kept strictly separate from customizable merchant marketing headers/footers so merchant
 * overrides can never strip statutory tax fields.
 */
data class BirComplianceBlock(
    val taxpayerName: String,
    val vatRegTin: String,
    val machineIdentificationNumber: String,
    val permitToUseNumber: String,
    val serialNumber: String,
    val accreditationNumber: String,
    val dateIssued: String,
    val validUntil: String,
    val officialReceiptNumber: String,
    val vatableSales: String,
    val vatAmount: String,
    val vatExemptSales: String,
    val zeroRatedSales: String,
    val isReprint: Boolean = false
)

data class ReceiptLineItem(
    val description: String,
    val quantity: String,
    val unitPrice: String,
    val lineTotal: String
)

data class ReceiptPrintPayload(
    val jobId: String,
    val merchantStoreName: String,
    val merchantCustomHeaderLines: List<String>,
    val birCompliance: BirComplianceBlock,
    val cashierName: String,
    val terminalId: String,
    val transactionTimestamp: String,
    val items: List<ReceiptLineItem>,
    val totalAmount: String,
    val paymentMethod: String,
    val amountTendered: String,
    val changeDue: String,
    val qrVerificationUrl: String?,
    val barcodeData: String?,
    val merchantCustomFooterLines: List<String>,
    val autoCutPaper: Boolean = true
)

data class PrinterStatusReport(
    val statusCode: Int,
    val isReady: Boolean,
    val statusName: String,
    val humanReadableMessage: String
)

/**
 * Production thermal printer manager for SUNMI POS terminals.
 * Serializes print jobs via Mutex and enforces Philippine BIR statutory layout blocks.
 */
class SunmiPrinterManager(
    private val serviceAdapter: SunmiWoyouServiceAdapter
) {
    private val printMutex = Mutex()

    /**
     * Initializes the underlying Woyou service connection and resets printer state.
     */
    suspend fun initializeService(): Result<HardwareCapabilities> {
        val initResult = serviceAdapter.connectAndInitialize()
        if (initResult.isSuccess) {
            serviceAdapter.executePrintOperation { service, callback ->
                service.printerInit(callback)
            }
        }
        return initResult
    }

    /**
     * Checks whether the thermal printer is ready to accept jobs and decodes any hardware fault.
     */
    suspend fun checkPrinterAvailability(): PrinterStatusReport {
        val code = serviceAdapter.queryPrinterStateCode()
        return decodePrinterError(code)
    }

    /**
     * Decodes SUNMI Woyou `updatePrinterState()` return codes into actionable diagnostics.
     */
    fun decodePrinterError(code: Int): PrinterStatusReport {
        return when (code) {
            1 -> PrinterStatusReport(1, true, "NORMAL", "Printer is online and ready.")
            2 -> PrinterStatusReport(2, false, "PREPARING", "Printer is initializing or updating firmware.")
            3 -> PrinterStatusReport(3, false, "COMM_ERROR", "Abnormal communication with printer controller.")
            4 -> PrinterStatusReport(4, false, "OUT_OF_PAPER", "Printer is out of thermal paper.")
            5 -> PrinterStatusReport(5, false, "OVERHEATED", "Thermal printhead is overheated; waiting to cool down.")
            6 -> PrinterStatusReport(6, false, "COVER_OPEN", "Printer paper compartment cover is open.")
            7 -> PrinterStatusReport(7, false, "CUTTER_ERROR", "Auto-cutter jam or mechanical fault detected.")
            8 -> PrinterStatusReport(8, true, "CUTTER_RECOVERED", "Auto-cutter recovered and ready.")
            9 -> PrinterStatusReport(9, false, "BLACK_MARK_NOT_FOUND", "Black mark sensor did not detect label mark.")
            505 -> PrinterStatusReport(505, false, "NO_PRINTER_DETECTED", "No built-in thermal printer hardware detected.")
            507 -> PrinterStatusReport(507, false, "FIRMWARE_FAILED", "Printer firmware update failed.")
            else -> PrinterStatusReport(code, false, "OFFLINE_OR_UNKNOWN", "Printer service unreachable (code: $code).")
        }
    }

    /**
     * Renders and prints a full Philippine BIR-compliant receipt atomically.
     */
    suspend fun printReceipt(payload: ReceiptPrintPayload): Result<Unit> = printMutex.withLock {
        withContext(Dispatchers.IO) {
            val availability = checkPrinterAvailability()
            if (!availability.isReady) {
                return@withContext Result.failure(
                    IllegalStateException("Printer unavailable [${availability.statusName}]: ${availability.humanReadableMessage}")
                )
            }

            val bir = payload.birCompliance
            val receiptText = buildString {
                // 1. Mandatory BIR Taxpayer Header
                appendLine(centerText(payload.merchantStoreName.uppercase(), 32))
                appendLine(centerText(bir.taxpayerName, 32))
                appendLine(centerText("VAT REG TIN: ${bir.vatRegTin}", 32))
                appendLine(centerText("MIN: ${bir.machineIdentificationNumber}", 32))
                appendLine(centerText("PTU NO: ${bir.permitToUseNumber}", 32))
                appendLine(centerText("S/N: ${bir.serialNumber}", 32))

                // 2. Merchant Custom Header Lines
                payload.merchantCustomHeaderLines.forEach { line ->
                    appendLine(centerText(line, 32))
                }
                appendLine("-".repeat(32))

                if (bir.isReprint) {
                    appendLine(centerText("*** REPRINT COPY ***", 32))
                    appendLine("-".repeat(32))
                }

                // 3. Transaction Metadata
                appendLine("SI/OR #: ${bir.officialReceiptNumber}")
                appendLine("Term: ${payload.terminalId}  Cashier: ${payload.cashierName}")
                appendLine("Date: ${payload.transactionTimestamp}")
                appendLine("-".repeat(32))

                // 4. Line Items
                payload.items.forEach { item ->
                    appendLine(item.description.take(32))
                    val left = "  ${item.quantity} x ${item.unitPrice}"
                    val right = item.lineTotal
                    appendLine(formatTwoColumns(left, right, 32))
                }
                appendLine("-".repeat(32))

                // 5. Totals & Tender
                appendLine(formatTwoColumns("TOTAL AMOUNT", payload.totalAmount, 32))
                appendLine(formatTwoColumns("PAYMENT (${payload.paymentMethod})", payload.amountTendered, 32))
                appendLine(formatTwoColumns("CHANGE", payload.changeDue, 32))
                appendLine("-".repeat(32))

                // 6. Mandatory BIR VAT Breakdown
                appendLine(formatTwoColumns("VATable Sales", bir.vatableSales, 32))
                appendLine(formatTwoColumns("VAT Amount (12%)", bir.vatAmount, 32))
                appendLine(formatTwoColumns("VAT-Exempt Sales", bir.vatExemptSales, 32))
                appendLine(formatTwoColumns("Zero-Rated Sales", bir.zeroRatedSales, 32))
                appendLine("-".repeat(32))

                // 7. Merchant Custom Footer
                payload.merchantCustomFooterLines.forEach { line ->
                    appendLine(centerText(line, 32))
                }
                appendLine("-".repeat(32))

                // 8. Mandatory BIR Provider Accreditation Footer
                appendLine(centerText("ACCRED. NO: ${bir.accreditationNumber}", 32))
                appendLine(centerText("DATE ISSUED: ${bir.dateIssued}", 32))
                appendLine(centerText("VALID UNTIL: ${bir.validUntil}", 32))
                appendLine(centerText("THIS SERVES AS YOUR SALES INVOICE", 32))
            }

            val textResult = printText(receiptText)
            if (textResult.isFailure) {
                return@withContext Result.failure(textResult.exceptionOrNull()!!)
            }

            payload.qrVerificationUrl?.takeIf { it.isNotBlank() }?.let { qr ->
                printQrCode(qr, moduleSize = 6, errorLevel = 2)
            }

            payload.barcodeData?.takeIf { it.isNotBlank() }?.let { barcode ->
                printBarcode(barcode, symbology = 8, height = 64, width = 2, textPosition = 2)
            }

            serviceAdapter.executePrintOperation { service, callback ->
                service.lineWrap(4, callback)
            }

            if (payload.autoCutPaper) {
                serviceAdapter.executePrintOperation { service, callback ->
                    service.cutPaper(callback)
                }
            }

            Result.success(Unit)
        }
    }

    /**
     * Prints UTF-8 text via Woyou AIDL service.
     */
    suspend fun printText(text: String, alignment: Int = 0, fontSize: Float = 24f): Result<Unit> {
        val alignRes = serviceAdapter.executePrintOperation { service, callback ->
            service.setAlignment(alignment, callback)
        }
        if (alignRes.isFailure) return Result.failure(alignRes.exceptionOrNull()!!)

        val fontRes = serviceAdapter.executePrintOperation { service, callback ->
            service.setFontSize(fontSize, callback)
        }
        if (fontRes.isFailure) return Result.failure(fontRes.exceptionOrNull()!!)

        val printRes = serviceAdapter.executePrintOperation { service, callback ->
            service.printText(text, callback)
        }
        return printRes.map { }
    }

    /**
     * Prints rasterized ESC/POS image bytes via `sendRAWData`.
     */
    suspend fun printImage(escPosRasterBytes: ByteArray): Result<Unit> {
        if (escPosRasterBytes.isEmpty()) {
            return Result.failure(IllegalArgumentException("Image byte payload cannot be empty"))
        }
        return serviceAdapter.executePrintOperation { service, callback ->
            service.sendRAWData(escPosRasterBytes, callback)
        }.map { }
    }

    /**
     * Prints a 2D QR code on the receipt.
     */
    suspend fun printQrCode(data: String, moduleSize: Int = 6, errorLevel: Int = 2): Result<Unit> {
        serviceAdapter.executePrintOperation { service, callback ->
            service.setAlignment(1, callback)
        }
        return serviceAdapter.executePrintOperation { service, callback ->
            service.printQRCode(data, moduleSize, errorLevel, callback)
        }.map { }
    }

    /**
     * Prints a 1D barcode on the receipt.
     */
    suspend fun printBarcode(
        data: String,
        symbology: Int = 8,
        height: Int = 64,
        width: Int = 2,
        textPosition: Int = 2
    ): Result<Unit> {
        serviceAdapter.executePrintOperation { service, callback ->
            service.setAlignment(1, callback)
        }
        return serviceAdapter.executePrintOperation { service, callback ->
            service.printBarCode(data, symbology, height, width, textPosition, callback)
        }.map { }
    }

    /**
     * Releases underlying service resources.
     */
    fun releaseResources() {
        serviceAdapter.disconnect()
    }

    private fun centerText(input: String, width: Int): String {
        val trimmed = input.trim()
        if (trimmed.length >= width) return trimmed.take(width)
        val totalPadding = width - trimmed.length
        val leftPad = totalPadding / 2
        return " ".repeat(leftPad) + trimmed
    }

    private fun formatTwoColumns(left: String, right: String, width: Int): String {
        val safeRight = right.trim()
        val maxLeft = (width - safeRight.length - 1).coerceAtLeast(1)
        val safeLeft = left.take(maxLeft)
        val spaces = (width - safeLeft.length - safeRight.length).coerceAtLeast(1)
        return safeLeft + " ".repeat(spaces) + safeRight
    }
}
