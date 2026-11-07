package com.powerpos.hardware.sunmi

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.Build
import android.os.IBinder
import android.os.RemoteException
import android.os.SystemClock
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import woyou.aidlservice.jiuiv5.ICallback
import woyou.aidlservice.jiuiv5.IWoyouService
import java.util.concurrent.atomic.AtomicInteger
import kotlin.coroutines.resume
import kotlin.math.min

/**
 * Production-grade Android AIDL Service Adapter for SUNMI's built-in Woyou service
 * (`woyou.aidlservice.jiuiv5` / `com.sunmi.peripheral.printer`).
 *
 * Responsibilities:
 * - Lifecycle-safe ServiceConnection with IBinder.DeathRecipient (`binderDied`)
 * - Automatic exponential-backoff reconnection when service crashes or restarts
 * - Model-aware hardware capability classification (T1/T2/T2s/T3/D2/D2s/D3 vs V1/V2/P2 handhelds)
 * - Polarity-calibrated interpretation of `getDrawerStatus()`
 * - Strict separation between `openDrawer()` pulse dispatch and physical drawer state
 */
class SunmiWoyouServiceAdapter(
    private val appContext: Context,
    private var switchPolarity: SwitchPolarity = SwitchPolarity.ACTIVE_HIGH_OPEN,
    private val ioScope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
) {
    companion object {
        const val WOYOU_PACKAGE = "woyou.aidlservice.jiuiv5"
        const val WOYOU_ACTION = "woyou.aidlservice.jiuiv5.IWoyouService"
        private const val BIND_TIMEOUT_MS = 6_000L
        private const val CALLBACK_TIMEOUT_MS = 4_000L
        private const val BASE_RECONNECT_DELAY_MS = 500L
        private const val MAX_RECONNECT_DELAY_MS = 15_000L

        /** Desktop POS terminals with dedicated RJ11/RJ12 cash drawer ports and microswitch status lines. */
        private val DESKTOP_DRAWER_MODELS = listOf("T1", "T2", "T2S", "T2MINI", "T3", "D1", "D2", "D2S", "D3", "K2")

        /** Handheld POS terminals without built-in cash drawer ports. */
        private val HANDHELD_MODELS = listOf("V1", "V1S", "V2", "V2PRO", "V2_PRO", "V2S", "P1", "P2", "P2PRO", "L2", "M2")
    }

    @Volatile
    private var woyouService: IWoyouService? = null

    @Volatile
    private var activeBinder: IBinder? = null

    private val bindMutex = Mutex()
    private var reconnectJob: Job? = null
    private val consecutiveErrors = AtomicInteger(0)

    private val _connectionState = MutableStateFlow(false)
    val connectionState: StateFlow<Boolean> = _connectionState.asStateFlow()

    @Volatile
    private var cachedCapabilities: HardwareCapabilities? = null

    private val deathRecipient = object : IBinder.DeathRecipient {
        override fun binderDied() {
            handleServiceDisconnect(triggerReconnect = true)
        }
    }

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, service: IBinder?) {
            if (service == null) {
                handleServiceDisconnect(triggerReconnect = true)
                return
            }
            try {
                service.linkToDeath(deathRecipient, 0)
            } catch (_: RemoteException) {
                handleServiceDisconnect(triggerReconnect = true)
                return
            }
            activeBinder = service
            woyouService = IWoyouService.Stub.asInterface(service)
            consecutiveErrors.set(0)
            _connectionState.value = true
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            handleServiceDisconnect(triggerReconnect = true)
        }
    }

    private fun handleServiceDisconnect(triggerReconnect: Boolean) {
        try {
            activeBinder?.unlinkToDeath(deathRecipient, 0)
        } catch (_: NoSuchElementException) {
            // Already unlinked
        }
        activeBinder = null
        woyouService = null
        _connectionState.value = false

        if (triggerReconnect) {
            scheduleReconnect()
        }
    }

    private fun scheduleReconnect() {
        if (reconnectJob?.isActive == true) return
        reconnectJob = ioScope.launch {
            var attempt = 0
            while (!_connectionState.value) {
                val delayMs = min(MAX_RECONNECT_DELAY_MS, BASE_RECONNECT_DELAY_MS * (1L shl min(attempt, 5)))
                delay(delayMs)
                attempt++
                bindServiceInternal()
            }
        }
    }

    /**
     * Binds to the SUNMI Woyou AIDL service and interrogates device capabilities.
     */
    suspend fun connectAndInitialize(): Result<HardwareCapabilities> = bindMutex.withLock {
        withContext(Dispatchers.IO) {
            if (!_connectionState.value || woyouService == null) {
                val bound = bindServiceInternal()
                if (!bound) {
                    val offlineCaps = buildOfflineCapabilities("Service bind failed or timed out")
                    cachedCapabilities = offlineCaps
                    return@withContext Result.failure(IllegalStateException("Unable to bind SUNMI Woyou AIDL service"))
                }
            }
            val caps = interrogateCapabilities()
            cachedCapabilities = caps
            Result.success(caps)
        }
    }

    private suspend fun bindServiceInternal(): Boolean = withContext(Dispatchers.IO) {
        val intent = Intent().apply {
            setPackage(WOYOU_PACKAGE)
            action = WOYOU_ACTION
        }
        try {
            val initiated = appContext.bindService(intent, serviceConnection, Context.BIND_AUTO_CREATE)
            if (!initiated) return@withContext false

            val connected = withTimeoutOrNull(BIND_TIMEOUT_MS) {
                while (!_connectionState.value || woyouService == null) {
                    delay(50)
                }
                true
            } ?: false
            connected
        } catch (_: SecurityException) {
            consecutiveErrors.incrementAndGet()
            false
        } catch (_: Exception) {
            consecutiveErrors.incrementAndGet()
            false
        }
    }

    /**
     * Updates the switch polarity calibration dynamically (e.g., after interactive terminal calibration).
     */
    fun updateSwitchPolarity(polarity: SwitchPolarity) {
        this.switchPolarity = polarity
        cachedCapabilities = cachedCapabilities?.copy(switchPolarity = polarity)
    }

    fun getSwitchPolarity(): SwitchPolarity = switchPolarity

    /**
     * Interrogates the SUNMI terminal model, serial number, firmware version, and classifies
     * whether cash drawer kick & sensor feedback are supported.
     */
    suspend fun interrogateCapabilities(): HardwareCapabilities = withContext(Dispatchers.IO) {
        val service = woyouService ?: return@withContext buildOfflineCapabilities("Service not connected")
        try {
            val rawModel = (service.printerModal?.trim()?.takeIf { it.isNotEmpty() } ?: Build.MODEL ?: "UNKNOWN").uppercase()
            val serial = service.printerSerialNo?.trim().orEmpty().ifEmpty { "UNKNOWN_SERIAL" }
            val fwVersion = service.printerVersion?.trim().orEmpty().ifEmpty { "UNKNOWN_FW" }
            val srvVersion = service.serviceVersion?.trim().orEmpty().ifEmpty { "UNKNOWN_SRV" }
            val printerState = service.updatePrinterState()

            val printerCap = if (printerState == 505) CapabilityStatus.UNSUPPORTED else CapabilityStatus.SUPPORTED
            val isHandheld = HANDHELD_MODELS.any { rawModel.contains(it) }
            val isDesktop = DESKTOP_DRAWER_MODELS.any { rawModel.contains(it) }

            val cutterCap = when {
                isHandheld -> CapabilityStatus.UNSUPPORTED
                isDesktop -> CapabilityStatus.SUPPORTED
                else -> CapabilityStatus.UNVERIFIED
            }

            val drawerPulseCap = when {
                isHandheld -> CapabilityStatus.UNSUPPORTED
                isDesktop -> CapabilityStatus.SUPPORTED
                else -> CapabilityStatus.UNVERIFIED
            }

            val drawerSensorCap = when {
                isHandheld -> CapabilityStatus.UNSUPPORTED
                isDesktop -> CapabilityStatus.SUPPORTED
                else -> CapabilityStatus.UNVERIFIED
            }

            HardwareCapabilities(
                deviceModel = rawModel,
                serialNumber = serial,
                firmwareVersion = fwVersion,
                serviceVersion = srvVersion,
                printerCapability = printerCap,
                cutterCapability = cutterCap,
                drawerPulseCapability = drawerPulseCap,
                drawerSensorCapability = drawerSensorCap,
                switchPolarity = switchPolarity
            )
        } catch (e: RemoteException) {
            consecutiveErrors.incrementAndGet()
            buildOfflineCapabilities("RemoteException: ${e.message}")
        } catch (e: SecurityException) {
            consecutiveErrors.incrementAndGet()
            buildOfflineCapabilities("SecurityException: ${e.message}")
        }
    }

    /**
     * Reads the physical cash drawer microswitch state via `woyouService.getDrawerStatus()`.
     * Never infers physical state from `getOpenDrawerTimes()` or `openDrawer()` callback.
     */
    suspend fun readDrawerPhysicalStatus(): DrawerStatusReading = withContext(Dispatchers.IO) {
        val nowEpoch = System.currentTimeMillis()
        val nowMono = SystemClock.elapsedRealtime()
        val service = woyouService
        if (service == null || !_connectionState.value) {
            return@withContext DrawerStatusReading(
                state = DrawerPhysicalState.OFFLINE,
                rawSignal = null,
                observedAtEpochMs = nowEpoch,
                monotonicElapsedRealtimeMs = nowMono,
                errorDetail = "Woyou AIDL service is offline"
            )
        }

        val caps = cachedCapabilities ?: interrogateCapabilities().also { cachedCapabilities = it }
        if (caps.drawerSensorCapability == CapabilityStatus.UNSUPPORTED) {
            return@withContext DrawerStatusReading(
                state = DrawerPhysicalState.UNSUPPORTED,
                rawSignal = null,
                observedAtEpochMs = nowEpoch,
                monotonicElapsedRealtimeMs = nowMono,
                errorDetail = "Model ${caps.deviceModel} does not support cash drawer status sensor"
            )
        }

        try {
            val rawSignal = service.drawerStatus
            consecutiveErrors.set(0)
            val normalizedState = when (switchPolarity) {
                SwitchPolarity.ACTIVE_HIGH_OPEN -> if (rawSignal) DrawerPhysicalState.OPEN else DrawerPhysicalState.CLOSED
                SwitchPolarity.ACTIVE_LOW_OPEN -> if (!rawSignal) DrawerPhysicalState.OPEN else DrawerPhysicalState.CLOSED
            }
            DrawerStatusReading(
                state = normalizedState,
                rawSignal = rawSignal,
                observedAtEpochMs = nowEpoch,
                monotonicElapsedRealtimeMs = nowMono
            )
        } catch (e: RemoteException) {
            consecutiveErrors.incrementAndGet()
            handleServiceDisconnect(triggerReconnect = true)
            DrawerStatusReading(
                state = DrawerPhysicalState.OFFLINE,
                rawSignal = null,
                observedAtEpochMs = nowEpoch,
                monotonicElapsedRealtimeMs = nowMono,
                errorDetail = "RemoteException reading drawer status: ${e.message}"
            )
        } catch (e: SecurityException) {
            consecutiveErrors.incrementAndGet()
            DrawerStatusReading(
                state = DrawerPhysicalState.ERROR,
                rawSignal = null,
                observedAtEpochMs = nowEpoch,
                monotonicElapsedRealtimeMs = nowMono,
                errorDetail = "Permission denied reading drawer status: ${e.message}"
            )
        } catch (e: Exception) {
            consecutiveErrors.incrementAndGet()
            DrawerStatusReading(
                state = DrawerPhysicalState.ERROR,
                rawSignal = null,
                observedAtEpochMs = nowEpoch,
                monotonicElapsedRealtimeMs = nowMono,
                errorDetail = "Unexpected error reading drawer status: ${e.message}"
            )
        }
    }

    /**
     * Dispatches the electrical solenoid pulse to open the cash drawer via `woyouService.openDrawer(callback)`.
     * Note: Callback `onRunResult(true)` only confirms the electrical pulse was queued by the driver.
     */
    suspend fun dispatchOpenDrawerPulse(): Result<Boolean> = withContext(Dispatchers.IO) {
        val service = woyouService
            ?: return@withContext Result.failure(IllegalStateException("Woyou AIDL service is offline"))

        val caps = cachedCapabilities ?: interrogateCapabilities().also { cachedCapabilities = it }
        if (caps.drawerPulseCapability == CapabilityStatus.UNSUPPORTED) {
            return@withContext Result.failure(
                UnsupportedOperationException("Cash drawer kick is unsupported on model ${caps.deviceModel}")
            )
        }

        try {
            val callbackResult = withTimeoutOrNull(CALLBACK_TIMEOUT_MS) {
                suspendCancellableCoroutine<Result<Boolean>> { cont ->
                    val binderCallback = object : ICallback.Stub() {
                        override fun onRunResult(isSuccess: Boolean) {
                            if (cont.isActive) cont.resume(Result.success(isSuccess))
                        }

                        override fun onReturnString(result: String?) {}

                        override fun onRaiseException(code: Int, msg: String?) {
                            if (cont.isActive) {
                                cont.resume(Result.failure(RuntimeException("AIDL exception ($code): $msg")))
                            }
                        }

                        override fun onPrintResult(code: Int, msg: String?) {}
                    }
                    service.openDrawer(binderCallback)
                }
            }
            callbackResult ?: Result.failure(RuntimeException("Timed out waiting for openDrawer AIDL callback"))
        } catch (e: RemoteException) {
            consecutiveErrors.incrementAndGet()
            handleServiceDisconnect(triggerReconnect = true)
            Result.failure(e)
        } catch (e: SecurityException) {
            consecutiveErrors.incrementAndGet()
            Result.failure(e)
        } catch (e: Exception) {
            consecutiveErrors.incrementAndGet()
            Result.failure(e)
        }
    }

    /**
     * Reads the cumulative counter of drawer open pulses since terminal boot (`getOpenDrawerTimes()`).
     * Used strictly for telemetry/diagnostics, NEVER as proof of current physical state.
     */
    suspend fun readCumulativeOpenDrawerTimes(): Int = withContext(Dispatchers.IO) {
        val service = woyouService ?: return@withContext -1
        try {
            service.openDrawerTimes
        } catch (_: Exception) {
            -1
        }
    }

    /**
     * Queries `updatePrinterState()` on `Dispatchers.IO`.
     */
    suspend fun queryPrinterStateCode(): Int = withContext(Dispatchers.IO) {
        val service = woyouService ?: return@withContext -1
        try {
            service.updatePrinterState()
        } catch (e: RemoteException) {
            consecutiveErrors.incrementAndGet()
            handleServiceDisconnect(triggerReconnect = true)
            -1
        } catch (_: Exception) {
            consecutiveErrors.incrementAndGet()
            -1
        }
    }

    /**
     * Executes a raw or structured print operation against `IWoyouService` with callback suspension.
     */
    suspend fun executePrintOperation(block: (IWoyouService, ICallback) -> Unit): Result<Boolean> =
        withContext(Dispatchers.IO) {
            val service = woyouService
                ?: return@withContext Result.failure(IllegalStateException("Woyou printer service is offline"))
            try {
                val res = withTimeoutOrNull(CALLBACK_TIMEOUT_MS) {
                    suspendCancellableCoroutine<Result<Boolean>> { cont ->
                        val callback = object : ICallback.Stub() {
                            override fun onRunResult(isSuccess: Boolean) {
                                if (cont.isActive) cont.resume(Result.success(isSuccess))
                            }

                            override fun onReturnString(result: String?) {}

                            override fun onRaiseException(code: Int, msg: String?) {
                                if (cont.isActive) {
                                    cont.resume(Result.failure(RuntimeException("Printer AIDL error ($code): $msg")))
                                }
                            }

                            override fun onPrintResult(code: Int, msg: String?) {
                                if (cont.isActive) {
                                    cont.resume(Result.success(code == 0))
                                }
                            }
                        }
                        block(service, callback)
                    }
                }
                res ?: Result.failure(RuntimeException("Printer operation timed out after ${CALLBACK_TIMEOUT_MS}ms"))
            } catch (e: RemoteException) {
                consecutiveErrors.incrementAndGet()
                handleServiceDisconnect(triggerReconnect = true)
                Result.failure(e)
            } catch (e: Exception) {
                consecutiveErrors.incrementAndGet()
                Result.failure(e)
            }
        }

    fun getConsecutiveErrorCount(): Int = consecutiveErrors.get()

    /**
     * Cleanly unbinds from the Woyou AIDL service and cancels reconnection jobs.
     */
    fun disconnect() {
        reconnectJob?.cancel()
        reconnectJob = null
        handleServiceDisconnect(triggerReconnect = false)
        try {
            appContext.unbindService(serviceConnection)
        } catch (_: IllegalArgumentException) {
            // Service was not registered
        }
        ioScope.cancel()
    }

    private fun buildOfflineCapabilities(reason: String): HardwareCapabilities {
        return HardwareCapabilities(
            deviceModel = Build.MODEL ?: "UNKNOWN_SUNMI",
            serialNumber = "OFFLINE",
            firmwareVersion = "OFFLINE",
            serviceVersion = reason,
            printerCapability = CapabilityStatus.ERROR,
            cutterCapability = CapabilityStatus.ERROR,
            drawerPulseCapability = CapabilityStatus.ERROR,
            drawerSensorCapability = CapabilityStatus.ERROR,
            switchPolarity = switchPolarity
        )
    }
}
