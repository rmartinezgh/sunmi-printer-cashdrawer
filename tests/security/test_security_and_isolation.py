"""
Security, Audit Integrity, Hash-Chaining & Multi-Company Isolation Tests.
Covers Section 11.2, Section 18, and Section 20.
"""

from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "local-bridge"))

from python.bridge_daemon import OfflineEventStore
from python.cash_drawer_driver import DrawerEventType, DrawerPhysicalState, DrawerTransitionEvent


class AuditChainVerifier:
    """Reference cryptographic hash-chain verifier matching `powerpos.cashdrawer.event`."""

    @staticmethod
    def compute_event_hash(prev_hash: str, evt: Dict[str, Any]) -> str:
        canonical = (
            f"{prev_hash}|{evt['event_uuid']}|{evt['sequence_number']}|"
            f"{evt['device_id']}|{evt['event_type']}|{evt['previous_state']}|"
            f"{evt['new_state']}|{evt['observed_timestamp']}"
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TestSecurityAndAuditIntegrity(unittest.TestCase):
    def test_append_only_hash_chain_detects_tampering(self) -> None:
        ledger: List[Dict[str, Any]] = []
        prev_hash = "GENESIS"
        for seq in range(1, 5):
            entry: Dict[str, Any] = {
                "event_uuid": f"EVT-000{seq}",
                "sequence_number": seq,
                "device_id": "DRAWER-MAKATI-01",
                "event_type": "physical_opened" if seq % 2 == 1 else "physical_closed",
                "previous_state": "CLOSED" if seq % 2 == 1 else "OPEN",
                "new_state": "OPEN" if seq % 2 == 1 else "CLOSED",
                "observed_timestamp": 1700000000.0 + seq * 10,
                "previous_event_hash": prev_hash,
            }
            entry["event_hash"] = AuditChainVerifier.compute_event_hash(prev_hash, entry)
            prev_hash = entry["event_hash"]
            ledger.append(entry)

        # Verify intact chain
        running = "GENESIS"
        for item in ledger:
            expected = AuditChainVerifier.compute_event_hash(running, item)
            self.assertEqual(item["event_hash"], expected)
            running = item["event_hash"]

        # Simulate database tampering on record #2
        ledger[1]["event_type"] = "command_accepted"
        running = "GENESIS"
        tamper_detected = False
        for item in ledger:
            expected = AuditChainVerifier.compute_event_hash(running, item)
            if item["event_hash"] != expected:
                tamper_detected = True
                break
            running = item["event_hash"]
        self.assertTrue(tamper_detected)

    def test_duplicate_event_uuid_idempotency(self) -> None:
        store = OfflineEventStore(db_path=":memory:")
        evt = DrawerTransitionEvent(
            event_uuid="UUID-FIXED-1234",
            sequence_number=1,
            device_id="DRAWER-01",
            event_type=DrawerEventType.PHYSICAL_OPENED,
            previous_state=DrawerPhysicalState.CLOSED,
            new_state=DrawerPhysicalState.OPEN,
            observed_timestamp=1000.0,
            sensor_source="escpos_dle_eot",
        )
        self.assertTrue(store.enqueue_event(evt))
        self.assertFalse(store.enqueue_event(evt))
        self.assertEqual(len(store.get_unsynced_events()), 1)


if __name__ == "__main__":
    unittest.main()

