"""Pure-Python synthesized audio feedback chimes for PocoBox voice interaction."""

from __future__ import annotations

import io
import math
import os
import struct
import subprocess
import wave

SAMPLE_RATE = 16000


def synthesize_tone_wav(notes: list[tuple[float, float]], volume: float = 0.25) -> bytes:
    """Generate a smooth 16-bit mono WAV from a list of (frequency_hz, duration_sec)."""
    frames = bytearray()
    for freq, dur in notes:
        n_samples = int(SAMPLE_RATE * dur)
        fade = max(1, int(SAMPLE_RATE * 0.015))
        for i in range(n_samples):
            env = 1.0
            if i < fade:
                env = i / fade
            elif i > n_samples - fade:
                env = (n_samples - i) / fade
            val = math.sin(2.0 * math.pi * freq * (i / SAMPLE_RATE)) * volume * env
            frames.extend(struct.pack("<h", int(max(-32767, min(32767, val * 32767)))))

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(bytes(frames))
    return buf.getvalue()


CHIMES: dict[str, list[tuple[float, float]]] = {
    "listen": [(587.33, 0.09), (880.00, 0.13)],       # D5 -> A5 rising chime
    "ok": [(659.25, 0.08), (987.77, 0.12)],           # E5 -> B5 confirmation
    "error": [(330.00, 0.11), (261.63, 0.15)],        # E4 -> C4 gentle error
}


def play_chime(kind: str = "listen") -> None:
    """Play a brief feedback chime via PulseAudio (best-effort, never raises)."""
    if os.environ.get("ENABLE_CHIME", "1").strip().lower() in ("0", "false", "no"):
        return
    notes = CHIMES.get(kind, CHIMES["listen"])
    wav_data = synthesize_tone_wav(notes)
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", "/run/user/10000")
    env.setdefault("PULSE_SERVER", "unix:/run/user/10000/pulse/native")
    try:
        subprocess.run(
            ["paplay", "--file-format=wav"],
            input=wav_data,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
            env=env,
            check=False,
        )
    except Exception:
        pass
