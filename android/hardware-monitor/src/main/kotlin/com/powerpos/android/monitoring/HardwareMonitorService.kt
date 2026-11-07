package com.powerpos.android.monitoring

import android.os.SystemClock
import com.powerpos.android.hardware.cashdrawer.CashDrawerManager
import com.powerpos.android.hardware.printer.PrinterStatusReport
import com.powerpos.android.hardware.printer.SunmiPrinterManager
import com.powerpos.hardware.sunmi.DrawerPhysicalState
import com.powerpos.hardware.sunmi.DrawerStatusReading
import com.powerpos.hardware.sunmi.SunmiWoyouServiceAdapter
import com.powerpos.hardware.sunmi.SwitchPolarity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlin.math.min

/**
 * Allowed polling intervals for cash drawer microswitch interrogation.
 */
enum class PollingCadence(val intervalMs: Long) {
    ULTRA_FAST_250MS(250L),
    STANDARD_500MS(500L),
    BALANCED_1000MS(1000L),
    RELAXED_2000MS(2000L),
    LOW_POWER_5000MS(5000L)
}

/**
 * Escalating prolonged drawer-open warning thresholds.
 */
enum class OpenDrawerAlertSeverity(val thresholdSeconds: Long) {
    INFO_15S(15L),
    WARNING_30S(30L),
    CRITICAL_60S(60L),
    SECURITY_ESCALATION_120S(120L)
}

data class ProlongedOpenAlert(
    val severity: OpenDrawerAlertSeverity,
    val openDurationSeconds: Long,
    val detectedAtEpochMs: Long
)

data class CalibrationResult(
    val success: Boolean,
    val detectedPolarity: SwitchPolarity,
    val closedRawSignal: Boolean?,
    val openRawSignal: Boolean?,
    val diagnosticMessage: String
)

data class CombinedHardwareSnapshot(
    val drawerReading: DrawerStatusReading,
    val printerStatus: PrinterStatusReport,
    val pollingIntervalMs: Long,
    val activeOpenAlert: ProlongedOpenAlert?
)

/**
 * Continuous Kotlin Coroutine & StateFlow hardware monitor service.
 *
 * Features:
 * - Configurable polling cadence (250ms, 500ms, 1000ms, 2000ms, 5000ms)
 * - 2-sample signal debounce filter to reject electrical contact bounce on the RJ11 microswitch
 * - Exponential error backoff when AIDL service is offline
 * - Escalating prolonged-open alerting (15s, 30s, 60s, 120s)
 * - Interactive hardware polarity calibration routine
 */
class HardwareMonitorService(
    private val serviceAdapter: SunmiWoyouServiceAdapter,
    private val cashDrawerManager: CashDrawerManager,
    private val printerManager: SunmiPrinterManager,
    initialCadence: PollingCadence = PollingCadence.STANDARD_500MS,
    private val monitorScope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
) {
    companion object {
        private const val DEBOUNCE_REQUIRED_SAMPLES = 2
        private const val STALE_READING_TTL_MS = 10_000L
    }

    @Volatile
    private var currentCadence: PollingCadence = initialCadence

    private var pollingJob: Job? = null
    private var candidateState: DrawerPhysicalState? = null
    private var candidateSampleCount: Int = 0
    private var confirmedState: DrawerPhysicalState = DrawerPhysicalState.UNKNOWN
    private var openStartMonotonicMs: Long? = null
    private var lastEmittedAlertSeverity: OpenDrawerAlertSeverity? = null
    private var lastValidReadingMonotonicMs: Long = 0L

    private val _hardwareSnapshot = MutableStateFlow(
        CombinedHardwareSnapshot(
            drawerReading = DrawerStatusReading(
                state = DrawerPhysicalState.UNKNOWN,
                rawSignal = null,
                observedAtEpochMs = System.currentTimeMillis(),
                monotonicElapsedRealtimeMs = SystemClock.elapsedRealtime()
            ),
            printerStatus = PrinterStatusReport(1, true, "INITIALIZING", "Initializing hardware monitor"),
            pollingIntervalMs = initialCadence.intervalMs,
            activeOpenAlert = null
        )
    )
    val hardwareSnapshot: StateFlow<CombinedHardwareSnapshot> = _hardwareSnapshot.asStateFlow()

    private val _prolongedOpenAlerts = MutableSharedFlow<ProlongedOpenAlert>(extraBufferCapacity = 16)
    val prolongedOpenAlerts: SharedFlow<ProlongedOpenAlert> = _prolongedOpenAlerts.asSharedFlow()

    /**
     * Starts continuous background hardware monitoring.
     */
    fun startMonitoring() {
        if (pollingJob?.isActive == true) return
        pollingJob = monitorScope.launch {
            var consecutiveErrorPolls = 0
            var tickCount = 0L

            while (isActive) {
                val rawReading = serviceAdapter.readDrawerPhysicalStatus()
                val nowMono = SystemClock.elapsedRealtime()

                val effectiveReading = if (rawReading.state == DrawerPhysicalState.OPEN ||
                    rawReading.state == DrawerPhysicalState.CLOSED
                ) {
                    consecutiveErrorPolls = 0
                    lastValidReadingMonotonicMs = nowMono
                    applyTwoSampleDebounce(rawReading)
                } else {
                    consecutiveErrorPolls++
                    if (lastValidReadingMonotonicMs > 0L &&
                        (nowMono - lastValidReadingMonotonicMs) > STALE_READING_TTL_MS &&
                        rawReading.state != DrawerPhysicalState.OFFLINE
                    ) {
                        rawReading.copy(
                            state = DrawerPhysicalState.STALE,
                            errorDetail = "Sensor reading exceeded freshness TTL (${STALE_READING_TTL_MS}ms)"
                        )
                    } else {
                        rawReading
                    }
                }

                // Forward debounced reading to CashDrawerManager state machine
                cashDrawerManager.onObservedReading(effectiveReading)

                // Track prolonged open alerts
                val activeAlert = evaluateProlongedOpenAlert(effectiveReading.state, nowMono)

                // Interrogate printer status every 4th drawer poll to minimize IPC overhead
                val printerReport = if (tickCount % 4L == 0L) {
                    printerManager.checkPrinterAvailability()
                } else {
                    _hardwareSnapshot.value.printerStatus
                }
                tickCount++

                val baseDelay = currentCadence.intervalMs
                val backoffDelay = if (consecutiveErrorPolls > 0) {
                    min(5_000L, baseDelay * (1L shl min(consecutiveErrorPolls, 4)))
                } else {
                    baseDelay
                }

                _hardwareSnapshot.value = CombinedHardwareSnapshot(
                    drawerReading = effectiveReading,
                    printerStatus = printerReport,
                    pollingIntervalMs = backoffDelay,
                    activeOpenAlert = activeAlert
                )

                delay(backoffDelay)
            }
        }
    }

    /**
     * Updates polling cadence dynamically without restarting the service.
     */
    fun setPollingCadence(cadence: PollingCadence) {
        currentCadence = cadence
    }

    /**
     * Applies a 2-consecutive-sample debounce filter on physical OPEN/CLOSED signals
     * to reject transient electrical bounce during latch movement.
     */
    private fun applyTwoSampleDebounce(reading: DrawerStatusReading): DrawerStatusReading {
        val observed = reading.state
        if (confirmedState == DrawerPhysicalState.UNKNOWN) {
            confirmedState = observed
            candidateState = observed
            candidateSampleCount = DEBOUNCE_REQUIRED_SAMPLES
            return reading
        }

        if (observed == confirmedState) {
            candidateState = observed
            candidateSampleCount = DEBOUNCE_REQUIRED_SAMPLES
            return reading
        }

        if (observed == candidateState) {
            candidateSampleCount++
        } else {
            candidateState = observed
            candidateSampleCount = 1
        }

        return if (candidateSampleCount >= DEBOUNCE_REQUIRED_SAMPLES) {
            confirmedState = observed
            reading
        } else {
            // Hold previous confirmed state until 2 consecutive samples agree
            reading.copy(state = confirmedState)
        }
    }

    private fun evaluateProlongedOpenAlert(
        state: DrawerPhysicalState,
        nowMono: Long
    ): ProlongedOpenAlert? {
        if (state != DrawerPhysicalState.OPEN) {
            openStartMonotonicMs = null
            lastEmittedAlertSeverity = null
            return null
        }

        val startMono = openStartMonotonicMs ?: nowMono.also { openStartMonotonicMs = it }
        val openSeconds = (nowMono - startMono) / 1000L

        val matchedSeverity = OpenDrawerAlertSeverity.entries
            .filter { openSeconds >= it.thresholdSeconds }
            .maxByOrNull { it.thresholdSeconds }
            ?: return null

        val alert = ProlongedOpenAlert(
            severity = matchedSeverity,
            openDurationSeconds = openSeconds,
            detectedAtEpochMs = System.currentTimeMillis()
        )

        if (lastEmittedAlertSeverity != matchedSeverity) {
            lastEmittedAlertSeverity = matchedSeverity
            _prolongedOpenAlerts.tryEmit(alert)
        }

        return alert
    }

    /**
     * Runs a hardware polarity calibration check comparing raw sensor boolean when closed vs open.
     */
    suspend fun calibrateSwitchPolarity(
        closedSampleRaw: Boolean,
        openSampleRaw: Boolean
    ): CalibrationResult {
        if (closedSampleRaw == openSampleRaw) {
            return CalibrationResult(
                success = false,
                detectedPolarity = serviceAdapter.getSwitchPolarity(),
                closedRawSignal = closedSampleRaw,
                openRawSignal = openSampleRaw,
                diagnosticMessage = "Sensor returned identical signal ($closedSampleRaw) in both CLOSED and OPEN positions. Drawer microswitch may be unsupported or disconnected."
            )
        }

        val detected = if (openSampleRaw && !closedSampleRaw) {
            SwitchPolarity.ACTIVE_HIGH_OPEN
        } else {
            SwitchPolarity.ACTIVE_LOW_OPEN
        }
        serviceAdapter.updateSwitchPolarity(detected)

        return CalibrationResult(
            success = true,
            detectedPolarity = detected,
            closedRawSignal = closedSampleRaw,
            openRawSignal = openSampleRaw,
            diagnosticMessage = "Calibrated switch polarity to $detected (closed=$closedSampleRaw, open=$openSampleRaw)"
        )
    }

    fun stopMonitoring() {
        pollingJob?.cancel()
        pollingJob = null
    }

    fun shutdown() {
        stopMonitoring()
        monitorScope.cancel()
    }
}
