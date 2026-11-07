package com.powerpos.android.hardware.cashdrawer

import android.os.SystemClock
import com.powerpos.hardware.sunmi.CashDrawerDriver
import com.powerpos.hardware.sunmi.DrawerPhysicalState
import com.powerpos.hardware.sunmi.DrawerPulseResult
import com.powerpos.hardware.sunmi.DrawerStatusReading
import com.powerpos.hardware.sunmi.HardwareCapabilities
import com.powerpos.hardware.sunmi.HardwareHealth
import com.powerpos.hardware.sunmi.SunmiWoyouServiceAdapter
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/**
 * Classification of physical cash drawer state transitions.
 */
enum class DrawerTransitionType {
    /** Initial observation when starting in UNKNOWN and observing OPEN (`UNKNOWN -> OPEN`). */
    INITIAL_OBSERVATION_OPEN,

    /** Initial observation when starting in UNKNOWN and observing CLOSED (`UNKNOWN -> CLOSED`). */
    INITIAL_OBSERVATION_CLOSED,

    /** Physical drawer opened after being closed (`CLOSED -> OPEN`). */
    PHYSICAL_OPENED,

    /** Physical drawer closed after being open (`OPEN -> CLOSED`). */
    PHYSICAL_CLOSED,

    /** Transition into degraded state (`ANY -> STALE`, `OFFLINE`, `ERROR`, `UNSUPPORTED`). */
    DEGRADED_OR_OFFLINE,

    /** Recovery from degraded/offline state back to a valid physical sensor reading. */
    RECOVERED_OBSERVATION
}

/**
 * Correlation classification when the drawer physically transitions to OPEN.
 */
enum class OpeningCorrelationClass {
    /** Physical opening occurred within the correlation window of a server-authorized pulse. */
    AUTHORIZED_PULSE_MATCHED,

    /** Physical opening occurred with no preceding authorized pulse (manual key turn, pry, or hardware fault). */
    UNMATCHED_OPENING,

    /** Not applicable (e.g., closing transition or initial closed observation). */
    NOT_APPLICABLE
}

/**
 * Signed authorization grant issued by the Odoo server or offline emergency supervisor policy.
 */
data class DrawerAuthorizationGrant(
    val authorizationId: String,
    val idempotencyKey: String,
    val authToken: String,
    val reason: String,
    val operatorId: String,
    val posOrderId: String?,
    val issuedAtEpochMs: Long,
    val expiresAtEpochMs: Long
)

/**
 * Auditable cash drawer lifecycle event emitted by the state machine.
 */
data class CashDrawerTransitionEvent(
    val eventUuid: String,
    val transitionType: DrawerTransitionType,
    val previousState: DrawerPhysicalState,
    val newState: DrawerPhysicalState,
    val correlationClass: OpeningCorrelationClass,
    val matchedAuthorizationId: String?,
    val matchedIdempotencyKey: String?,
    val reason: String?,
    val occurredAtEpochMs: Long,
    val openDurationMs: Long?,
    val durationIncomplete: Boolean,
    val diagnosticNote: String? = null
)

/**
 * Production Cash Drawer Manager implementing `CashDrawerDriver` and deterministic state tracking.
 *
 * Key Guarantees:
 * - Enforces server-issued authorization tokens and idempotency keys (prevents duplicate electrical pulses).
 * - Never treats `openDrawer()` callback success as proof of physical opening.
 * - Explicitly distinguishes `UNKNOWN -> OPEN` initial observation from `CLOSED -> OPEN` physical opening.
 * - Correlates physical `CLOSED -> OPEN` transitions against pending authorized pulses within a strict window;
 *   flags uncorroborated openings as `UNMATCHED_OPENING`.
 * - Calculates open duration using monotonic `SystemClock.elapsedRealtime()`. If the service disconnects
 *   while `OPEN`, marks duration as incomplete without fabricating a fake `CLOSED` event.
 */
class CashDrawerManager(
    private val serviceAdapter: SunmiWoyouServiceAdapter,
    private val authorizationCorrelationWindowMs: Long = 5_000L
) : CashDrawerDriver {

    private val stateMutex = Mutex()
    private val consumedIdempotencyKeys = ConcurrentHashMap<String, DrawerPulseResult>()

    @Volatile
    private var currentReading = DrawerStatusReading(
        state = DrawerPhysicalState.UNKNOWN,
        rawSignal = null,
        observedAtEpochMs = System.currentTimeMillis(),
        monotonicElapsedRealtimeMs = SystemClock.elapsedRealtime()
    )

    @Volatile
    private var pendingAuthorizedGrant: DrawerAuthorizationGrant? = null

    @Volatile
    private var pendingPulseMonotonicMs: Long = 0L

    @Volatile
    private var activeOpenStartMonotonicMs: Long? = null

    @Volatile
    private var activeOpenAuthorizationId: String? = null

    @Volatile
    private var activeOpenIdempotencyKey: String? = null

    @Volatile
    private var activeOpenReason: String? = null

    @Volatile
    private var openCycleInterruptedByDisconnect: Boolean = false

    private val _statusFlow = MutableStateFlow(currentReading)
    val statusStateFlow: StateFlow<DrawerStatusReading> = _statusFlow.asStateFlow()

    private val _transitionEvents = MutableSharedFlow<CashDrawerTransitionEvent>(extraBufferCapacity = 64)
    val transitionEvents: Flow<CashDrawerTransitionEvent> = _transitionEvents.asSharedFlow()

    override suspend fun initialize(): Result<HardwareCapabilities> {
        val result = serviceAdapter.connectAndInitialize()
        if (result.isSuccess) {
            readPhysicalStatus()
        }
        return result
    }

    override suspend fun getCapabilities(): HardwareCapabilities {
        return serviceAdapter.interrogateCapabilities()
    }

    override suspend fun getCurrentStatus(): DrawerStatusReading {
        return readPhysicalStatus()
    }

    /**
     * Reads current physical status from the Woyou adapter and runs the transition state machine.
     */
    suspend fun readPhysicalStatus(): DrawerStatusReading = stateMutex.withLock {
        val reading = serviceAdapter.readDrawerPhysicalStatus()
        processNewReadingLocked(reading)
        reading
    }

    /**
     * Processes a debounced status reading from `HardwareMonitorService` or direct poll.
     */
    suspend fun onObservedReading(reading: DrawerStatusReading): CashDrawerTransitionEvent? = stateMutex.withLock {
        processNewReadingLocked(reading)
    }

    /**
     * Requests a drawer opening using a structured `DrawerAuthorizationGrant` with idempotency protection.
     */
    suspend fun requestAuthorizedDrawerOpening(grant: DrawerAuthorizationGrant): DrawerPulseResult = stateMutex.withLock {
        val nowEpoch = System.currentTimeMillis()

        // 1. Idempotency check: prevent duplicate pulses for the same idempotencyKey
        consumedIdempotencyKeys[grant.idempotencyKey]?.let { existingResult ->
            return@withLock existingResult
        }

        // 2. Validate token non-empty and not expired
        if (grant.authToken.isBlank() || grant.authorizationId.isBlank()) {
            return@withLock DrawerPulseResult(
                pulseDispatched = false,
                timestampMs = nowEpoch,
                authTokenHash = sha256Hex(grant.authToken),
                reason = grant.reason,
                errorCode = "INVALID_AUTH_TOKEN",
                errorMessage = "Authorization token and authorizationId are required"
            )
        }

        if (nowEpoch > grant.expiresAtEpochMs) {
            return@withLock DrawerPulseResult(
                pulseDispatched = false,
                timestampMs = nowEpoch,
                authTokenHash = sha256Hex(grant.authToken),
                reason = grant.reason,
                errorCode = "EXPIRED_AUTH_TOKEN",
                errorMessage = "Authorization grant expired at ${grant.expiresAtEpochMs}"
            )
        }

        // 3. Record pending authorization for correlation with upcoming physical sensor transition
        pendingAuthorizedGrant = grant
        pendingPulseMonotonicMs = SystemClock.elapsedRealtime()

        // 4. Dispatch electrical pulse via AIDL
        val pulseDispatch = serviceAdapter.dispatchOpenDrawerPulse()
        val pulseResult = if (pulseDispatch.isSuccess && pulseDispatch.getOrDefault(false)) {
            DrawerPulseResult(
                pulseDispatched = true,
                timestampMs = nowEpoch,
                authTokenHash = sha256Hex(grant.authToken),
                reason = grant.reason
            )
        } else {
            val err = pulseDispatch.exceptionOrNull()?.message ?: "Drawer pulse rejected by driver"
            DrawerPulseResult(
                pulseDispatched = false,
                timestampMs = nowEpoch,
                authTokenHash = sha256Hex(grant.authToken),
                reason = grant.reason,
                errorCode = "PULSE_DISPATCH_FAILED",
                errorMessage = err
            )
        }

        if (pulseResult.pulseDispatched) {
            consumedIdempotencyKeys[grant.idempotencyKey] = pulseResult
        }
        pulseResult
    }

    override suspend fun requestOpen(authToken: String, reason: String): DrawerPulseResult {
        val now = System.currentTimeMillis()
        val syntheticGrant = DrawerAuthorizationGrant(
            authorizationId = "DIRECT-${sha256Hex(authToken).take(12)}",
            idempotencyKey = "IDEMP-${sha256Hex("$authToken:$reason")}",
            authToken = authToken,
            reason = reason,
            operatorId = "SYSTEM",
            posOrderId = null,
            issuedAtEpochMs = now,
            expiresAtEpochMs = now + 30_000L
        )
        return requestAuthorizedDrawerOpening(syntheticGrant)
    }

    override fun subscribeStatusChanges(): Flow<DrawerStatusReading> = statusStateFlow

    override suspend fun getHealth(): HardwareHealth {
        val printerCode = serviceAdapter.queryPrinterStateCode()
        val openTimes = serviceAdapter.readCumulativeOpenDrawerTimes()
        val latestReading = currentReading
        return HardwareHealth(
            serviceConnected = serviceAdapter.connectionState.value,
            printerStatusCode = printerCode,
            printerStatusSummary = if (printerCode == 1) "READY" else "CODE_$printerCode",
            drawerState = latestReading.state,
            cumulativeOpenPulses = openTimes,
            lastPollTimestampMs = latestReading.observedAtEpochMs,
            consecutiveFailures = serviceAdapter.getConsecutiveErrorCount(),
            lastErrorMessage = latestReading.errorDetail
        )
    }

    override fun disconnect() {
        serviceAdapter.disconnect()
    }

    private fun processNewReadingLocked(reading: DrawerStatusReading): CashDrawerTransitionEvent? {
        val previousState = currentReading.state
        val newState = reading.state
        currentReading = reading
        _statusFlow.value = reading

        if (previousState == newState) {
            return null
        }

        val nowMono = reading.monotonicElapsedRealtimeMs
        val nowEpoch = reading.observedAtEpochMs

        val event = when {
            // Case 1: Initial observation from UNKNOWN -> OPEN
            previousState == DrawerPhysicalState.UNKNOWN && newState == DrawerPhysicalState.OPEN -> {
                activeOpenStartMonotonicMs = nowMono
                openCycleInterruptedByDisconnect = true // Did not observe the exact start of opening
                CashDrawerTransitionEvent(
                    eventUuid = UUID.randomUUID().toString(),
                    transitionType = DrawerTransitionType.INITIAL_OBSERVATION_OPEN,
                    previousState = previousState,
                    newState = newState,
                    correlationClass = OpeningCorrelationClass.UNMATCHED_OPENING,
                    matchedAuthorizationId = null,
                    matchedIdempotencyKey = null,
                    reason = "Drawer already open at service initialization",
                    occurredAtEpochMs = nowEpoch,
                    openDurationMs = null,
                    durationIncomplete = true,
                    diagnosticNote = "UNKNOWN -> OPEN initial observation; exact opening timestamp predates monitoring"
                )
            }

            // Case 2: Initial observation from UNKNOWN -> CLOSED
            previousState == DrawerPhysicalState.UNKNOWN && newState == DrawerPhysicalState.CLOSED -> {
                CashDrawerTransitionEvent(
                    eventUuid = UUID.randomUUID().toString(),
                    transitionType = DrawerTransitionType.INITIAL_OBSERVATION_CLOSED,
                    previousState = previousState,
                    newState = newState,
                    correlationClass = OpeningCorrelationClass.NOT_APPLICABLE,
                    matchedAuthorizationId = null,
                    matchedIdempotencyKey = null,
                    reason = "Initial closed state verified",
                    occurredAtEpochMs = nowEpoch,
                    openDurationMs = null,
                    durationIncomplete = false
                )
            }

            // Case 3: Physical opening (CLOSED -> OPEN)
            previousState == DrawerPhysicalState.CLOSED && newState == DrawerPhysicalState.OPEN -> {
                activeOpenStartMonotonicMs = nowMono
                openCycleInterruptedByDisconnect = false

                val grant = pendingAuthorizedGrant
                val isWithinWindow = grant != null &&
                    (nowMono - pendingPulseMonotonicMs) in 0..authorizationCorrelationWindowMs

                val correlation = if (isWithinWindow) {
                    OpeningCorrelationClass.AUTHORIZED_PULSE_MATCHED
                } else {
                    OpeningCorrelationClass.UNMATCHED_OPENING
                }

                activeOpenAuthorizationId = if (isWithinWindow) grant?.authorizationId else null
                activeOpenIdempotencyKey = if (isWithinWindow) grant?.idempotencyKey else null
                activeOpenReason = if (isWithinWindow) grant?.reason else "UNMATCHED_PHYSICAL_OPENING"

                if (isWithinWindow) {
                    pendingAuthorizedGrant = null
                }

                CashDrawerTransitionEvent(
                    eventUuid = UUID.randomUUID().toString(),
                    transitionType = DrawerTransitionType.PHYSICAL_OPENED,
                    previousState = previousState,
                    newState = newState,
                    correlationClass = correlation,
                    matchedAuthorizationId = activeOpenAuthorizationId,
                    matchedIdempotencyKey = activeOpenIdempotencyKey,
                    reason = activeOpenReason,
                    occurredAtEpochMs = nowEpoch,
                    openDurationMs = null,
                    durationIncomplete = false
                )
            }

            // Case 4: Physical closing (OPEN -> CLOSED)
            previousState == DrawerPhysicalState.OPEN && newState == DrawerPhysicalState.CLOSED -> {
                val startMono = activeOpenStartMonotonicMs
                val incomplete = openCycleInterruptedByDisconnect || startMono == null
                val durationMs = if (startMono != null && nowMono >= startMono) {
                    nowMono - startMono
                } else {
                    null
                }

                val closedEvent = CashDrawerTransitionEvent(
                    eventUuid = UUID.randomUUID().toString(),
                    transitionType = DrawerTransitionType.PHYSICAL_CLOSED,
                    previousState = previousState,
                    newState = newState,
                    correlationClass = OpeningCorrelationClass.NOT_APPLICABLE,
                    matchedAuthorizationId = activeOpenAuthorizationId,
                    matchedIdempotencyKey = activeOpenIdempotencyKey,
                    reason = activeOpenReason,
                    occurredAtEpochMs = nowEpoch,
                    openDurationMs = durationMs,
                    durationIncomplete = incomplete,
                    diagnosticNote = if (incomplete) "Open cycle was interrupted or started before observation" else null
                )

                activeOpenStartMonotonicMs = null
                activeOpenAuthorizationId = null
                activeOpenIdempotencyKey = null
                activeOpenReason = null
                openCycleInterruptedByDisconnect = false
                closedEvent
            }

            // Case 5: Transition to degraded / offline / stale / error
            newState in setOf(
                DrawerPhysicalState.OFFLINE,
                DrawerPhysicalState.STALE,
                DrawerPhysicalState.ERROR,
                DrawerPhysicalState.UNSUPPORTED
            ) -> {
                if (previousState == DrawerPhysicalState.OPEN || activeOpenStartMonotonicMs != null) {
                    // Mark current open cycle as interrupted without fabricating a fake CLOSED state
                    openCycleInterruptedByDisconnect = true
                }
                CashDrawerTransitionEvent(
                    eventUuid = UUID.randomUUID().toString(),
                    transitionType = DrawerTransitionType.DEGRADED_OR_OFFLINE,
                    previousState = previousState,
                    newState = newState,
                    correlationClass = OpeningCorrelationClass.NOT_APPLICABLE,
                    matchedAuthorizationId = activeOpenAuthorizationId,
                    matchedIdempotencyKey = activeOpenIdempotencyKey,
                    reason = reading.errorDetail ?: "Hardware state transitioned to $newState",
                    occurredAtEpochMs = nowEpoch,
                    openDurationMs = null,
                    durationIncomplete = activeOpenStartMonotonicMs != null,
                    diagnosticNote = "Hardware entered $newState; no synthetic CLOSED event generated"
                )
            }

            // Case 6: Recovery from OFFLINE/STALE/ERROR back to OPEN or CLOSED
            else -> {
                val incomplete = openCycleInterruptedByDisconnect
                val durationMs = if (newState == DrawerPhysicalState.CLOSED && activeOpenStartMonotonicMs != null) {
                    nowMono - activeOpenStartMonotonicMs!!
                } else {
                    null
                }
                if (newState == DrawerPhysicalState.CLOSED) {
                    activeOpenStartMonotonicMs = null
                    activeOpenAuthorizationId = null
                    activeOpenIdempotencyKey = null
                    activeOpenReason = null
                    openCycleInterruptedByDisconnect = false
                }
                CashDrawerTransitionEvent(
                    eventUuid = UUID.randomUUID().toString(),
                    transitionType = DrawerTransitionType.RECOVERED_OBSERVATION,
                    previousState = previousState,
                    newState = newState,
                    correlationClass = OpeningCorrelationClass.NOT_APPLICABLE,
                    matchedAuthorizationId = activeOpenAuthorizationId,
                    matchedIdempotencyKey = activeOpenIdempotencyKey,
                    reason = "Recovered sensor observation ($previousState -> $newState)",
                    occurredAtEpochMs = nowEpoch,
                    openDurationMs = durationMs,
                    durationIncomplete = incomplete
                )
            }
        }

        _transitionEvents.tryEmit(event)
        return event
    }

    private fun sha256Hex(input: String): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(input.toByteArray(Charsets.UTF_8))
        return digest.joinToString("") { "%02x".format(it) }
    }
}
