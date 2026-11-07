package com.powerpos.android.persistence

import android.content.Context
import androidx.room.ColumnInfo
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Index
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import kotlinx.coroutines.flow.Flow

/**
 * Synchronization lifecycle states for offline-persisted hardware events.
 *
 * Durable Synchronization Invariant:
 * Events are NEVER deleted or purged until the Odoo server explicitly returns an accepted
 * persistence acknowledgment (`serverAckToken`) for the specific `eventUuid`.
 */
object SyncDeliveryStatus {
    const val PENDING = "PENDING"
    const val IN_FLIGHT = "IN_FLIGHT"
    const val SERVER_ACKNOWLEDGED = "SERVER_ACKNOWLEDGED"
    const val FAILED_RETRYABLE = "FAILED_RETRYABLE"
}

/**
 * Durable Room Entity representing an auditable cash drawer transition or security event.
 */
@Entity(
    tableName = "offline_drawer_events",
    indices = [
        Index(value = ["sync_status", "occurred_at_epoch_ms"]),
        Index(value = ["matched_authorization_id"])
    ]
)
data class OfflineDrawerEventEntity(
    @PrimaryKey
    @ColumnInfo(name = "event_uuid")
    val eventUuid: String,

    @ColumnInfo(name = "terminal_id")
    val terminalId: String,

    @ColumnInfo(name = "transition_type")
    val transitionType: String,

    @ColumnInfo(name = "previous_state")
    val previousState: String,

    @ColumnInfo(name = "new_state")
    val newState: String,

    @ColumnInfo(name = "correlation_class")
    val correlationClass: String,

    @ColumnInfo(name = "matched_authorization_id")
    val matchedAuthorizationId: String?,

    @ColumnInfo(name = "matched_idempotency_key")
    val matchedIdempotencyKey: String?,

    @ColumnInfo(name = "reason")
    val reason: String?,

    @ColumnInfo(name = "occurred_at_epoch_ms")
    val occurredAtEpochMs: Long,

    @ColumnInfo(name = "open_duration_ms")
    val openDurationMs: Long?,

    @ColumnInfo(name = "duration_incomplete")
    val durationIncomplete: Boolean,

    @ColumnInfo(name = "sync_status")
    val syncStatus: String = SyncDeliveryStatus.PENDING,

    @ColumnInfo(name = "retry_count")
    val retryCount: Int = 0,

    @ColumnInfo(name = "last_sync_attempt_ms")
    val lastSyncAttemptMs: Long? = null,

    @ColumnInfo(name = "server_ack_token")
    val serverAckToken: String? = null,

    @ColumnInfo(name = "server_acknowledged_at_ms")
    val serverAcknowledgedAtMs: Long? = null,

    @ColumnInfo(name = "last_error")
    val lastError: String? = null
)

/**
 * Durable Room Entity storing server-issued or offline supervisor authorization grants.
 */
@Entity(
    tableName = "offline_auth_records",
    indices = [
        Index(value = ["idempotency_key"], unique = true),
        Index(value = ["pos_order_id"])
    ]
)
data class OfflineAuthRecordEntity(
    @PrimaryKey
    @ColumnInfo(name = "authorization_id")
    val authorizationId: String,

    @ColumnInfo(name = "idempotency_key")
    val idempotencyKey: String,

    @ColumnInfo(name = "operator_id")
    val operatorId: String,

    @ColumnInfo(name = "pos_order_id")
    val posOrderId: String?,

    @ColumnInfo(name = "reason")
    val reason: String,

    @ColumnInfo(name = "auth_token_hash")
    val authTokenHash: String,

    @ColumnInfo(name = "issued_at_epoch_ms")
    val issuedAtEpochMs: Long,

    @ColumnInfo(name = "expires_at_epoch_ms")
    val expiresAtEpochMs: Long,

    @ColumnInfo(name = "pulse_dispatched")
    val pulseDispatched: Boolean,

    @ColumnInfo(name = "dispatched_at_epoch_ms")
    val dispatchedAtEpochMs: Long?
)

/**
 * Durable Room Entity storing queued or completed receipt print jobs for audit and retry.
 */
@Entity(
    tableName = "offline_print_jobs",
    indices = [
        Index(value = ["job_status", "created_at_epoch_ms"])
    ]
)
data class OfflinePrintJobEntity(
    @PrimaryKey
    @ColumnInfo(name = "job_id")
    val jobId: String,

    @ColumnInfo(name = "official_receipt_number")
    val officialReceiptNumber: String,

    @ColumnInfo(name = "terminal_id")
    val terminalId: String,

    @ColumnInfo(name = "payload_json")
    val payloadJson: String,

    @ColumnInfo(name = "job_status")
    val jobStatus: String,

    @ColumnInfo(name = "printer_error_code")
    val printerErrorCode: Int? = null,

    @ColumnInfo(name = "created_at_epoch_ms")
    val createdAtEpochMs: Long,

    @ColumnInfo(name = "completed_at_epoch_ms")
    val completedAtEpochMs: Long? = null
)

/**
 * Room DAO enforcing durable offline storage and explicit server-acknowledgment rules.
 */
@Dao
interface HardwareEventDao {

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insertDrawerEvent(event: OfflineDrawerEventEntity): Long

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertAuthRecord(record: OfflineAuthRecordEntity)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertPrintJob(job: OfflinePrintJobEntity)

    @Query(
        """
        SELECT * FROM offline_drawer_events
        WHERE sync_status IN ('PENDING', 'FAILED_RETRYABLE')
        ORDER BY occurred_at_epoch_ms ASC
        LIMIT :batchSize
        """
    )
    suspend fun getUnsyncedDrawerEventsBatch(batchSize: Int = 50): List<OfflineDrawerEventEntity>

    @Query("SELECT COUNT(*) FROM offline_drawer_events WHERE sync_status != 'SERVER_ACKNOWLEDGED'")
    fun observePendingSyncCount(): Flow<Int>

    @Query("SELECT * FROM offline_drawer_events ORDER BY occurred_at_epoch_ms DESC LIMIT :limit")
    fun observeRecentDrawerEvents(limit: Int = 100): Flow<List<OfflineDrawerEventEntity>>

    @Query(
        """
        UPDATE offline_drawer_events
        SET sync_status = 'IN_FLIGHT',
            last_sync_attempt_ms = :attemptTimeMs
        WHERE event_uuid IN (:eventUuids)
          AND sync_status != 'SERVER_ACKNOWLEDGED'
        """
    )
    suspend fun markEventsInFlight(eventUuids: List<String>, attemptTimeMs: Long)

    /**
     * Marks an event as durably persisted on the Odoo server ONLY when a non-empty `serverAckToken`
     * is returned by `/api/v1/drawer/events`.
     */
    @Query(
        """
        UPDATE offline_drawer_events
        SET sync_status = 'SERVER_ACKNOWLEDGED',
            server_ack_token = :serverAckToken,
            server_acknowledged_at_ms = :acknowledgedAtMs,
            last_error = NULL
        WHERE event_uuid = :eventUuid
        """
    )
    suspend fun markEventServerAcknowledged(
        eventUuid: String,
        serverAckToken: String,
        acknowledgedAtMs: Long
    ): Int

    @Query(
        """
        UPDATE offline_drawer_events
        SET sync_status = 'FAILED_RETRYABLE',
            retry_count = retry_count + 1,
            last_sync_attempt_ms = :attemptTimeMs,
            last_error = :errorMessage
        WHERE event_uuid = :eventUuid
          AND sync_status != 'SERVER_ACKNOWLEDGED'
        """
    )
    suspend fun markEventSyncFailed(
        eventUuid: String,
        attemptTimeMs: Long,
        errorMessage: String
    )

    /**
     * Purges historical drawer events ONLY if they are explicitly marked `SERVER_ACKNOWLEDGED`,
     * carry a non-null `server_ack_token`, and are older than `cutoffEpochMs`.
     * Unacknowledged events can NEVER be deleted by retention cleanup.
     */
    @Query(
        """
        DELETE FROM offline_drawer_events
        WHERE sync_status = 'SERVER_ACKNOWLEDGED'
          AND server_ack_token IS NOT NULL
          AND server_ack_token != ''
          AND occurred_at_epoch_ms < :cutoffEpochMs
        """
    )
    suspend fun purgeAcknowledgedEventsOlderThan(cutoffEpochMs: Long): Int
}

@Database(
    entities = [
        OfflineDrawerEventEntity::class,
        OfflineAuthRecordEntity::class,
        OfflinePrintJobEntity::class
    ],
    version = 1,
    exportSchema = false
)
abstract class OfflineHardwareDatabase : RoomDatabase() {
    abstract fun hardwareEventDao(): HardwareEventDao

    companion object {
        @Volatile
        private var INSTANCE: OfflineHardwareDatabase? = null

        fun getInstance(context: Context): OfflineHardwareDatabase {
            return INSTANCE ?: synchronized(this) {
                INSTANCE ?: Room.databaseBuilder(
                    context.applicationContext,
                    OfflineHardwareDatabase::class.java,
                    "powerpos_offline_hardware.db"
                )
                    .fallbackToDestructiveMigrationOnDowngrade()
                    .build()
                    .also { INSTANCE = it }
            }
        }
    }
}
