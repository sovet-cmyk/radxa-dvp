#!/usr/bin/env python3
"""Read a Linux USB touchscreen and send authenticated DVP touch events by UDP."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import math
import os
from pathlib import Path
import secrets
import select
import signal
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional


LOG = logging.getLogger("dvp-touch-interface")
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")
MAX_SEQUENCE = 0xFFFFFFFF
MAX_CONTROL_BYTES = 512
DEFAULT_POWEROFF_REQUEST = Path("/run/dvp-touch-sender/poweroff")


class ConfigurationError(ValueError):
    """Raised when config.json contains an unsafe or unsupported value."""


@dataclass(frozen=True)
class SenderConfig:
    destination_host: str = "10.42.0.1"
    destination_port: int = 4210
    local_port: int = 4211
    shared_key: str = ""
    device: str = ""
    device_name_contains: str = ""
    grab_device: bool = True
    rotation: int = 0
    invert_x: bool = False
    invert_y: bool = False
    x_min: Optional[float] = None
    x_max: Optional[float] = None
    y_min: Optional[float] = None
    y_max: Optional[float] = None
    heartbeat_seconds: float = 0.25
    hello_seconds: float = 1.0
    active_refresh_seconds: float = 0.10
    device_retry_seconds: float = 2.0

    @classmethod
    def load(cls, path: Path) -> "SenderConfig":
        try:
            supplied = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigurationError(f"Configuration file not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"Invalid JSON in {path}: {exc}") from exc
        if not isinstance(supplied, dict):
            raise ConfigurationError("Configuration must be a JSON object")
        pairing_path = Path(os.environ.get("DVP_PAIRING", path.with_name("pairing.json")))
        if pairing_path.is_file():
            try:
                pairing = json.loads(pairing_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ConfigurationError(f"Invalid JSON in {pairing_path}: {exc}") from exc
            if not isinstance(pairing, dict):
                raise ConfigurationError("pairing.json must contain a JSON object")
            allowed_pairing = {"unit_id", "ssid", "wifi_password", "touch_shared_key"}
            unknown_pairing = set(pairing) - allowed_pairing
            if unknown_pairing:
                raise ConfigurationError(
                    f"Unknown pairing settings: {sorted(unknown_pairing)}")
            touch_key = pairing.get("touch_shared_key")
            if not isinstance(touch_key, str):
                raise ConfigurationError("pairing.json touch_shared_key must be a string")
            supplied["shared_key"] = touch_key
        allowed = set(cls.__dataclass_fields__)
        unknown = set(supplied) - allowed
        if unknown:
            raise ConfigurationError(f"Unknown configuration keys: {sorted(unknown)}")
        try:
            config = cls(**supplied)
        except TypeError as exc:
            raise ConfigurationError(str(exc)) from exc
        config.validate()
        return config

    def validate(self) -> None:
        if not isinstance(self.destination_host, str) or not self.destination_host:
            raise ConfigurationError("destination_host must be a non-empty string")
        for name, value, allow_zero in (
            ("destination_port", self.destination_port, False),
            ("local_port", self.local_port, True),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigurationError(f"{name} must be an integer")
            lower = 0 if allow_zero else 1
            if not lower <= value <= 65535:
                raise ConfigurationError(f"{name} must be between {lower} and 65535")
        if not isinstance(self.shared_key, str) or len(self.shared_key) < 16:
            raise ConfigurationError("shared_key must contain at least 16 characters")
        if not isinstance(self.device, str) or not isinstance(self.device_name_contains, str):
            raise ConfigurationError("device selectors must be strings")
        if self.rotation not in (0, 90, 180, 270):
            raise ConfigurationError("rotation must be 0, 90, 180, or 270")
        if not isinstance(self.grab_device, bool):
            raise ConfigurationError("grab_device must be true or false")
        if not isinstance(self.invert_x, bool) or not isinstance(self.invert_y, bool):
            raise ConfigurationError("invert_x and invert_y must be true or false")
        for low_name, high_name in (("x_min", "x_max"), ("y_min", "y_max")):
            low, high = getattr(self, low_name), getattr(self, high_name)
            if (low is None) != (high is None):
                raise ConfigurationError(f"{low_name} and {high_name} must both be set or both be null")
            if low is not None:
                if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                       for v in (low, high)) or low >= high:
                    raise ConfigurationError(f"{low_name} must be smaller than {high_name}")
        for name in ("heartbeat_seconds", "hello_seconds", "active_refresh_seconds",
                     "device_retry_seconds"):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                raise ConfigurationError(f"{name} must be a positive number")
        if self.heartbeat_seconds >= 0.7:
            raise ConfigurationError("heartbeat_seconds must stay below the DVP receiver timeout")
        if self.active_refresh_seconds >= 0.7:
            raise ConfigurationError("active_refresh_seconds must stay below the DVP receiver timeout")


@dataclass(frozen=True)
class RawContact:
    active: bool
    identity: Optional[int] = None
    x: Optional[float] = None
    y: Optional[float] = None


@dataclass
class _Slot:
    active: bool = False
    identity: Optional[int] = None
    x: Optional[float] = None
    y: Optional[float] = None
    order: int = 0


class CoordinateMapper:
    """Normalize an input device's advertised axis range and apply orientation."""

    def __init__(self, x_min: float, x_max: float, y_min: float, y_max: float,
                 rotation: int = 0, invert_x: bool = False, invert_y: bool = False):
        if x_min >= x_max or y_min >= y_max:
            raise ValueError("Touch axis maximum must be greater than minimum")
        self.x_min, self.x_max = x_min, x_max
        self.y_min, self.y_max = y_min, y_max
        self.rotation = rotation
        self.invert_x, self.invert_y = invert_x, invert_y

    @staticmethod
    def _axis(value: float, low: float, high: float) -> float:
        return min(1.0, max(0.0, (value - low) / (high - low)))

    def map(self, raw_x: float, raw_y: float) -> tuple[float, float]:
        x = self._axis(raw_x, self.x_min, self.x_max)
        y = self._axis(raw_y, self.y_min, self.y_max)
        if self.rotation == 90:
            x, y = 1.0 - y, x
        elif self.rotation == 180:
            x, y = 1.0 - x, 1.0 - y
        elif self.rotation == 270:
            x, y = y, 1.0 - x
        if self.invert_x:
            x = 1.0 - x
        if self.invert_y:
            y = 1.0 - y
        return round(x, 6), round(y, 6)


def _load_evdev():
    try:
        import evdev  # type: ignore
    except ImportError as exc:
        raise RuntimeError("python3-evdev is not installed; run the DVP installer") from exc
    return evdev


def _touch_score(device: Any, evdev: Any) -> tuple[int, str]:
    e = evdev.ecodes
    # Ask evdev for plain numeric ABS codes. With its default absinfo=True,
    # absolute axes are returned as (code, AbsInfo) tuples and membership tests
    # incorrectly reject otherwise valid touchscreens such as WaveShare WS170120.
    capabilities = device.capabilities(absinfo=False)
    abs_codes = set(capabilities.get(e.EV_ABS, []))
    key_codes = set(capabilities.get(e.EV_KEY, []))
    mt = {e.ABS_MT_POSITION_X, e.ABS_MT_POSITION_Y, e.ABS_MT_TRACKING_ID} <= abs_codes
    single = {e.ABS_X, e.ABS_Y} <= abs_codes and (
        e.BTN_TOUCH in key_codes or e.BTN_TOOL_FINGER in key_codes or e.ABS_PRESSURE in abs_codes
    )
    if not (mt or single):
        return -1, "not a touchscreen"
    score = 100 if mt else 70
    name = (device.name or "").lower()
    if "touchscreen" in name:
        score += 35
    elif "touch" in name:
        score += 25
    try:
        if e.INPUT_PROP_DIRECT in device.input_props():
            score += 40
    except OSError:
        pass
    mode = "multitouch" if mt else "single-touch"
    return score, mode


def list_touch_devices() -> int:
    evdev = _load_evdev()
    found = 0
    for path in sorted(evdev.list_devices()):
        device = None
        try:
            device = evdev.InputDevice(path)
            score, mode = _touch_score(device, evdev)
            if score >= 0:
                found += 1
                print(f"{path}\t{device.name}\t{mode}\tscore={score}")
        except (OSError, PermissionError) as exc:
            print(f"{path}\tUNREADABLE\t{exc}")
        finally:
            if device is not None:
                device.close()
    if not found:
        print("No readable touchscreen candidates found.")
        return 1
    return 0


def find_touch_device(config: SenderConfig, override: str = "") -> tuple[Any, Any, str]:
    evdev = _load_evdev()
    requested = override or config.device
    if requested:
        device = evdev.InputDevice(requested)
        score, mode = _touch_score(device, evdev)
        if score < 0:
            device.close()
            raise RuntimeError(f"Configured device is not a touchscreen: {requested}")
        return evdev, device, mode

    wanted_name = config.device_name_contains.casefold().strip()
    candidates = []
    for path in evdev.list_devices():
        device = None
        try:
            device = evdev.InputDevice(path)
            if wanted_name and wanted_name not in (device.name or "").casefold():
                device.close()
                continue
            score, mode = _touch_score(device, evdev)
            if score >= 0:
                candidates.append((score, path, mode, device))
            else:
                device.close()
        except (OSError, PermissionError):
            if device is not None:
                device.close()
    if not candidates:
        suffix = f" matching {config.device_name_contains!r}" if wanted_name else ""
        raise RuntimeError(f"No readable touchscreen device found{suffix}")
    candidates.sort(key=lambda item: (-item[0], item[1]))
    selected = candidates[0]
    for candidate in candidates[1:]:
        candidate[3].close()
    return evdev, selected[3], selected[2]


class TouchTracker:
    """Convert Linux input frames into one active contact for the DVP UI."""

    def __init__(self, device: Any, evdev: Any):
        self.device, self.e = device, evdev.ecodes
        caps = device.capabilities(absinfo=False)
        abs_codes = set(caps.get(self.e.EV_ABS, []))
        key_codes = set(caps.get(self.e.EV_KEY, []))
        self.multitouch = {
            self.e.ABS_MT_POSITION_X, self.e.ABS_MT_POSITION_Y, self.e.ABS_MT_TRACKING_ID
        } <= abs_codes
        self.x_code = self.e.ABS_MT_POSITION_X if self.multitouch else self.e.ABS_X
        self.y_code = self.e.ABS_MT_POSITION_Y if self.multitouch else self.e.ABS_Y
        x_info, y_info = device.absinfo(self.x_code), device.absinfo(self.y_code)
        if x_info is None or y_info is None:
            raise RuntimeError("Touchscreen did not publish usable X/Y axis information")
        self.axis_bounds = (x_info.min, x_info.max, y_info.min, y_info.max)
        self.slot = 0
        self.slots: dict[int, _Slot] = {}
        self.order = 0
        self.single_x: Optional[float] = None
        self.single_y: Optional[float] = None
        self.single_active = False
        self.has_btn_touch = self.e.BTN_TOUCH in key_codes
        self.has_tool_finger = self.e.BTN_TOOL_FINGER in key_codes
        self.has_pressure = self.e.ABS_PRESSURE in abs_codes
        pressure = device.absinfo(self.e.ABS_PRESSURE) if self.has_pressure else None
        self.pressure_min = pressure.min if pressure is not None else 0

    def _slot(self) -> _Slot:
        return self.slots.setdefault(self.slot, _Slot())

    def feed(self, event: Any) -> Optional[RawContact]:
        if self.multitouch and event.type == self.e.EV_ABS:
            if event.code == self.e.ABS_MT_SLOT:
                self.slot = event.value
            elif event.code == self.e.ABS_MT_TRACKING_ID:
                slot = self._slot()
                if event.value < 0:
                    slot.active = False
                    slot.identity = None
                else:
                    self.order += 1
                    slot.active = True
                    slot.identity = event.value
                    slot.x = slot.y = None
                    slot.order = self.order
            elif event.code == self.e.ABS_MT_POSITION_X:
                self._slot().x = event.value
            elif event.code == self.e.ABS_MT_POSITION_Y:
                self._slot().y = event.value
        elif not self.multitouch:
            if event.type == self.e.EV_ABS:
                if event.code == self.e.ABS_X:
                    self.single_x = event.value
                elif event.code == self.e.ABS_Y:
                    self.single_y = event.value
                elif event.code == self.e.ABS_PRESSURE and not (self.has_btn_touch or self.has_tool_finger):
                    self.single_active = event.value > self.pressure_min
            elif event.type == self.e.EV_KEY:
                if event.code == self.e.BTN_TOUCH:
                    self.single_active = bool(event.value)
                elif event.code == self.e.BTN_TOOL_FINGER and not self.has_btn_touch:
                    self.single_active = bool(event.value)
        if event.type != self.e.EV_SYN or event.code != self.e.SYN_REPORT:
            return None
        if self.multitouch:
            contacts = [slot for slot in self.slots.values()
                        if slot.active and slot.x is not None and slot.y is not None]
            if not contacts:
                return RawContact(False)
            contact = min(contacts, key=lambda item: item.order)
            return RawContact(True, contact.identity, contact.x, contact.y)
        if self.single_active and self.single_x is not None and self.single_y is not None:
            return RawContact(True, 0, self.single_x, self.single_y)
        return RawContact(False)


class UdpTransport:
    def __init__(self, config: SenderConfig):
        self.config = config
        self.destination = (config.destination_host, config.destination_port)
        self.expected_peer = (socket.gethostbyname(config.destination_host),
                              config.destination_port)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("0.0.0.0", config.local_port))
        self.local_port = self.socket.getsockname()[1]
        machine = "dvp-interface"
        try:
            machine = Path("/etc/machine-id").read_text(encoding="ascii").strip()[:12] or machine
        except OSError:
            pass
        self.session = f"{machine}-{secrets.token_hex(4)}"
        self.sequence = 0
        self.sent = 0
        self.errors = 0
        self._last_error_log = 0.0

    def _send(self, message: dict[str, Any]) -> bool:
        message.update(session=self.session, key=self.config.shared_key)
        payload = json.dumps(message, separators=(",", ":"), allow_nan=False).encode("utf-8")
        try:
            self.socket.sendto(payload, self.destination)
            self.sent += 1
            return True
        except OSError as exc:
            self.errors += 1
            now = time.monotonic()
            if now - self._last_error_log >= 2:
                LOG.warning("UDP send failed: %s", exc)
                self._last_error_log = now
            return False

    def _next_sequence(self) -> int:
        if self.sequence >= MAX_SEQUENCE:
            self.session = f"dvp-interface-{secrets.token_hex(8)}"
            self.sequence = 0
            self.hello()
        self.sequence += 1
        return self.sequence

    def hello(self) -> bool:
        return self._send({"type": "hello", "seq": 0})

    def heartbeat(self) -> bool:
        return self._send({"type": "heartbeat", "seq": self._next_sequence()})

    def touch(self, event: str, x: float, y: float) -> bool:
        return self._send({"type": "touch", "event": event, "x": x, "y": y,
                           "seq": self._next_sequence()})

    def receive_poweroff(self) -> bool:
        """Accept poweroff only from this session's configured DVP endpoint."""
        try:
            payload, peer = self.socket.recvfrom(MAX_CONTROL_BYTES + 1)
        except OSError as exc:
            LOG.warning("Control receive failed: %s", exc)
            return False
        if peer != self.expected_peer or len(payload) > MAX_CONTROL_BYTES:
            return False
        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            return False
        if not isinstance(message, dict) or set(message) != {"type", "session", "key"}:
            return False
        supplied_key = message.get("key")
        supplied_session = message.get("session")
        return (
            message.get("type") == "poweroff"
            and isinstance(supplied_key, str)
            and isinstance(supplied_session, str)
            and hmac.compare_digest(supplied_key, self.config.shared_key)
            and hmac.compare_digest(supplied_session, self.session)
        )

    def close(self) -> None:
        self.socket.close()


class MonitorTransport:
    """Local diagnostic sink used by --monitor; it never opens a network socket."""

    sent = 0
    errors = 0

    def hello(self) -> bool:
        return True

    def heartbeat(self) -> bool:
        return True

    def touch(self, event: str, x: float, y: float) -> bool:
        print(f"TOUCH {event.upper():4s} x={x:.4f} y={y:.4f}", flush=True)
        self.sent += 1
        return True

    def close(self) -> None:
        pass


def request_system_poweroff(stop: threading.Event) -> None:
    """Create the root-helper marker, then wait for systemd to stop the process."""
    marker = Path(os.environ.get("DVP_POWEROFF_REQUEST", str(DEFAULT_POWEROFF_REQUEST)))
    marker.write_text("authenticated request\n", encoding="ascii")
    LOG.warning("Authenticated DVP poweroff received; requesting shutdown")
    if not stop.wait(10):
        try:
            marker.unlink()
        except FileNotFoundError:
            pass
        raise RuntimeError("DVP poweroff helper did not respond within 10 seconds")


class EventTransmitter:
    def __init__(self, transport: Any, mapper: CoordinateMapper):
        self.transport, self.mapper = transport, mapper
        self.identity: Optional[int] = None
        self.last_xy = (0.5, 0.5)
        self.last_touch_sent = 0.0

    @property
    def active(self) -> bool:
        return self.identity is not None

    def _touch(self, event: str, xy: tuple[float, float], now: float) -> None:
        self.transport.touch(event, *xy)
        self.last_touch_sent = now

    def apply(self, contact: RawContact, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        if contact.active and contact.x is not None and contact.y is not None:
            xy = self.mapper.map(contact.x, contact.y)
            if self.identity is None:
                self.identity = contact.identity if contact.identity is not None else 0
                self.last_xy = xy
                self._touch("down", xy, now)
            elif contact.identity != self.identity:
                self._touch("up", self.last_xy, now)
                self.identity = contact.identity if contact.identity is not None else 0
                self.last_xy = xy
                self._touch("down", xy, now)
            elif xy != self.last_xy:
                self.last_xy = xy
                self._touch("move", xy, now)
        elif self.identity is not None:
            self._touch("up", self.last_xy, now)
            self.identity = None

    def refresh(self, interval: float, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        if self.active and now - self.last_touch_sent >= interval:
            self._touch("move", self.last_xy, now)

    def release(self) -> None:
        if self.identity is not None:
            self._touch("up", self.last_xy, time.monotonic())
            self.identity = None


def _mapper(config: SenderConfig, tracker: TouchTracker) -> CoordinateMapper:
    x_min, x_max, y_min, y_max = tracker.axis_bounds
    if config.x_min is not None:
        x_min, x_max = config.x_min, config.x_max
    if config.y_min is not None:
        y_min, y_max = config.y_min, config.y_max
    return CoordinateMapper(x_min, x_max, y_min, y_max, config.rotation,
                            config.invert_x, config.invert_y)


def run_device(config: SenderConfig, stop: threading.Event, override: str, monitor: bool) -> None:
    evdev, device, mode = find_touch_device(config, override)
    transport: Any = MonitorTransport() if monitor else UdpTransport(config)
    transmitter: Optional[EventTransmitter] = None
    grabbed = False
    try:
        tracker = TouchTracker(device, evdev)
        mapper = _mapper(config, tracker)
        transmitter = EventTransmitter(transport, mapper)
        if config.grab_device and not monitor:
            try:
                device.grab()
            except OSError as exc:
                raise RuntimeError(
                    f"Could not exclusively claim touchscreen {device.path}: {exc}"
                ) from exc
            grabbed = True
            LOG.info("Touchscreen exclusively claimed; local desktop input is suppressed")
        LOG.info("Touch input %s (%s), %s, axes x=%s..%s y=%s..%s",
                 device.path, device.name, mode, *tracker.axis_bounds)
        if not monitor:
            LOG.info("UDP %s:%d -> %s:%d, session=%s", "0.0.0.0",
                     transport.local_port, config.destination_host,
                     config.destination_port, transport.session)
        transport.hello()
        next_hello = time.monotonic() + config.hello_seconds
        next_heartbeat = time.monotonic() + config.heartbeat_seconds
        next_status = time.monotonic() + 10
        while not stop.is_set():
            now = time.monotonic()
            deadline = min(next_hello, next_heartbeat, next_status,
                           transmitter.last_touch_sent + config.active_refresh_seconds
                           if transmitter.active else now + 1)
            timeout = max(0.0, min(0.25, deadline - now))
            readers = [device.fd]
            if not monitor:
                readers.append(transport.socket)
            readable, _, _ = select.select(readers, [], [], timeout)
            if not monitor and transport.socket in readable and transport.receive_poweroff():
                transmitter.release()
                request_system_poweroff(stop)
                return
            if device.fd in readable:
                for event in device.read():
                    contact = tracker.feed(event)
                    if contact is not None:
                        transmitter.apply(contact)
            now = time.monotonic()
            transmitter.refresh(config.active_refresh_seconds, now)
            if now >= next_heartbeat:
                transport.heartbeat()
                next_heartbeat = now + config.heartbeat_seconds
            if now >= next_hello:
                transport.hello()
                next_hello = now + config.hello_seconds
            if now >= next_status:
                LOG.info("Running: packets=%d errors=%d touch=%s",
                         transport.sent, transport.errors,
                         "down" if transmitter.active else "up")
                next_status = now + 10
    finally:
        if transmitter is not None:
            transmitter.release()
        transport.close()
        if grabbed:
            try:
                device.ungrab()
            except OSError:
                pass
        device.close()


def run(config: SenderConfig, device_override: str = "", monitor: bool = False) -> int:
    stop = threading.Event()

    def request_stop(_signum: int, _frame: Any) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    while not stop.is_set():
        try:
            run_device(config, stop, device_override, monitor)
        except (OSError, RuntimeError) as exc:
            if stop.is_set():
                break
            LOG.warning("Touch input unavailable: %s; retrying in %.1f seconds",
                        exc, config.device_retry_seconds)
            stop.wait(config.device_retry_seconds)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH,
                        help="configuration JSON path")
    parser.add_argument("--device", default="", help="override /dev/input/eventN")
    parser.add_argument("--list-devices", action="store_true",
                        help="list readable touchscreen candidates and exit")
    parser.add_argument("--monitor", action="store_true",
                        help="print normalized touch events without using Wi-Fi")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.list_devices:
            return list_touch_devices()
        config = SenderConfig.load(args.config.resolve())
        return run(config, args.device, args.monitor)
    except (ConfigurationError, RuntimeError, OSError) as exc:
        LOG.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

