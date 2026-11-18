"""Speech-band DSP filters (250Hz-3500Hz bandpass) and RNNoise neural denoiser for PocoBox.

- SpeechBandpassFilter: Cascaded 2nd-order Butterworth HPF (250Hz) + LPF (3500Hz)
  to remove low-frequency speaker bass/kick energy and high-frequency hiss.
- RNNoiseProcessor: Optional ctypes wrapper around native librnnoise.so.0 (48kHz, 10ms frames)
  that suppresses room noise (-40dB+) and background music (-10dB) while keeping speech intact.
- Works cleanly with zero mandatory external dependencies (graceful pure-Python fallback).
"""

from __future__ import annotations

import array
import ctypes
import ctypes.util
import io
import logging
import math
import os
import wave

log = logging.getLogger("pocobox-voice.dsp")

RNNOISE_FRAME_SAMPLES = 480  # 10ms @ 48kHz
RNNOISE_SAMPLE_RATE = 48000


class BiquadFilter:
    """Direct Form II Transposed biquad IIR filter with persistent streaming state."""

    def __init__(self, b0: float, b1: float, b2: float, a1: float, a2: float) -> None:
        self.b0 = b0
        self.b1 = b1
        self.b2 = b2
        self.a1 = a1
        self.a2 = a2
        self.z1 = 0.0
        self.z2 = 0.0

    def reset(self) -> None:
        self.z1 = 0.0
        self.z2 = 0.0

    def process_samples(self, samples: array.array) -> array.array:
        b0, b1, b2, a1, a2 = self.b0, self.b1, self.b2, self.a1, self.a2
        z1, z2 = self.z1, self.z2
        out = array.array("h", [0] * len(samples))
        for i, x in enumerate(samples):
            y = b0 * x + z1
            z1 = b1 * x - a1 * y + z2
            z2 = b2 * x - a2 * y
            if y > 32767.0:
                out[i] = 32767
            elif y < -32768.0:
                out[i] = -32768
            else:
                out[i] = int(y)
        self.z1 = z1
        self.z2 = z2
        return out


def make_highpass(cutoff_hz: float = 250.0, sample_rate: int = 16000, q: float = 0.70710678) -> BiquadFilter:
    w0 = 2.0 * math.pi * cutoff_hz / sample_rate
    cosw = math.cos(w0)
    alpha = math.sin(w0) / (2.0 * q)
    a0 = 1.0 + alpha
    return BiquadFilter(
        b0=((1.0 + cosw) * 0.5) / a0,
        b1=(-(1.0 + cosw)) / a0,
        b2=((1.0 + cosw) * 0.5) / a0,
        a1=(-2.0 * cosw) / a0,
        a2=(1.0 - alpha) / a0,
    )


def make_lowpass(cutoff_hz: float = 3500.0, sample_rate: int = 16000, q: float = 0.70710678) -> BiquadFilter:
    w0 = 2.0 * math.pi * cutoff_hz / sample_rate
    cosw = math.cos(w0)
    alpha = math.sin(w0) / (2.0 * q)
    a0 = 1.0 + alpha
    return BiquadFilter(
        b0=((1.0 - cosw) * 0.5) / a0,
        b1=(1.0 - cosw) / a0,
        b2=((1.0 - cosw) * 0.5) / a0,
        a1=(-2.0 * cosw) / a0,
        a2=(1.0 - alpha) / a0,
    )


class SpeechBandpassFilter:
    """Cascaded 250Hz HPF + 3500Hz LPF tailored for human speech intelligibility."""

    def __init__(
        self,
        sample_rate: int = 16000,
        low_hz: float = 250.0,
        high_hz: float = 3500.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.low_hz = low_hz
        self.high_hz = high_hz
        self._hpf = make_highpass(low_hz, sample_rate)
        self._lpf = make_lowpass(high_hz, sample_rate)

    def reset(self) -> None:
        self._hpf.reset()
        self._lpf.reset()

    def process_pcm(self, pcm_bytes: bytes) -> bytes:
        n = len(pcm_bytes) // 2
        if n == 0:
            return pcm_bytes
        samples = array.array("h")
        samples.frombytes(pcm_bytes[: n * 2])
        filtered = self._lpf.process_samples(self._hpf.process_samples(samples))
        return filtered.tobytes()


def filter_wav_speech_band(
    wav_bytes: bytes,
    low_hz: float = 250.0,
    high_hz: float = 3500.0,
) -> bytes:
    """Apply 250Hz-3500Hz bandpass filter to a 16-bit mono WAV payload."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        rate = wf.getframerate()
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())

    if sw != 2 or ch != 1 or not raw:
        return wav_bytes

    safe_high = min(high_hz, rate * 0.45)
    safe_low = min(low_hz, safe_high * 0.5)
    bp = SpeechBandpassFilter(sample_rate=rate, low_hz=safe_low, high_hz=safe_high)
    out_pcm = bp.process_pcm(raw)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(out_pcm)
    return buf.getvalue()


def extract_mono_channel(stereo_pcm: bytes, channel: str = "right") -> bytes:
    """Extract left, right, or averaged mono 16-bit PCM from interleaved 2ch s16le bytes."""
    n_samples = len(stereo_pcm) // 2
    if n_samples < 2:
        return b""
    samples = array.array("h")
    samples.frombytes(stereo_pcm[: (n_samples // 2) * 4])
    ch = channel.strip().lower()
    if ch == "left":
        return samples[0::2].tobytes()
    if ch == "right":
        return samples[1::2].tobytes()
    # Average L + R
    n_frames = len(samples) // 2
    out = array.array("h", [0] * n_frames)
    for i in range(n_frames):
        out[i] = (samples[2 * i] + samples[2 * i + 1]) // 2
    return out.tobytes()


def decimate_48k_to_16k(pcm48_mono: bytes) -> bytes:
    """Fast 3:1 box-averaged decimation from 48kHz mono s16le to 16kHz mono s16le."""
    n = (len(pcm48_mono) // 2) // 3
    if n == 0:
        return b""
    try:
        import numpy as np  # type: ignore

        arr = np.frombuffer(pcm48_mono[: n * 6], dtype=np.int16).reshape(n, 3)
        return arr.mean(axis=1).astype(np.int16).tobytes()
    except Exception:
        samples = array.array("h")
        samples.frombytes(pcm48_mono[: n * 6])
        out = array.array("h", [0] * n)
        for i in range(n):
            j = i * 3
            out[i] = (samples[j] + samples[j + 1] + samples[j + 2]) // 3
        return out.tobytes()


class RNNoiseProcessor:
    """Real-time neural noise suppressor using native librnnoise.so.0 (48kHz mono)."""

    def __init__(self, lib_path: str | None = None) -> None:
        self._lib = None
        self._state = None
        self._np = None
        self._out_buf = None
        self._out_ptr = None
        self.available = False
        self.last_vad_prob = 0.0

        candidates = [
            lib_path,
            os.environ.get("RNNOISE_LIB_PATH"),
            "/usr/lib/librnnoise.so.0",
            "/usr/local/lib/librnnoise.so.0",
            ctypes.util.find_library("rnnoise"),
        ]
        for path in candidates:
            if not path:
                continue
            try:
                lib = ctypes.CDLL(path)
                import numpy as np  # type: ignore

                lib.rnnoise_create.argtypes = [ctypes.c_void_p]
                lib.rnnoise_create.restype = ctypes.c_void_p
                lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]
                lib.rnnoise_process_frame.argtypes = [
                    ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_float),
                    ctypes.POINTER(ctypes.c_float),
                ]
                lib.rnnoise_process_frame.restype = ctypes.c_float
                state = lib.rnnoise_create(None)
                if not state:
                    continue
                self._lib = lib
                self._state = state
                self._np = np
                self._out_buf = np.zeros(RNNOISE_FRAME_SAMPLES, dtype=np.float32)
                self._out_ptr = self._out_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
                self.available = True
                log.info("Loaded RNNoise neural denoiser from %s", path)
                break
            except Exception:
                continue

    def process_48k_pcm(self, pcm48_mono: bytes) -> tuple[bytes, float]:
        """Denoise 48kHz mono s16le PCM (must be multiple of 480 samples). Returns (pcm, mean_vad_prob)."""
        if not self.available or self._lib is None or self._state is None or self._np is None:
            return pcm48_mono, 0.0
        np = self._np
        n_frames = (len(pcm48_mono) // 2) // RNNOISE_FRAME_SAMPLES
        if n_frames == 0:
            return pcm48_mono, 0.0

        arr = np.frombuffer(pcm48_mono[: n_frames * RNNOISE_FRAME_SAMPLES * 2], dtype=np.int16)
        out = np.empty_like(arr)
        vad_sum = 0.0
        for i in range(n_frames):
            start = i * RNNOISE_FRAME_SAMPLES
            frame = arr[start : start + RNNOISE_FRAME_SAMPLES].astype(np.float32)
            in_ptr = frame.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
            vad_sum += float(self._lib.rnnoise_process_frame(self._state, self._out_ptr, in_ptr))
            out[start : start + RNNOISE_FRAME_SAMPLES] = np.clip(self._out_buf, -32768, 32767).astype(np.int16)
        mean_vad = vad_sum / n_frames
        self.last_vad_prob = mean_vad
        return out.tobytes(), mean_vad

    def close(self) -> None:
        if self._lib is not None and self._state is not None:
            try:
                self._lib.rnnoise_destroy(self._state)
            except Exception:
                pass
            self._state = None
            self.available = False
