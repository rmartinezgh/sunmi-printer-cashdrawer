"""
Unit Tests for Module B — SUNMI Cloud Printer SDK & Philippine BIR Receipt Builder.
Covers Section 7 and Section 21.1 testing requirements.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "local-bridge"))

from python.sunmi_cloud_sdk import (
    BirComplianceFields,
    EscPosReceiptBuilder,
    MerchantReceiptLayout,
    PhilippineBirReceiptFormatter,
    PrintJobState,
    ReceiptType,
    SunmiCloudPrinterSDK,
)


class TestSunmiCloudPrinterSDK(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.mock_responses: List[Any] = []

        def mock_transport(url: str, headers: Dict[str, str], body: bytes, timeout: float) -> Dict[str, Any]:
            self.calls.append(
                {
                    "url": url,
                    "headers": headers,
                    "body": json.loads(body.decode("utf-8")),
                    "raw_body": body.decode("utf-8"),
                }
            )
            if self.mock_responses:
                nxt = self.mock_responses.pop(0)
                if isinstance(nxt, Exception):
                    raise nxt
                return nxt
            return {"code": "1", "msg": "success", "data": {"ticket_status": 1}}

        self.sdk = SunmiCloudPrinterSDK(
            app_id="SUNMI_TEST_APP_ID",
            app_key="SUNMI_SUPER_SECRET_KEY_2026",
            http_transport=mock_transport,
        )

    def test_hmac_sha256_signature_and_headers(self) -> None:
        body = '{"sn":"NT311-001"}'
        headers = self.sdk.build_signed_headers(body, timestamp="1700000000", nonce="123456")
        self.assertEqual(headers["Sunmi-Appid"], "SUNMI_TEST_APP_ID")
        self.assertEqual(headers["Sunmi-Timestamp"], "1700000000")
        self.assertEqual(headers["Sunmi-Nonce"], "123456")
        self.assertEqual(len(headers["Sunmi-Sign"]), 64)
        self.assertTrue(
            self.sdk.verify_webhook_signature(body, "1700000000", "123456", headers["Sunmi-Sign"])
        )
        self.assertFalse(
            self.sdk.verify_webhook_signature(body, "1700000000", "999999", headers["Sunmi-Sign"])
        )

    def test_all_eight_cloud_sdk_methods(self) -> None:
        self.sdk.bindShop("NT311-SN01", 101)
        self.sdk.unbindShop("NT311-SN01", 101)
        self.sdk.onlineStatus("NT311-SN01")
        with self.assertRaises(PermissionError):
            self.sdk.clearPrintJob("NT311-SN01", authorized_by="")
        self.sdk.clearPrintJob("NT311-SN01", authorized_by="SUPERVISOR-01")
        self.sdk.pushVoice("NT311-SN01", "New order received")
        self.sdk.pushContent("NT311-SN01", "JOB-001", "1B4048656C6C6F0A")
        self.sdk.printStatus("JOB-001")

        self.assertEqual(len(self.calls), 7)
        webhook_body = json.dumps({"trade_no": "JOB-001", "status": "PRINTED"})
        sig = self.sdk.compute_signature(webhook_body, "1700000100", "654321")
        notify_res = self.sdk.newTicketNotify(webhook_body, "1700000100", "654321", sig)
        self.assertTrue(notify_res["accepted"])

    def test_ambiguous_timeout_reconciliation_prevents_duplicate_print(self) -> None:
        # 1st attempt times out after server actually accepted the job
        self.mock_responses.append(TimeoutError("Read timed out after 10s"))
        job = self.sdk.submit_or_reconcile_job(
            job_id="TX-9001",
            printer_sn="NT311-SN01",
            receipt_type=ReceiptType.OFFICIAL_SALES,
            content_hex="1B40",
        )
        self.assertEqual(job.state, PrintJobState.UNKNOWN)
        self.assertEqual(job.attempts, 1)

        # Retry calls submit_or_reconcile_job -> queries printStatus first!
        # Remote API reports ticket_status = 1 (already printed)
        self.mock_responses.append({"code": "1", "data": {"ticket_status": 1}})
        reconciled = self.sdk.submit_or_reconcile_job(
            job_id="TX-9001",
            printer_sn="NT311-SN01",
            receipt_type=ReceiptType.OFFICIAL_SALES,
            content_hex="1B40",
        )
        self.assertEqual(reconciled.state, PrintJobState.PRINTED)
        # Verify pushContent was NOT called a second time (attempts remains 1)
        self.assertEqual(reconciled.attempts, 1)

    def test_cloud_sdk_never_fabricates_physical_drawer_status(self) -> None:
        status = self.sdk.get_physical_drawer_status()
        self.assertEqual(status["physical_state"], "UNSUPPORTED")
        self.assertEqual(status["capability"], "UNSUPPORTED")

    def test_all_14_philippine_bir_receipt_types_and_escpos_commands(self) -> None:
        bir = BirComplianceFields(
            taxpayer_name="POWERPOS RETAIL CORP.",
            vat_reg_tin="123-456-789",
            branch_code="00000",
            machine_identification_number="MIN-202600012345",
            permit_to_use_number="FP012026-001-0001234-00000",
            ptu_date_issued="2026-01-10",
            terminal_serial_number="SUNMI-T2S-998877",
        )
        layout = MerchantReceiptLayout(
            trade_name="POWERPOS SUPERMARKET",
            store_address_lines=["Ayala Ave, Makati City, Metro Manila"],
        )
        tx = {
            "document_number": "SI-00004512",
            "timestamp": "2026-11-07 21:00:00",
            "terminal_name": "REG-01",
            "cashier_name": "Maria Santos",
            "lines": [{"name": "Organic Brewed Coffee", "qty": 2, "price_unit": 112.0}],
            "amount_total": 224.0,
            "payment_method": "CASH",
            "amount_tendered": 300.0,
            "change": 76.0,
            "verification_url": "https://pos.powerpos.local/verify/SI-00004512",
        }
        for r_type in ReceiptType:
            builder = PhilippineBirReceiptFormatter.build_receipt(
                receipt_type=r_type,
                bir=bir,
                layout=layout,
                transaction=tx,
                open_drawer_pulse=(r_type == ReceiptType.CASH_RECEIPT),
            )
            raw = builder.to_bytes()
            self.assertTrue(raw.startswith(b"\x1b\x40"))  # ESC @
            self.assertIn(b"MIN-202600012345", raw)
            if r_type == ReceiptType.CASH_RECEIPT:
                self.assertIn(b"\x1b\x70\x00\x32\x96", raw)  # Drawer kick pulse


if __name__ == "__main__":
    unittest.main()

