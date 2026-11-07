package com.powerpos.hardware.sunmi

import kotlinx.coroutines.flow.Flow

/**
 * Normalized physical cash drawer states used across the entire PowerPOS platform.
 * Strict separation is maintained between electrical pulse dispatch, raw microswitch signal,
 * and normalized state.
 */
enum class DrawerPhysicalState {
    /** Physical microswitch confirms the cash drawer tray is open. */
    OPEN,

    /** Physical microswitch confirms the cash drawer tray is closed. */
    CLOSED,

    /** Initial state before first valid sensor read or when sensor polarity is uncalibrated. */
    UNKNOWN,

    /** Terminal model or connected drawer hardware does not support drawer kick or status feedback. */
    UNSUPPORTED,

    /** Last sensor reading exceeded the freshness TTL without a new valid sample. */
    STALE,

    /** AIDL RemoteException, SecurityException, or hardware fault occurred during interrogation. */
    ERROR,

    /** Woyou AIDL service is unbound, crashed, or unreachable. */
    OFFLINE
}

/**
 * Capability classification for hardware features on a specific SUNMI terminal model.
 */
enum class CapabilityStatus {
    /** Confirmed supported by terminal hardware and verified at runtime. */
    SUPPORTED,

    /** Known unsupported on this terminal model (e.g., handheld V2 series without RJ11 port). */
    UNSUPPORTED,

    /** Potentially supported via dock/cradle, requires runtime verification. */
    UNVERIFIED,

    /** Hardware interrogation failed due to service error. */
    ERROR
}

/**
 * Calibration profile for interpreting `getDrawerStatus()` boolean polarity on specific hardware.
 */
enum class SwitchPolarity {
    /** `getDrawerStatus() == true` means physical drawer is OPEN. (Standard on most SUNMI desktop terminals) */
    ACTIVE_HIGH_OPEN,

    /** `getDrawerStatus() == false` means physical drawer is OPEN. (Inverted microswitch wiring) */
    ACTIVE_LOW_OPEN
}

/**
 * Detailed hardware capability report for the bound SUNMI terminal.
 */
data class HardwareCapabilities(
    val deviceModel: String,
    val serialNumber: String,
    val firmwareVersion: String,
    val serviceVersion: String,
    val printerCapability: CapabilityStatus,
    val cutterCapability: CapabilityStatus,
    val drawerPulseCapability: CapabilityStatus,
    val drawerSensorCapability: CapabilityStatus,
    val switchPolarity: SwitchPolarity = SwitchPolarity.ACTIVE_HIGH_OPEN
)

/**
 * Real-time health snapshot of the hardware subsystem.
 */
data class HardwareHealth(
    val serviceConnected: Boolean,
    val printerStatusCode: Int,
    val printerStatusSummary: String,
    val drawerState: DrawerPhysicalState,
    val cumulativeOpenPulses: Int,
    val lastPollTimestampMs: Long,
    val consecutiveFailures: Int,
    val lastErrorMessage: String? = null
)

/**
 * Result of a cash drawer pulse command dispatch.
 * Note: `pulseDispatched = true` NEVER implies the drawer physically opened;
 * physical opening must be verified via `DrawerPhysicalState.OPEN` transition.
 */
data class DrawerPulseResult(
    val pulseDispatched: Boolean,
    val timestampMs: Long,
    val authTokenHash: String,
    val reason: String,
    val errorCode: String? = null,
    val errorMessage: String? = null
)

/**
 * Snapshot of a single cash drawer status observation.
 */
data class DrawerStatusReading(
    val state: DrawerPhysicalState,
    val rawSignal: Boolean?,
    val observedAtEpochMs: Long,
    val monotonicElapsedRealtimeMs: Long,
    val errorDetail: String? = null
)

/**
 * Hardware abstraction contract for cash drawer operations.
 */
interface CashDrawerDriver {
    /** Binds to underlying hardware service and discovers terminal capabilities. */
    suspend fun initialize(): Result<HardwareCapabilities>

    /** Returns discovered capabilities for the current SUNMI terminal. */
    suspend fun getCapabilities(): HardwareCapabilities

    /** Performs an immediate hardware sensor read and returns normalized status. */
    suspend fun getCurrentStatus(): DrawerStatusReading

    /**
     * Dispatches an electrical pulse to open the drawer after validating authorization parameters.
     */
    suspend fun requestOpen(authToken: String, reason: String): DrawerPulseResult

    /** Subscribes to continuous, debounced physical state changes. */
    fun subscribeStatusChanges(): Flow<DrawerStatusReading>

    /** Returns current health of the service connection, printer, and drawer port. */
    suspend fun getHealth(): HardwareHealth

    /** Cleanly unbinds service connections and stops background coroutines. */
    fun disconnect()
}
