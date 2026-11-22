"""Pure-Python synthesized audio feedback chimes & internal speaker sink routing for PocoBox."""

from __future__ import annotations

import io
import math
import os
import re
import struct
import subprocess
import time
import wave

SAMPLE_RATE = 24000

_CACHED_SPEAKER_SINK: tuple[float, str | None] = (0.0, None)
_SPEAKER_CACHE_TTL_SEC = 30.0


def pulse_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", "/run/user/10000")
    env.setdefault("PULSE_SERVER", "unix:/run/user/10000/pulse/native")
    return env


def select_internal_speaker_sink(sinks_short_output: str) -> str | None:
    """Pick the built-in POCO F1 Speaker PulseAudio sink from `pactl list short sinks` output."""
    sinks: list[str] = []
    for line in (sinks_short_output or "").splitlines():
        parts = line.strip().split("\t")
        if len(parts) >= 2 and parts[1]:
            sinks.append(parts[1])
    if not sinks:
        return None

    # Priority 1: built-in platform-sound Speaker sink (never USB DAC or Headphones)
    for s in sinks:
        if "platform-sound" in s and "speaker" in s.lower() and not s.endswith(".monitor"):
            return s
    # Priority 2: any built-in platform-sound sink that is not Headphones/Earpiece
    for s in sinks:
        sl = s.lower()
        if "platform-sound" in sl and "headphone" not in sl and "earpiece" not in sl and not sl.endswith(".monitor"):
            return s
    # Priority 3: any non-USB Speaker sink
    for s in sinks:
        sl = s.lower()
        if "speaker" in sl and "usb-" not in sl and not sl.endswith(".monitor"):
            return s
    # Priority 4: any non-USB onboard/SoC/PCI sink (e.g. Raspberry Pi, Intel NUC, PC) that is not Headphones/Earpiece
    for s in sinks:
        sl = s.lower()
        if "usb-" not in sl and "headphone" not in sl and "earpiece" not in sl and not sl.endswith(".monitor"):
            return s
    return None


def find_usb_dac_pulse_card(cards_short_output: str) -> str | None:
    """Find the USB DAC PulseAudio card name from `pactl list short cards`."""
    for line in (cards_short_output or "").splitlines():
        parts = line.strip().split("\t")
        if len(parts) >= 2 and parts[1].startswith("alsa_card.usb-"):
            return parts[1]
    return None


def find_usb_dac_pulse_sink(sinks_short_output: str) -> str | None:
    """Find the USB DAC PulseAudio sink name from `pactl list short sinks`."""
    for line in (sinks_short_output or "").splitlines():
        parts = line.strip().split("\t")
        if len(parts) >= 2 and parts[1].startswith("alsa_output.usb-"):
            return parts[1]
    return None


def is_usb_dac_pulseaudio_active(env: dict[str, str] | None = None) -> bool:
    """Return True if a USB DAC sink is currently active and available in PulseAudio."""
    pa_env = env if env is not None else pulse_env()
    try:
        res = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            capture_output=True,
            text=True,
            timeout=2.0,
            env=pa_env,
            check=False,
        )
        if res.returncode == 0:
            return bool(find_usb_dac_pulse_sink(res.stdout))
    except Exception:
        pass
    return False


def enable_usb_dac_pulseaudio(env: dict[str, str] | None = None) -> str | None:
    """Enable USB Audio DAC profile in PulseAudio and route all browser/system audio to it.

    Ensures any background player holding ALSA (e.g. Squeezelite) releases the hardware device,
    forces PulseAudio to transition card profile from 'off' -> 'output:analog-stereo',
    sets the DAC as default sink, and moves all active sink inputs to it at 100% volume.
    """
    pa_env = env if env is not None else pulse_env()
    try:
        # 1. Stop Squeezelite if running so ALSA device is instantly free
        subprocess.run(
            ["sudo", "-n", "systemctl", "stop", "pocobox-squeezelite.service"],
            capture_output=True,
            timeout=2.0,
            check=False,
        )

        # 2. Find the USB DAC PulseAudio card
        res_cards = subprocess.run(
            ["pactl", "list", "short", "cards"],
            capture_output=True,
            text=True,
            timeout=2.0,
            env=pa_env,
            check=False,
        )
        if res_cards.returncode != 0:
            return None
        usb_card = find_usb_dac_pulse_card(res_cards.stdout)
        if not usb_card:
            return None

        # 3. Force profile transition: off -> output:analog-stereo
        # PulseAudio ignores set-card-profile if it already considers the profile active
        # even when the sink failed to initialize due to ALSA device busy.
        subprocess.run(
            ["pactl", "set-card-profile", usb_card, "off"],
            capture_output=True,
            timeout=2.0,
            env=pa_env,
            check=False,
        )
        time.sleep(0.08)
        subprocess.run(
            ["pactl", "set-card-profile", usb_card, "output:analog-stereo"],
            capture_output=True,
            timeout=2.0,
            env=pa_env,
            check=False,
        )

        # 4. Wait for newly created USB sink (poll up to 1.5s)
        usb_sink: str | None = None
        t_deadline = time.monotonic() + 1.5
        while time.monotonic() < t_deadline:
            res_sinks = subprocess.run(
                ["pactl", "list", "short", "sinks"],
                capture_output=True,
                text=True,
                timeout=2.0,
                env=pa_env,
                check=False,
            )
            usb_sink = find_usb_dac_pulse_sink(res_sinks.stdout)
            if usb_sink:
                break
            time.sleep(0.08)

        if not usb_sink:
            return None

        # 5. Set as default sink for browser and new streams
        subprocess.run(
            ["pactl", "set-default-sink", usb_sink],
            capture_output=True,
            timeout=2.0,
            env=pa_env,
            check=False,
        )

        # 6. Unmute and lock DAC sink volume to 100% (0.00 dB unity gain)
        subprocess.run(
            ["pactl", "set-sink-volume", usb_sink, "100%"],
            capture_output=True,
            timeout=2.0,
            env=pa_env,
            check=False,
        )
        subprocess.run(
            ["pactl", "set-sink-mute", usb_sink, "0"],
            capture_output=True,
            timeout=2.0,
            env=pa_env,
            check=False,
        )

        # 7. Move active sink-inputs (e.g. Firefox) to the USB DAC sink and ensure 100% volume
        res_inputs = subprocess.run(
            ["pactl", "list", "short", "sink-inputs"],
            capture_output=True,
            text=True,
            timeout=2.0,
            env=pa_env,
            check=False,
        )
        for line in (res_inputs.stdout or "").splitlines():
            parts = line.strip().split("\t")
            if parts and parts[0].isdigit():
                inp_id = parts[0]
                subprocess.run(
                    ["pactl", "move-sink-input", inp_id, usb_sink],
                    capture_output=True,
                    timeout=2.0,
                    env=pa_env,
                    check=False,
                )
                subprocess.run(
                    ["pactl", "set-sink-input-volume", inp_id, "100%"],
                    capture_output=True,
                    timeout=2.0,
                    env=pa_env,
                    check=False,
                )
                subprocess.run(
                    ["pactl", "set-sink-input-mute", inp_id, "0"],
                    capture_output=True,
                    timeout=2.0,
                    env=pa_env,
                    check=False,
                )

        return usb_sink
    except Exception:
        return None


def release_usb_dac_pulseaudio(env: dict[str, str] | None = None) -> bool:
    """Release USB Audio DAC from PulseAudio back to 'off' so Squeezelite has exclusive direct ALSA access."""
    pa_env = env if env is not None else pulse_env()
    try:
        speaker_sink = resolve_internal_speaker_sink(pa_env)
        if speaker_sink:
            subprocess.run(
                ["pactl", "set-default-sink", speaker_sink],
                capture_output=True,
                timeout=2.0,
                env=pa_env,
                check=False,
            )
            # Move active sink-inputs back to speaker sink
            res_inputs = subprocess.run(
                ["pactl", "list", "short", "sink-inputs"],
                capture_output=True,
                text=True,
                timeout=2.0,
                env=pa_env,
                check=False,
            )
            for line in (res_inputs.stdout or "").splitlines():
                parts = line.strip().split("\t")
                if parts and parts[0].isdigit():
                    subprocess.run(
                        ["pactl", "move-sink-input", parts[0], speaker_sink],
                        capture_output=True,
                        timeout=2.0,
                        env=pa_env,
                        check=False,
                    )

        # Turn USB audio card profile(s) to 'off'
        res_cards = subprocess.run(
            ["pactl", "list", "short", "cards"],
            capture_output=True,
            text=True,
            timeout=2.0,
            env=pa_env,
            check=False,
        )
        if res_cards.returncode == 0:
            for line in (res_cards.stdout or "").splitlines():
                parts = line.strip().split("\t")
                if len(parts) >= 2 and parts[1].startswith("alsa_card.usb-"):
                    subprocess.run(
                        ["pactl", "set-card-profile", parts[1], "off"],
                        capture_output=True,
                        timeout=2.0,
                        env=pa_env,
                        check=False,
                    )

        # Start Squeezelite so LMS playback works with direct ALSA access
        subprocess.run(
            ["sudo", "-n", "systemctl", "start", "pocobox-squeezelite.service"],
            capture_output=True,
            timeout=2.0,
            check=False,
        )
        return True
    except Exception:
        return False


def resolve_internal_speaker_sink(env: dict[str, str] | None = None, force_refresh: bool = False) -> str | None:
    """Resolve the PulseAudio sink name for the built-in speaker (honors VOICE_SPEAKER_SINK override)."""
    global _CACHED_SPEAKER_SINK
    override = os.environ.get("VOICE_SPEAKER_SINK", "").strip()
    if override:
        return override

    now = time.monotonic()
    cached_ts, cached_sink = _CACHED_SPEAKER_SINK
    if not force_refresh and cached_sink is not None and (now - cached_ts) < _SPEAKER_CACHE_TTL_SEC:
        return cached_sink

    pa_env = env if env is not None else pulse_env()
    try:
        res = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            capture_output=True,
            text=True,
            timeout=2.0,
            env=pa_env,
            check=False,
        )
        if res.returncode == 0:
            sink = select_internal_speaker_sink(res.stdout)
            _CACHED_SPEAKER_SINK = (now, sink)
            return sink
    except Exception:
        pass
    return cached_sink


def get_internal_speaker_volume(env: dict[str, str] | None = None) -> int:
    """Read current PulseAudio volume percentage (0..150) of the built-in speaker sink."""
    pa_env = env if env is not None else pulse_env()
    sink = resolve_internal_speaker_sink(pa_env)
    if not sink:
        return 100
    try:
        res = subprocess.run(
            ["pactl", "get-sink-volume", sink],
            capture_output=True,
            text=True,
            timeout=2.0,
            env=pa_env,
            check=False,
        )
        if res.returncode == 0:
            m = re.search(r"(\d+)%", res.stdout)
            if m:
                return max(0, min(150, int(m.group(1))))
    except Exception:
        pass
    return 100


def set_internal_speaker_volume(percent: int, env: dict[str, str] | None = None) -> int:
    """Set PulseAudio volume percentage (0..150) on the built-in speaker sink."""
    clamped = max(0, min(150, int(percent)))
    pa_env = env if env is not None else pulse_env()
    sink = resolve_internal_speaker_sink(pa_env)
    if not sink:
        return clamped
    try:
        subprocess.run(
            ["pactl", "set-sink-mute", sink, "1" if clamped == 0 else "0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
            env=pa_env,
            check=False,
        )
        subprocess.run(
            ["pactl", "set-sink-volume", sink, f"{clamped}%"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
            env=pa_env,
            check=False,
        )
    except Exception:
        pass
    return clamped


def synthesize_tone_wav(notes: list[tuple[float, float]], volume: float = 0.25) -> bytes:
    """Synthesize a bright, woody polyphonic marimba chime WAV from [(freq_hz, step_sec), ...].

    Models a real arch-tuned rosewood marimba bar:
    - Warm fundamental (1x f0) with smooth exponential resonance tail
    - Classic arch-cut 4th harmonic overtone (4x f0, two octaves up) with fast wooden decay
    - Brief woody mallet attack transient (2.7x f0)
    - Natural polyphonic ring-out so consecutive notes blend musically instead of cutting off
    """
    if not notes:
        return b""

    nyquist = SAMPLE_RATE * 0.46
    ring_tail_sec = 0.22
    total_dur = sum(max(0.03, d) for _, d in notes) + ring_tail_sec
    total_samples = max(1, int(SAMPLE_RATE * total_dur))
    mix = [0.0] * total_samples

    onset_sec = 0.0
    attack_sec = 0.0025  # 2.5ms crisp mallet strike
    for idx, (freq, step_dur) in enumerate(notes):
        start = int(onset_sec * SAMPLE_RATE)
        is_last = idx == len(notes) - 1
        note_dur = (0.28 if is_last else 0.20)
        n_note = min(total_samples - start, int(note_dur * SAMPLE_RATE))
        tau_fund = 0.115 if is_last else 0.080
        tau_over = 0.024
        tau_knock = 0.006
        w1 = 2.0 * math.pi * freq
        f4 = freq * 4.0
        w4 = 2.0 * math.pi * f4
        f_knock = freq * 2.75
        w_knock = 2.0 * math.pi * f_knock
        has_f4 = f4 < nyquist
        has_knock = f_knock < nyquist
        # Slight brightness lift on final resolved note
        note_gain = 1.05 if is_last else 0.92

        for i in range(n_note):
            t = i / SAMPLE_RATE
            atk = (t / attack_sec) if t < attack_sec else 1.0
            env1 = math.exp(-t / tau_fund)
            sample = math.sin(w1 * t) * env1
            if has_f4:
                sample += 0.30 * math.sin(w4 * t) * math.exp(-t / tau_over)
            if has_knock and t < 0.025:
                sample += 0.14 * math.sin(w_knock * t) * math.exp(-t / tau_knock)
            mix[start + i] += sample * atk * note_gain

        onset_sec += max(0.03, step_dur)

    # Normalize softly so polyphonic overlap never clips while honoring requested volume
    peak = max((abs(x) for x in mix), default=1.0)
    scale = (volume / peak) if peak > 1.0 else volume
    frames = bytearray(total_samples * 2)
    for i, x in enumerate(mix):
        val = int(max(-32767, min(32767, x * scale * 32767.0)))
        struct.pack_into("<h", frames, i * 2, val)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(bytes(frames))
    return buf.getvalue()


CHIMES: dict[str, list[tuple[float, float]]] = {
    # Bright cheerful marimba triad pop: C5 -> E5 -> C6
    "listen": [(523.25, 0.065), (659.25, 0.065), (1046.50, 0.08)],
    # Crisp rising confirmation: G5 -> C6
    "ok": [(783.99, 0.075), (1046.50, 0.09)],
    # Soft wooden descending marimba: E5 -> C5
    "error": [(659.25, 0.095), (523.25, 0.10)],
    # Bright unmistakable bell-like cue telling the user "your turn to answer now!": E5 -> B5
    "quiz_prompt": [(659.25, 0.07), (987.77, 0.12)],
    # Cheerful kid-friendly correct ding-dong-daeng arpeggio: C5 -> E5 -> G5 -> C6
    "quiz_correct": [(523.25, 0.06), (659.25, 0.06), (783.99, 0.065), (1046.50, 0.11)],
    # Gentle, soft retry marimba: G4 -> E4
    "quiz_wrong": [(392.00, 0.095), (329.63, 0.11)],
    # Celebratory quiz completion fanfare: C5 -> E5 -> G5 -> C6 -> E6
    "quiz_finish": [(523.25, 0.06), (659.25, 0.06), (783.99, 0.06), (1046.50, 0.085), (1318.51, 0.13)],
}


def play_chime(kind: str = "listen", *, volume: float | None = None, enabled: bool | None = None) -> None:
    """Play a brief feedback chime explicitly via the built-in speaker sink (best-effort, never raises)."""
    if enabled is False:
        return
    if enabled is None and os.environ.get("ENABLE_CHIME", "1").strip().lower() in ("0", "false", "no"):
        return
    notes = CHIMES.get(kind, CHIMES["listen"])
    chime_vol = max(0.05, min(1.0, float(volume if volume is not None else os.environ.get("VOICE_CHIME_VOLUME", "0.25"))))
    wav_data = synthesize_tone_wav(notes, volume=chime_vol)
    env = pulse_env()
    sink = resolve_internal_speaker_sink(env)
    cmd = ["paplay", "--file-format=wav"]
    if sink:
        cmd.append(f"--device={sink}")
    try:
        res = subprocess.run(
            cmd,
            input=wav_data,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
            env=env,
            check=False,
        )
        if res.returncode != 0 and sink:
            # Retry once after refreshing sink cache in case PulseAudio restarted
            fresh_sink = resolve_internal_speaker_sink(env, force_refresh=True)
            retry_cmd = ["paplay", "--file-format=wav"]
            if fresh_sink:
                retry_cmd.append(f"--device={fresh_sink}")
            subprocess.run(
                retry_cmd,
                input=wav_data,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
                env=env,
                check=False,
            )
    except Exception:
        pass
