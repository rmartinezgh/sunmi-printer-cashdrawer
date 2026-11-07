"""
PowerPOS — SUNMI Cloud Printer OpenAPI SDK & Philippine BIR ESC/POS Builder.

Implements the verified SUNMI Cloud Printer OpenAPI (`https://openapi.sunmi.com`):
  - bindShop()
  - unbindShop()
  - onlineStatus()
  - clearPrintJob()
  - pushVoice()
  - pushContent()
  - printStatus()
  - newTicketNotify() (Webhook signature & payload verifier)

Architectural Safety Rule:
  The SUNMI Cloud Printer OpenAPI does NOT expose a callable physical cash drawer
  status endpoint, and `pushContent()` does not return raw `DLE EOT 1` (`10 04 01`)
  response bytes. This SDK never fabricates physical OPEN or CLOSED states.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class PrintJobState(str, Enum):
    PENDING = "Pending"
    SUBMITTED = "Submitted"
    ACCEPTED = "Accepted"
    PRINTED = "Printed"
    FAILED = "Failed"
    UNKNOWN = "Unknown"
    CANCELLED = "Cancelled"


class ReceiptType(str, Enum):
    OFFICIAL_SALES = "official_sales"
    CASH_RECEIPT = "cash_receipt"
    CARD_RECEIPT = "card_receipt"
    QR_RECEIPT = "qr_receipt"
    REFUND_RECEIPT = "refund_receipt"
    VOID_RECORD = "void_record"
    REPRINT_RECEIPT = "reprint_receipt"
    CASH_IN = "cash_in"
    CASH_OUT = "cash_out"
    X_REPORT = "x_report"
    Z_REPORT = "z_report"
    EOD_SUMMARY = "eod_summary"
    KITCHEN_ORDER_TICKET = "kot"
    CUSTOMER_ORDER_NUMBER = "customer_order_number"


@dataclass(frozen=True)
class BirComplianceFields:
    """
    Mandatory Philippine BIR (Bureau of Internal Revenue) compliance metadata.
    Separated strictly from merchant-customizable receipt layout settings.
    Note: Software receipt templates alone do not constitute BIR PTU/CRM/POS
    accreditation; hardware and taxpayer registration must be independently verified.
    """

    taxpayer_name: str
    vat_reg_tin: str
    branch_code: str
    machine_identification_number: str  # MIN
    permit_to_use_number: str           # PTU No.
    ptu_date_issued: str
    terminal_serial_number: str
    is_vat_registered: bool = True
    accreditation_provider_name: str = "PowerPOS Enterprise Solutions Inc."
    accreditation_tin: str = "000-000-000-00000"
    accreditation_number: str = "ACC-PPOS-2026-0001"
    accreditation_date_issued: str = "2026-01-15"
    accreditation_valid_until: str = "2031-01-15"

    def validate(self) -> None:
        if not self.taxpayer_name or not self.vat_reg_tin:
            raise ValueError("BIR mandatory taxpayer_name and vat_reg_tin are required.")
        if not self.machine_identification_number or not self.permit_to_use_number:
            raise ValueError("BIR mandatory MIN and PTU numbers are required.")
        if not self.terminal_serial_number:
            raise ValueError("BIR mandatory terminal_serial_number is required.")


@dataclass
class MerchantReceiptLayout:
    """Merchant-customizable branding and layout settings."""

    trade_name: str
    store_address_lines: List[str] = field(default_factory=list)
    contact_phone: str = ""
    custom_header_lines: List[str] = field(default_factory=list)
    custom_footer_lines: List[str] = field(
        default_factory=lambda: ["Thank you for shopping with us!"]
    )
    paper_width_mm: int = 80
    print_qr_verification: bool = True

    @property
    def chars_per_line(self) -> int:
        return 48 if self.paper_width_mm >= 80 else 32


class EscPosReceiptBuilder:
    """
    Generates verified ESC/POS binary and hexadecimal byte streams for SUNMI
    Cloud Printers (`pushContent`) and local ESC/POS printers.
    """

    ESC = b"\x1b"
    GS = b"\x1d"
    LF = b"\x0a"

    def __init__(self, chars_per_line: int = 48) -> None:
        self.chars_per_line = chars_per_line
        self._buffer = bytearray()
        self.init_printer()

    def init_printer(self) -> "EscPosReceiptBuilder":
        self._buffer.extend(b"\x1b\x40")  # ESC @ Initialize printer
        return self

    def align(self, mode: str = "left") -> "EscPosReceiptBuilder":
        mapping = {"left": 0, "center": 1, "right": 2}
        n = mapping.get(mode.lower(), 0)
        self._buffer.extend(bytes([0x1B, 0x61, n]))
        return self

    def bold(self, enabled: bool = True) -> "EscPosReceiptBuilder":
        self._buffer.extend(bytes([0x1B, 0x45, 1 if enabled else 0]))
        return self

    def text_size(self, width_mult: int = 1, height_mult: int = 1) -> "EscPosReceiptBuilder":
        w = max(1, min(width_mult, 8)) - 1
        h = max(1, min(height_mult, 8)) - 1
        n = (w << 4) | h
        self._buffer.extend(bytes([0x1D, 0x21, n]))
        return self

    def text_line(self, text: str = "") -> "EscPosReceiptBuilder":
        self._buffer.extend(text.encode("utf-8", errors="replace"))
        self._buffer.extend(self.LF)
        return self

    def separator(self, char: str = "-") -> "EscPosReceiptBuilder":
        return self.text_line((char[0] if char else "-") * self.chars_per_line)

    def key_value_line(self, left: str, right: str) -> "EscPosReceiptBuilder":
        space_count = max(1, self.chars_per_line - len(left) - len(right))
        return self.text_line(f"{left}{' ' * space_count}{right}")

    def qr_code(self, data: str, module_size: int = 6) -> "EscPosReceiptBuilder":
        raw = data.encode("utf-8")
        store_len = len(raw) + 3
        p_l = store_len & 0xFF
        p_h = (store_len >> 8) & 0xFF
        # Function 165: Select QR model 2
        self._buffer.extend(b"\x1d\x28\x6b\x04\x00\x31\x41\x32\x00")
        # Function 167: Set module size
        self._buffer.extend(bytes([0x1D, 0x28, 0x6B, 0x03, 0x00, 0x31, 0x43, max(3, min(module_size, 16))]))
        # Function 169: Error correction level M (49)
        self._buffer.extend(b"\x1d\x28\x6b\x03\x00\x31\x45\x31")
        # Function 180: Store data
        self._buffer.extend(bytes([0x1D, 0x28, 0x6B, p_l, p_h, 0x31, 0x50, 0x30]) + raw)
        # Function 181: Print QR
        self._buffer.extend(b"\x1d\x28\x6b\x03\x00\x31\x51\x30")
        self._buffer.extend(self.LF)
        return self

    def barcode_code128(self, code: str, height: int = 64) -> "EscPosReceiptBuilder":
        raw = code.encode("ascii", errors="ignore")
        self._buffer.extend(bytes([0x1D, 0x68, max(20, min(height, 255))]))  # GS h
        self._buffer.extend(b"\x1d\x48\x02")  # HRI below barcode
        self._buffer.extend(bytes([0x1D, 0x6B, 73, len(raw)]) + raw)  # CODE128 (m=73)
        self._buffer.extend(self.LF)
        return self

    def append_drawer_kick_pulse(
        self, pin: int = 0, on_time_units: int = 0x32, off_time_units: int = 0x96
    ) -> "EscPosReceiptBuilder":
        """
        Appends `ESC p m t1 t2` (`1B 70 m t1 t2`) to energize the cash drawer solenoid.
        WARNING: This is a write-only kick command. It does NOT confirm physical opening.
        """
        m = 0 if pin == 0 else 1
        self._buffer.extend(bytes([0x1B, 0x70, m, on_time_units & 0xFF, off_time_units & 0xFF]))
        return self

    def feed_and_cut(self, lines: int = 4) -> "EscPosReceiptBuilder":
        self._buffer.extend(self.LF * max(1, lines))
        self._buffer.extend(b"\x1d\x56\x42\x00")  # GS V 66 0 (Partial cut with feed)
        return self

    def to_bytes(self) -> bytes:
        return bytes(self._buffer)

    def to_hex(self) -> str:
        return self._buffer.hex().upper()


class PhilippineBirReceiptFormatter:
    """
    Renders all 14 supported PowerPOS receipt types while enforcing separation
    between mandatory BIR tax/machine compliance blocks and merchant branding.
    """

    RECEIPT_TITLES: Dict[ReceiptType, str] = {
        ReceiptType.OFFICIAL_SALES: "SALES INVOICE / OFFICIAL RECEIPT",
        ReceiptType.CASH_RECEIPT: "CASH SALES RECEIPT",
        ReceiptType.CARD_RECEIPT: "CARD PAYMENT RECEIPT",
        ReceiptType.QR_RECEIPT: "QR PH PAYMENT RECEIPT",
        ReceiptType.REFUND_RECEIPT: "REFUND / CREDIT NOTE",
        ReceiptType.VOID_RECORD: "VOIDED TRANSACTION RECORD",
        ReceiptType.REPRINT_RECEIPT: "*** REPRINT COPY ***",
        ReceiptType.CASH_IN: "POS CASH-IN VOUCHER",
        ReceiptType.CASH_OUT: "POS CASH-OUT VOUCHER",
        ReceiptType.X_REPORT: "X-READING REPORT (MID-SHIFT)",
        ReceiptType.Z_REPORT: "Z-READING REPORT (END OF DAY)",
        ReceiptType.EOD_SUMMARY: "END-OF-DAY BRANCH SUMMARY",
        ReceiptType.KITCHEN_ORDER_TICKET: "KITCHEN ORDER TICKET (KOT)",
        ReceiptType.CUSTOMER_ORDER_NUMBER: "CUSTOMER QUEUE NUMBER",
    }

    @classmethod
    def build_receipt(
        cls,
        receipt_type: ReceiptType,
        bir: BirComplianceFields,
        layout: MerchantReceiptLayout,
        transaction: Dict[str, Any],
        open_drawer_pulse: bool = False,
    ) -> EscPosReceiptBuilder:
        bir.validate()
        builder = EscPosReceiptBuilder(chars_per_line=layout.chars_per_line)

        # 1. Merchant Header + Mandatory BIR Header
        builder.align("center").bold(True).text_size(1, 2)
        builder.text_line(layout.trade_name)
        builder.text_size(1, 1).bold(False)
        builder.text_line(bir.taxpayer_name)
        for addr_line in layout.store_address_lines:
            builder.text_line(addr_line)
        tin_prefix = "VAT REG TIN" if bir.is_vat_registered else "NON-VAT REG TIN"
        builder.text_line(f"{tin_prefix}: {bir.vat_reg_tin}-{bir.branch_code}")
        builder.text_line(f"MIN: {bir.machine_identification_number}")
        builder.text_line(f"S/N: {bir.terminal_serial_number}")
        for h_line in layout.custom_header_lines:
            builder.text_line(h_line)

        builder.separator("=")
        title = cls.RECEIPT_TITLES.get(receipt_type, receipt_type.value.upper())
        builder.align("center").bold(True).text_line(title).bold(False)
        if receipt_type == ReceiptType.REPRINT_RECEIPT or transaction.get("is_reprint"):
            builder.bold(True).text_line("*** REPRINTED DOCUMENT ***").bold(False)
        builder.separator("-")

        # 2. Metadata
        builder.align("left")
        builder.key_value_line("Doc/SI No.:", str(transaction.get("document_number", "00000001")))
        builder.key_value_line("Date/Time:", str(transaction.get("timestamp", "")))
        builder.key_value_line("Terminal:", str(transaction.get("terminal_name", "POS-01")))
        builder.key_value_line("Cashier:", str(transaction.get("cashier_name", "Cashier")))
        if transaction.get("session_name"):
            builder.key_value_line("Session:", str(transaction["session_name"]))
        builder.separator("-")

        # 3. Body depending on receipt type
        if receipt_type == ReceiptType.CUSTOMER_ORDER_NUMBER:
            builder.align("center").bold(True).text_size(2, 2)
            builder.text_line(f"#{transaction.get('queue_number', '001')}")
            builder.text_size(1, 1).bold(False)
        elif receipt_type in (ReceiptType.X_REPORT, ReceiptType.Z_REPORT, ReceiptType.EOD_SUMMARY):
            builder.key_value_line("Opening Fund:", f"PHP {float(transaction.get('opening_cash', 0.0)):,.2f}")
            builder.key_value_line("Gross Sales:", f"PHP {float(transaction.get('gross_sales', 0.0)):,.2f}")
            builder.key_value_line("Total Refunds:", f"PHP {float(transaction.get('total_refunds', 0.0)):,.2f}")
            builder.key_value_line("Net Sales:", f"PHP {float(transaction.get('net_sales', 0.0)):,.2f}")
            if receipt_type == ReceiptType.Z_REPORT:
                builder.key_value_line("Old Grand Total:", f"PHP {float(transaction.get('old_grand_total', 0.0)):,.2f}")
                builder.key_value_line("New Grand Total:", f"PHP {float(transaction.get('new_grand_total', 0.0)):,.2f}")
                builder.key_value_line("Z-Counter:", str(transaction.get("z_counter", 1)))
        elif receipt_type in (ReceiptType.CASH_IN, ReceiptType.CASH_OUT):
            builder.key_value_line("Reason:", str(transaction.get("reason", "Cash Adjustment")))
            builder.bold(True)
            builder.key_value_line("Amount:", f"PHP {float(transaction.get('amount', 0.0)):,.2f}")
            builder.bold(False)
        else:
            for item in transaction.get("lines", []):
                qty = item.get("qty", 1)
                name = str(item.get("name", "Item"))
                price = float(item.get("price_unit", 0.0))
                line_total = float(item.get("line_total", qty * price))
                builder.text_line(name[: layout.chars_per_line])
                builder.key_value_line(f"  {qty} x {price:,.2f}", f"{line_total:,.2f}")

            builder.separator("-")
            total = float(transaction.get("amount_total", 0.0))
            vatable = float(transaction.get("vatable_sales", round(total / 1.12, 2)))
            vat_amt = float(transaction.get("vat_amount", round(total - vatable, 2)))
            vat_exempt = float(transaction.get("vat_exempt_sales", 0.0))
            zero_rated = float(transaction.get("zero_rated_sales", 0.0))

            builder.bold(True).key_value_line("TOTAL AMOUNT:", f"PHP {total:,.2f}").bold(False)
            builder.key_value_line("Payment Method:", str(transaction.get("payment_method", "CASH")))
            builder.key_value_line("Amount Tendered:", f"PHP {float(transaction.get('amount_tendered', total)):,.2f}")
            builder.key_value_line("Change:", f"PHP {float(transaction.get('change', 0.0)):,.2f}")
            builder.separator("-")
            builder.key_value_line("VATable Sales:", f"{vatable:,.2f}")
            builder.key_value_line("VAT Amount (12%):", f"{vat_amt:,.2f}")
            builder.key_value_line("VAT-Exempt Sales:", f"{vat_exempt:,.2f}")
            builder.key_value_line("Zero-Rated Sales:", f"{zero_rated:,.2f}")

        # 4. Customer TIN / Buyer Details + Mandatory BIR PTU & Provider Block
        if receipt_type not in (ReceiptType.KITCHEN_ORDER_TICKET, ReceiptType.CUSTOMER_ORDER_NUMBER):
            builder.separator("-")
            buyer_name = transaction.get("buyer_name", "__________________________")
            buyer_tin = transaction.get("buyer_tin", "__________________________")
            builder.text_line(f"Sold To: {buyer_name}")
            builder.text_line(f"TIN:     {buyer_tin}")
            builder.separator("-")
            builder.align("center")
            for f_line in layout.custom_footer_lines:
                builder.text_line(f_line)
            if layout.print_qr_verification and transaction.get("verification_url"):
                builder.qr_code(str(transaction["verification_url"]), module_size=5)
            builder.text_line(f"POS Provider: {bir.accreditation_provider_name}")
            builder.text_line(f"Provider TIN: {bir.accreditation_tin}")
            builder.text_line(f"Accred. No.: {bir.accreditation_number}")
            builder.text_line(f"PTU No.: {bir.permit_to_use_number} ({bir.ptu_date_issued})")

        if open_drawer_pulse:
            builder.append_drawer_kick_pulse()

        builder.feed_and_cut(4)
        return builder


@dataclass
class TrackedPrintJob:
    job_id: str
    printer_sn: str
    receipt_type: ReceiptType
    content_hex: str
    state: PrintJobState = PrintJobState.PENDING
    attempts: int = 0
    last_error: Optional[str] = None
    remote_trade_no: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class SunmiCloudPrinterSDK:
    """
    Production SUNMI Cloud Printer OpenAPI Client (`https://openapi.sunmi.com`).
    Implements HMAC-SHA256 signing, all 8 Cloud Printer SDK methods, and
    ambiguous print job reconciliation before retry.
    """

    DEFAULT_BASE_URL = "https://openapi.sunmi.com"

    ENDPOINT_BIND_SHOP = "/v2/printer/open/open/device/bindShop"
    ENDPOINT_UNBIND_SHOP = "/v2/printer/open/open/device/unbindShop"
    ENDPOINT_ONLINE_STATUS = "/v2/printer/open/open/device/onlineStatus"
    ENDPOINT_CLEAR_PRINT_JOB = "/v2/printer/open/open/device/clearPrintJob"
    ENDPOINT_PUSH_VOICE = "/v2/printer/open/open/device/pushVoice"
    ENDPOINT_PUSH_CONTENT = "/v2/printer/open/open/device/pushContent"
    ENDPOINT_PRINT_STATUS = "/v2/printer/open/open/device/ticketStatus"

    def __init__(
        self,
        app_id: str,
        app_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 10.0,
        http_transport: Optional[Callable[[str, Dict[str, str], bytes, float], Dict[str, Any]]] = None,
    ) -> None:
        if not app_id or not app_key:
            raise ValueError("SUNMI Cloud app_id and app_key must be non-empty.")
        self._app_id = app_id
        self._app_key = app_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._http_transport = http_transport or self._default_http_post
        self._jobs: Dict[str, TrackedPrintJob] = {}

    @staticmethod
    def generate_nonce(length: int = 6) -> str:
        """Generates a random numeric/hex nonce as required by `Sunmi-Nonce`."""
        return "".join(str(secrets.randbelow(10)) for _ in range(length))

    def compute_signature(self, json_body_str: str, timestamp: str, nonce: str) -> str:
        """
        Computes SUNMI OpenAPI HMAC-SHA256 signature:
        `HMAC-SHA256(json_body + app_id + timestamp + nonce, app_key).hexdigest().lower()`
        """
        message = f"{json_body_str}{self._app_id}{timestamp}{nonce}".encode("utf-8")
        return hmac.new(
            self._app_key.encode("utf-8"),
            message,
            hashlib.sha256,
        ).hexdigest().lower()

    def build_signed_headers(
        self, json_body_str: str, timestamp: Optional[str] = None, nonce: Optional[str] = None
    ) -> Dict[str, str]:
        ts = timestamp or str(int(time.time()))
        nc = nonce or self.generate_nonce(6)
        sign = self.compute_signature(json_body_str, ts, nc)
        return {
            "Content-Type": "application/json; charset=utf-8",
            "Sunmi-Appid": self._app_id,
            "Sunmi-Timestamp": ts,
            "Sunmi-Nonce": nc,
            "Sunmi-Sign": sign,
            "Source": "openapi",
        }

    def verify_webhook_signature(
        self, raw_body: str, timestamp: str, nonce: str, received_signature: str
    ) -> bool:
        expected = self.compute_signature(raw_body, timestamp, nonce)
        return hmac.compare_digest(expected, (received_signature or "").lower())

    @staticmethod
    def _default_http_post(
        url: str, headers: Dict[str, str], body_bytes: bytes, timeout: float
    ) -> Dict[str, Any]:
        req = urllib.request.Request(url=url, data=body_bytes, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    def _post(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        body_str = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        headers = self.build_signed_headers(body_str)
        url = f"{self._base_url}{endpoint}"
        return self._http_transport(url, headers, body_str.encode("utf-8"), self._timeout)

    # -------------------------------------------------------------------------
    # Mandatory 8 SUNMI Cloud Printer SDK Functions
    # -------------------------------------------------------------------------

    def bindShop(self, sn: str, shop_id: int) -> Dict[str, Any]:
        """Binds a SUNMI cloud printer serial number to a shop ID."""
        return self._post(self.ENDPOINT_BIND_SHOP, {"sn": sn, "shop_id": shop_id})

    def unbindShop(self, sn: str, shop_id: int) -> Dict[str, Any]:
        """Unbinds a SUNMI cloud printer from a shop ID."""
        return self._post(self.ENDPOINT_UNBIND_SHOP, {"sn": sn, "shop_id": shop_id})

    def onlineStatus(self, sn: Optional[str] = None, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """
        Queries printer connectivity status (`onlineStatus`).
        NOTE: Returns printer network status only, NEVER cash drawer physical status.
        """
        payload: Dict[str, Any] = {"page_no": page, "page_size": page_size}
        if sn:
            payload["sn"] = sn
        return self._post(self.ENDPOINT_ONLINE_STATUS, payload)

    def clearPrintJob(self, sn: str, authorized_by: str) -> Dict[str, Any]:
        """Clears pending print jobs in the printer queue (requires explicit authorization)."""
        if not authorized_by:
            raise PermissionError("Clearing the printer queue requires an authorized user reference.")
        return self._post(self.ENDPOINT_CLEAR_PRINT_JOB, {"sn": sn})

    def pushVoice(
        self, sn: str, content: str, cycle: int = 1, interval: int = 2, expire_in: int = 300
    ) -> Dict[str, Any]:
        """Pushes voice broadcast notification to the printer."""
        return self._post(
            self.ENDPOINT_PUSH_VOICE,
            {
                "sn": sn,
                "content": content,
                "cycle": cycle,
                "interval": interval,
                "expire_in": expire_in,
            },
        )

    def pushContent(
        self,
        sn: str,
        trade_no: str,
        content_hex: str,
        count: int = 1,
        order_type: int = 1,
        media_text: str = "",
    ) -> Dict[str, Any]:
        """
        Submits ESC/POS hex content to the SUNMI Cloud Printer.
        NOTE: `pushContent` is unidirectional; it does NOT return `DLE EOT 1` bytes.
        """
        payload: Dict[str, Any] = {
            "trade_no": trade_no,
            "sn": sn,
            "order_type": order_type,
            "content": content_hex,
            "count": count,
        }
        if media_text:
            payload["media_text"] = media_text
        return self._post(self.ENDPOINT_PUSH_CONTENT, payload)

    def printStatus(self, trade_no: str) -> Dict[str, Any]:
        """Queries the execution state of a submitted print job (`trade_no`)."""
        return self._post(self.ENDPOINT_PRINT_STATUS, {"trade_no": trade_no})

    def newTicketNotify(
        self, raw_body: str, timestamp: str, nonce: str, signature: str
    ) -> Dict[str, Any]:
        """
        Processes and validates an incoming `newTicketNotify` webhook from SUNMI Cloud.
        Updates local tracked job state if matching `trade_no` exists.
        """
        if not self.verify_webhook_signature(raw_body, timestamp, nonce, signature):
            raise PermissionError("Invalid SUNMI webhook HMAC signature.")
        data = json.loads(raw_body)
        trade_no = data.get("trade_no")
        status_code = data.get("status")
        if trade_no and trade_no in self._jobs:
            job = self._jobs[trade_no]
            if status_code in (1, "1", "SUCCESS", "PRINTED"):
                job.state = PrintJobState.PRINTED
            elif status_code in (2, "2", "FAILED", "ERROR"):
                job.state = PrintJobState.FAILED
            job.updated_at = time.time()
        return {"accepted": True, "trade_no": trade_no, "status": status_code}

    # -------------------------------------------------------------------------
    # Idempotent Queue & Ambiguous Print Reconciliation
    # -------------------------------------------------------------------------

    def submit_or_reconcile_job(
        self,
        job_id: str,
        printer_sn: str,
        receipt_type: ReceiptType,
        content_hex: str,
    ) -> TrackedPrintJob:
        """
        Prevents duplicate receipts caused by network timeouts:
        - If a job previously timed out (`UNKNOWN`), calls `printStatus(job_id)` first
          to reconcile whether the remote API accepted/printed it before re-submitting.
        """
        job = self._jobs.get(job_id)
        if job is None:
            job = TrackedPrintJob(
                job_id=job_id,
                printer_sn=printer_sn,
                receipt_type=receipt_type,
                content_hex=content_hex,
                remote_trade_no=job_id,
            )
            self._jobs[job_id] = job

        if job.state in (PrintJobState.ACCEPTED, PrintJobState.PRINTED):
            return job

        if job.state == PrintJobState.UNKNOWN:
            reconciled = self.reconcile_job(job_id)
            if reconciled.state in (PrintJobState.ACCEPTED, PrintJobState.PRINTED):
                return reconciled

        job.attempts += 1
        job.state = PrintJobState.SUBMITTED
        job.updated_at = time.time()
        try:
            resp = self.pushContent(sn=printer_sn, trade_no=job_id, content_hex=content_hex)
            code = str(resp.get("code", ""))
            if code in ("1", "0", "10000", "200"):
                job.state = PrintJobState.ACCEPTED
                job.last_error = None
            else:
                job.state = PrintJobState.FAILED
                job.last_error = str(resp.get("msg") or resp.get("message") or f"API error {code}")
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            # Ambiguous outcome: remote server may have accepted the job before timeout!
            job.state = PrintJobState.UNKNOWN
            job.last_error = f"Ambiguous timeout/transport error: {exc}"
        job.updated_at = time.time()
        return job

    def reconcile_job(self, job_id: str) -> TrackedPrintJob:
        """Queries `printStatus` to resolve an `UNKNOWN` or `ACCEPTED` job."""
        job = self._jobs.get(job_id)
        if not job:
            raise KeyError(f"Unknown print job_id: {job_id}")
        try:
            resp = self.printStatus(trade_no=job_id)
            data = resp.get("data") or {}
            ticket_status = data.get("ticket_status", resp.get("status"))
            if ticket_status in (1, "1", "PRINTED", "SUCCESS"):
                job.state = PrintJobState.PRINTED
                job.last_error = None
            elif ticket_status in (0, "0", "QUEUEING", "ACCEPTED", "PENDING"):
                job.state = PrintJobState.ACCEPTED
            elif ticket_status in (2, "2", "FAILED"):
                job.state = PrintJobState.FAILED
            elif resp.get("code") in ("30001", "NOT_FOUND"):
                # Remote API confirms it never received the job; safe to retry
                job.state = PrintJobState.PENDING
        except Exception as exc:
            job.last_error = f"Reconciliation check failed: {exc}"
        job.updated_at = time.time()
        return job

    def get_physical_drawer_status(self) -> Dict[str, Any]:
        """
        Explicit safety gate: SUNMI Cloud OpenAPI does NOT provide physical drawer status.
        """
        return {
            "physical_state": "UNSUPPORTED",
            "capability": "UNSUPPORTED",
            "reason": (
                "SUNMI Cloud OpenAPI (https://openapi.sunmi.com) does not expose a "
                "physical cash drawer sensor endpoint, and pushContent() does not "
                "return DLE EOT 1 response bytes. Use Local Printer Bridge or External "
                "Sensor Gateway for physical status monitoring."
            ),
        }

