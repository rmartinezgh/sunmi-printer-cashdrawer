"""
PowerPOS — Physical Cash Drawer Status Monitor & Driver Abstraction (Module E).

Implements the unified `CashDrawerDriver` contract:
    initialize()
    getCapabilities()
    getCurrentStatus()
    requestOpen()
    subscribeStatusChanges()
    getHealth()
    disconnect()

Adapters implemented:
  1. EscPosLocalDrawerDriver      (Bidirectional `DLE EOT 1` / `0x10 0x04 0x01`)
  2. ExternalSensorGatewayDriver  (ESP32 magnetic reed switch / dry-contact gateway)
  3. SunmiAndroidBridgeDriver     (SUNMI Android Woyou AIDL bridge telemetry)
  4. CommandOnlyDrawerDriver      (Solenoid kick only; physical status = UNSUPPORTED)
  5. HardwareSimulatorDriver      (Development simulator; explicitly tagged `simulator`)
"""

from __future__ import annotations

import abc
import hashlib
import hmac
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


class DrawerPhysicalState(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"
    UNSUPPORTED = "UNSUPPORTED"
    STALE = "STALE"
    ERROR = "ERROR"
    OFFLINE = "OFFLINE"


class CapabilityResult(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNVERIFIED = "UNVERIFIED"
    ERROR = "ERROR"


class DeviceClassification(str, Enum):
    FULL_PHYSICAL_MONITORING = "full_physical_monitoring"
    OPENING_CONTROL_ONLY = "opening_control_only"
    PRINTER_CONNECTIVITY_ONLY = "printer_connectivity_only"
    REQUIRES_EXTERNAL_SENSOR = "requires_external_sensor"
    UNSUPPORTED = "unsupported"
    NOT_YET_VERIFIED = "not_yet_verified"


class DrawerEventType(str, Enum):
    OPEN_REQUESTED = "open_requested"
    COMMAND_ACCEPTED = "command_accepted"
    INITIAL_OBSERVATION = "initial_observation"
    PHYSICAL_OPENED = "physical_opened"
    PHYSICAL_CLOSED = "physical_closed"
    MANUAL_OPENING = "manual_opening"
    UNMATCHED_OPENING = "unmatched_opening"
    OPENING_TIMEOUT = "opening_timeout"
    PRINTER_DISCONNECTED = "printer_disconnected"
    SENSOR_DISCONNECTED = "sensor_disconnected"
    COMMUNICATION_RESTORED = "communication_restored"
    HARDWARE_ERROR = "hardware_error"
    SUPERVISOR_OVERRIDE = "supervisor_override"


@dataclass
class AuthorizationContext:
    authorization_ref: str
    reason: str
    cashier_id: Optional[str] = None
    pos_session_id: Optional[str] = None
    pos_order_ref: Optional[str] = None
    supervisor_id: Optional[str] = None
    issued_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 10.0)
    consumed: bool = False

    def is_valid_at(self, timestamp: float) -> bool:
        return (not self.consumed) and (self.issued_at - 1.0 <= timestamp <= self.expires_at)


@dataclass
class DrawerTransitionEvent:
    event_uuid: str
    sequence_number: int
    device_id: str
    event_type: DrawerEventType
    previous_state: DrawerPhysicalState
    new_state: DrawerPhysicalState
    observed_timestamp: float
    sensor_source: str
    sensor_quality: str = "verified"
    authorization_ref: Optional[str] = None
    opening_reason: Optional[str] = None
    cashier_id: Optional[str] = None
    pos_session_id: Optional[str] = None
    pos_order_ref: Optional[str] = None
    opening_duration_seconds: Optional[float] = None
    is_duration_incomplete: bool = False
    is_initial_observation: bool = False
    raw_status_byte: Optional[int] = None
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_uuid": self.event_uuid,
            "sequence_number": self.sequence_number,
            "device_id": self.device_id,
            "event_type": self.event_type.value,
            "previous_state": self.previous_state.value,
            "new_state": self.new_state.value,
            "observed_timestamp": self.observed_timestamp,
            "sensor_source": self.sensor_source,
            "sensor_quality": self.sensor_quality,
            "authorization_ref": self.authorization_ref,
            "opening_reason": self.opening_reason,
            "cashier_id": self.cashier_id,
            "pos_session_id": self.pos_session_id,
            "pos_order_ref": self.pos_order_ref,
            "opening_duration_seconds": self.opening_duration_seconds,
            "is_duration_incomplete": self.is_duration_incomplete,
            "is_initial_observation": self.is_initial_observation,
            "raw_status_byte": self.raw_status_byte,
            "details": self.details,
        }


@dataclass
class DriverCapabilities:
    device_id: str
    driver_type: str
    classification: DeviceClassification
    drawer_kick_capability: CapabilityResult
    physical_status_capability: CapabilityResult
    manual_open_detection_capability: CapabilityResult
    hardware_verified: bool
    is_simulation: bool = False
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "driver_type": self.driver_type,
            "classification": self.classification.value,
            "drawer_kick_capability": self.drawer_kick_capability.value,
            "physical_status_capability": self.physical_status_capability.value,
            "manual_open_detection_capability": self.manual_open_detection_capability.value,
            "hardware_verified": self.hardware_verified,
            "is_simulation": self.is_simulation,
            "notes": self.notes,
        }


@dataclass
class DriverHealth:
    device_id: str
    connected: bool
    physical_state: DrawerPhysicalState
    last_verified_timestamp: Optional[float]
    last_heartbeat_timestamp: Optional[float]
    open_since_timestamp: Optional[float]
    current_open_duration_seconds: Optional[float]
    is_open_duration_incomplete: bool
    consecutive_errors: int
    backoff_delay_seconds: float
    last_error: Optional[str] = None
    sdk_open_counter: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "connected": self.connected,
            "physical_state": self.physical_state.value,
            "last_verified_timestamp": self.last_verified_timestamp,
            "last_heartbeat_timestamp": self.last_heartbeat_timestamp,
            "open_since_timestamp": self.open_since_timestamp,
            "current_open_duration_seconds": self.current_open_duration_seconds,
            "is_open_duration_incomplete": self.is_open_duration_incomplete,
            "consecutive_errors": self.consecutive_errors,
            "backoff_delay_seconds": self.backoff_delay_seconds,
            "last_error": self.last_error,
            "sdk_open_counter": self.sdk_open_counter,
        }


StatusChangeCallback = Callable[[DrawerTransitionEvent], None]


class CashDrawerDriver(abc.ABC):
    """
    Core CashDrawerDriver interface shared across Local Bridge, External Sensor,
    Android Bridge, Command-Only devices, and Simulator.
    """

    ALLOWED_POLLING_INTERVALS_MS = (250, 500, 1000, 2000, 5000)

    def __init__(
        self,
        device_id: str,
        sensor_source: str,
        polling_interval_ms: int = 500,
        staleness_timeout_seconds: float = 5.0,
        prolonged_open_threshold_seconds: float = 30.0,
        cooldown_lock_seconds: float = 1.5,
    ) -> None:
        self.device_id = device_id
        self.sensor_source = sensor_source
        self.polling_interval_ms = (
            polling_interval_ms if polling_interval_ms in self.ALLOWED_POLLING_INTERVALS_MS else 500
        )
        self.staleness_timeout_seconds = staleness_timeout_seconds
        self.prolonged_open_threshold_seconds = prolonged_open_threshold_seconds
        self.cooldown_lock_seconds = cooldown_lock_seconds

        self._state: DrawerPhysicalState = DrawerPhysicalState.UNKNOWN
        self._connected: bool = False
        self._sequence: int = 0
        self._subscribers: List[StatusChangeCallback] = []
        self._active_authorizations: Dict[str, AuthorizationContext] = {}
        self._seen_auth_refs: set[str] = set()
        self._last_open_command_ts: float = 0.0

        self._last_verified_ts: Optional[float] = None
        self._last_heartbeat_ts: Optional[float] = None
        self._open_since_ts: Optional[float] = None
        self._last_completed_duration: Optional[float] = None
        self._is_open_duration_incomplete: bool = False
        self._active_open_auth: Optional[AuthorizationContext] = None
        self._prolonged_alert_emitted: bool = False

        self._consecutive_errors: int = 0
        self._last_error: Optional[str] = None
        self._sdk_open_counter: Optional[int] = None
        self._event_history: List[DrawerTransitionEvent] = []
        self._lock = threading.RLock()

    @abc.abstractmethod
    def initialize(self) -> bool:
        """Initializes connection to the hardware interface."""

    @abc.abstractmethod
    def getCapabilities(self) -> DriverCapabilities:
        """Returns verified vs declared capabilities of this cash drawer interface."""

    @abc.abstractmethod
    def _send_hardware_open_pulse(self) -> bool:
        """Executes the low-level hardware open command without assuming physical open."""

    @abc.abstractmethod
    def _query_raw_physical_state(self) -> Tuple[DrawerPhysicalState, Optional[int]]:
        """Queries the actual physical sensor and returns (normalized_state, raw_byte)."""

    @abc.abstractmethod
    def disconnect(self) -> None:
        """Disconnects cleanly and updates state if currently OPEN."""

    def subscribeStatusChanges(self, callback: StatusChangeCallback) -> None:
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def get_event_history(self) -> List[DrawerTransitionEvent]:
        with self._lock:
            return list(self._event_history)

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def _emit_event(
        self,
        event_type: DrawerEventType,
        prev_state: DrawerPhysicalState,
        new_state: DrawerPhysicalState,
        now: float,
        auth: Optional[AuthorizationContext] = None,
        duration: Optional[float] = None,
        incomplete: bool = False,
        initial: bool = False,
        raw_byte: Optional[int] = None,
        details: str = "",
    ) -> DrawerTransitionEvent:
        evt = DrawerTransitionEvent(
            event_uuid=str(uuid.uuid4()),
            sequence_number=self._next_sequence(),
            device_id=self.device_id,
            event_type=event_type,
            previous_state=prev_state,
            new_state=new_state,
            observed_timestamp=now,
            sensor_source=self.sensor_source,
            sensor_quality="simulated" if self.sensor_source == "simulator" else "verified",
            authorization_ref=auth.authorization_ref if auth else None,
            opening_reason=auth.reason if auth else None,
            cashier_id=auth.cashier_id if auth else None,
            pos_session_id=auth.pos_session_id if auth else None,
            pos_order_ref=auth.pos_order_ref if auth else None,
            opening_duration_seconds=duration,
            is_duration_incomplete=incomplete,
            is_initial_observation=initial,
            raw_status_byte=raw_byte,
            details=details,
        )
        self._event_history.append(evt)
        callbacks = list(self._subscribers)
        for cb in callbacks:
            cb(evt)
        return evt

    def requestOpen(
        self,
        authorization_ref: str,
        reason: str,
        cashier_id: Optional[str] = None,
        pos_session_id: Optional[str] = None,
        pos_order_ref: Optional[str] = None,
        supervisor_id: Optional[str] = None,
        correlation_window_seconds: float = 8.0,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Logs and executes an authorized cash drawer opening command.
        RULES:
          1. Rejects missing authorization_ref or missing reason.
          2. Prevents duplicate opening pulses via idempotency and cooldown lock.
          3. NEVER sets physical state to OPEN solely because the kick pulse succeeded.
        """
        ts = now if now is not None else time.time()
        with self._lock:
            if not authorization_ref or not reason:
                raise PermissionError("Authorized drawer opening requires authorization_ref and reason.")

            if authorization_ref in self._seen_auth_refs:
                return {
                    "accepted": False,
                    "duplicate": True,
                    "authorization_ref": authorization_ref,
                    "reason": "Duplicate authorization_ref blocked to prevent double solenoid pulse.",
                }

            if (ts - self._last_open_command_ts) < self.cooldown_lock_seconds:
                return {
                    "accepted": False,
                    "cooldown_active": True,
                    "authorization_ref": authorization_ref,
                    "reason": "Cooldown lock active to prevent rapid repeated solenoid pulses.",
                }

            caps = self.getCapabilities()
            if caps.drawer_kick_capability != CapabilityResult.SUPPORTED:
                return {
                    "accepted": False,
                    "unsupported": True,
                    "authorization_ref": authorization_ref,
                    "reason": f"Drawer kick unsupported on {self.device_id}.",
                }

            auth_ctx = AuthorizationContext(
                authorization_ref=authorization_ref,
                reason=reason,
                cashier_id=cashier_id,
                pos_session_id=pos_session_id,
                pos_order_ref=pos_order_ref,
                supervisor_id=supervisor_id,
                issued_at=ts,
                expires_at=ts + correlation_window_seconds,
            )

            # Log BEFORE execution as mandated in Section 9.4
            self._emit_event(
                event_type=(
                    DrawerEventType.SUPERVISOR_OVERRIDE
                    if supervisor_id or reason == "supervisor_no_sale"
                    else DrawerEventType.OPEN_REQUESTED
                ),
                prev_state=self._state,
                new_state=self._state,
                now=ts,
                auth=auth_ctx,
                details=f"Opening requested for reason={reason}",
            )

            if not self._connected:
                return {
                    "accepted": False,
                    "offline": True,
                    "authorization_ref": authorization_ref,
                    "reason": "Hardware device is currently offline.",
                }

            pulse_ok = self._send_hardware_open_pulse()
            if not pulse_ok:
                self._emit_event(
                    event_type=DrawerEventType.HARDWARE_ERROR,
                    prev_state=self._state,
                    new_state=DrawerPhysicalState.ERROR,
                    now=ts,
                    auth=auth_ctx,
                    details="Hardware open pulse failed.",
                )
                return {
                    "accepted": False,
                    "authorization_ref": authorization_ref,
                    "reason": "Hardware failed to execute open pulse.",
                }

            self._seen_auth_refs.add(authorization_ref)
            self._last_open_command_ts = ts
            self._active_authorizations[authorization_ref] = auth_ctx

            self._emit_event(
                event_type=DrawerEventType.COMMAND_ACCEPTED,
                prev_state=self._state,
                new_state=self._state,  # Physical state unchanged until sensor confirms!
                now=ts,
                auth=auth_ctx,
                details="Solenoid kick command accepted; awaiting physical sensor confirmation.",
            )
            return {
                "accepted": True,
                "authorization_ref": authorization_ref,
                "physical_state": self._state.value,
                "awaiting_physical_confirmation": (
                    caps.physical_status_capability == CapabilityResult.SUPPORTED
                ),
            }

    def _match_authorization(self, now: float) -> Optional[AuthorizationContext]:
        for auth_ref, ctx in list(self._active_authorizations.items()):
            if ctx.is_valid_at(now):
                ctx.consumed = True
                return ctx
            if now > ctx.expires_at:
                self._active_authorizations.pop(auth_ref, None)
        return None

    def getCurrentStatus(self, now: Optional[float] = None) -> Dict[str, Any]:
        """
        Polls or evaluates the current physical status and executes all state machine
        transition rules from Section 10.4 and 10.5.
        """
        ts = now if now is not None else time.time()
        with self._lock:
            caps = self.getCapabilities()
            if caps.physical_status_capability == CapabilityResult.UNSUPPORTED:
                self._state = DrawerPhysicalState.UNSUPPORTED
                return {
                    "device_id": self.device_id,
                    "physical_state": DrawerPhysicalState.UNSUPPORTED.value,
                    "last_verified_timestamp": None,
                    "open_duration_seconds": None,
                    "is_duration_incomplete": False,
                }

            if not self._connected:
                self._handle_loss_of_communication(DrawerPhysicalState.OFFLINE, ts, "Device offline")
                return self._status_snapshot(ts)

            try:
                observed_state, raw_byte = self._query_raw_physical_state()
            except Exception as exc:
                self._consecutive_errors += 1
                self._last_error = str(exc)
                self._handle_loss_of_communication(
                    DrawerPhysicalState.ERROR, ts, f"Sensor query exception: {exc}"
                )
                return self._status_snapshot(ts)

            if observed_state in (DrawerPhysicalState.ERROR, DrawerPhysicalState.OFFLINE):
                self._consecutive_errors += 1
                self._handle_loss_of_communication(observed_state, ts, "Sensor returned error/offline")
                return self._status_snapshot(ts)

            # Check staleness if sensor returned STALE
            if observed_state == DrawerPhysicalState.STALE:
                self._handle_loss_of_communication(DrawerPhysicalState.STALE, ts, "Sensor reading stale")
                return self._status_snapshot(ts)

            # Valid verified physical reading (OPEN or CLOSED)
            self._consecutive_errors = 0
            self._last_error = None
            self._last_verified_ts = ts
            self._last_heartbeat_ts = ts

            prev = self._state
            if prev in (DrawerPhysicalState.OFFLINE, DrawerPhysicalState.ERROR, DrawerPhysicalState.STALE):
                self._emit_event(
                    event_type=DrawerEventType.COMMUNICATION_RESTORED,
                    prev_state=prev,
                    new_state=observed_state,
                    now=ts,
                    raw_byte=raw_byte,
                    details=f"Communication restored from {prev.value} to {observed_state.value}",
                )

            if prev == DrawerPhysicalState.UNKNOWN and observed_state in (
                DrawerPhysicalState.OPEN,
                DrawerPhysicalState.CLOSED,
            ):
                # Section 10.4: UNKNOWN -> OPEN or UNKNOWN -> CLOSED is an initial observation,
                # NOT counted as a new physical opening event.
                self._state = observed_state
                if observed_state == DrawerPhysicalState.OPEN:
                    self._open_since_ts = ts
                    self._is_open_duration_incomplete = False
                    self._prolonged_alert_emitted = False
                self._emit_event(
                    event_type=DrawerEventType.INITIAL_OBSERVATION,
                    prev_state=prev,
                    new_state=observed_state,
                    now=ts,
                    initial=True,
                    raw_byte=raw_byte,
                    details=f"Initial physical observation: {observed_state.value}",
                )

            elif prev != DrawerPhysicalState.OPEN and observed_state == DrawerPhysicalState.OPEN:
                # Verified physical opening (CLOSED -> OPEN or recovery -> OPEN)
                self._state = DrawerPhysicalState.OPEN
                self._open_since_ts = ts
                self._is_open_duration_incomplete = False
                self._prolonged_alert_emitted = False
                matched_auth = self._match_authorization(ts)
                self._active_open_auth = matched_auth

                if matched_auth is not None:
                    self._emit_event(
                        event_type=DrawerEventType.PHYSICAL_OPENED,
                        prev_state=prev,
                        new_state=DrawerPhysicalState.OPEN,
                        now=ts,
                        auth=matched_auth,
                        raw_byte=raw_byte,
                        details=f"Authorized physical opening matched to {matched_auth.authorization_ref}",
                    )
                else:
                    # Unmatched physical opening (e.g., physical key or unauthorized trigger)
                    self._emit_event(
                        event_type=DrawerEventType.UNMATCHED_OPENING,
                        prev_state=prev,
                        new_state=DrawerPhysicalState.OPEN,
                        now=ts,
                        auth=None,  # Never falsely attribute to last cashier without valid rule!
                        raw_byte=raw_byte,
                        details="Physical OPEN detected without matching authorization request.",
                    )

            elif (
                prev
                in (
                    DrawerPhysicalState.OPEN,
                    DrawerPhysicalState.OFFLINE,
                    DrawerPhysicalState.ERROR,
                    DrawerPhysicalState.STALE,
                )
                and observed_state == DrawerPhysicalState.CLOSED
            ):
                # Verified physical closing (OPEN -> CLOSED or recovery from offline/error -> CLOSED)
                duration = (
                    round(max(0.0, ts - self._open_since_ts), 3)
                    if self._open_since_ts is not None
                    else None
                )
                incomplete = self._is_open_duration_incomplete
                self._state = DrawerPhysicalState.CLOSED
                if prev == DrawerPhysicalState.OPEN or self._open_since_ts is not None:
                    self._last_completed_duration = duration
                    self._emit_event(
                        event_type=DrawerEventType.PHYSICAL_CLOSED,
                        prev_state=prev,
                        new_state=DrawerPhysicalState.CLOSED,
                        now=ts,
                        auth=self._active_open_auth,
                        duration=duration,
                        incomplete=incomplete,
                        raw_byte=raw_byte,
                        details=f"Physical drawer closed after {duration}s (incomplete={incomplete})",
                    )
                self._open_since_ts = None
                self._active_open_auth = None
                self._is_open_duration_incomplete = False
                self._prolonged_alert_emitted = False

            elif self._state == DrawerPhysicalState.OPEN and self._open_since_ts is not None:
                elapsed = ts - self._open_since_ts
                if (
                    elapsed >= self.prolonged_open_threshold_seconds
                    and not self._prolonged_alert_emitted
                ):
                    self._prolonged_alert_emitted = True
                    self._emit_event(
                        event_type=DrawerEventType.OPENING_TIMEOUT,
                        prev_state=DrawerPhysicalState.OPEN,
                        new_state=DrawerPhysicalState.OPEN,
                        now=ts,
                        auth=self._active_open_auth,
                        duration=round(elapsed, 3),
                        raw_byte=raw_byte,
                        details=(
                            f"Drawer remained physically OPEN for {elapsed:.1f}s "
                            f"(threshold={self.prolonged_open_threshold_seconds}s)"
                        ),
                    )

            return self._status_snapshot(ts)

    def _handle_loss_of_communication(
        self, target_state: DrawerPhysicalState, ts: float, reason: str
    ) -> None:
        """
        Section 10.5 rule:
        If communication is lost while OPEN, mark the observation incomplete and
        NEVER generate an artificial CLOSED event.
        """
        prev = self._state
        if prev == DrawerPhysicalState.OPEN or self._open_since_ts is not None:
            self._is_open_duration_incomplete = True
        if prev != target_state:
            self._state = target_state
            evt_type = (
                DrawerEventType.SENSOR_DISCONNECTED
                if target_state in (DrawerPhysicalState.OFFLINE, DrawerPhysicalState.STALE)
                else DrawerEventType.HARDWARE_ERROR
            )
            self._emit_event(
                event_type=evt_type,
                prev_state=prev,
                new_state=target_state,
                now=ts,
                auth=self._active_open_auth,
                duration=(
                    round(max(0.0, ts - self._open_since_ts), 3)
                    if self._open_since_ts is not None
                    else None
                ),
                incomplete=self._is_open_duration_incomplete,
                details=f"{reason} (incomplete_open={self._is_open_duration_incomplete})",
            )

    def check_staleness(self, now: Optional[float] = None) -> DrawerPhysicalState:
        ts = now if now is not None else time.time()
        with self._lock:
            if (
                self._last_verified_ts is not None
                and self._state in (DrawerPhysicalState.OPEN, DrawerPhysicalState.CLOSED)
                and (ts - self._last_verified_ts) > self.staleness_timeout_seconds
            ):
                self._handle_loss_of_communication(
                    DrawerPhysicalState.STALE,
                    ts,
                    f"Last sensor reading older than {self.staleness_timeout_seconds}s",
                )
            return self._state

    def _status_snapshot(self, ts: float) -> Dict[str, Any]:
        current_duration = None
        if self._state == DrawerPhysicalState.OPEN and self._open_since_ts is not None:
            current_duration = round(max(0.0, ts - self._open_since_ts), 3)
        return {
            "device_id": self.device_id,
            "physical_state": self._state.value,
            "last_verified_timestamp": self._last_verified_ts,
            "open_since_timestamp": self._open_since_ts,
            "open_duration_seconds": current_duration,
            "last_completed_duration_seconds": self._last_completed_duration,
            "is_duration_incomplete": self._is_open_duration_incomplete,
            "sensor_source": self.sensor_source,
        }

    def getHealth(self, now: Optional[float] = None) -> DriverHealth:
        ts = now if now is not None else time.time()
        with self._lock:
            backoff = min(30.0, (self.polling_interval_ms / 1000.0) * (2 ** min(self._consecutive_errors, 6)))
            cur_dur = (
                round(max(0.0, ts - self._open_since_ts), 3)
                if self._state == DrawerPhysicalState.OPEN and self._open_since_ts is not None
                else None
            )
            return DriverHealth(
                device_id=self.device_id,
                connected=self._connected,
                physical_state=self._state,
                last_verified_timestamp=self._last_verified_ts,
                last_heartbeat_timestamp=self._last_heartbeat_ts,
                open_since_timestamp=self._open_since_ts,
                current_open_duration_seconds=cur_dur,
                is_open_duration_incomplete=self._is_open_duration_incomplete,
                consecutive_errors=self._consecutive_errors,
                backoff_delay_seconds=backoff,
                last_error=self._last_error,
                sdk_open_counter=self._sdk_open_counter,
            )


class EscPosLocalDrawerDriver(CashDrawerDriver):
    """
    Adapter 1: Verified Local ESC/POS Bidirectional Printer Interface.
    Uses `DLE EOT 1` (`0x10 0x04 0x01`) real-time status command to read RJ11 Pin 3.
    """

    DLE_EOT_1 = b"\x10\x04\x01"
    ESC_P_PIN2 = b"\x1b\x70\x00\x32\x96"

    def __init__(
        self,
        device_id: str,
        host: str = "127.0.0.1",
        port: int = 9100,
        pin3_open_level: int = 1,
        hardware_verified: bool = True,
        debounce_samples: int = 2,
        transport_exchange: Optional[Callable[[bytes], bytes]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(device_id=device_id, sensor_source="escpos_dle_eot", **kwargs)
        self.host = host
        self.port = port
        self.pin3_open_level = 1 if pin3_open_level else 0
        self.hardware_verified = hardware_verified
        self.debounce_samples = max(1, debounce_samples)
        self._transport_exchange = transport_exchange
        self._socket: Optional[socket.socket] = None

    def initialize(self) -> bool:
        with self._lock:
            if self._transport_exchange is not None:
                self._connected = True
                return True
            try:
                sock = socket.create_connection((self.host, self.port), timeout=2.0)
                self._socket = sock
                self._connected = True
                return True
            except OSError as exc:
                self._connected = False
                self._last_error = str(exc)
                return False

    def getCapabilities(self) -> DriverCapabilities:
        if not self.hardware_verified:
            return DriverCapabilities(
                device_id=self.device_id,
                driver_type="escpos_dle_eot",
                classification=DeviceClassification.NOT_YET_VERIFIED,
                drawer_kick_capability=CapabilityResult.SUPPORTED,
                physical_status_capability=CapabilityResult.UNVERIFIED,
                manual_open_detection_capability=CapabilityResult.UNVERIFIED,
                hardware_verified=False,
                notes="Requires 3-step hardware calibration to verify RJ11 Pin 3 polarity.",
            )
        return DriverCapabilities(
            device_id=self.device_id,
            driver_type="escpos_dle_eot",
            classification=DeviceClassification.FULL_PHYSICAL_MONITORING,
            drawer_kick_capability=CapabilityResult.SUPPORTED,
            physical_status_capability=CapabilityResult.SUPPORTED,
            manual_open_detection_capability=CapabilityResult.SUPPORTED,
            hardware_verified=True,
            notes=f"Verified ESC/POS DLE EOT 1 (10 04 01) with pin3_open_level={self.pin3_open_level}.",
        )

    @staticmethod
    def decode_dle_eot_1_byte(status_byte: int, pin3_open_level: int = 1) -> DrawerPhysicalState:
        """
        Validates an ESC/POS `DLE EOT 1` (`10 04 01`) 1-byte response:
          - Bit 0 (0x01) must be 0
          - Bit 1 (0x02) must be 1
          - Bit 4 (0x10) must be 1
          - Bit 7 (0x80) must be 0
          Mask check: `(status_byte & 0x93) == 0x12`
          - Bit 3 (0x08): 1 = Printer offline
          - Bit 2 (0x04): Drawer kick-out connector Pin 3 level
        """
        if (status_byte & 0x93) != 0x12:
            raise ValueError(f"Malformed DLE EOT 1 response byte: 0x{status_byte:02X}")
        if (status_byte & 0x08) != 0:
            return DrawerPhysicalState.OFFLINE
        pin3_bit = 1 if (status_byte & 0x04) != 0 else 0
        return (
            DrawerPhysicalState.OPEN
            if pin3_bit == (1 if pin3_open_level else 0)
            else DrawerPhysicalState.CLOSED
        )

    def _raw_exchange(self, command: bytes, expect_reply: bool = True) -> bytes:
        if self._transport_exchange is not None:
            return self._transport_exchange(command)
        if self._socket is None:
            raise ConnectionError("Local ESC/POS socket is not connected.")
        self._socket.sendall(command)
        if not expect_reply:
            return b""
        data = self._socket.recv(1)
        if not data:
            raise ConnectionError("Empty response from printer on DLE EOT 1.")
        return data

    def _send_hardware_open_pulse(self) -> bool:
        try:
            self._raw_exchange(self.ESC_P_PIN2, expect_reply=False)
            return True
        except Exception as exc:
            self._last_error = str(exc)
            return False

    def _query_raw_physical_state(self) -> Tuple[DrawerPhysicalState, Optional[int]]:
        if not self.hardware_verified:
            return DrawerPhysicalState.UNKNOWN, None

        readings: List[DrawerPhysicalState] = []
        last_byte: Optional[int] = None
        for _ in range(self.debounce_samples):
            resp = self._raw_exchange(self.DLE_EOT_1, expect_reply=True)
            if not resp:
                raise ConnectionError("No byte returned for DLE EOT 1 query.")
            last_byte = resp[0]
            state = self.decode_dle_eot_1_byte(last_byte, self.pin3_open_level)
            readings.append(state)

        # Require unanimous debounce agreement across samples
        if len(set(readings)) == 1:
            return readings[0], last_byte
        # If bouncing during physical motion, keep current state until settled
        return (
            self._state
            if self._state in (DrawerPhysicalState.OPEN, DrawerPhysicalState.CLOSED)
            else readings[-1],
            last_byte,
        )

    def calibrate_pin3_polarity(
        self, closed_sample_byte: int, open_sample_byte: int
    ) -> Dict[str, Any]:
        """
        Executes the 2-state electrical calibration check to verify that Pin 3
        actually changes state between physically CLOSED and physically OPEN.
        """
        if (closed_sample_byte & 0x93) != 0x12 or (open_sample_byte & 0x93) != 0x12:
            self.hardware_verified = False
            return {
                "verified": False,
                "classification": DeviceClassification.UNSUPPORTED.value,
                "reason": "Invalid DLE EOT 1 frame during calibration.",
            }
        closed_bit = 1 if (closed_sample_byte & 0x04) != 0 else 0
        open_bit = 1 if (open_sample_byte & 0x04) != 0 else 0
        if closed_bit == open_bit:
            self.hardware_verified = False
            return {
                "verified": False,
                "classification": DeviceClassification.REQUIRES_EXTERNAL_SENSOR.value,
                "reason": (
                    "Pin 3 level did not change between CLOSED and OPEN. "
                    "Drawer is 2-wire solenoid-only or printer lacks Pin 3 input."
                ),
            }
        self.pin3_open_level = open_bit
        self.hardware_verified = True
        return {
            "verified": True,
            "pin3_open_level": self.pin3_open_level,
            "classification": DeviceClassification.FULL_PHYSICAL_MONITORING.value,
        }

    def disconnect(self, now: Optional[float] = None) -> None:
        ts = now if now is not None else time.time()
        with self._lock:
            self._connected = False
            if self._socket is not None:
                try:
                    self._socket.close()
                except OSError:
                    pass
                self._socket = None
            self._handle_loss_of_communication(
                DrawerPhysicalState.OFFLINE, ts, "Local ESC/POS driver disconnected"
            )


class ExternalSensorGatewayDriver(CashDrawerDriver):
    """
    Adapter 2: Independent External IoT Sensor Gateway (ESP32 magnetic reed switch
    + tamper loop). Reports physical drawer state completely independently of
    the printer and printer opening commands.
    """

    def __init__(
        self,
        device_id: str,
        shared_secret: str,
        open_pulse_callback: Optional[Callable[[], bool]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(device_id=device_id, sensor_source="external_sensor", **kwargs)
        self._shared_secret = shared_secret
        self._open_pulse_callback = open_pulse_callback
        self._latest_sensor_state: DrawerPhysicalState = DrawerPhysicalState.UNKNOWN
        self._tamper_detected: bool = False

    def initialize(self) -> bool:
        with self._lock:
            self._connected = True
            return True

    def getCapabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            device_id=self.device_id,
            driver_type="external_sensor",
            classification=DeviceClassification.FULL_PHYSICAL_MONITORING,
            drawer_kick_capability=(
                CapabilityResult.SUPPORTED
                if self._open_pulse_callback is not None
                else CapabilityResult.UNSUPPORTED
            ),
            physical_status_capability=CapabilityResult.SUPPORTED,
            manual_open_detection_capability=CapabilityResult.SUPPORTED,
            hardware_verified=True,
            notes="Independent ESP32 magnetic reed switch & tamper supervision loop.",
        )

    def ingest_signed_sensor_frame(
        self,
        contact_closed: bool,
        tamper_loop_intact: bool,
        timestamp: float,
        nonce: str,
        signature: str,
    ) -> Dict[str, Any]:
        """Verifies HMAC-SHA256 signature from ESP32 sensor gateway and updates state."""
        payload_str = f"{self.device_id}:{int(contact_closed)}:{int(tamper_loop_intact)}:{int(timestamp)}:{nonce}"
        expected_sig = hmac.new(
            self._shared_secret.encode("utf-8"),
            payload_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected_sig, signature.lower()):
            raise PermissionError("Invalid ESP32 sensor gateway HMAC signature.")

        with self._lock:
            self._connected = True
            if not tamper_loop_intact:
                self._tamper_detected = True
                self._latest_sensor_state = DrawerPhysicalState.ERROR
            else:
                self._tamper_detected = False
                self._latest_sensor_state = (
                    DrawerPhysicalState.CLOSED if contact_closed else DrawerPhysicalState.OPEN
                )
            return self.getCurrentStatus(now=timestamp)

    def _send_hardware_open_pulse(self) -> bool:
        if self._open_pulse_callback is None:
            return False
        return self._open_pulse_callback()

    def _query_raw_physical_state(self) -> Tuple[DrawerPhysicalState, Optional[int]]:
        return self._latest_sensor_state, (0 if self._latest_sensor_state == DrawerPhysicalState.CLOSED else 1)

    def disconnect(self, now: Optional[float] = None) -> None:
        ts = now if now is not None else time.time()
        with self._lock:
            self._connected = False
            self._handle_loss_of_communication(
                DrawerPhysicalState.OFFLINE, ts, "External sensor gateway disconnected"
            )


class CommandOnlyDrawerDriver(CashDrawerDriver):
    """
    Adapter 3: Command-Only Drawer Device (e.g., SUNMI Cloud Printer without local
    sensor or 2-wire solenoid-only cash drawer).
    Supports authorized opening commands, but ALWAYS reports `UNSUPPORTED` for
    physical state—never inventing OPEN or CLOSED states (AC-15).
    """

    def __init__(
        self,
        device_id: str,
        kick_callback: Callable[[], bool],
        **kwargs: Any,
    ) -> None:
        super().__init__(device_id=device_id, sensor_source="command_only", **kwargs)
        self._kick_callback = kick_callback
        self._state = DrawerPhysicalState.UNSUPPORTED

    def initialize(self) -> bool:
        with self._lock:
            self._connected = True
            self._state = DrawerPhysicalState.UNSUPPORTED
            return True

    def getCapabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            device_id=self.device_id,
            driver_type="command_only",
            classification=DeviceClassification.OPENING_CONTROL_ONLY,
            drawer_kick_capability=CapabilityResult.SUPPORTED,
            physical_status_capability=CapabilityResult.UNSUPPORTED,
            manual_open_detection_capability=CapabilityResult.UNSUPPORTED,
            hardware_verified=True,
            notes="Hardware supports opening kick command only; no physical status feedback.",
        )

    def _send_hardware_open_pulse(self) -> bool:
        return self._kick_callback()

    def _query_raw_physical_state(self) -> Tuple[DrawerPhysicalState, Optional[int]]:
        return DrawerPhysicalState.UNSUPPORTED, None

    def disconnect(self, now: Optional[float] = None) -> None:
        with self._lock:
            self._connected = False


class SunmiAndroidBridgeDriver(CashDrawerDriver):
    """
    Adapter 4: SUNMI Android Built-in Printer AIDL Bridge (`woyouService`).
    Uses `getDrawerStatus()` for verified physical status and records
    `getOpenDrawerTimes()` strictly as a supplementary diagnostic metric.
    """

    def __init__(
        self,
        device_id: str,
        open_drawer_fn: Callable[[], bool],
        get_drawer_status_fn: Callable[[], int],
        get_open_times_fn: Optional[Callable[[], int]] = None,
        open_status_value: int = 1,
        status_supported: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(device_id=device_id, sensor_source="android_aidl", **kwargs)
        self._open_drawer_fn = open_drawer_fn
        self._get_drawer_status_fn = get_drawer_status_fn
        self._get_open_times_fn = get_open_times_fn
        self._open_status_value = open_status_value
        self._status_supported = status_supported

    def initialize(self) -> bool:
        with self._lock:
            self._connected = True
            if self._get_open_times_fn is not None:
                try:
                    self._sdk_open_counter = self._get_open_times_fn()
                except Exception:
                    self._sdk_open_counter = None
            return True

    def getCapabilities(self) -> DriverCapabilities:
        if not self._status_supported:
            return DriverCapabilities(
                device_id=self.device_id,
                driver_type="android_aidl",
                classification=DeviceClassification.OPENING_CONTROL_ONLY,
                drawer_kick_capability=CapabilityResult.SUPPORTED,
                physical_status_capability=CapabilityResult.UNSUPPORTED,
                manual_open_detection_capability=CapabilityResult.UNSUPPORTED,
                hardware_verified=True,
                notes="Android terminal does not expose physical Pin 3 sensor.",
            )
        return DriverCapabilities(
            device_id=self.device_id,
            driver_type="android_aidl",
            classification=DeviceClassification.FULL_PHYSICAL_MONITORING,
            drawer_kick_capability=CapabilityResult.SUPPORTED,
            physical_status_capability=CapabilityResult.SUPPORTED,
            manual_open_detection_capability=CapabilityResult.SUPPORTED,
            hardware_verified=True,
            notes="SUNMI Android Woyou AIDL getDrawerStatus() verified.",
        )

    def _send_hardware_open_pulse(self) -> bool:
        ok = self._open_drawer_fn()
        if ok and self._get_open_times_fn is not None:
            try:
                self._sdk_open_counter = self._get_open_times_fn()
            except Exception:
                pass
        return ok

    def _query_raw_physical_state(self) -> Tuple[DrawerPhysicalState, Optional[int]]:
        if not self._status_supported:
            return DrawerPhysicalState.UNSUPPORTED, None
        raw = self._get_drawer_status_fn()
        if raw < 0:
            return DrawerPhysicalState.ERROR, raw
        state = (
            DrawerPhysicalState.OPEN
            if raw == self._open_status_value
            else DrawerPhysicalState.CLOSED
        )
        return state, raw

    def disconnect(self, now: Optional[float] = None) -> None:
        ts = now if now is not None else time.time()
        with self._lock:
            self._connected = False
            self._handle_loss_of_communication(
                DrawerPhysicalState.OFFLINE, ts, "Android AIDL binder disconnected"
            )


class HardwareSimulatorDriver(CashDrawerDriver):
    """
    Adapter 5: Deterministic Hardware Simulator for Development & Automated QA.
    Explicitly marks `is_simulation = True` and `sensor_source = 'simulator'`
    so simulation telemetry can never be confused with physical hardware UAT.
    """

    def __init__(
        self,
        device_id: str = "SIM-DRAWER-01",
        auto_open_on_pulse: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(device_id=device_id, sensor_source="simulator", **kwargs)
        self.auto_open_on_pulse = auto_open_on_pulse
        self._simulated_physical_position: DrawerPhysicalState = DrawerPhysicalState.CLOSED
        self._pulse_count: int = 0

    def initialize(self) -> bool:
        with self._lock:
            self._connected = True
            return True

    def getCapabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            device_id=self.device_id,
            driver_type="simulator",
            classification=DeviceClassification.NOT_YET_VERIFIED,
            drawer_kick_capability=CapabilityResult.SUPPORTED,
            physical_status_capability=CapabilityResult.SUPPORTED,
            manual_open_detection_capability=CapabilityResult.SUPPORTED,
            hardware_verified=False,
            is_simulation=True,
            notes="DEVELOPMENT SIMULATOR ONLY — Not valid as physical hardware verification.",
        )

    def _send_hardware_open_pulse(self) -> bool:
        self._pulse_count += 1
        self._sdk_open_counter = self._pulse_count
        if self.auto_open_on_pulse:
            self._simulated_physical_position = DrawerPhysicalState.OPEN
        return True

    def simulate_manual_physical_open(self, now: Optional[float] = None) -> Dict[str, Any]:
        """Simulates opening the cash drawer with a physical key (no software command)."""
        with self._lock:
            self._simulated_physical_position = DrawerPhysicalState.OPEN
            return self.getCurrentStatus(now=now)

    def simulate_manual_physical_close(self, now: Optional[float] = None) -> Dict[str, Any]:
        """Simulates physically pushing the cash drawer shut."""
        with self._lock:
            self._simulated_physical_position = DrawerPhysicalState.CLOSED
            return self.getCurrentStatus(now=now)

    def _query_raw_physical_state(self) -> Tuple[DrawerPhysicalState, Optional[int]]:
        return (
            self._simulated_physical_position,
            1 if self._simulated_physical_position == DrawerPhysicalState.OPEN else 0,
        )

    def disconnect(self, now: Optional[float] = None) -> None:
        ts = now if now is not None else time.time()
        with self._lock:
            self._connected = False
            self._handle_loss_of_communication(
                DrawerPhysicalState.OFFLINE, ts, "Simulator interface disconnected"
            )

