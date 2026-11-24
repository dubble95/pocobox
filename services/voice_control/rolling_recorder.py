"""Rolling in-memory/tmpfs audio recorder for debugging hands-free voice and wake-word issues.

Preserves ~10 minutes of rolling audio in tmpfs (default /tmp/pocobox_audio_history)
as dual-channel 16kHz s16le WAV segments:
  - Channel 0 (Left):  Raw microphone PCM (anti-aliased 16kHz mono, before AEC3)
  - Channel 1 (Right): Processed wake PCM (after AEC3 echo cancellation + bandpass)

Total RAM overhead is ~38 MB (approx 1.4% of /tmp tmpfs), zero flash eMMC wear,
and segments older than 10 minutes are automatically pruned.
"""

from __future__ import annotations

import array
import json
import logging
import math
import os
import queue
import shutil
import struct
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("pocobox-voice.rolling")

DEFAULT_BUFFER_DIR = "/tmp/pocobox_audio_history"
DEFAULT_RETENTION_SEC = 600.0  # 10 minutes
DEFAULT_SEGMENT_SEC = 30.0  # 30 seconds per file
SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # 16-bit
CHANNELS = 2  # stereo: ch0=raw, ch1=clean


def _pcm_rms(pcm_bytes: bytes) -> float:
    n = len(pcm_bytes) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack(f"<{n}h", pcm_bytes[: n * 2])
    mean_sq = sum(s * s for s in samples) / n
    return math.sqrt(mean_sq)


def interleave_stereo(ch0_bytes: bytes, ch1_bytes: bytes) -> bytes:
    """Interleave two mono s16le streams into one stereo s16le stream."""
    n_samples = min(len(ch0_bytes), len(ch1_bytes)) // 2
    if n_samples == 0:
        return b""
    try:
        import numpy as np  # type: ignore

        a0 = np.frombuffer(ch0_bytes[: n_samples * 2], dtype=np.int16)
        a1 = np.frombuffer(ch1_bytes[: n_samples * 2], dtype=np.int16)
        stereo = np.empty((n_samples, 2), dtype=np.int16)
        stereo[:, 0] = a0
        stereo[:, 1] = a1
        return stereo.tobytes()
    except Exception:
        s0 = array.array("h")
        s0.frombytes(ch0_bytes[: n_samples * 2])
        s1 = array.array("h")
        s1.frombytes(ch1_bytes[: n_samples * 2])
        out = array.array("h", [0] * (n_samples * 2))
        out[0::2] = s0
        out[1::2] = s1
        return out.tobytes()


def extract_stereo_channel(stereo_bytes: bytes, channel_idx: int) -> bytes:
    """Extract mono s16le PCM from interleaved stereo s16le (channel 0 or 1)."""
    n_frames = len(stereo_bytes) // 4
    if n_frames == 0:
        return b""
    try:
        import numpy as np  # type: ignore

        arr = np.frombuffer(stereo_bytes[: n_frames * 4], dtype=np.int16).reshape((n_frames, 2))
        return arr[:, channel_idx].tobytes()
    except Exception:
        s = array.array("h")
        s.frombytes(stereo_bytes[: n_frames * 4])
        out = array.array("h", s[channel_idx::2])
        return out.tobytes()


def build_wav_bytes(pcm_data: bytes, sample_rate: int = SAMPLE_RATE, channels: int = 1) -> bytes:
    """Wrap raw s16le PCM in a standard RIFF/WAV header."""
    byte_rate = sample_rate * channels * SAMPLE_WIDTH
    block_align = channels * SAMPLE_WIDTH
    data_size = len(pcm_data)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        data_size + 36,
        b"WAVE",
        b"fmt ",
        16,  # PCM subchunk size
        1,  # format = PCM
        channels,
        sample_rate,
        byte_rate,
        block_align,
        SAMPLE_WIDTH * 8,  # bits per sample
        b"data",
        data_size,
    )
    return header + pcm_data


class RollingAudioRecorder:
    """Non-blocking rolling audio history manager storing recent audio in tmpfs."""

    def __init__(
        self,
        buffer_dir: str | Path | None = None,
        retention_sec: float | None = None,
        segment_sec: float | None = None,
        sample_rate: int = SAMPLE_RATE,
        enabled: bool | None = None,
    ) -> None:
        raw_enabled = os.environ.get("VOICE_ROLLING_RECORDER", "1").strip().lower()
        self.enabled = (
            enabled
            if enabled is not None
            else raw_enabled in ("1", "true", "yes", "on")
        )
        self.buffer_dir = Path(
            buffer_dir or os.environ.get("VOICE_ROLLING_DIR", DEFAULT_BUFFER_DIR)
        )
        self.retention_sec = float(
            retention_sec
            if retention_sec is not None
            else os.environ.get("VOICE_ROLLING_RETENTION_SEC", str(DEFAULT_RETENTION_SEC))
        )
        self.segment_sec = float(
            segment_sec
            if segment_sec is not None
            else os.environ.get("VOICE_ROLLING_SEGMENT_SEC", str(DEFAULT_SEGMENT_SEC))
        )
        self.sample_rate = sample_rate
        self.segment_samples = int(self.segment_sec * self.sample_rate)
        self.segment_mono_bytes = self.segment_samples * SAMPLE_WIDTH

        # Active segment buffer in memory
        self._lock = threading.Lock()
        self._buf_raw = bytearray()
        self._buf_clean = bytearray()
        self._buf_start_ts: float = 0.0
        self._buf_music_chunks = 0
        self._buf_total_chunks = 0
        self._buf_aec3_sum = 0.0

        # Background writer thread so audio capture loop is NEVER blocked by I/O
        self._queue: queue.Queue[tuple[bytes, bytes, dict[str, Any]] | None] = queue.Queue(maxsize=30)
        self._writer_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        if self.enabled:
            self._ensure_dir()
            self._start_writer()

    def _ensure_dir(self) -> None:
        try:
            self.buffer_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            log.warning("Could not create rolling buffer dir %s: %s", self.buffer_dir, exc)

    def _start_writer(self) -> None:
        if self._writer_thread and self._writer_thread.is_alive():
            return
        self._stop_event.clear()
        self._writer_thread = threading.Thread(
            target=self._writer_loop, name="rolling-audio-writer", daemon=True
        )
        self._writer_thread.start()

    def feed(
        self,
        raw_mono16: bytes,
        clean_mono16: bytes,
        is_music_playing: bool = False,
        aec3_db: float = 0.0,
    ) -> None:
        """Feed one chunk (~100ms) of 16kHz audio. Must be fast and non-blocking."""
        if not self.enabled or not raw_mono16 or not clean_mono16:
            return

        now = time.time()
        to_flush: tuple[bytes, bytes, dict[str, Any]] | None = None

        with self._lock:
            if not self._buf_raw:
                self._buf_start_ts = now
                self._buf_music_chunks = 0
                self._buf_total_chunks = 0
                self._buf_aec3_sum = 0.0

            self._buf_raw.extend(raw_mono16)
            self._buf_clean.extend(clean_mono16)
            self._buf_total_chunks += 1
            if is_music_playing:
                self._buf_music_chunks += 1
            self._buf_aec3_sum += aec3_db

            if len(self._buf_raw) >= self.segment_mono_bytes:
                raw_slice = bytes(self._buf_raw[: self.segment_mono_bytes])
                clean_slice = bytes(self._buf_clean[: self.segment_mono_bytes])
                del self._buf_raw[: self.segment_mono_bytes]
                del self._buf_clean[: self.segment_mono_bytes]

                total_c = max(1, self._buf_total_chunks)
                meta: dict[str, Any] = {
                    "start_ts": self._buf_start_ts,
                    "end_ts": now,
                    "start_iso": datetime.fromtimestamp(self._buf_start_ts, tz=timezone.utc).astimezone().isoformat(),
                    "end_iso": datetime.fromtimestamp(now, tz=timezone.utc).astimezone().isoformat(),
                    "duration_sec": round(len(raw_slice) / (self.sample_rate * SAMPLE_WIDTH), 2),
                    "music_ratio": round(self._buf_music_chunks / total_c, 3),
                    "aec3_mean_db": round(self._buf_aec3_sum / total_c, 1),
                    "rms_raw": round(_pcm_rms(raw_slice), 1),
                    "rms_clean": round(_pcm_rms(clean_slice), 1),
                }

                # Reset counters for the next segment
                self._buf_start_ts = now
                self._buf_music_chunks = 0
                self._buf_total_chunks = 0
                self._buf_aec3_sum = 0.0
                to_flush = (raw_slice, clean_slice, meta)

        if to_flush is not None:
            try:
                self._queue.put_nowait(to_flush)
            except queue.Full:
                log.warning("Rolling audio writer queue full; dropping segment.")

    def _writer_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if item is None:
                break

            raw_bytes, clean_bytes, meta = item
            try:
                self._write_segment(raw_bytes, clean_bytes, meta)
                self.prune()
            except Exception as exc:
                log.error("Failed writing rolling audio segment: %s", exc)
            finally:
                self._queue.task_done()

    def _write_segment(self, raw_bytes: bytes, clean_bytes: bytes, meta: dict[str, Any]) -> Path | None:
        stereo_pcm = interleave_stereo(raw_bytes, clean_bytes)
        if not stereo_pcm:
            return None

        wav_bytes = build_wav_bytes(stereo_pcm, sample_rate=self.sample_rate, channels=CHANNELS)
        ts_str = datetime.fromtimestamp(meta["start_ts"], tz=timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S_%f")[:19]
        stem = f"segment_{ts_str}"
        wav_path = self.buffer_dir / f"{stem}.wav"
        json_path = self.buffer_dir / f"{stem}.json"

        tmp_wav = self.buffer_dir / f".{stem}.wav.tmp"
        tmp_json = self.buffer_dir / f".{stem}.json.tmp"

        tmp_wav.write_bytes(wav_bytes)
        tmp_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        tmp_wav.replace(wav_path)
        tmp_json.replace(json_path)
        return wav_path

    def prune(self, now: float | None = None) -> int:
        """Remove audio segments older than retention_sec."""
        if not self.buffer_dir.is_dir():
            return 0
        cur_time = now if now is not None else time.time()
        cutoff = cur_time - self.retention_sec
        removed = 0

        try:
            wav_files = sorted(self.buffer_dir.glob("segment_*.wav"))
            for wav_path in wav_files:
                meta_path = wav_path.with_suffix(".json")
                should_remove = False
                if meta_path.is_file():
                    try:
                        m = json.loads(meta_path.read_text(encoding="utf-8"))
                        if m.get("end_ts", m.get("start_ts", 0)) < cutoff:
                            should_remove = True
                    except Exception:
                        should_remove = wav_path.stat().st_mtime < cutoff
                else:
                    should_remove = wav_path.stat().st_mtime < cutoff

                if should_remove:
                    try:
                        wav_path.unlink(missing_ok=True)
                        meta_path.unlink(missing_ok=True)
                        removed += 1
                    except Exception:
                        pass
        except Exception as exc:
            log.warning("Error pruning rolling audio history: %s", exc)

        return removed

    def list_segments(self) -> list[dict[str, Any]]:
        """List all available stored segments in chronological order."""
        if not self.buffer_dir.is_dir():
            return []
        segments: list[dict[str, Any]] = []
        for wav_path in sorted(self.buffer_dir.glob("segment_*.wav")):
            meta_path = wav_path.with_suffix(".json")
            entry: dict[str, Any] = {
                "file": wav_path.name,
                "size_bytes": wav_path.stat().st_size,
            }
            if meta_path.is_file():
                try:
                    entry.update(json.loads(meta_path.read_text(encoding="utf-8")))
                except Exception:
                    pass
            segments.append(entry)
        return segments

    def get_recent_wav(self, seconds: float = 60.0, channel: str = "both") -> bytes:
        """Fetch the last N seconds of audio as standard WAV bytes.
        
        Args:
            seconds: Duration in seconds to retrieve (max retention_sec).
            channel: 'raw' (mic mono), 'clean' (processed mono), or 'both' (stereo).
        """
        secs = max(0.5, min(self.retention_sec, float(seconds)))
        need_samples = int(secs * self.sample_rate)
        need_stereo_bytes = need_samples * CHANNELS * SAMPLE_WIDTH

        collected_stereo = bytearray()

        # 1. Take current in-memory partial segment
        with self._lock:
            if self._buf_raw and self._buf_clean:
                partial_stereo = interleave_stereo(bytes(self._buf_raw), bytes(self._buf_clean))
                collected_stereo.extend(partial_stereo)

        # 2. Read recent segment WAV files backwards until target duration is met
        if len(collected_stereo) < need_stereo_bytes and self.buffer_dir.is_dir():
            wav_files = sorted(self.buffer_dir.glob("segment_*.wav"), reverse=True)
            for wav_path in wav_files:
                try:
                    data = wav_path.read_bytes()
                    # Skip 44-byte standard RIFF header
                    if len(data) > 44 and data.startswith(b"RIFF"):
                        pcm = data[44:]
                        collected_stereo = bytearray(pcm) + collected_stereo
                        if len(collected_stereo) >= need_stereo_bytes:
                            break
                except Exception:
                    continue

        if not collected_stereo:
            return build_wav_bytes(b"", sample_rate=self.sample_rate, channels=1)

        # Slice exact tail duration
        if len(collected_stereo) > need_stereo_bytes:
            excess = len(collected_stereo) - need_stereo_bytes
            excess -= excess % (CHANNELS * SAMPLE_WIDTH)
            collected_stereo = collected_stereo[excess:]

        final_stereo = bytes(collected_stereo)
        ch_lower = (channel or "both").strip().lower()

        if ch_lower == "raw":
            mono_pcm = extract_stereo_channel(final_stereo, 0)
            return build_wav_bytes(mono_pcm, sample_rate=self.sample_rate, channels=1)
        if ch_lower == "clean":
            mono_pcm = extract_stereo_channel(final_stereo, 1)
            return build_wav_bytes(mono_pcm, sample_rate=self.sample_rate, channels=1)

        return build_wav_bytes(final_stereo, sample_rate=self.sample_rate, channels=CHANNELS)

    def status_dict(self) -> dict[str, Any]:
        segments = self.list_segments()
        total_dur = sum(s.get("duration_sec", self.segment_sec) for s in segments)
        with self._lock:
            in_mem_dur = round(len(self._buf_raw) / (self.sample_rate * SAMPLE_WIDTH), 2)
        total_dur += in_mem_dur

        return {
            "ok": True,
            "enabled": self.enabled,
            "buffer_dir": str(self.buffer_dir),
            "retention_sec": self.retention_sec,
            "segment_sec": self.segment_sec,
            "segments_count": len(segments),
            "total_duration_sec": round(total_dur, 1),
            "in_memory_sec": in_mem_dur,
            "channels": {
                "ch0_left": "raw_mic_16k (anti-aliased decimated)",
                "ch1_right": "clean_wake_16k (AEC3 + bandpass)",
            },
            "segments": segments[-20:],
        }

    def clear(self) -> None:
        """Clear all stored segments in the buffer directory."""
        with self._lock:
            self._buf_raw.clear()
            self._buf_clean.clear()
        if self.buffer_dir.is_dir():
            for p in self.buffer_dir.glob("segment_*"):
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass

    def close(self) -> None:
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
        if self._writer_thread and self._writer_thread.is_alive():
            self._writer_thread.join(timeout=1.5)
