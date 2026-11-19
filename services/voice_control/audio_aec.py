"""Decoupled WebRTC AEC3 and Squeezelite VISEXPORT shared-memory reference client.

Runs full multi-channel WebRTC Acoustic Echo Cancellation (AEC3) in userspace
using Squeezelite's non-blocking POSIX shared memory PCM tap (`/squeezelite-<MAC>`)
so USB DAC bit-perfect ALSA playback is NEVER interrupted by mic timestamp skew.

Gracefully disables itself when `libpocobox_aec3.so` or Squeezelite `-v` shm is
not yet installed on the host.
"""

from __future__ import annotations

import array
import ctypes
import logging
import math
import os
import time

log = logging.getLogger("pocobox-voice.aec3")

AEC3_SAMPLE_RATE = 16000
AEC3_FRAME_MS = 10
AEC3_FRAME_SAMPLES = AEC3_SAMPLE_RATE * AEC3_FRAME_MS // 1000  # 160 samples
AEC3_MONO_FRAME_BYTES = AEC3_FRAME_SAMPLES * 2  # 320 bytes
AEC3_STEREO_FRAME_BYTES = AEC3_FRAME_SAMPLES * 4  # 640 bytes
VIS_MAX_READ_FRAMES = 8192

_DEFAULT_LIB_CANDIDATES = (
    "/usr/local/lib/libpocobox_aec3.so",
    "/usr/lib/libpocobox_aec3.so",
)


def _find_aec3_library(lib_path: str | None = None) -> ctypes.CDLL | None:
    candidates = [lib_path, os.environ.get("POCOBOX_AEC3_LIB"), *_DEFAULT_LIB_CANDIDATES]
    for path in candidates:
        if not path:
            continue
        try:
            return ctypes.CDLL(path)
        except OSError:
            continue
    return None


def _pcm_rms(pcm_bytes: bytes) -> float:
    n = len(pcm_bytes) // 2
    if n == 0:
        return 0.0
    samples = array.array("h")
    samples.frombytes(pcm_bytes[: n * 2])
    mean_sq = sum(s * s for s in samples) / n
    return math.sqrt(mean_sq)


def resample_stereo_pcm(stereo_pcm: bytes, src_rate: int, dst_rate: int = AEC3_SAMPLE_RATE) -> bytes:
    """Resample interleaved stereo s16le PCM from arbitrary Squeezelite rate to dst_rate (16kHz)."""
    n_frames = len(stereo_pcm) // 4
    if n_frames == 0 or src_rate <= 0 or dst_rate <= 0:
        return b""
    if src_rate == dst_rate:
        return stereo_pcm[: n_frames * 4]

    out_frames = max(1, int(round(n_frames * dst_rate / float(src_rate))))
    try:
        import numpy as np  # type: ignore

        arr = np.frombuffer(stereo_pcm[: n_frames * 4], dtype=np.int16).reshape(n_frames, 2).astype(np.float32)

        # Light anti-alias pre-binning when downsampling >= 3x (48k/88.2k/96k/192k -> 16k)
        decim = int(src_rate // dst_rate)
        if decim >= 3 and n_frames >= decim:
            trimmed = (n_frames // decim) * decim
            arr = arr[:trimmed].reshape(trimmed // decim, decim, 2).mean(axis=1)
            eff_rate = src_rate / float(decim)
            if abs(eff_rate - dst_rate) < 1.0:
                return np.clip(np.rint(arr), -32768, 32767).astype(np.int16).tobytes()
            n_cur = arr.shape[0]
            out_frames = max(1, int(round(n_cur * dst_rate / eff_rate)))
        else:
            n_cur = n_frames

        if n_cur == 1:
            out = np.repeat(arr, out_frames, axis=0)
        else:
            step = float(src_rate) / float(dst_rate)
            dst_x = np.clip(np.arange(out_frames, dtype=np.float32) * step, 0.0, float(n_cur - 1))
            src_x = np.arange(n_cur, dtype=np.float32)
            left = np.interp(dst_x, src_x, arr[:, 0])
            right = np.interp(dst_x, src_x, arr[:, 1])
            out = np.stack((left, right), axis=1)
        return np.clip(np.rint(out), -32768, 32767).astype(np.int16).tobytes()
    except Exception:
        # Pure-Python fallback (keeps unit tests and minimal environments dependency-free)
        samples = array.array("h")
        samples.frombytes(stereo_pcm[: n_frames * 4])
        out = array.array("h", [0] * (out_frames * 2))
        scale = (n_frames - 1) / float(max(1, out_frames - 1)) if out_frames > 1 else 0.0
        for i in range(out_frames):
            pos = i * scale
            idx0 = int(pos)
            idx1 = min(n_frames - 1, idx0 + 1)
            frac = pos - idx0
            l0, r0 = samples[idx0 * 2], samples[idx0 * 2 + 1]
            l1, r1 = samples[idx1 * 2], samples[idx1 * 2 + 1]
            out[i * 2] = int(round(l0 + (l1 - l0) * frac))
            out[i * 2 + 1] = int(round(r0 + (r1 - r0) * frac))
        return out.tobytes()


class SqueezeliteVisClient:
    """Non-blocking reader for Squeezelite `-v` (VISEXPORT) shared memory reference audio."""

    def __init__(
        self,
        shm_name: str | None = None,
        target_rate: int = AEC3_SAMPLE_RATE,
        lib_path: str | None = None,
        max_fifo_ms: int = 160,
        _lib: ctypes.CDLL | None = None,
    ) -> None:
        self.shm_name = shm_name or os.environ.get("VOICE_VIS_SHM", "/squeezelite-02:70:6f:63:6f:01")
        self.target_rate = target_rate
        self.max_fifo_bytes = (target_rate * max_fifo_ms // 1000) * 4

        self._lib = _lib if _lib is not None else _find_aec3_library(lib_path)
        self._handle = None
        self._buf = (ctypes.c_int16 * (VIS_MAX_READ_FRAMES * 2))()
        self._fifo = bytearray()
        self._last_open_attempt = 0.0

        self.library_available = False
        self.connected = False
        self.is_playing = False
        self.current_rate = 0

        if self._lib is not None:
            try:
                self._lib.pocobox_vis_open.argtypes = [ctypes.c_char_p]
                self._lib.pocobox_vis_open.restype = ctypes.c_void_p
                self._lib.pocobox_vis_read_new.argtypes = [
                    ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_int16),
                    ctypes.c_int,
                    ctypes.POINTER(ctypes.c_uint32),
                    ctypes.POINTER(ctypes.c_int),
                ]
                self._lib.pocobox_vis_read_new.restype = ctypes.c_int
                self._lib.pocobox_vis_close.argtypes = [ctypes.c_void_p]
                self.library_available = True
                self.ensure_open()
            except AttributeError:
                self._lib = None
                self.library_available = False

    def ensure_open(self) -> bool:
        if self._handle:
            return True
        if not self.library_available or self._lib is None:
            return False
        now = time.monotonic()
        if now - self._last_open_attempt < 3.0:
            return False
        self._last_open_attempt = now
        handle = self._lib.pocobox_vis_open(self.shm_name.encode("utf-8"))
        if handle:
            self._handle = handle
            self.connected = True
            self._fifo.clear()
            log.info("Attached to Squeezelite VISEXPORT shm %s for AEC3 reference", self.shm_name)
            return True
        self.connected = False
        return False

    def poll_reference(self) -> None:
        """Read any newly written Squeezelite stereo PCM frames and append resampled 16kHz stereo to FIFO."""
        if not self.ensure_open() or self._lib is None or not self._handle:
            return
        rate_out = ctypes.c_uint32(0)
        running_out = ctypes.c_int(0)
        n_frames = int(
            self._lib.pocobox_vis_read_new(
                self._handle,
                self._buf,
                VIS_MAX_READ_FRAMES,
                ctypes.byref(rate_out),
                ctypes.byref(running_out),
            )
        )
        if n_frames < 0:
            self.close()
            return

        self.current_rate = int(rate_out.value)
        new_is_playing = bool(running_out.value)

        # Clear stale reference audio when playback restarts after pause/stop
        if new_is_playing and not self.is_playing:
            self._fifo.clear()
        self.is_playing = new_is_playing

        if not self.is_playing:
            self._fifo.clear()
            return

        if n_frames > 0 and self.current_rate > 0:
            raw_bytes = ctypes.string_at(self._buf, n_frames * 4)
            pcm16_stereo = resample_stereo_pcm(raw_bytes, self.current_rate, self.target_rate)
            self._fifo.extend(pcm16_stereo)
            if len(self._fifo) > self.max_fifo_bytes:
                excess = len(self._fifo) - self.max_fifo_bytes
                # Keep 4-byte stereo frame alignment
                excess -= excess % 4
                if excess > 0:
                    del self._fifo[:excess]

    def pull_16k_stereo(self, num_frames: int) -> bytes | None:
        """Poll VISEXPORT and return exactly num_frames of 16kHz stereo s16le reference, or None if stopped."""
        self.poll_reference()
        if not self.is_playing:
            return None
        need_bytes = num_frames * 4
        if len(self._fifo) >= need_bytes:
            out = bytes(self._fifo[:need_bytes])
            del self._fifo[:need_bytes]
            return out
        if len(self._fifo) > 0:
            out = bytes(self._fifo) + b"\x00" * (need_bytes - len(self._fifo))
            self._fifo.clear()
            return out
        return b"\x00" * need_bytes

    def close(self) -> None:
        if self._lib is not None and self._handle:
            try:
                self._lib.pocobox_vis_close(self._handle)
            except Exception:
                pass
        self._handle = None
        self.connected = False
        self.is_playing = False
        self._fifo.clear()


class WebRTCAEC3Processor:
    """Real-time userspace WebRTC AEC3 processor (16kHz stereo render -> mono capture cancellation)."""

    def __init__(
        self,
        sample_rate: int = AEC3_SAMPLE_RATE,
        render_channels: int = 2,
        capture_channels: int = 1,
        delay_ms: int | None = None,
        enable_hp_filter: bool = True,
        lib_path: str | None = None,
        _lib: ctypes.CDLL | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.render_channels = render_channels
        self.capture_channels = capture_channels
        self.delay_ms = delay_ms if delay_ms is not None else int(os.environ.get("VOICE_AEC3_DELAY_MS", "150"))
        self.frame_samples = sample_rate * AEC3_FRAME_MS // 1000
        self.render_frame_bytes = self.frame_samples * render_channels * 2
        self.capture_frame_bytes = self.frame_samples * capture_channels * 2

        self._lib = _lib if _lib is not None else _find_aec3_library(lib_path)
        self._handle = None
        self._out_buf = (ctypes.c_int16 * (self.frame_samples * capture_channels))()
        self.available = False
        self.active = False
        self.echo_reduction_db = 0.0
        self._tail_silent_frames = 0

        if self._lib is None:
            return

        try:
            self._lib.pocobox_aec3_create.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
            self._lib.pocobox_aec3_create.restype = ctypes.c_void_p
            self._lib.pocobox_aec3_set_delay_ms.argtypes = [ctypes.c_void_p, ctypes.c_int]
            self._lib.pocobox_aec3_set_delay_ms.restype = ctypes.c_int
            self._lib.pocobox_aec3_process_render.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16)]
            self._lib.pocobox_aec3_process_render.restype = ctypes.c_int
            self._lib.pocobox_aec3_process_capture.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_int16),
                ctypes.POINTER(ctypes.c_int16),
            ]
            self._lib.pocobox_aec3_process_capture.restype = ctypes.c_int
            self._lib.pocobox_aec3_destroy.argtypes = [ctypes.c_void_p]

            handle = self._lib.pocobox_aec3_create(
                self.sample_rate,
                self.render_channels,
                self.capture_channels,
                1 if enable_hp_filter else 0,
            )
            if handle:
                self._handle = handle
                self._lib.pocobox_aec3_set_delay_ms(self._handle, self.delay_ms)
                self.available = True
                log.info(
                    "Loaded WebRTC AEC3 processor (%dHz, render=%dch, capture=%dch, delay=%dms)",
                    self.sample_rate,
                    self.render_channels,
                    self.capture_channels,
                    self.delay_ms,
                )
        except Exception as exc:
            log.warning("Failed initializing WebRTC AEC3: %s", exc)
            self.available = False

    def set_delay_ms(self, delay_ms: int) -> None:
        self.delay_ms = max(0, min(500, int(delay_ms)))
        if self.available and self._lib is not None and self._handle:
            self._lib.pocobox_aec3_set_delay_ms(self._handle, self.delay_ms)

    def process_chunk(self, capture_pcm: bytes, render_pcm: bytes | None) -> bytes:
        """Cancel far-end speaker echo from capture_pcm (multiple of 10ms).

        When `render_pcm` is None (music stopped), drains a short 300ms room-reverb
        tail with silent render frames, then passes capture through untouched so
        quiet-room speech has zero processing overhead or distortion.
        """
        if not self.available or self._lib is None or not self._handle:
            self.active = False
            self.echo_reduction_db = 0.0
            return capture_pcm

        n_frames = len(capture_pcm) // self.capture_frame_bytes
        if n_frames == 0:
            return capture_pcm

        if render_pcm is None:
            if self._tail_silent_frames >= 30:  # 300ms room decay finished -> bypass cleanly
                self.active = False
                self.echo_reduction_db *= 0.8
                return capture_pcm
            self._tail_silent_frames += n_frames
            render_pcm = b"\x00" * (n_frames * self.render_frame_bytes)
        else:
            self._tail_silent_frames = 0

        self.active = True
        in_rms = _pcm_rms(capture_pcm[: n_frames * self.capture_frame_bytes])
        out_chunks = bytearray()
        zero_render = b"\x00" * self.render_frame_bytes

        for i in range(n_frames):
            r_start = i * self.render_frame_bytes
            r_slice = render_pcm[r_start : r_start + self.render_frame_bytes]
            if len(r_slice) < self.render_frame_bytes:
                r_slice = zero_render
            r_arr = (ctypes.c_int16 * (self.frame_samples * self.render_channels)).from_buffer_copy(r_slice)
            self._lib.pocobox_aec3_process_render(self._handle, r_arr)
            self._lib.pocobox_aec3_set_delay_ms(self._handle, self.delay_ms)

            c_start = i * self.capture_frame_bytes
            c_slice = capture_pcm[c_start : c_start + self.capture_frame_bytes]
            c_arr = (ctypes.c_int16 * (self.frame_samples * self.capture_channels)).from_buffer_copy(c_slice)
            rc = self._lib.pocobox_aec3_process_capture(self._handle, c_arr, self._out_buf)
            if rc == 0:
                out_chunks.extend(ctypes.string_at(self._out_buf, self.capture_frame_bytes))
            else:
                out_chunks.extend(c_slice)

        out_bytes = bytes(out_chunks)
        out_rms = _pcm_rms(out_bytes)
        if in_rms > 30.0:
            inst_db = max(0.0, min(45.0, 20.0 * math.log10(in_rms / max(out_rms, 1.0))))
            self.echo_reduction_db = self.echo_reduction_db * 0.75 + inst_db * 0.25
        return out_bytes

    def close(self) -> None:
        if self._lib is not None and self._handle:
            try:
                self._lib.pocobox_aec3_destroy(self._handle)
            except Exception:
                pass
        self._handle = None
        self.available = False
        self.active = False
