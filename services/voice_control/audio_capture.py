"""PulseAudio 16kHz PCM microphone capture and pure-Python adaptive RMS VAD for PocoBox."""

from __future__ import annotations

import io
import math
import os
import struct
import subprocess
import time
import wave
from dataclasses import dataclass

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit signed LE
CHUNK_MS = 100
CHUNK_SAMPLES = SAMPLE_RATE * CHUNK_MS // 1000
CHUNK_BYTES = CHUNK_SAMPLES * SAMPLE_WIDTH


@dataclass
class VadMetrics:
    duration_sec: float
    rms_mean: float
    rms_peak: float
    speech_chunks: int
    total_chunks: int
    has_speech: bool


def chunk_rms(pcm_bytes: bytes) -> float:
    count = len(pcm_bytes) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack(f"<{count}h", pcm_bytes[: count * 2])
    mean_sq = sum(s * s for s in samples) / count
    return math.sqrt(mean_sq)


def pcm_to_wav_bytes(pcm_bytes: bytes, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def analyze_wav_bytes(wav_bytes: bytes, speech_threshold: float = 350.0) -> VadMetrics:
    """Analyze a 16-bit mono WAV payload and compute speech/RMS metrics."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        rate = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)

    step = max(2, (rate * CHUNK_MS // 1000) * 2)
    rms_values: list[float] = []
    speech_chunks = 0
    for i in range(0, len(raw), step):
        c = raw[i : i + step]
        if len(c) < 2:
            continue
        r = chunk_rms(c)
        rms_values.append(r)
        if r >= speech_threshold:
            speech_chunks += 1

    duration = nframes / float(rate) if rate else 0.0
    return VadMetrics(
        duration_sec=round(duration, 3),
        rms_mean=round(sum(rms_values) / len(rms_values), 1) if rms_values else 0.0,
        rms_peak=round(max(rms_values), 1) if rms_values else 0.0,
        speech_chunks=speech_chunks,
        total_chunks=len(rms_values),
        has_speech=speech_chunks >= 2,
    )


def trim_silence_wav(wav_bytes: bytes, threshold: float = 40.0, pad_chunks: int = 3) -> bytes:
    """Trim leading and trailing silence from a 16-bit mono WAV."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        rate = wf.getframerate()
        ch = wf.getnchannels()
        raw = wf.readframes(wf.getnframes())

    step = max(2, (rate * CHUNK_MS // 1000) * 2 * ch)
    chunks = [raw[i : i + step] for i in range(0, len(raw), step) if len(raw[i : i + step]) >= 2]
    active = [idx for idx, c in enumerate(chunks) if chunk_rms(c) >= threshold]
    if not active:
        return wav_bytes
    start = max(0, active[0] - pad_chunks)
    end = min(len(chunks), active[-1] + pad_chunks + 1)
    return pcm_to_wav_bytes(b"".join(chunks[start:end]), sample_rate=rate, channels=ch)


def normalize_wav_gain(wav_bytes: bytes, target_peak: float = 20000.0, max_gain: float = 40.0) -> bytes:
    """Amplify quiet microphone captures cleanly without clipping so cloud STT hears them clearly."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        rate = wf.getframerate()
        ch = wf.getnchannels()
        raw = wf.readframes(wf.getnframes())

    count = len(raw) // 2
    if count == 0:
        return wav_bytes
    samples = struct.unpack(f"<{count}h", raw[: count * 2])
    peak = max((abs(s) for s in samples), default=0)
    if peak < 10 or peak >= target_peak:
        return wav_bytes

    gain = min(max_gain, target_peak / float(peak))
    if gain <= 1.2:
        return wav_bytes

    boosted = [max(-32767, min(32767, int(s * gain))) for s in samples]
    return pcm_to_wav_bytes(struct.pack(f"<{count}h", *boosted), sample_rate=rate, channels=ch)


def record_utterance_wav(
    max_duration_sec: float = 8.0,
    silence_timeout_sec: float = 1.2,
    no_speech_timeout_sec: float = 4.0,
    min_threshold: float = 350.0,
    source: str | None = None,
) -> tuple[bytes, VadMetrics]:
    """Capture one spoken command from PulseAudio microphone using adaptive RMS VAD."""
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", "/run/user/10000")
    env.setdefault("PULSE_SERVER", "unix:/run/user/10000/pulse/native")

    cmd = [
        "parecord",
        "--raw",
        "--format=s16le",
        f"--rate={SAMPLE_RATE}",
        f"--channels={CHANNELS}",
        "--latency-msec=100",
    ]
    if source:
        cmd.extend(["--device", source])

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    assert proc.stdout is not None

    chunks: list[bytes] = []
    ambient_samples: list[float] = []
    speech_started = False
    speech_chunks = 0
    silent_chunks_after_speech = 0
    max_chunks = int(max_duration_sec * 1000 // CHUNK_MS)
    silence_limit = max(3, int(silence_timeout_sec * 1000 // CHUNK_MS))
    no_speech_limit = max(5, int(no_speech_timeout_sec * 1000 // CHUNK_MS))
    calibration_chunks = 3  # 0.3s ambient noise floor calibration

    try:
        for idx in range(max_chunks):
            data = proc.stdout.read(CHUNK_BYTES)
            if not data:
                break
            chunks.append(data)
            r = chunk_rms(data)

            if idx < calibration_chunks:
                ambient_samples.append(r)
                continue

            ambient = sum(ambient_samples) / len(ambient_samples) if ambient_samples else 100.0
            threshold = max(min_threshold, ambient * 2.2)

            if r >= threshold:
                speech_started = True
                speech_chunks += 1
                silent_chunks_after_speech = 0
            elif speech_started:
                silent_chunks_after_speech += 1
                if silent_chunks_after_speech >= silence_limit:
                    break
            elif idx >= no_speech_limit:
                break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            proc.kill()

    wav_bytes = pcm_to_wav_bytes(b"".join(chunks))
    metrics = analyze_wav_bytes(wav_bytes, speech_threshold=min_threshold)
    return wav_bytes, metrics
