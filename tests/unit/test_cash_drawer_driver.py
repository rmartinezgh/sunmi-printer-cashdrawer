"""
Unit Tests for Module D & E — Cash Drawer Driver Abstraction & DLE EOT 1 Decoder.
Covers Section 9, Section 10, and Section 21.2 testing requirements.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "local-bridge"))

from python.cash_drawer_driver import (
    CapabilityResult,
    CommandOnlyDrawerDriver,
    DeviceClassification,
    DrawerPhysicalState,
    EscPosLocalDrawerDriver,
    ExternalSensorGatewayDriver,
    HardwareSimulatorDriver,
    SunmiAndroidBridgeDriver,
)


class TestCashDrawerDrivers(unittest.TestCase):
    def test_dle_eot_1_frame_decoding_and_validation(self) -> None:
        # Valid DLE EOT 1 frame: bit7=0, bit4=1 (0x10), bit1=1 (0x02), bit0=0 -> base 0x12
        # Bit 2 (0x04) = 0 -> 0x12; Bit 2 (0x04) = 1 -> 0x16
        self.assertEqual(
            EscPosLocalDrawerDriver.decode_dle_eot_1_byte(0x12, pin3_open_level=1),
            DrawerPhysicalState.CLOSED,
        )
        self.assertEqual(
            EscPosLocalDrawerDriver.decode_dle_eot_1_byte(0x16, pin3_open_level=1),
            DrawerPhysicalState.OPEN,
        )
        # Inverted polarity drawer (pin3_open_level=0)
        self.assertEqual(
            EscPosLocalDrawerDriver.decode_dle_eot_1_byte(0x12, pin3_open_level=0),
            DrawerPhysicalState.OPEN,
        )
        self.assertEqual(
            EscPosLocalDrawerDriver.decode_dle_eot_1_byte(0x16, pin3_open_level=0),
            DrawerPhysicalState.CLOSED,
        )
        # Bit 3 (0x08) = 1 -> Printer Offline (0x1A)
        self.assertEqual(
            EscPosLocalDrawerDriver.decode_dle_eot_1_byte(0x1A, pin3_open_level=1),
            DrawerPhysicalState.OFFLINE,
        )
        # Invalid frame byte (e.g. 0x00 or 0xFF) must raise ValueError
        with self.assertRaises(ValueError):
            EscPosLocalDrawerDriver.decode_dle_eot_1_byte(0x00)
        with self.assertRaises(ValueError):
            EscPosLocalDrawerDriver.decode_dle_eot_1_byte(0xFF)

    def test_calibration_rejects_static_2_wire_drawer(self) -> None:
        driver = EscPosLocalDrawerDriver(device_id="ESCPOS-01", hardware_verified=False)
        # Both closed and open return 0x12 (Pin 3 floating/static)
        res = driver.calibrate_pin3_polarity(closed_sample_byte=0x12, open_sample_byte=0x12)
        self.assertFalse(res["verified"])
        self.assertEqual(
            res["classification"], DeviceClassification.REQUIRES_EXTERNAL_SENSOR.value
        )
        self.assertFalse(driver.hardware_verified)

        # Valid 5-wire drawer where closed=0x12 and open=0x16
        res_ok = driver.calibrate_pin3_polarity(closed_sample_byte=0x12, open_sample_byte=0x16)
        self.assertTrue(res_ok["verified"])
        self.assertEqual(res_ok["pin3_open_level"], 1)
        self.assertEqual(
            res_ok["classification"], DeviceClassification.FULL_PHYSICAL_MONITORING.value
        )

    def test_command_only_driver_never_invents_open_or_closed(self) -> None:
        pulses = []
        driver = CommandOnlyDrawerDriver(
            device_id="CLOUD-ONLY-DRAWER",
            kick_callback=lambda: (pulses.append(1) or True),
        )
        driver.initialize()
        caps = driver.getCapabilities()
        self.assertEqual(caps.drawer_kick_capability, CapabilityResult.SUPPORTED)
        self.assertEqual(caps.physical_status_capability, CapabilityResult.UNSUPPORTED)

        open_res = driver.requestOpen(
            authorization_ref="AUTH-CMD-001",
            reason="cash_sale",
            cashier_id="CASHIER-1",
            now=1000.0,
        )
        self.assertTrue(open_res["accepted"])
        self.assertEqual(len(pulses), 1)
        # Critical rule AC-15: Physical state must remain UNSUPPORTED, never OPEN or CLOSED!
        self.assertEqual(open_res["physical_state"], "UNSUPPORTED")
        status = driver.getCurrentStatus(now=1001.0)
        self.assertEqual(status["physical_state"], "UNSUPPORTED")

    def test_duplicate_opening_and_cooldown_protection(self) -> None:
        sim = HardwareSimulatorDriver(device_id="SIM-01", cooldown_lock_seconds=2.0)
        sim.initialize()
        # Rejects missing authorization
        with self.assertRaises(PermissionError):
            sim.requestOpen(authorization_ref="", reason="cash_sale")

        res1 = sim.requestOpen(
            authorization_ref="AUTH-100",
            reason="cash_sale",
            now=5000.0,
        )
        self.assertTrue(res1["accepted"])

        # Duplicate authorization_ref is rejected idempotently
        res_dup = sim.requestOpen(
            authorization_ref="AUTH-100",
            reason="cash_sale",
            now=5010.0,
        )
        self.assertFalse(res_dup["accepted"])
        self.assertTrue(res_dup.get("duplicate"))

        # Different authorization_ref within 2.0s cooldown window is blocked
        res_cooldown = sim.requestOpen(
            authorization_ref="AUTH-101",
            reason="cash_sale",
            now=5000.5,
        )
        self.assertFalse(res_cooldown["accepted"])
        self.assertTrue(res_cooldown.get("cooldown_active"))

    def test_android_aidl_bridge_driver_tracks_open_counter_separately(self) -> None:
        hw_state = {"open": 0, "counter": 10}
        driver = SunmiAndroidBridgeDriver(
            device_id="SUNMI-T2S-01",
            open_drawer_fn=lambda: (
                hw_state.update({"counter": hw_state["counter"] + 1}) or True
            ),
            get_drawer_status_fn=lambda: hw_state["open"],
            get_open_times_fn=lambda: hw_state["counter"],
            open_status_value=1,
        )
        driver.initialize()
        self.assertEqual(driver.getHealth().sdk_open_counter, 10)

        # Initial observation CLOSED
        driver.getCurrentStatus(now=100.0)
        self.assertEqual(driver.getHealth().physical_state, DrawerPhysicalState.CLOSED)

        # Software open increments counter to 11, but physical latch hasn't opened yet!
        res = driver.requestOpen(authorization_ref="AUTH-AIDL-1", reason="cash_sale", now=105.0)
        self.assertTrue(res["accepted"])
        self.assertEqual(driver.getHealth().sdk_open_counter, 11)
        # Must NOT report OPEN until getDrawerStatus() actually reads 1
        self.assertEqual(driver.getHealth().physical_state, DrawerPhysicalState.CLOSED)

        # Now physical drawer opens
        hw_state["open"] = 1
        driver.getCurrentStatus(now=105.3)
        self.assertEqual(driver.getHealth().physical_state, DrawerPhysicalState.OPEN)


if __name__ == "__main__":
    unittest.main()

