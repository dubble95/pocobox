#!/usr/bin/env python3
"""PocoBox Wayland Kiosk Auto-Rotation Daemon (iio-sensor-proxy -> wlr-randr on cage)."""

from __future__ import annotations

import json
import logging
import os
import pty
import re
import select
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pocobox-autorotate")

ORIENTATION_TO_TRANSFORM: dict[str, str] = {
    "normal": "normal",
    "left-up": "90",
    "bottom-up": "180",
    "right-up": "270",
}

_RE_CHANGED = re.compile(r"Accelerometer orientation changed:\s*([a-z-]+)")
_RE_INITIAL = re.compile(r"Has accelerometer\s*\(orientation:\s*([a-z-]+)")


def parse_orientation_line(line: str) -> str | None:
    """Extract an iio-sensor-proxy orientation string from a monitor-sensor output line."""
    m = _RE_CHANGED.search(line)
    if m:
        return m.group(1)
    m = _RE_INITIAL.search(line)
    if m:
        return m.group(1)
    return None


def map_orientation(orient: str) -> str | None:
    return ORIENTATION_TO_TRANSFORM.get(orient.strip().lower())


def query_current_orientation() -> str | None:
    """Directly query SensorProxy DBus property when monitor-sensor emits 'undefined' on start."""
    try:
        from gi.repository import Gio, GLib  # type: ignore

        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        proxy = Gio.DBusProxy.new_sync(
            bus, 0, None, "net.hadess.SensorProxy", "/net/hadess/SensorProxy", "net.hadess.SensorProxy", None
        )
        proxy.call_sync("ClaimAccelerometer", None, 0, -1, None)
        prop = Gio.DBusProxy.new_sync(
            bus, 0, None, "net.hadess.SensorProxy", "/net/hadess/SensorProxy", "org.freedesktop.DBus.Properties", None
        )
        v = prop.call_sync("Get", GLib.Variant("(ss)", ("net.hadess.SensorProxy", "AccelerometerOrientation")), 0, -1, None)
        val = str(v.unpack()[0]).strip().lower()
        if val and val != "undefined":
            return val
    except Exception:
        pass
    return None


def _wayland_env(runtime_dir: str) -> dict[str, str]:
    env = os.environ.copy()
    env["XDG_RUNTIME_DIR"] = runtime_dir
    env.setdefault("WAYLAND_DISPLAY", "wayland-0")
    return env


def detect_output(runtime_dir: str, preferred: str = "") -> str | None:
    if preferred:
        return preferred
    wlr = shutil.which("wlr-randr")
    if not wlr:
        return None
    try:
        res = subprocess.run(
            [wlr, "--json"],
            env=_wayland_env(runtime_dir),
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout)
            if isinstance(data, list) and data and isinstance(data[0], dict):
                return str(data[0].get("name") or "")
    except Exception:
        pass
    return "DSI-1"


def apply_transform(runtime_dir: str, transform: str, preferred_output: str = "") -> bool:
    sock = Path(runtime_dir) / os.environ.get("WAYLAND_DISPLAY", "wayland-0")
    if not sock.exists():
        return False
    wlr = shutil.which("wlr-randr")
    if not wlr:
        return False
    output = detect_output(runtime_dir, preferred=preferred_output)
    if not output:
        return False
    try:
        res = subprocess.run(
            [wlr, "--output", output, "--transform", transform],
            env=_wayland_env(runtime_dir),
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
        if res.returncode == 0:
            log.info("Applied %s transform=%s", output, transform)
            return True
        log.warning("wlr-randr failed (%s): %s", res.returncode, res.stderr.strip())
    except Exception as exc:
        log.warning("wlr-randr error: %s", exc)
    return False


def _socket_id(sock: Path) -> tuple[int, int] | None:
    try:
        st = sock.stat()
        return (st.st_ino, int(st.st_mtime_ns))
    except OSError:
        return None


def restore_persistent_rotation(lock_file: Path) -> None:
    """Restore the persisted rotation lock (survives reboots) into the runtime lock.

    /run/user is tmpfs, so a UI-locked orientation would otherwise be lost on
    every reboot and the kiosk would boot portrait.
    """
    if lock_file.exists():
        return
    persist = Path(os.environ.get("DISPLAY_ROTATION_PERSIST", "/var/lib/pocobox/display-rotation"))
    try:
        val = persist.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return
    if val not in ORIENTATION_TO_TRANSFORM.values():
        return
    try:
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file.write_text(val, encoding="utf-8")
        # Voice daemon (uid 10000) must be able to overwrite/clear this lock later.
        os.chown(lock_file, 10000, 10000)
        log.info("Restored persisted rotation lock: %s", val)
    except OSError:
        pass


def main() -> int:
    rot_mode = os.environ.get("DISPLAY_ROTATION", "auto").strip().lower()
    kiosk_uid = os.environ.get("KIOSK_UID", "10000").strip()
    runtime_dir = os.environ.get("KIOSK_RUNTIME_DIR", f"/run/user/{kiosk_uid}")
    preferred_output = os.environ.get("DISPLAY_OUTPUT", "").strip()
    wayland_sock = Path(runtime_dir) / os.environ.get("WAYLAND_DISPLAY", "wayland-0")
    lock_file = Path(runtime_dir) / "pocobox-rotation-lock"

    stop = False

    def _handle_sig(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _handle_sig)
    signal.signal(signal.SIGINT, _handle_sig)

    restore_persistent_rotation(lock_file)

    # Fixed rotation mode if explicitly configured
    if rot_mode in ("normal", "90", "180", "270"):
        log.info("Fixed display rotation mode: %s", rot_mode)
        last_sock = None
        applied_rot = None
        while not stop:
            cur_sock = _socket_id(wayland_sock)
            if cur_sock and (cur_sock != last_sock or applied_rot != rot_mode):
                if apply_transform(runtime_dir, rot_mode, preferred_output):
                    last_sock = cur_sock
                    applied_rot = rot_mode
            time.sleep(1.0)
        return 0

    monitor_bin = shutil.which("monitor-sensor")
    if not monitor_bin or not shutil.which("wlr-randr"):
        log.warning("monitor-sensor or wlr-randr missing; auto-rotation disabled.")
        return 0

    log.info("Starting accelerometer auto-rotation (runtime_dir=%s)", runtime_dir)
    current_transform: str | None = None
    applied_sock: tuple[int, int] | None = None
    applied_transform: str | None = None

    while not stop:
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            [monitor_bin, "--accel"],
            stdin=subprocess.DEVNULL,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
        os.close(slave_fd)
        buf = ""
        try:
            while not stop:
                rlist, _, _ = select.select([master_fd], [], [], 1.0)
                if rlist:
                    try:
                        chunk = os.read(master_fd, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    buf += chunk.decode("utf-8", errors="ignore")
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        orient = parse_orientation_line(line)
                        if orient:
                            tr = map_orientation(orient)
                            if tr:
                                current_transform = tr

                # Read rotation lock if set manually (e.g. /display/rotate or UI HUD button)
                locked_target: str | None = None
                if lock_file.exists():
                    try:
                        val = lock_file.read_text(encoding="utf-8").strip().lower()
                        if val in ORIENTATION_TO_TRANSFORM.values():
                            locked_target = val
                    except OSError:
                        pass

                cur_sock = _socket_id(wayland_sock)

                if locked_target:
                    # Locked mode: ensure the locked target is applied, including across cage/greetd restarts
                    if cur_sock and (cur_sock != applied_sock or locked_target != applied_transform):
                        if apply_transform(runtime_dir, locked_target, preferred_output):
                            applied_sock = cur_sock
                            applied_transform = locked_target
                            current_transform = locked_target
                else:
                    # Auto mode: if orientation not yet known, query SensorProxy DBus directly
                    if current_transform is None:
                        dbus_orient = query_current_orientation()
                        if dbus_orient:
                            current_transform = map_orientation(dbus_orient)

                    # Apply detected orientation whenever changed OR when cage/greetd restarted
                    if current_transform and cur_sock:
                        if cur_sock != applied_sock or current_transform != applied_transform:
                            if apply_transform(runtime_dir, current_transform, preferred_output):
                                applied_sock = cur_sock
                                applied_transform = current_transform

                if proc.poll() is not None:
                    break
        finally:
            try:
                os.close(master_fd)
            except OSError:
                pass
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if not stop:
            time.sleep(2.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
