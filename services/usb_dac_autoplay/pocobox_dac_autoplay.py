#!/usr/bin/env python3
"""PocoBox USB DAC hotplug monitor and LMS auto-play daemon."""

from __future__ import annotations

import logging
import os
import select
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/srv/pocobox/bin")
from lms_client import LMSClient  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("pocobox-dac-autoplay")

SQUEEZELITE_ENV = Path("/etc/pocobox/squeezelite.env")
SOUND_SYSFS = Path("/sys/class/sound")


def cfg(name: str, default: str) -> str:
    return os.environ.get(name, default)


def cfg_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def find_usb_audio_cards() -> list[tuple[int, str]]:
    """Return sorted list of (card_num, alsa_card_id) for attached USB playback audio devices."""
    found: list[tuple[int, str]] = []
    if not SOUND_SYSFS.exists():
        return found

    for entry in sorted(SOUND_SYSFS.glob("card[0-9]*")):
        name = entry.name
        suffix = name.removeprefix("card")
        if not suffix.isdigit():
            continue
        card_num = int(suffix)
        dev_link = entry / "device"
        if not dev_link.exists():
            continue
        try:
            real_dev = os.path.realpath(dev_link)
        except OSError:
            continue
        # Detect USB audio cards via sysfs path containing /usb
        if "/usb" not in real_dev:
            continue
        # Ensure card has at least one playback PCM device (pcmC<N>D*p)
        if not list(entry.glob(f"pcmC{card_num}D*p")):
            continue
        id_file = entry / "id"
        try:
            card_id = id_file.read_text(encoding="utf-8").strip()
        except OSError:
            card_id = str(card_num)
        found.append((card_num, card_id))
    return found


def write_squeezelite_env(output_device: str, extra_args: str) -> bool:
    """Write /etc/pocobox/squeezelite.env. Return True if content changed."""
    content = (
        "# Managed by pocobox-dac-autoplay\n"
        f"SQUEEZELITE_NAME={cfg('PLAYER_NAME', 'pocobox')}\n"
        f"SQUEEZELITE_SERVER={cfg('LMS_HOST', '127.0.0.1')}\n"
        f"SQUEEZELITE_OUTPUT={output_device}\n"
        f"SQUEEZELITE_EXTRA_ARGS={extra_args}\n"
    )
    old = SQUEEZELITE_ENV.read_text(encoding="utf-8") if SQUEEZELITE_ENV.exists() else ""
    if old == content:
        return False
    SQUEEZELITE_ENV.parent.mkdir(parents=True, exist_ok=True)
    SQUEEZELITE_ENV.write_text(content, encoding="utf-8")
    return True


def restart_squeezelite() -> None:
    subprocess.run(["systemctl", "restart", "pocobox-squeezelite.service"], check=True)


def wait_for_player(client: LMSClient, timeout: float = 12.0) -> str | None:
    preferred_mac = cfg("PLAYER_MAC", "02:70:6f:63:6f:01").lower()
    preferred_name = cfg("PLAYER_NAME", "pocobox").lower()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            for p in client.list_players():
                if not p.get("connected"):
                    continue
                if p.get("playerid", "").lower() == preferred_mac or p.get("name", "").lower() == preferred_name:
                    return p["playerid"]
        except Exception:
            pass
        time.sleep(0.5)
    return None


def trigger_autoplay(client: LMSClient) -> None:
    if not cfg_bool("AUTOPLAY_ON_ATTACH", True):
        log.info("AUTOPLAY_ON_ATTACH disabled; skipping auto-play.")
        return

    player_id = wait_for_player(client, timeout=15.0)
    if not player_id:
        log.warning("Player did not reconnect to LMS within timeout; cannot auto-play.")
        return

    try:
        status = client.player_status(player_id)
        tracks = int(status.get("playlist_tracks", 0) or 0)
        mode = status.get("mode", "")
        log.info("LMS player=%s mode=%s playlist_tracks=%d", player_id, mode, tracks)

        # Ensure player is powered on
        client.call(player_id, ["power", 1])

        if tracks > 0:
            log.info("Resuming existing LMS playlist queue...")
            client.play(player_id)
            return

        if cfg_bool("FALLBACK_TIDAL_MIX", True):
            log.info("Queue is empty; attempting fallback TIDAL My Mix...")
            root = client.tidal_items(player_id, limit=20)
            for item in root.get("loop_loop", []):
                if item.get("name", "").lower() == "my mix":
                    mix_id = item.get("id")
                    if mix_id:
                        mixes = client.tidal_items(player_id, item_id=mix_id, limit=10)
                        for m in mixes.get("loop_loop", []):
                            if m.get("id"):
                                log.info("Playing TIDAL fallback item: %s (%s)", m.get("name"), m.get("id"))
                                client.tidal_play_item(player_id, m["id"])
                                return
        log.info("Queue is empty and no fallback track started.")
    except Exception as exc:
        log.error("Failed to trigger LMS auto-play: %s", exc)


def trigger_pause(client: LMSClient) -> None:
    if not cfg_bool("PAUSE_ON_DETACH", True):
        return
    try:
        player_id = client.resolve_player_id(cfg("PLAYER_NAME", "pocobox"))
        log.info("Pausing LMS player=%s on USB DAC detach...", player_id)
        client.pause(player_id, 1)
    except Exception as exc:
        log.warning("Could not pause LMS player on detach: %s", exc)


def apply_state(client: LMSClient, active_dac: str | None, is_startup: bool = False) -> str | None:
    cards = find_usb_audio_cards()
    new_dac = cards[0][1] if cards else None

    if new_dac == active_dac and not is_startup:
        return active_dac

    if new_dac is not None:
        output_dev = cfg("DAC_OUTPUT_TEMPLATE", "plughw:CARD={card_id},DEV=0").format(card_id=new_dac)
        extra_args = cfg("DAC_SQUEEZELITE_EXTRA_ARGS", "-m 02:70:6f:63:6f:01 -p 95 -a 80:4::1 -C 5 -W")
        log.info("USB DAC attached: card_id=%s -> switching Squeezelite to %s", new_dac, output_dev)
        changed = write_squeezelite_env(output_dev, extra_args)
        if changed or not is_startup:
            restart_squeezelite()
        trigger_autoplay(client)
        return new_dac

    # No USB DAC present
    if active_dac is not None:
        log.info("USB DAC detached (was %s)", active_dac)
        trigger_pause(client)

    fallback_out = cfg("FALLBACK_OUTPUT", "pulse")
    fallback_args = cfg("FALLBACK_SQUEEZELITE_EXTRA_ARGS", "-m 02:70:6f:63:6f:01 -C 5 -W -R -u hLE")
    changed = write_squeezelite_env(fallback_out, fallback_args)
    if changed or active_dac is not None:
        log.info("Switching Squeezelite back to fallback output: %s", fallback_out)
        restart_squeezelite()
    return None


def main() -> int:
    client = LMSClient(host=cfg("LMS_HOST", "127.0.0.1"), port=int(cfg("LMS_PORT", "9000")))
    debounce = float(cfg("DEBOUNCE_SECONDS", "1.5"))

    log.info("Starting PocoBox USB DAC Auto-Play daemon...")
    active_dac = apply_state(client, active_dac=None, is_startup=True)
    log.info("Initial state: active_dac=%s", active_dac)

    proc = subprocess.Popen(
        ["udevadm", "monitor", "--udev", "--subsystem-match=sound", "--subsystem-match=usb"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    fd = proc.stdout.fileno()

    pending_deadline: float | None = None
    while True:
        timeout = 2.0
        if pending_deadline is not None:
            timeout = max(0.05, pending_deadline - time.monotonic())

        ready, _, _ = select.select([fd], [], [], timeout)
        if ready:
            line = proc.stdout.readline()
            if not line:
                log.warning("udevadm monitor exited; restarting loop")
                return 1
            if "UDEV" in line and (" add " in line or " remove " in line or " change " in line):
                pending_deadline = time.monotonic() + debounce
        elif pending_deadline is not None and time.monotonic() >= pending_deadline:
            pending_deadline = None
            try:
                active_dac = apply_state(client, active_dac=active_dac, is_startup=False)
            except Exception as exc:
                log.error("Error applying USB DAC state: %s", exc)


if __name__ == "__main__":
    sys.exit(main())
