package com.powerpos.android.pos

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.powerpos.android.hardware.cashdrawer.CashDrawerManager
import com.powerpos.android.hardware.cashdrawer.CashDrawerTransitionEvent
import com.powerpos.android.hardware.cashdrawer.DrawerAuthorizationGrant
import com.powerpos.android.hardware.printer.ReceiptPrintPayload
import com.powerpos.android.hardware.printer.SunmiPrinterManager
import com.powerpos.android.monitoring.CombinedHardwareSnapshot
import com.powerpos.android.monitoring.HardwareMonitorService
import com.powerpos.android.monitoring.PollingCadence
import com.powerpos.android.network.PowerPosSyncClient
import com.powerpos.android.persistence.HardwareEventDao
import com.powerpos.android.persistence.OfflineAuthRecordEntity
import com.powerpos.android.persistence.OfflineDrawerEventEntity
import com.powerpos.android.persistence.SyncDeliveryStatus
import com.powerpos.hardware.sunmi.DrawerPhysicalState
import com.powerpos.hardware.sunmi.DrawerPulseResult
import com.powerpos.hardware.sunmi.HardwareCapabilities
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

data class PosTerminalUiState(
    val terminalId: String = "SUNMI-TERM-01",
    val capabilities: HardwareCapabilities? = null,
    val drawerState: DrawerPhysicalState = DrawerPhysicalState.UNKNOWN,
    val printerReady: Boolean = false,
    val printerStatusText: String = "Initializing...",
    val pendingOfflineEventsCount: Int = 0,
    val lastPulseResultMessage: String? = null,
    val activeSecurityAlert: String? = null
)

/**
 * MVVM ViewModel coordinating SUNMI hardware managers, Room offline persistence,
 * and background Odoo synchronization.
 */
class PosHardwareViewModel(
    private val terminalId: String,
    private val cashDrawerManager: CashDrawerManager,
    private val printerManager: SunmiPrinterManager,
    private val monitorService: HardwareMonitorService,
    private val eventDao: HardwareEventDao,
    private val syncClient: PowerPosSyncClient
) : ViewModel() {

    private val _uiState = MutableStateFlow(PosTerminalUiState(terminalId = terminalId))
    val uiState: StateFlow<PosTerminalUiState> = _uiState.asStateFlow()

    val hardwareSnapshot: StateFlow<CombinedHardwareSnapshot> = monitorService.hardwareSnapshot

    val recentAuditEvents: StateFlow<List<OfflineDrawerEventEntity>> =
        eventDao.observeRecentDrawerEvents(50)
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    init {
        initializeHardwareAndObserve()
    }

    private fun initializeHardwareAndObserve() {
        viewModelScope.launch {
            val capsResult = cashDrawerManager.initialize()
            val caps = capsResult.getOrNull()
            _uiState.value = _uiState.value.copy(capabilities = caps)
            monitorService.startMonitoring()
        }

        // Observe continuous hardware snapshot
        viewModelScope.launch {
            monitorService.hardwareSnapshot.collect { snapshot ->
                val alertMsg = snapshot.activeOpenAlert?.let {
                    "[${it.severity.name}] Cash drawer open for ${it.openDurationSeconds}s"
                }
                _uiState.value = _uiState.value.copy(
                    drawerState = snapshot.drawerReading.state,
                    printerReady = snapshot.printerStatus.isReady,
                    printerStatusText = "${snapshot.printerStatus.statusName}: ${snapshot.printerStatus.humanReadableMessage}",
                    activeSecurityAlert = alertMsg
                )
            }
        }

        // Persist every physical cash drawer transition to Room and trigger sync
        viewModelScope.launch {
            cashDrawerManager.transitionEvents.collect { transition ->
                persistTransitionEvent(transition)
                syncClient.syncPendingDrawerEvents()
            }
        }

        // Observe pending sync queue depth
        viewModelScope.launch {
            eventDao.observePendingSyncCount().collect { count ->
                _uiState.value = _uiState.value.copy(pendingOfflineEventsCount = count)
            }
        }
    }

    /**
     * Executes a server-authorized cash drawer opening, records the authorization grant in Room,
     * and dispatches the hardware solenoid pulse.
     */
    suspend fun executeAuthorizedDrawerOpen(grant: DrawerAuthorizationGrant): DrawerPulseResult {
        val pulseResult = cashDrawerManager.requestAuthorizedDrawerOpening(grant)

        eventDao.upsertAuthRecord(
            OfflineAuthRecordEntity(
                authorizationId = grant.authorizationId,
                idempotencyKey = grant.idempotencyKey,
                operatorId = grant.operatorId,
                posOrderId = grant.posOrderId,
                reason = grant.reason,
                authTokenHash = pulseResult.authTokenHash,
                issuedAtEpochMs = grant.issuedAtEpochMs,
                expiresAtEpochMs = grant.expiresAtEpochMs,
                pulseDispatched = pulseResult.pulseDispatched,
                dispatchedAtEpochMs = if (pulseResult.pulseDispatched) pulseResult.timestampMs else null
            )
        )

        val statusSummary = if (pulseResult.pulseDispatched) {
            "Pulse dispatched for Auth ${grant.authorizationId} (awaiting physical sensor OPEN)"
        } else {
            "Pulse rejected [${pulseResult.errorCode}]: ${pulseResult.errorMessage}"
        }
        _uiState.value = _uiState.value.copy(lastPulseResultMessage = statusSummary)
        return pulseResult
    }

    /**
     * Prints a BIR-compliant receipt and reports status to Odoo.
     */
    suspend fun printPosReceipt(payload: ReceiptPrintPayload): Result<Unit> {
        val result = printerManager.printReceipt(payload)
        val printerReport = printerManager.checkPrinterAvailability()
        viewModelScope.launch {
            syncClient.reportPrintJobStatus(
                jobId = payload.jobId,
                officialReceiptNumber = payload.birCompliance.officialReceiptNumber,
                status = if (result.isSuccess) "PRINTED" else "FAILED",
                printerStatusCode = printerReport.statusCode
            )
        }
        return result
    }

    fun updatePollingCadence(cadence: PollingCadence) {
        monitorService.setPollingCadence(cadence)
    }

    fun triggerManualSync() {
        viewModelScope.launch {
            syncClient.syncPendingDrawerEvents()
        }
    }

    private suspend fun persistTransitionEvent(event: CashDrawerTransitionEvent) {
        val entity = OfflineDrawerEventEntity(
            eventUuid = event.eventUuid,
            terminalId = terminalId,
            transitionType = event.transitionType.name,
            previousState = event.previousState.name,
            newState = event.newState.name,
            correlationClass = event.correlationClass.name,
            matchedAuthorizationId = event.matchedAuthorizationId,
            matchedIdempotencyKey = event.matchedIdempotencyKey,
            reason = event.reason,
            occurredAtEpochMs = event.occurredAtEpochMs,
            openDurationMs = event.openDurationMs,
            durationIncomplete = event.durationIncomplete,
            syncStatus = SyncDeliveryStatus.PENDING
        )
        eventDao.insertDrawerEvent(entity)
    }

    override fun onCleared() {
        super.onCleared()
        monitorService.shutdown()
        cashDrawerManager.disconnect()
    }
}
