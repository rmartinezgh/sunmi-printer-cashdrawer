"""
PowerPOS — Local Printer & Cash Drawer Bridge Service (`bridge_daemon.py`).

Responsibilities:
  1. Wraps a `CashDrawerDriver` (e.g., `EscPosLocalDrawerDriver` or `ExternalSensorGatewayDriver`).
  2. Persists every physical transition and opening command into a local SQLite database
     (`OfflineEventStore`) before attempting network transmission.
  3. Synchronizes events to the Central Odoo Backend (`/api/v1/drawer/events` and
     `/api/v1/hardware/heartbeat`) signed with HMAC-SHA256.
  4. Never purges locally stored events until the Odoo server confirms accepted persistence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable, Dict, List, Optional

from .cash_drawer_driver import CashDrawerDriver, DrawerTransitionEvent


class OfflineEventStore:
    """
    Durable SQLite store for offline drawer events, authorizations, and print jobs.
    Events remain in `sync_status = 'PENDING'` or `'RETRY'` until the server returns
    explicit acknowledgment for `event_uuid`.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS offline_drawer_events (
                    event_uuid TEXT PRIMARY KEY,
                    sequence_number INTEGER NOT NULL,
                    device_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    previous_state TEXT NOT NULL,
                    new_state TEXT NOT NULL,
                    observed_timestamp REAL NOT NULL,
                    sensor_source TEXT NOT NULL,
                    sensor_quality TEXT NOT NULL,
                    authorization_ref TEXT,
                    opening_reason TEXT,
                    cashier_id TEXT,
                    pos_session_id TEXT,
                    pos_order_ref TEXT,
                    opening_duration_seconds REAL,
                    is_duration_incomplete INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL,
                    sync_status TEXT NOT NULL DEFAULT 'PENDING',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    synced_at REAL
                );

                CREATE INDEX IF NOT EXISTS idx_events_sync_status
                ON offline_drawer_events(sync_status, sequence_number);
                """
            )
            self._conn.commit()

    def enqueue_event(self, event: DrawerTransitionEvent) -> bool:
        """Idempotently persists a `DrawerTransitionEvent` to SQLite."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT event_uuid FROM offline_drawer_events WHERE event_uuid = ?",
                (event.event_uuid,),
            )
            if cur.fetchone() is not None:
                return False
            self._conn.execute(
                """
                INSERT INTO offline_drawer_events (
                    event_uuid, sequence_number, device_id, event_type,
                    previous_state, new_state, observed_timestamp,
                    sensor_source, sensor_quality, authorization_ref,
                    opening_reason, cashier_id, pos_session_id, pos_order_ref,
                    opening_duration_seconds, is_duration_incomplete,
                    payload_json, sync_status, retry_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 0, ?)
                """,
                (
                    event.event_uuid,
                    event.sequence_number,
                    event.device_id,
                    event.event_type.value,
                    event.previous_state.value,
                    event.new_state.value,
                    event.observed_timestamp,
                    event.sensor_source,
                    event.sensor_quality,
                    event.authorization_ref,
                    event.opening_reason,
                    event.cashier_id,
                    event.pos_session_id,
                    event.pos_order_ref,
                    event.opening_duration_seconds,
                    1 if event.is_duration_incomplete else 0,
                    json.dumps(event.to_dict(), sort_keys=True),
                    time.time(),
                ),
            )
            self._conn.commit()
            return True

    def get_unsynced_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT payload_json, retry_count
                FROM offline_drawer_events
                WHERE sync_status IN ('PENDING', 'RETRY')
                ORDER BY sequence_number ASC
                LIMIT ?
                """,
                (limit,),
            )
            return [json.loads(row["payload_json"]) for row in cur.fetchall()]

    def mark_events_acknowledged(self, accepted_uuids: List[str], now: Optional[float] = None) -> int:
        """
        Marks events as `SYNCED` ONLY when the server explicitly returns their UUIDs
        in `accepted_event_uuids`.
        """
        if not accepted_uuids:
            return 0
        ts = now if now is not None else time.time()
        with self._lock:
            placeholders = ",".join("?" for _ in accepted_uuids)
            cur = self._conn.execute(
                f"""
                UPDATE offline_drawer_events
                SET sync_status = 'SYNCED', synced_at = ?, last_error = NULL
                WHERE event_uuid IN ({placeholders})
                """,
                [ts, *accepted_uuids],
            )
            self._conn.commit()
            return cur.rowcount

    def mark_batch_failed(self, event_uuids: List[str], error_msg: str) -> None:
        if not event_uuids:
            return
        with self._lock:
            placeholders = ",".join("?" for _ in event_uuids)
            self._conn.execute(
                f"""
                UPDATE offline_drawer_events
                SET sync_status = 'RETRY',
                    retry_count = retry_count + 1,
                    last_error = ?
                WHERE event_uuid IN ({placeholders}) AND sync_status != 'SYNCED'
                """,
                [error_msg, *event_uuids],
            )
            self._conn.commit()

    def purge_synced_events(self, older_than_seconds: float = 86400 * 7, now: Optional[float] = None) -> int:
        """Only deletes events that have ALREADY been acknowledged (`sync_status = 'SYNCED'`)."""
        ts = now if now is not None else time.time()
        cutoff = ts - older_than_seconds
        with self._lock:
            cur = self._conn.execute(
                """
                DELETE FROM offline_drawer_events
                WHERE sync_status = 'SYNCED' AND synced_at IS NOT NULL AND synced_at <= ?
                """,
                (cutoff,),
            )
            self._conn.commit()
            return cur.rowcount


class LocalBridgeSyncService:
    """
    Coordinates continuous physical status monitoring, durable offline storage,
    and authenticated synchronization with the Central Odoo Backend.
    """

    def __init__(
        self,
        driver: CashDrawerDriver,
        store: OfflineEventStore,
        odoo_base_url: str,
        device_token: str,
        device_secret: str,
        http_sender: Optional[Callable[[str, Dict[str, str], bytes], Dict[str, Any]]] = None,
    ) -> None:
        self.driver = driver
        self.store = store
        self.odoo_base_url = odoo_base_url.rstrip("/")
        self.device_token = device_token
        self.device_secret = device_secret
        self._http_sender = http_sender or self._default_http_sender
        self.driver.subscribeStatusChanges(self._on_driver_event)

    def _on_driver_event(self, event: DrawerTransitionEvent) -> None:
        self.store.enqueue_event(event)

    def sign_request(
        self, raw_body: str, timestamp: Optional[str] = None, nonce: Optional[str] = None
    ) -> Dict[str, str]:
        ts = timestamp or str(int(time.time()))
        nc = nonce or uuid.uuid4().hex[:12]
        signing_input = f"{ts}.{nc}.{raw_body}".encode("utf-8")
        signature = hmac.new(
            self.device_secret.encode("utf-8"),
            signing_input,
            hashlib.sha256,
        ).hexdigest()
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.device_token}",
            "X-PowerPOS-Timestamp": ts,
            "X-PowerPOS-Nonce": nc,
            "X-PowerPOS-Signature": signature,
        }

    @staticmethod
    def _default_http_sender(url: str, headers: Dict[str, str], body: bytes) -> Dict[str, Any]:
        req = urllib.request.Request(url=url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    def sync_pending_events(self, batch_size: int = 50) -> Dict[str, Any]:
        pending = self.store.get_unsynced_events(limit=batch_size)
        if not pending:
            return {"synced_count": 0, "remaining_count": 0}

        uuids = [evt["event_uuid"] for evt in pending]
        payload = {"device_id": self.driver.device_id, "events": pending}
        raw_body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        headers = self.sign_request(raw_body)
        url = f"{self.odoo_base_url}/api/v1/drawer/events"

        try:
            response = self._http_sender(url, headers, raw_body.encode("utf-8"))
            accepted_uuids = response.get("accepted_event_uuids", [])
            ack_count = self.store.mark_events_acknowledged(accepted_uuids)
            unaccepted = [u for u in uuids if u not in set(accepted_uuids)]
            if unaccepted:
                self.store.mark_batch_failed(unaccepted, "Not in server accepted_event_uuids")
            return {
                "synced_count": ack_count,
                "remaining_count": len(self.store.get_unsynced_events(limit=1000)),
            }
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            self.store.mark_batch_failed(uuids, str(exc))
            return {
                "synced_count": 0,
                "error": str(exc),
                "remaining_count": len(self.store.get_unsynced_events(limit=1000)),
            }

    def send_heartbeat(self, printer_status: str = "online") -> Dict[str, Any]:
        health = self.driver.getHealth()
        payload = {
            "device_id": self.driver.device_id,
            "device_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "printer_status": printer_status,
            "drawer_physical_state": health.physical_state.value,
            "sensor_source": self.driver.sensor_source,
            "sdk_open_counter": health.sdk_open_counter,
        }
        raw_body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        headers = self.sign_request(raw_body)
        url = f"{self.odoo_base_url}/api/v1/hardware/heartbeat"
        return self._http_sender(url, headers, raw_body.encode("utf-8"))

