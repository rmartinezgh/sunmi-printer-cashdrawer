package com.powerpos.android.network

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import com.powerpos.android.persistence.HardwareEventDao
import com.powerpos.android.persistence.OfflineDrawerEventEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.UUID
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * Server acknowledgment entry returned from `/api/v1/drawer/events`.
 */
data class ServerEventAck(
    val eventUuid: String,
    val accepted: Boolean,
    val ackToken: String?,
    val errorReason: String? = null
)

/**
 * Secure Android Keystore credential store using `EncryptedSharedPreferences`.
 * Never exposes `APP_KEY` or device HMAC secret to WebView JavaScript or logs.
 */
class DeviceCredentialStore(context: Context) {
    companion object {
        private const val PREFS_FILE = "powerpos_hardware_secure_creds"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_DEVICE_TOKEN = "device_token"
        private const val KEY_HMAC_SECRET = "hmac_secret"
        private const val KEY_SERVER_BASE_URL = "server_base_url"
    }

    private val encryptedPrefs: SharedPreferences by lazy {
        val masterKey = MasterKey.Builder(context.applicationContext)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()

        EncryptedSharedPreferences.create(
            context.applicationContext,
            PREFS_FILE,
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
        )
    }

    fun saveProvisionedCredentials(
        deviceId: String,
        deviceToken: String,
        hmacSecret: String,
        serverBaseUrl: String
    ) {
        encryptedPrefs.edit()
            .putString(KEY_DEVICE_ID, deviceId)
            .putString(KEY_DEVICE_TOKEN, deviceToken)
            .putString(KEY_HMAC_SECRET, hmacSecret)
            .putString(KEY_SERVER_BASE_URL, serverBaseUrl.trimEnd('/'))
            .apply()
    }

    fun getDeviceId(): String = encryptedPrefs.getString(KEY_DEVICE_ID, "") ?: ""
    fun getDeviceToken(): String = encryptedPrefs.getString(KEY_DEVICE_TOKEN, "") ?: ""
    fun getHmacSecret(): String = encryptedPrefs.getString(KEY_HMAC_SECRET, "") ?: ""
    fun getServerBaseUrl(): String = encryptedPrefs.getString(KEY_SERVER_BASE_URL, "https://pos.powerpos.local") ?: "https://pos.powerpos.local"
}

/**
 * Cryptographic request signer for PowerPOS Android -> Odoo 18 API communication.
 *
 * Canonical String-To-Sign format:
 *   `HTTP_METHOD + "\n" + ENDPOINT_PATH + "\n" + TIMESTAMP_SEC + "\n" + NONCE + "\n" + SHA256_HEX(BODY)`
 */
object HmacRequestSigner {
    fun computeCanonicalSignature(
        secretKey: String,
        httpMethod: String,
        endpointPath: String,
        timestampSec: Long,
        nonce: String,
        rawBody: String
    ): String {
        val bodyHash = sha256Hex(rawBody)
        val canonicalPayload = buildString {
            append(httpMethod.uppercase().trim())
            append('\n')
            append(endpointPath.trim())
            append('\n')
            append(timestampSec)
            append('\n')
            append(nonce.trim())
            append('\n')
            append(bodyHash)
        }
        val mac = Mac.getInstance("HmacSHA256")
        val keySpec = SecretKeySpec(secretKey.toByteArray(Charsets.UTF_8), "HmacSHA256")
        mac.init(keySpec)
        val rawHmac = mac.doFinal(canonicalPayload.toByteArray(Charsets.UTF_8))
        return rawHmac.joinToString("") { "%02x".format(it) }
    }

    fun sha256Hex(content: String): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(content.toByteArray(Charsets.UTF_8))
        return digest.joinToString("") { "%02x".format(it) }
    }
}

/**
 * Production REST Synchronization Client for Odoo 18 PowerPOS Hardware Controllers.
 *
 * Supported Endpoints:
 * - `POST /api/v1/hardware/register`
 * - `POST /api/v1/hardware/heartbeat`
 * - `POST /api/v1/drawer/authorize`
 * - `POST /api/v1/drawer/events`
 * - `POST /api/v1/printer/print`
 *
 * Enforces `X-PowerPOS-Signature`, `X-PowerPOS-Timestamp`, `X-PowerPOS-Nonce`, and
 * durable event acknowledgment before marking local Room rows as `SERVER_ACKNOWLEDGED`.
 */
class PowerPosSyncClient(
    private val credentialStore: DeviceCredentialStore,
    private val eventDao: HardwareEventDao
) {
    companion object {
        const val ENDPOINT_REGISTER = "/api/v1/hardware/register"
        const val ENDPOINT_HEARTBEAT = "/api/v1/hardware/heartbeat"
        const val ENDPOINT_DRAWER_AUTHORIZE = "/api/v1/drawer/authorize"
        const val ENDPOINT_DRAWER_EVENTS = "/api/v1/drawer/events"
        const val ENDPOINT_PRINTER_PRINT = "/api/v1/printer/print"
        private const val CONNECT_TIMEOUT_MS = 8_000
        private const val READ_TIMEOUT_MS = 10_000
    }

    /**
     * Registers the SUNMI terminal capabilities with Odoo (`/api/v1/hardware/register`).
     */
    suspend fun registerTerminal(
        serialNumber: String,
        deviceModel: String,
        firmwareVersion: String,
        serviceVersion: String,
        supportsDrawerSensor: Boolean
    ): Result<JSONObject> = withContext(Dispatchers.IO) {
        val body = JSONObject().apply {
            put("device_id", credentialStore.getDeviceId())
            put("serial_number", serialNumber)
            put("device_model", deviceModel)
            put("firmware_version", firmwareVersion)
            put("service_version", serviceVersion)
            put("supports_drawer_sensor", supportsDrawerSensor)
        }
        executeSignedPost(ENDPOINT_REGISTER, body)
    }

    /**
     * Sends a periodic terminal health heartbeat (`/api/v1/hardware/heartbeat`).
     */
    suspend fun sendHeartbeat(
        drawerState: String,
        printerStatusCode: Int,
        cumulativeOpenPulses: Int
    ): Result<JSONObject> = withContext(Dispatchers.IO) {
        val body = JSONObject().apply {
            put("device_id", credentialStore.getDeviceId())
            put("drawer_state", drawerState)
            put("printer_status_code", printerStatusCode)
            put("cumulative_open_pulses", cumulativeOpenPulses)
            put("reported_at_ms", System.currentTimeMillis())
        }
        executeSignedPost(ENDPOINT_HEARTBEAT, body)
    }

    /**
     * Requests a signed cash drawer opening authorization from Odoo (`/api/v1/drawer/authorize`).
     */
    suspend fun requestServerDrawerAuthorization(
        operatorId: String,
        reason: String,
        posOrderId: String?,
        idempotencyKey: String
    ): Result<JSONObject> = withContext(Dispatchers.IO) {
        val body = JSONObject().apply {
            put("device_id", credentialStore.getDeviceId())
            put("operator_id", operatorId)
            put("reason", reason)
            put("pos_order_id", posOrderId ?: JSONObject.NULL)
            put("idempotency_key", idempotencyKey)
            put("requested_at_ms", System.currentTimeMillis())
        }
        executeSignedPost(ENDPOINT_DRAWER_AUTHORIZE, body)
    }

    /**
     * Flushes a batch of unsynced drawer events from Room to `/api/v1/drawer/events`.
     * Events are ONLY marked `SERVER_ACKNOWLEDGED` if the server explicitly returns
     * `accepted = true` and a non-empty `ack_token` for that specific `eventUuid`.
     */
    suspend fun syncPendingDrawerEvents(batchSize: Int = 50): Result<Int> = withContext(Dispatchers.IO) {
        val pendingBatch = eventDao.getUnsyncedDrawerEventsBatch(batchSize)
        if (pendingBatch.isEmpty()) {
            return@withContext Result.success(0)
        }

        val nowMs = System.currentTimeMillis()
        val uuids = pendingBatch.map { it.eventUuid }
        eventDao.markEventsInFlight(uuids, nowMs)

        val eventsArray = JSONArray()
        pendingBatch.forEach { entity ->
            eventsArray.put(serializeDrawerEvent(entity))
        }

        val payload = JSONObject().apply {
            put("device_id", credentialStore.getDeviceId())
            put("events", eventsArray)
        }

        val responseResult = executeSignedPost(ENDPOINT_DRAWER_EVENTS, payload)
        if (responseResult.isFailure) {
            val errMsg = responseResult.exceptionOrNull()?.message ?: "Network failure during event sync"
            pendingBatch.forEach { entity ->
                eventDao.markEventSyncFailed(entity.eventUuid, System.currentTimeMillis(), errMsg)
            }
            return@withContext Result.failure(responseResult.exceptionOrNull()!!)
        }

        val responseJson = responseResult.getOrNull()!!
        val acksArray = responseJson.optJSONArray("acknowledgments") ?: JSONArray()
        val ackMap = mutableMapOf<String, ServerEventAck>()

        for (i in 0 until acksArray.length()) {
            val item = acksArray.optJSONObject(i) ?: continue
            val uuid = item.optString("event_uuid", "")
            val accepted = item.optBoolean("accepted", false)
            val ackToken = item.optString("ack_token", "").takeIf { it.isNotBlank() }
            val errorReason = item.optString("error", "").takeIf { it.isNotBlank() }
            if (uuid.isNotBlank()) {
                ackMap[uuid] = ServerEventAck(uuid, accepted, ackToken, errorReason)
            }
        }

        var acknowledgedCount = 0
        val ackTimestamp = System.currentTimeMillis()
        pendingBatch.forEach { entity ->
            val ack = ackMap[entity.eventUuid]
            if (ack != null && ack.accepted && !ack.ackToken.isNullOrBlank()) {
                eventDao.markEventServerAcknowledged(
                    eventUuid = entity.eventUuid,
                    serverAckToken = ack.ackToken,
                    acknowledgedAtMs = ackTimestamp
                )
                acknowledgedCount++
            } else {
                val reason = ack?.errorReason ?: "Missing explicit server acknowledgment for eventUuid"
                eventDao.markEventSyncFailed(entity.eventUuid, ackTimestamp, reason)
            }
        }

        Result.success(acknowledgedCount)
    }

    /**
     * Reports print job execution telemetry to `/api/v1/printer/print`.
     */
    suspend fun reportPrintJobStatus(
        jobId: String,
        officialReceiptNumber: String,
        status: String,
        printerStatusCode: Int
    ): Result<JSONObject> = withContext(Dispatchers.IO) {
        val body = JSONObject().apply {
            put("device_id", credentialStore.getDeviceId())
            put("job_id", jobId)
            put("official_receipt_number", officialReceiptNumber)
            put("status", status)
            put("printer_status_code", printerStatusCode)
            put("reported_at_ms", System.currentTimeMillis())
        }
        executeSignedPost(ENDPOINT_PRINTER_PRINT, body)
    }

    private fun serializeDrawerEvent(entity: OfflineDrawerEventEntity): JSONObject {
        return JSONObject().apply {
            put("event_uuid", entity.eventUuid)
            put("terminal_id", entity.terminalId)
            put("transition_type", entity.transitionType)
            put("previous_state", entity.previousState)
            put("new_state", entity.newState)
            put("correlation_class", entity.correlationClass)
            put("matched_authorization_id", entity.matchedAuthorizationId ?: JSONObject.NULL)
            put("matched_idempotency_key", entity.matchedIdempotencyKey ?: JSONObject.NULL)
            put("reason", entity.reason ?: JSONObject.NULL)
            put("occurred_at_epoch_ms", entity.occurredAtEpochMs)
            put("open_duration_ms", entity.openDurationMs ?: JSONObject.NULL)
            put("duration_incomplete", entity.durationIncomplete)
        }
    }

    private fun executeSignedPost(endpointPath: String, bodyJson: JSONObject): Result<JSONObject> {
        val baseUrl = credentialStore.getServerBaseUrl()
        val deviceToken = credentialStore.getDeviceToken()
        val hmacSecret = credentialStore.getHmacSecret()

        if (deviceToken.isBlank() || hmacSecret.isBlank()) {
            return Result.failure(IllegalStateException("Device credentials not provisioned in Android Keystore"))
        }

        val rawBody = bodyJson.toString()
        val timestampSec = System.currentTimeMillis() / 1000L
        val nonce = UUID.randomUUID().toString()
        val signature = HmacRequestSigner.computeCanonicalSignature(
            secretKey = hmacSecret,
            httpMethod = "POST",
            endpointPath = endpointPath,
            timestampSec = timestampSec,
            nonce = nonce,
            rawBody = rawBody
        )

        var connection: HttpURLConnection? = null
        return try {
            val url = URL(baseUrl + endpointPath)
            connection = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = CONNECT_TIMEOUT_MS
                readTimeout = READ_TIMEOUT_MS
                doOutput = true
                setRequestProperty("Content-Type", "application/json; charset=utf-8")
                setRequestProperty("Accept", "application/json")
                setRequestProperty("Authorization", "Bearer $deviceToken")
                setRequestProperty("X-PowerPOS-Signature", signature)
                setRequestProperty("X-PowerPOS-Timestamp", timestampSec.toString())
                setRequestProperty("X-PowerPOS-Nonce", nonce)
            }

            OutputStreamWriter(connection.outputStream, Charsets.UTF_8).use { writer ->
                writer.write(rawBody)
                writer.flush()
            }

            val responseCode = connection.responseCode
            val stream = if (responseCode in 200..299) connection.inputStream else connection.errorStream
            val responseText = stream?.let {
                BufferedReader(InputStreamReader(it, Charsets.UTF_8)).use { reader -> reader.readText() }
            }.orEmpty()

            if (responseCode in 200..299) {
                Result.success(if (responseText.isNotBlank()) JSONObject(responseText) else JSONObject())
            } else {
                Result.failure(RuntimeException("HTTP $responseCode from $endpointPath: $responseText"))
            }
        } catch (e: Exception) {
            Result.failure(e)
        } finally {
            connection?.disconnect()
        }
    }
}
