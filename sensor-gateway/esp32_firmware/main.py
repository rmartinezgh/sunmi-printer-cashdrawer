"""
PowerPOS — ESP32 MicroPython Cash Drawer Sensor Firmware (`main.py`).
Monitors dry-contact reed switch + tamper loop and signs every transition with HMAC-SHA256.
"""

import hashlib
import hmac
import json
import time


class Esp32DrawerSensorNode:
    """
    Pure-Python / MicroPython compatible sensor state monitor with debounce,
    tamper supervision, offline ring-buffer, and HMAC-SHA256 frame signing.
    """

    def __init__(
        self,
        device_id: str,
        shared_secret: str,
        debounce_ms: int = 50,
    ) -> None:
        self.device_id = device_id
        self.shared_secret = shared_secret
        self.debounce_ms = debounce_ms
        self.sequence_number = 0
        self.stable_contact_closed: bool = True
        self.tamper_loop_intact: bool = True
        self.offline_buffer: list[dict] = []

    def build_signed_frame(
        self,
        contact_closed: bool,
        tamper_loop_intact: bool,
        timestamp: float | None = None,
        nonce: str | None = None,
    ) -> dict:
        ts = float(timestamp if timestamp is not None else time.time())
        nc = nonce or f"nc-{self.sequence_number + 1}-{int(ts * 1000)}"
        self.sequence_number += 1
        payload_str = (
            f"{self.device_id}:{int(contact_closed)}:{int(tamper_loop_intact)}:{int(ts)}:{nc}"
        )
        sig = hmac.new(
            self.shared_secret.encode("utf-8"),
            payload_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        frame = {
            "device_id": self.device_id,
            "sequence_number": self.sequence_number,
            "contact_closed": contact_closed,
            "tamper_loop_intact": tamper_loop_intact,
            "timestamp": ts,
            "nonce": nc,
            "signature": sig,
        }
        self.offline_buffer.append(frame)
        return frame

