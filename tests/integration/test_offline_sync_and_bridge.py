"""
Integration Tests for Offline Event Persistence, Durable Synchronization, and
ESP32 External Sensor Gateway.
Covers Section 4.6, Section 14.5, Section 17.2, and Section 19.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "local-bridge"))
sys.path.insert(0, str(ROOT_DIR / "sensor-gateway"))

from esp32_firmware.main import Esp32DrawerSensorNode
from gateway_service import SensorGatewayCoordinator
from python.bridge_daemon import LocalBridgeSyncService, OfflineEventStore
from python.cash_drawer_driver import HardwareSimulatorDriver


class TestOfflineSyncAndSensorGateway(unittest.TestCase):
    def test_durable_offline_store_never_deletes_unacknowledged_events(self) -> None:
        store = OfflineEventStore(db_path=":memory:")
        driver = HardwareSimulatorDriver(device_id="SIM-REG-01")
        driver.initialize()

        network_online = {"up": False}
        sent_batches: List[Dict[str, Any]] = []

        def mock_sender(url: str, headers: Dict[str, str], body: bytes) -> Dict[str, Any]:
            if not network_online["up"]:
                raise ConnectionError("Store internet offline")
            data = json.loads(body.decode("utf-8"))
            sent_batches.append(data)
            # Acknowledge only the first 2 events in the first sync to test partial ACK durability
            evts = data.get("events", [])
            accepted = [e["event_uuid"] for e in evts[:2]]
            return {"status": "ok", "accepted_event_uuids": accepted}

        sync_svc = LocalBridgeSyncService(
            driver=driver,
            store=store,
            odoo_base_url="https://pos.powerpos.local",
            device_token="TOK-01",
            device_secret="SEC-01",
            http_sender=mock_sender,
        )

        # Generate offline events: initial observation + manual open + manual close
        driver.getCurrentStatus(now=2000.0)
        driver.simulate_manual_physical_open(now=2005.0)
        driver.simulate_manual_physical_close(now=2012.0)

        unsynced = store.get_unsynced_events()
        self.assertEqual(len(unsynced), 3)

        # Attempt sync while network is down
        res_offline = sync_svc.sync_pending_events()
        self.assertEqual(res_offline["synced_count"], 0)
        self.assertEqual(res_offline["remaining_count"], 3)

        # Attempt purge while still unacknowledged -> 0 deleted!
        purged = store.purge_synced_events(older_than_seconds=0.0, now=999999.0)
        self.assertEqual(purged, 0)
        self.assertEqual(len(store.get_unsynced_events()), 3)

        # Network recovers; server acknowledges first 2 events
        network_online["up"] = True
        res_partial = sync_svc.sync_pending_events()
        self.assertEqual(res_partial["synced_count"], 2)
        self.assertEqual(res_partial["remaining_count"], 1)

    def test_esp32_external_sensor_gateway_hmac_and_replay_protection(self) -> None:
        coordinator = SensorGatewayCoordinator(odoo_base_url="https://pos.powerpos.local")
        coordinator.register_sensor_node(
            device_id="ESP32-NODE-01",
            shared_secret="ESP32_SHARED_SECRET_KEY",
            device_token="ESP32_TOKEN",
        )
        node = Esp32DrawerSensorNode(
            device_id="ESP32-NODE-01",
            shared_secret="ESP32_SHARED_SECRET_KEY",
        )

        # 1. Initial closed frame
        f1 = node.build_signed_frame(contact_closed=True, tamper_loop_intact=True, timestamp=3000.0)
        s1 = coordinator.process_incoming_frame(f1)
        self.assertEqual(s1["physical_state"], "CLOSED")

        # 2. Replay same frame -> rejected!
        with self.assertRaises(PermissionError):
            coordinator.process_incoming_frame(f1)

        # 3. Physical key opens drawer -> contact_closed=False
        f2 = node.build_signed_frame(contact_closed=False, tamper_loop_intact=True, timestamp=3005.0)
        s2 = coordinator.process_incoming_frame(f2)
        self.assertEqual(s2["physical_state"], "OPEN")

        # 4. Tamper loop cut -> transitions to ERROR
        f3 = node.build_signed_frame(contact_closed=False, tamper_loop_intact=False, timestamp=3010.0)
        s3 = coordinator.process_incoming_frame(f3)
        self.assertEqual(s3["physical_state"], "ERROR")


if __name__ == "__main__":
    unittest.main()

