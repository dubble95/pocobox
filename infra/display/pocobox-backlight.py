#!/usr/bin/env python3
"""PocoBox Smart Backlight Controller: turns off the display when music is not playing.

Keeps the POCO F1 LCD backlight ON when:
  - LMS music is actively playing (`mode == "play"`), plus a short idle grace period
    (`DISPLAY_IDLE_OFF_SEC`) so track transitions and brief pauses do not flicker.
  - An English listening quiz, TTS response, manual voice listen, or recent wake/Q&A
    interaction is active (`display.keep_awake` from pocobox-voice).
  - The user taps the touchscreen or presses Power/Volume hardware keys while idle
    (`DISPLAY_TOUCH_WAKE_SEC`).

When none of the above is active (and `DISPLAY_POWER_MODE=auto`), powers down the
backlight via `/sys/class/backlight/backlight/{brightness,bl_power}` without disturbing
Wayland (`cage`) or Firefox kiosk rendering.
"""

from __future__ import annotations

import json
import logging
import os
import select
import signal
import struct
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pocobox-backlight")

_EV_KEY = 1
_EV_ABS = 3
_ABS_MT_POSITION_X = 53
_ABS_MT_POSITION_Y = 54
_ABS_MT_TRACKING_ID = 57
_INPUT_EVENT_FMT = "qqHHi"
_INPUT_EVENT_SIZE = struct.calcsize(_INPUT_EVENT_FMT)

_WAKE_DEVICE_INCLUDE = (
    "pwrkey",
    "power",
    "gpio-keys",
    "ft5x",
    "touch",
    "goodix",
    "synaptics",
    "novatek",
    "focaltech",
    "atmel",
)
_WAKE_DEVICE_EXCLUDE = (
    "headset",
    "jack",
    "dac",
    "audio",
    "haptics",
    "resin",
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(minimum, float(raw.strip()))
    except ValueError:
        return default


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(minimum, int(float(raw.strip())))
    except ValueError:
        return default


@dataclass
class ActivitySnapshot:
    music_playing: bool = False
    keep_awake: bool = False
    source: str = "unreachable"


def parse_display_state_payload(payload: dict[str, Any]) -> ActivitySnapshot:
    """Parse GET /display/state JSON from pocobox-voice into an ActivitySnapshot."""
    if not isinstance(payload, dict):
        return ActivitySnapshot(music_playing=False, keep_awake=False, source="invalid")

    np = payload.get("now_playing")
    np_mode = str(np.get("mode") or "").strip().lower() if isinstance(np, dict) else ""
    music_playing = np_mode == "play"

    disp = payload.get("display")
    if isinstance(disp, dict):
        if "music_playing" in disp:
            music_playing = bool(disp["music_playing"])
        keep_awake = bool(disp.get("keep_awake", False))
        return ActivitySnapshot(
            music_playing=music_playing,
            keep_awake=keep_awake,
            source="voice_api",
        )

    # Backward-compatible fallback if talking to an older daemon without `display` block
    quiz = payload.get("quiz")
    quiz_active = bool(quiz.get("active", False)) if isinstance(quiz, dict) else False
    return ActivitySnapshot(
        music_playing=music_playing,
        keep_awake=music_playing or quiz_active,
        source="voice_api_legacy",
    )


def fetch_activity_from_voice_api(url: str, timeout: float = 1.5) -> ActivitySnapshot | None:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
        return parse_display_state_payload(data)
    except Exception:
        return None


def fetch_activity_from_lms(lms_url: str, player_name: str = "pocobox", timeout: float = 1.5) -> ActivitySnapshot | None:
    """Fallback direct LMS JSON-RPC check when pocobox-voice is temporarily unavailable."""
    try:
        players_body = json.dumps({"id": 1, "method": "slim.request", "params": ["", ["players", 0, 20]]}).encode("utf-8")
        req = urllib.request.Request(lms_url, data=players_body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            players_data = json.loads(resp.read().decode("utf-8"))
        players = players_data.get("result", {}).get("players_loop", [])
        if not isinstance(players, list) or not players:
            return ActivitySnapshot(music_playing=False, keep_awake=False, source="lms_fallback")

        target_pid = None
        for p in players:
            if not isinstance(p, dict):
                continue
            if str(p.get("name") or "").strip().lower() == player_name.strip().lower():
                target_pid = str(p.get("playerid") or "")
                break
        if not target_pid:
            target_pid = str(players[0].get("playerid") or "")
        if not target_pid:
            return ActivitySnapshot(music_playing=False, keep_awake=False, source="lms_fallback")

        status_body = json.dumps(
            {"id": 2, "method": "slim.request", "params": [target_pid, ["status", "-", 1]]}
        ).encode("utf-8")
        req2 = urllib.request.Request(lms_url, data=status_body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req2, timeout=timeout) as resp2:
            status_data = json.loads(resp2.read().decode("utf-8"))
        mode = str(status_data.get("result", {}).get("mode") or "").strip().lower()
        playing = mode == "play"
        return ActivitySnapshot(music_playing=playing, keep_awake=playing, source="lms_fallback")
    except Exception:
        return None


def fetch_activity(state_url: str, lms_url: str, player_name: str) -> ActivitySnapshot:
    snap = fetch_activity_from_voice_api(state_url)
    if snap is not None:
        return snap
    lms_snap = fetch_activity_from_lms(lms_url, player_name=player_name)
    if lms_snap is not None:
        return lms_snap
    return ActivitySnapshot(music_playing=False, keep_awake=False, source="unreachable")


class DisplayPowerPolicy:
    """Pure state machine deciding whether the display backlight should be ON or OFF."""

    def __init__(
        self,
        *,
        power_mode: str = "auto",
        idle_off_sec: float = 10.0,
        touch_wake_sec: float = 15.0,
    ) -> None:
        mode = (power_mode or "auto").strip().lower()
        self.power_mode = mode if mode in ("auto", "always_on") else "auto"
        self.idle_off_sec = max(0.0, float(idle_off_sec))
        self.touch_wake_sec = max(0.0, float(touch_wake_sec))
        self.last_playing_at: float | None = None
        self.touch_wake_until: float = 0.0

    def on_input_event(self, now: float) -> None:
        if self.touch_wake_sec <= 0.0:
            return
        target = now + self.touch_wake_sec
        if target > self.touch_wake_until:
            self.touch_wake_until = target

    def evaluate(self, snapshot: ActivitySnapshot, now: float) -> tuple[bool, str]:
        if self.power_mode == "always_on":
            return True, "always_on"
        if snapshot.music_playing:
            self.last_playing_at = now
            return True, "music_playing"
        if snapshot.keep_awake:
            return True, "voice_or_quiz_active"
        if now < self.touch_wake_until:
            return True, "input_wake"
        if self.last_playing_at is not None and (now - self.last_playing_at) < self.idle_off_sec:
            return True, "idle_grace"
        return False, "idle_off"


class SysfsBacklight:
    """Controls Linux /sys/class/backlight/<name> brightness and bl_power."""

    def __init__(self, backlight_dir: str | Path = "/sys/class/backlight/backlight") -> None:
        self.base = Path(backlight_dir)
        self.brightness_path = self.base / "brightness"
        self.bl_power_path = self.base / "bl_power"
        self.max_brightness_path = self.base / "max_brightness"
        self.max_brightness = self._read_max_brightness()

    def _read_max_brightness(self) -> int:
        try:
            raw = self.max_brightness_path.read_text(encoding="utf-8").strip()
            val = int(raw)
            if val > 0:
                return val
        except Exception:
            pass
        return 4095

    def _write_int(self, path: Path, value: int) -> bool:
        try:
            path.write_text(f"{value}\n", encoding="utf-8")
            return True
        except OSError as exc:
            log.debug("Failed to write %s=%d: %s", path, value, exc)
            return False

    def set_on(self, brightness: int) -> bool:
        clamped = max(1, min(self.max_brightness, int(brightness)))
        ok_power = self._write_int(self.bl_power_path, 0) if self.bl_power_path.exists() else True
        ok_bright = self._write_int(self.brightness_path, clamped) if self.brightness_path.exists() else True
        return ok_power and ok_bright

    def set_off(self) -> bool:
        ok_bright = self._write_int(self.brightness_path, 0) if self.brightness_path.exists() else True
        ok_power = self._write_int(self.bl_power_path, 4) if self.bl_power_path.exists() else True
        return ok_bright and ok_power


def is_wake_input_device_name(name: str) -> bool:
    low = (name or "").strip().lower()
    if not low:
        return False
    if any(ex in low for ex in _WAKE_DEVICE_EXCLUDE):
        return False
    return any(inc in low for inc in _WAKE_DEVICE_INCLUDE)


def discover_wake_input_devices(
    sys_input_dir: str | Path = "/sys/class/input",
    dev_input_dir: str | Path = "/dev/input",
) -> list[tuple[Path, str]]:
    """Find touchscreen and hardware key /dev/input/eventN paths by sysfs device name."""
    sys_base = Path(sys_input_dir)
    dev_base = Path(dev_input_dir)
    found: list[tuple[Path, str]] = []
    if not sys_base.exists():
        return found
    for entry in sorted(sys_base.glob("event*")):
        name_file = entry / "device" / "name"
        try:
            dev_name = name_file.read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            continue
        if not is_wake_input_device_name(dev_name):
            continue
        dev_path = dev_base / entry.name
        if dev_path.exists():
            found.append((dev_path, dev_name))
    return found


def has_wake_trigger_in_input_bytes(raw: bytes) -> bool:
    """Return True if raw Linux input_event bytes contain a real key press or touch contact."""
    offset = 0
    total = len(raw)
    while offset + _INPUT_EVENT_SIZE <= total:
        _, _, ev_type, code, value = struct.unpack_from(_INPUT_EVENT_FMT, raw, offset)
        offset += _INPUT_EVENT_SIZE
        if ev_type == _EV_KEY and value == 1:
            return True
        if ev_type == _EV_ABS and code in (_ABS_MT_TRACKING_ID, _ABS_MT_POSITION_X, _ABS_MT_POSITION_Y):
            if code == _ABS_MT_TRACKING_ID and value < 0:
                continue
            return True
    return False


def _open_input_fds() -> dict[int, tuple[Path, str]]:
    fds: dict[int, tuple[Path, str]] = {}
    for dev_path, dev_name in discover_wake_input_devices():
        try:
            fd = os.open(str(dev_path), os.O_RDONLY | os.O_NONBLOCK)
            fds[fd] = (dev_path, dev_name)
        except OSError as exc:
            log.debug("Skipping input device %s (%s): %s", dev_path, dev_name, exc)
    return fds


def _close_input_fds(fds: dict[int, tuple[Path, str]]) -> None:
    for fd in list(fds.keys()):
        try:
            os.close(fd)
        except OSError:
            pass
        fds.pop(fd, None)


def main() -> int:
    brightness = _env_int("DISPLAY_BRIGHTNESS", 1800, minimum=1)
    power_mode = os.environ.get("DISPLAY_POWER_MODE", "auto").strip().lower()
    idle_off_sec = _env_float("DISPLAY_IDLE_OFF_SEC", 10.0, minimum=0.0)
    touch_wake_sec = _env_float("DISPLAY_TOUCH_WAKE_SEC", 15.0, minimum=0.0)
    poll_sec = _env_float("DISPLAY_POLL_SEC", 1.0, minimum=0.2)
    reassert_sec = _env_float("DISPLAY_REASSERT_SEC", 30.0, minimum=5.0)
    wake_on_input = _env_bool("DISPLAY_WAKE_ON_INPUT", True)
    state_url = os.environ.get("DISPLAY_STATE_URL", "http://127.0.0.1:8765/display/state").strip()
    lms_url = os.environ.get("LMS_URL", "http://127.0.0.1:9000/jsonrpc.js").strip()
    player_name = os.environ.get("PLAYER_NAME", "pocobox").strip() or "pocobox"
    backlight_dir = os.environ.get("BACKLIGHT_DIR", "/sys/class/backlight/backlight").strip()

    backlight = SysfsBacklight(backlight_dir)
    policy = DisplayPowerPolicy(
        power_mode=power_mode,
        idle_off_sec=idle_off_sec,
        touch_wake_sec=touch_wake_sec,
    )

    stop = False

    def _handle_sig(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _handle_sig)
    signal.signal(signal.SIGINT, _handle_sig)

    input_fds: dict[int, tuple[Path, str]] = {}
    if wake_on_input and policy.power_mode == "auto":
        input_fds = _open_input_fds()

    log.info(
        "Started PocoBox Smart Backlight Controller (mode=%s brightness=%d/%d idle_off=%.1fs touch_wake=%.1fs poll=%.1fs wake_inputs=%s)",
        policy.power_mode,
        brightness,
        backlight.max_brightness,
        policy.idle_off_sec,
        policy.touch_wake_sec,
        poll_sec,
        [f"{p.name}:{n}" for p, n in input_fds.values()],
    )

    applied_on: bool | None = None
    last_reason: str = ""
    last_apply_at: float = 0.0
    last_snapshot = ActivitySnapshot()
    next_poll_at: float = 0.0

    def _apply_state(want_on: bool, reason: str, now: float) -> None:
        nonlocal applied_on, last_reason, last_apply_at
        changed = applied_on is not want_on
        due_reassert = (now - last_apply_at) >= reassert_sec
        if not changed and not due_reassert:
            if reason != last_reason:
                last_reason = reason
            return
        if want_on:
            backlight.set_on(brightness)
        else:
            backlight.set_off()
        if changed:
            log.info(
                "Display backlight %s (reason=%s, brightness=%d, source=%s)",
                "ON" if want_on else "OFF",
                reason,
                brightness if want_on else 0,
                last_snapshot.source,
            )
        applied_on = want_on
        last_reason = reason
        last_apply_at = now

    try:
        while not stop:
            now = time.monotonic()
            if now >= next_poll_at:
                if policy.power_mode == "always_on":
                    last_snapshot = ActivitySnapshot(music_playing=False, keep_awake=True, source="always_on")
                else:
                    last_snapshot = fetch_activity(state_url, lms_url, player_name)
                want_on, reason = policy.evaluate(last_snapshot, now)
                _apply_state(want_on, reason, now)
                next_poll_at = now + poll_sec

            wait_sec = max(0.05, min(poll_sec, next_poll_at - time.monotonic()))
            if input_fds:
                try:
                    readable, _, _ = select.select(list(input_fds.keys()), [], [], wait_sec)
                except (InterruptedError, OSError):
                    readable = []
                triggered = False
                for fd in readable:
                    try:
                        chunk = os.read(fd, _INPUT_EVENT_SIZE * 32)
                    except BlockingIOError:
                        continue
                    except OSError:
                        info = input_fds.pop(fd, None)
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                        log.warning("Closed unreadable input device: %s", info)
                        continue
                    if not chunk:
                        continue
                    if has_wake_trigger_in_input_bytes(chunk):
                        triggered = True
                if triggered:
                    ev_now = time.monotonic()
                    policy.on_input_event(ev_now)
                    want_on, reason = policy.evaluate(last_snapshot, ev_now)
                    _apply_state(want_on, reason, ev_now)
            else:
                time.sleep(wait_sec)
    finally:
        _close_input_fds(input_fds)

    return 0


if __name__ == "__main__":
    sys.exit(main())
