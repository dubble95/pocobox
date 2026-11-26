#!/usr/bin/env python3
"""PocoBox USB DAC hotplug monitor, WebRTC AEC router, and LMS auto-play daemon."""

from __future__ import annotations

import json
import logging
import os
import re
import select
import shlex
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
AUDIO_STATE_FILE = Path("/etc/pocobox/audio-state.json")
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
        if "/usb" not in real_dev:
            continue
        if not list(entry.glob(f"pcmC{card_num}D*p")):
            continue
        id_file = entry / "id"
        try:
            card_id = id_file.read_text(encoding="utf-8").strip()
        except OSError:
            card_id = str(card_num)
        found.append((card_num, card_id))
    return found


def run_pactl(args: list[str], check: bool = False) -> subprocess.CompletedProcess:
    """Run pactl as user UID 10000 against the headless PulseAudio socket."""
    cmd_str = " ".join(shlex.quote(a) for a in ["pactl", *args])
    env_prefix = "XDG_RUNTIME_DIR=/run/user/10000 PULSE_SERVER=unix:/run/user/10000/pulse/native"
    if os.geteuid() == 0:
        cmd = ["su", "-s", "/bin/sh", "user", "-c", f"{env_prefix} {cmd_str}"]
    else:
        cmd = ["/bin/sh", "-c", f"{env_prefix} {cmd_str}"]
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _list_pa_short(kind: str) -> list[str]:
    res = run_pactl(["list", "short", kind])
    if res.returncode != 0:
        return []
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def _find_pa_sink_for_usb(timeout: float = 5.0) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for line in _list_pa_short("sinks"):
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1].startswith("alsa_output.usb-"):
                return parts[1]
        time.sleep(0.4)
    return None


def _find_pa_internal_speaker() -> str | None:
    sinks = [line.split("\t")[1] for line in _list_pa_short("sinks") if len(line.split("\t")) >= 2]
    for s in sinks:
        if "platform-sound" in s and "Speaker" in s:
            return s
    for s in sinks:
        if "platform-sound" in s:
            return s
    return sinks[0] if sinks else None


def _find_pa_mic_source() -> str | None:
    sources = [line.split("\t")[1] for line in _list_pa_short("sources") if len(line.split("\t")) >= 2]
    for src in sources:
        if "platform-sound" in src and "Mic" in src and not src.endswith(".monitor"):
            return src
    for src in sources:
        if src.startswith("alsa_input.") and not src.endswith(".monitor"):
            return src
    return None


def boost_hardware_mic_preamp() -> None:
    """Set Qualcomm WCD9340 hardware mic preamp/decimator gain for far-field (2-3m) voice pickup."""
    controls = [
        ("ADC2 Volume", "20"),   # +5 dB analog preamp max
        ("ADC3 Volume", "20"),   # +5 dB analog preamp max
        ("DEC0 Volume", "104"),  # +20 dB digital decimator (L channel)
        ("DEC7 Volume", "83"),   # -5 dB vs 88; reduces WCD9340 mic clipping (571 clips/7d measured) while preserving far-field pickup
    ]
    for name, val in controls:
        try:
            subprocess.run(["amixer", "-c", "0", "cset", f"name={name}", val], capture_output=True, check=False)
        except FileNotFoundError:
            return


_AMIXER_SCONTROL_RE = re.compile(r"Simple mixer control '([^']+)',(\d+)")


def release_usb_cards_from_pulseaudio() -> list[str]:
    """Set PulseAudio profile to 'off' on USB audio cards so Squeezelite gets exclusive direct ALSA access."""
    released: list[str] = []
    try:
        cards = _list_pa_short("cards")
    except Exception:
        return released
    for line in cards:
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1].startswith("alsa_card.usb-"):
            card_name = parts[1]
            res = run_pactl(["set-card-profile", card_name, "off"])
            if res.returncode == 0:
                released.append(card_name)
    if released:
        log.info("Released USB audio card(s) from PulseAudio for direct ALSA: %s", ", ".join(released))
    return released


def set_usb_dac_hw_full_volume(card_num: int) -> list[str]:
    """Ensure all ALSA hardware playback mixer controls on the USB DAC are unmuted and at 100% (0 dB)."""
    if not cfg_bool("DAC_FORCE_HW_FULL_VOLUME", True):
        return []
    adjusted: list[str] = []
    try:
        res = subprocess.run(
            ["amixer", "-c", str(card_num), "scontrols"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return adjusted
    if res.returncode != 0:
        return adjusted

    for match in _AMIXER_SCONTROL_RE.finditer(res.stdout or ""):
        name, idx = match.group(1), match.group(2)
        ctrl = f"{name},{idx}"
        info = subprocess.run(
            ["amixer", "-c", str(card_num), "sget", ctrl],
            capture_output=True,
            text=True,
            check=False,
        )
        if info.returncode != 0:
            continue
        caps_line = ""
        for line in (info.stdout or "").splitlines():
            if "Capabilities:" in line:
                caps_line = line
                break
        caps = set(caps_line.split())
        has_pvol = bool(caps & {"pvolume", "pvolume-joined", "volume", "volume-joined"})
        has_pswitch = bool(caps & {"pswitch", "pswitch-joined"})
        if not has_pvol and not has_pswitch:
            continue

        set_args = ["amixer", "-c", str(card_num), "sset", ctrl]
        if has_pvol:
            set_args.append("100%")
        if has_pswitch:
            set_args.append("unmute")
        set_res = subprocess.run(set_args, capture_output=True, text=True, check=False)
        if set_res.returncode == 0:
            adjusted.append(ctrl)
    if adjusted:
        log.info("Locked USB DAC card %d ALSA playback mixer to 100%%/unmute: %s", card_num, ", ".join(adjusted))
    return adjusted


def configure_dac_lms_volume(client: LMSClient, player_id: str, is_startup: bool = False) -> None:
    """Apply bit-perfect 100% LMS volume settings when USB DAC is active."""
    fixed_vol = cfg_bool("DAC_LMS_FIXED_VOLUME", False)
    if fixed_vol:
        try:
            client.call(player_id, ["playerpref", "digitalVolumeControl", 0])
            client.set_volume(player_id, 100)
            log.info("Locked LMS player=%s to fixed 100%% digital volume (digitalVolumeControl=0)", player_id)
        except Exception as exc:
            log.warning("Failed to lock LMS fixed volume: %s", exc)
        return

    # Ensure digitalVolumeControl is 1 so voice auto-ducking works
    try:
        client.call(player_id, ["playerpref", "digitalVolumeControl", 1])
    except Exception as exc:
        log.warning("Failed to set digitalVolumeControl=1: %s", exc)

    raw_vol = cfg("DAC_ATTACH_LMS_VOLUME", "100").strip().lower()

    if raw_vol in ("keep", "preserve"):
        log.info("Preserving current LMS volume for player=%s (DAC_ATTACH_LMS_VOLUME=%s)", player_id, raw_vol)
        return

    if not raw_vol:
        return

    try:
        target_vol = max(0, min(100, int(raw_vol)))
        client.set_volume(player_id, target_vol)
        log.info(
            "Set LMS player=%s volume to %d%% (digitalVolumeControl=1 for voice ducking) on USB DAC attach",
            player_id,
            target_vol,
        )
    except Exception as exc:
        log.warning("Failed to set LMS volume on USB DAC attach: %s", exc)


def setup_pulse_aec(usb_card_id: str | None) -> bool:
    """Unload old module-echo-cancel and load WebRTC AEC bound to USB DAC (or internal speaker) + Mic."""
    boost_hardware_mic_preamp()
    aec_sink = cfg("AEC_SINK_NAME", "pocobox_aec_sink")
    aec_source = cfg("AEC_SOURCE_NAME", "pocobox_aec_mic")

    # Unload any existing module-echo-cancel instances cleanly
    for line in _list_pa_short("modules"):
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1] == "module-echo-cancel":
            run_pactl(["unload-module", parts[0]])

    source_master = _find_pa_mic_source()
    sink_master = _find_pa_sink_for_usb(timeout=5.0) if usb_card_id else _find_pa_internal_speaker()

    if not sink_master or not source_master:
        log.warning("Cannot configure WebRTC AEC (sink_master=%s source_master=%s)", sink_master, source_master)
        return False

    # Ensure hardware sink is unmuted & full scale, and keep PulseAudio source at clean 100% (Qualcomm UCM HW_VOLUME_CTRL mutes on <100%)
    run_pactl(["set-sink-mute", sink_master, "0"])
    run_pactl(["set-sink-volume", sink_master, "100%"])
    run_pactl(["set-source-mute", source_master, "0"])
    run_pactl(["set-source-volume", source_master, "100%"])

    # Pure Echo Cancellation + High-Pass Filter (avoid aggressive WebRTC noise_suppression that attenuates quiet speech)
    aec_args = '"analog_gain_control=0 digital_gain_control=0 noise_suppression=0 high_pass_filter=1 voice_detection=0"'
    res = run_pactl([
        "load-module",
        "module-echo-cancel",
        "aec_method=webrtc",
        "use_master_format=yes",
        f"sink_name={aec_sink}",
        f"source_name={aec_source}",
        f"sink_master={sink_master}",
        f"source_master={source_master}",
        f"aec_args={aec_args}",
    ])
    if res.returncode != 0:
        log.error("Failed to load module-echo-cancel: %s", res.stderr.strip())
        return False

    run_pactl(["set-default-sink", aec_sink])
    run_pactl(["set-default-source", aec_source])
    run_pactl(["set-sink-mute", aec_sink, "0"])
    run_pactl(["set-sink-volume", aec_sink, "100%"])
    log.info("WebRTC AEC active: sink_master=%s source_master=%s -> (%s, %s)", sink_master, source_master, aec_sink, aec_source)
    return True


def write_audio_state(card_id: str | None, aec_active: bool) -> None:
    try:
        AUDIO_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        AUDIO_STATE_FILE.write_text(
            json.dumps({
                "mode": "usb_dac" if card_id else "speaker",
                "card_id": card_id,
                "aec": aec_active,
            }),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("Failed writing %s: %s", AUDIO_STATE_FILE, exc)


_VISEXPORT_SUPPORTED: bool | None = None


def squeezelite_supports_visexport() -> bool:
    """Return True if installed squeezelite binary supports -v (VISEXPORT shared memory PCM tap)."""
    global _VISEXPORT_SUPPORTED
    if _VISEXPORT_SUPPORTED is not None:
        return _VISEXPORT_SUPPORTED
    try:
        res = subprocess.run(["/usr/local/bin/squeezelite", "-?"], capture_output=True, text=True, timeout=3.0)
        help_text = ((res.stdout or "") + "\n" + (res.stderr or "")).lower()
        _VISEXPORT_SUPPORTED = ("visexport" in help_text) or ("-v" in help_text and ("visualiz" in help_text or "visualis" in help_text))
    except Exception:
        _VISEXPORT_SUPPORTED = False
    return _VISEXPORT_SUPPORTED


def ensure_visexport_arg(extra_args: str) -> str:
    """Append -v when ENABLE_VISEXPORT=1 and squeezelite supports -DVISEXPORT (zero impact on ALSA DAC)."""
    if not cfg_bool("ENABLE_VISEXPORT", True):
        return extra_args
    tokens = shlex.split(extra_args)
    if "-v" in tokens:
        return extra_args
    if squeezelite_supports_visexport():
        return f"{extra_args} -v".strip()
    return extra_args


def write_squeezelite_env(output_device: str, extra_args: str, pulse_sink: str | None = None) -> bool:
    """Write /etc/pocobox/squeezelite.env. Return True if content changed."""
    final_extra = ensure_visexport_arg(extra_args)
    lines = [
        "# Managed by pocobox-dac-autoplay",
        f"SQUEEZELITE_NAME={cfg('PLAYER_NAME', 'pocobox')}",
        f"SQUEEZELITE_SERVER={cfg('LMS_HOST', '127.0.0.1')}",
        f"SQUEEZELITE_OUTPUT={output_device}",
        f"SQUEEZELITE_EXTRA_ARGS={final_extra}",
    ]
    if pulse_sink:
        lines.append(f"PULSE_SINK={pulse_sink}")
    content = "\n".join(lines) + "\n"
    old = SQUEEZELITE_ENV.read_text(encoding="utf-8") if SQUEEZELITE_ENV.exists() else ""
    if old == content:
        return False
    SQUEEZELITE_ENV.parent.mkdir(parents=True, exist_ok=True)
    SQUEEZELITE_ENV.write_text(content, encoding="utf-8")
    return True


def stop_squeezelite() -> None:
    subprocess.run(["systemctl", "stop", "pocobox-squeezelite.service"], check=False)


def restart_squeezelite() -> None:
    subprocess.run(["systemctl", "restart", "pocobox-squeezelite.service"], check=True)


def restart_voice_if_active() -> None:
    res = subprocess.run(["systemctl", "is-active", "--quiet", "pocobox-voice.service"], check=False)
    if res.returncode == 0:
        subprocess.run(["systemctl", "restart", "pocobox-voice.service"], check=False)


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
    player_id = wait_for_player(client, timeout=15.0)
    if not player_id:
        log.warning("Player did not reconnect to LMS within timeout; cannot configure/auto-play.")
        return

    configure_dac_lms_volume(client, player_id)

    if not cfg_bool("AUTOPLAY_ON_ATTACH", True):
        log.info("AUTOPLAY_ON_ATTACH disabled; skipping auto-play.")
        return

    try:
        status = client.player_status(player_id)
        tracks = int(status.get("playlist_tracks", 0) or 0)
        mode = status.get("mode", "")
        log.info("LMS player=%s mode=%s playlist_tracks=%d", player_id, mode, tracks)

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
    new_dac_num = cards[0][0] if cards else None
    new_dac = cards[0][1] if cards else None
    enable_aec = cfg_bool("ENABLE_AEC", True)
    aec_sink = cfg("AEC_SINK_NAME", "pocobox_aec_sink")

    if new_dac == active_dac and not is_startup:
        return active_dac

    if new_dac is not None:
        boost_hardware_mic_preamp()
        if new_dac_num is not None:
            set_usb_dac_hw_full_volume(new_dac_num)
        if enable_aec:
            log.info("USB DAC attached: card_id=%s -> configuring WebRTC AEC pipeline", new_dac)
            stop_squeezelite()
            aec_ok = setup_pulse_aec(new_dac)
            if aec_ok:
                extra_args = cfg("FALLBACK_SQUEEZELITE_EXTRA_ARGS", "-m 02:70:6f:63:6f:01 -C 5 -W -R -u hLE")
                write_squeezelite_env("pulse", extra_args, pulse_sink=aec_sink)
                write_audio_state(new_dac, aec_active=True)
                restart_squeezelite()
                restart_voice_if_active()
                if not is_startup:
                    trigger_autoplay(client)
                else:
                    player_id = wait_for_player(client, timeout=10.0)
                    if player_id:
                        configure_dac_lms_volume(client, player_id)
                return new_dac
            log.warning("AEC setup failed for USB DAC %s; falling back to direct ALSA", new_dac)

        release_usb_cards_from_pulseaudio()
        output_dev = cfg("DAC_OUTPUT_TEMPLATE", "hw:CARD={card_id},DEV=0").format(card_id=new_dac)
        extra_args = cfg("DAC_SQUEEZELITE_EXTRA_ARGS", "-m 02:70:6f:63:6f:01 -p 95 -a 80:4::1 -C 1 -W -R -u vLE")
        log.info("USB DAC attached: card_id=%s -> switching Squeezelite to %s", new_dac, output_dev)
        changed = write_squeezelite_env(output_dev, extra_args)
        write_audio_state(new_dac, aec_active=False)
        if changed or not is_startup:
            restart_squeezelite()
        if not is_startup:
            trigger_autoplay(client)
        else:
            player_id = wait_for_player(client, timeout=10.0)
            if player_id:
                configure_dac_lms_volume(client, player_id)
        return new_dac

    # No USB DAC present
    boost_hardware_mic_preamp()
    if active_dac is not None:
        log.info("USB DAC detached (was %s)", active_dac)
        trigger_pause(client)
        try:
            player_id = client.resolve_player_id(cfg("PLAYER_NAME", "pocobox"))
            client.call(player_id, ["playerpref", "digitalVolumeControl", 1])
            log.info("Restored LMS player=%s digitalVolumeControl=1 on DAC detach", player_id)
        except Exception as exc:
            log.debug("Could not restore digitalVolumeControl on detach: %s", exc)

    fallback_out = cfg("FALLBACK_OUTPUT", "pulse")
    fallback_args = cfg("FALLBACK_SQUEEZELITE_EXTRA_ARGS", "-m 02:70:6f:63:6f:01 -C 1 -W -R -u hLE")
    aec_ok = False
    pulse_sink = None
    if enable_aec:
        aec_ok = setup_pulse_aec(None)
        if aec_ok:
            pulse_sink = aec_sink

    changed = write_squeezelite_env(fallback_out, fallback_args, pulse_sink=pulse_sink)
    write_audio_state(None, aec_active=aec_ok)
    if changed or active_dac is not None or (is_startup and aec_ok):
        log.info("Switching Squeezelite to fallback output: %s (aec=%s)", fallback_out, aec_ok)
        restart_squeezelite()
        if aec_ok:
            restart_voice_if_active()
    return None


def main() -> int:
    client = LMSClient(host=cfg("LMS_HOST", "127.0.0.1"), port=int(cfg("LMS_PORT", "9000")))
    debounce = float(cfg("DEBOUNCE_SECONDS", "1.5"))

    log.info("Starting PocoBox USB DAC Auto-Play & AEC daemon (ENABLE_AEC=%s)...", cfg_bool("ENABLE_AEC", True))
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
