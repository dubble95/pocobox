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
    return None


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
