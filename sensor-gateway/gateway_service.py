"""
PowerPOS — External Sensor Gateway Aggregator Service (`gateway_service.py`).

Receives HMAC-signed reed-switch and tamper telemetry from ESP32 sensor nodes,
feeds them into `ExternalSensorGatewayDriver`, and synchronizes verified physical
state transitions (`OPEN`, `CLOSED`, `ERROR`, `OFFLINE`) to Odoo.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local-bridge"))

from python.bridge_daemon import LocalBridgeSyncService, OfflineEventStore
from python.cash_drawer_driver import ExternalSensorGatewayDriver


class SensorGatewayCoordinator:
    """Manages one or more ESP32 external drawer sensor nodes for a store branch."""

    def __init__(self, odoo_base_url: str, db_path: str = ":memory:") -> None:
        self.odoo_base_url = odoo_base_url
        self.store = OfflineEventStore(db_path=db_path)
        self.drivers: Dict[str, ExternalSensorGatewayDriver] = {}
        self.sync_services: Dict[str, LocalBridgeSyncService] = {}
        self._seen_nonces: set[str] = set()

    def register_sensor_node(
        self,
        device_id: str,
        shared_secret: str,
        device_token: str,
        prolonged_open_threshold_seconds: float = 30.0,
    ) -> ExternalSensorGatewayDriver:
        driver = ExternalSensorGatewayDriver(
            device_id=device_id,
            shared_secret=shared_secret,
            prolonged_open_threshold_seconds=prolonged_open_threshold_seconds,
        )
        driver.initialize()
        sync_svc = LocalBridgeSyncService(
            driver=driver,
            store=self.store,
            odoo_base_url=self.odoo_base_url,
            device_token=device_token,
            device_secret=shared_secret,
        )
        self.drivers[device_id] = driver
        self.sync_services[device_id] = sync_svc
        return driver

    def process_incoming_frame(self, frame: Dict[str, Any]) -> Dict[str, Any]:
        device_id = frame.get("device_id", "")
        if device_id not in self.drivers:
            raise KeyError(f"Unregistered sensor node: {device_id}")

        nonce = str(frame.get("nonce", ""))
        replay_key = f"{device_id}:{nonce}"
        if not nonce or replay_key in self._seen_nonces:
            raise PermissionError("Replay attack detected: duplicate or missing sensor nonce.")

        driver = self.drivers[device_id]
        status = driver.ingest_signed_sensor_frame(
            contact_closed=bool(frame["contact_closed"]),
            tamper_loop_intact=bool(frame.get("tamper_loop_intact", True)),
            timestamp=float(frame["timestamp"]),
            nonce=nonce,
            signature=str(frame["signature"]),
        )
        self._seen_nonces.add(replay_key)
        return status

