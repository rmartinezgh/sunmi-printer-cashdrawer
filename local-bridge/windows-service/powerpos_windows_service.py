"""
PowerPOS Local Hardware Bridge — Windows Service Runner.
Runs `LocalBridgeSyncService` continuously as a background daemon on Windows POS hosts.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from python.bridge_daemon import LocalBridgeSyncService, OfflineEventStore
from python.cash_drawer_driver import EscPosLocalDrawerDriver


def run_windows_bridge(config_path: str) -> None:
    with open(config_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)

    driver = EscPosLocalDrawerDriver(
        device_id=cfg["device_id"],
        host=cfg.get("printer_host", "127.0.0.1"),
        port=int(cfg.get("printer_port", 9100)),
        pin3_open_level=int(cfg.get("pin3_open_level", 1)),
        hardware_verified=bool(cfg.get("hardware_verified", False)),
        polling_interval_ms=int(cfg.get("polling_interval_ms", 500)),
    )
    store = OfflineEventStore(db_path=cfg.get("sqlite_path", "powerpos_bridge_events.db"))
    service = LocalBridgeSyncService(
        driver=driver,
        store=store,
        odoo_base_url=cfg["odoo_base_url"],
        device_token=cfg["device_token"],
        device_secret=cfg["device_secret"],
    )
    driver.initialize()
    poll_sec = driver.polling_interval_ms / 1000.0
    last_sync = 0.0
    while True:
        driver.getCurrentStatus()
        now = time.time()
        if (now - last_sync) >= 2.0:
            service.sync_pending_events()
            last_sync = now
        time.sleep(poll_sec)


if __name__ == "__main__":
    cfg_file = os.environ.get("POWERPOS_BRIDGE_CONFIG", "bridge_config.json")
    run_windows_bridge(cfg_file)

