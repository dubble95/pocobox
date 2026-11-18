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

    # Fixed rotation mode if explicitly configured
    if rot_mode in ("normal", "90", "180", "270"):
        log.info("Fixed display rotation mode: %s", rot_mode)
        last_sock = None
        while not stop:
            cur_sock = _socket_id(wayland_sock)
            if cur_sock and cur_sock != last_sock:
                if apply_transform(runtime_dir, rot_mode, preferred_output):
                    last_sock = cur_sock
            time.sleep(2.0)
        return 0

    monitor_bin = shutil.which("monitor-sensor")
    if not monitor_bin or not shutil.which("wlr-randr"):
        log.warning("monitor-sensor or wlr-randr missing; auto-rotation disabled.")
        return 0

    log.info("Starting accelerometer auto-rotation (runtime_dir=%s)", runtime_dir)
    current_transform: str | None = None
    applied_sock: tuple[int, int] | None = None

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
                rlist, _, _ = select.select([master_fd], [], [], 2.0)
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
                        if not orient:
                            continue
                        transform = map_orientation(orient)
                        if not transform:
                            continue
                        if lock_file.exists():
                            continue
                        if transform != current_transform:
                            current_transform = transform
                            cur_sock = _socket_id(wayland_sock)
                            if cur_sock and apply_transform(runtime_dir, transform, preferred_output):
                                applied_sock = cur_sock
                # Re-apply current orientation if cage/greetd restarted
                if current_transform and not lock_file.exists():
                    cur_sock = _socket_id(wayland_sock)
                    if cur_sock and cur_sock != applied_sock:
                        if apply_transform(runtime_dir, current_transform, preferred_output):
                            applied_sock = cur_sock
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
