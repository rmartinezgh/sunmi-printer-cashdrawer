"""
Hardware-in-the-Loop & Protocol Simulation Tests for Section 21.3:
Executes the Mandatory 15-Step Physical Monitoring Test Protocol in exact sequence.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "local-bridge"))

from python.cash_drawer_driver import (
    DrawerEventType,
    DrawerPhysicalState,
    EscPosLocalDrawerDriver,
)


class Test15StepPhysicalMonitoringProtocol(unittest.TestCase):
    def test_15_step_physical_monitoring_sequence(self) -> None:
        # Simulated electrical state on RJ11 Pin 3 via DLE EOT 1 (`10 04 01`)
        # 0x12 = Online + Pin 3 Low (CLOSED); 0x16 = Online + Pin 3 High (OPEN)
        wire_state = {"byte": 0x12, "connected": True}

        def mock_escpos_exchange(cmd: bytes) -> bytes:
            if not wire_state["connected"]:
                raise ConnectionError("USB/LAN cable unplugged")
            if cmd == b"\x1b\x70\x00\x32\x96":
                # Solenoid kick energized -> physical latch pops open
                wire_state["byte"] = 0x16
                return b""
            if cmd == b"\x10\x04\x01":
                return bytes([wire_state["byte"]])
            return b""

        driver = EscPosLocalDrawerDriver(
            device_id="SUNMI-NT311-LOCAL-01",
            pin3_open_level=1,
            hardware_verified=True,
            debounce_samples=2,
            prolonged_open_threshold_seconds=30.0,
            staleness_timeout_seconds=5.0,
            cooldown_lock_seconds=1.0,
            transport_exchange=mock_escpos_exchange,
        )
        driver.initialize()

        # Step 1 & 2: Start with physically closed drawer and read actual status
        s2 = driver.getCurrentStatus(now=1000.0)
        self.assertEqual(s2["physical_state"], "CLOSED")
        history = driver.get_event_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[-1].event_type, DrawerEventType.INITIAL_OBSERVATION)
        self.assertTrue(history[-1].is_initial_observation)

        # Step 3: Open drawer through authorized POS action
        open_cmd = driver.requestOpen(
            authorization_ref="AUTH-POS-0001",
            reason="cash_sale",
            cashier_id="EMP-101",
            pos_session_id="POS/2026/0001",
            pos_order_ref="Order 0001-001-0001",
            now=1010.0,
        )
        self.assertTrue(open_cmd["accepted"])

        # Step 4: Verify physical OPEN status and correlation with authorization
        s4 = driver.getCurrentStatus(now=1010.5)
        self.assertEqual(s4["physical_state"], "OPEN")
        open_evt = driver.get_event_history()[-1]
        self.assertEqual(open_evt.event_type, DrawerEventType.PHYSICAL_OPENED)
        self.assertEqual(open_evt.authorization_ref, "AUTH-POS-0001")
        self.assertEqual(open_evt.cashier_id, "EMP-101")

        # Step 5 & 6: Close drawer manually and verify CLOSED status & exact duration
        wire_state["byte"] = 0x12
        s6 = driver.getCurrentStatus(now=1018.0)
        self.assertEqual(s6["physical_state"], "CLOSED")
        close_evt = driver.get_event_history()[-1]
        self.assertEqual(close_evt.event_type, DrawerEventType.PHYSICAL_CLOSED)
        self.assertAlmostEqual(close_evt.opening_duration_seconds or 0.0, 7.5, places=2)
        self.assertFalse(close_evt.is_duration_incomplete)

        # Step 7 & 8: Open drawer manually using physical key without software command
        wire_state["byte"] = 0x16
        s8 = driver.getCurrentStatus(now=1050.0)
        self.assertEqual(s8["physical_state"], "OPEN")
        unmatched_evt = driver.get_event_history()[-1]
        self.assertEqual(unmatched_evt.event_type, DrawerEventType.UNMATCHED_OPENING)
        self.assertIsNone(unmatched_evt.authorization_ref)
        self.assertIsNone(unmatched_evt.cashier_id)  # Never falsely blame last cashier!

        # Step 9 & 10: Keep drawer open beyond alert threshold (30s) -> verify timeout alert
        s10 = driver.getCurrentStatus(now=1085.0)
        self.assertEqual(s10["physical_state"], "OPEN")
        timeout_evt = driver.get_event_history()[-1]
        self.assertEqual(timeout_evt.event_type, DrawerEventType.OPENING_TIMEOUT)
        self.assertAlmostEqual(timeout_evt.opening_duration_seconds or 0.0, 35.0, places=2)

        # Step 11 & 12: Disconnect status interface while drawer is OPEN
        wire_state["connected"] = False
        s12 = driver.getCurrentStatus(now=1090.0)
        self.assertEqual(s12["physical_state"], "ERROR")
        self.assertTrue(s12["is_duration_incomplete"])
        # Ensure NO fake PHYSICAL_CLOSED event was fabricated after Step 7!
        for evt in driver.get_event_history()[5:]:
            self.assertNotEqual(evt.event_type, DrawerEventType.PHYSICAL_CLOSED)

        # Also test explicit disconnect -> OFFLINE
        driver.disconnect(now=1092.0)
        self.assertEqual(driver.getHealth(now=1092.0).physical_state, DrawerPhysicalState.OFFLINE)

        # Step 13 & 14: Reconnect device and verify state recovery
        wire_state["connected"] = True
        wire_state["byte"] = 0x12  # Drawer was closed while offline
        driver.initialize()
        s14 = driver.getCurrentStatus(now=1100.0)
        self.assertEqual(s14["physical_state"], "CLOSED")
        event_types = [e.event_type for e in driver.get_event_history()]
        self.assertIn(DrawerEventType.COMMUNICATION_RESTORED, event_types)


if __name__ == "__main__":
    unittest.main()

