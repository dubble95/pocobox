"""Always-on 24/7 hands-free microphone listener with decoupled WebRTC AEC3, anti-aliased DSP,
local Vosk wake-word spotting, music-safe trigger gating, and auto-ducking hooks."""

from __future__ import annotations

import collections
import json
import logging
import os
import queue
import re
import subprocess
import threading
import time
import urllib.request
from typing import Any, Callable

from .audio_aec import SqueezeliteVisClient, WebRTCAEC3Processor
from .audio_capture import CHUNK_BYTES, CHUNK_MS, SAMPLE_RATE, chunk_rms, pcm_to_wav_bytes
from .audio_dsp import (
    RNNOISE_SAMPLE_RATE,
    AntiAliasDecimator48kTo16k,
    RNNoiseProcessor,
    SpeechBandpassFilter,
    extract_mono_channel,
    measure_pcm_peak_and_clip,
)
from .rolling_recorder import RollingAudioRecorder, build_wav_bytes
from .telemetry import VoiceTelemetryLogger
from .wakeword import VoskWakeWordSpotter

log = logging.getLogger("pocobox-voice.continuous")

SUBCHUNK_MS = 20
SUBCHUNKS_PER_CHUNK = max(1, CHUNK_MS // SUBCHUNK_MS)  # 5 x 20ms = 100ms
SUBCHUNK_48K_STEREO_BYTES = (RNNOISE_SAMPLE_RATE * SUBCHUNK_MS // 1000) * 4  # 3840 bytes
SUBCHUNK_16K_FRAMES = SAMPLE_RATE * SUBCHUNK_MS // 1000  # 320 frames


class LocalWakeVerifier:
    """Fast non-blocking local SenseVoice INT8 wake-word spotter for Korean/English during music.

    Runs in a dedicated background daemon thread. When an audio slice containing a candidate
    speech burst is submitted, it transcribes with local SenseVoice INT8 on cores 4-7
    (taking ~200ms) with ZERO cloud calls. If wake phrases ("포코야", "코코야", "헤이 포코",
    "hey poco", etc.) are matched, it invokes the wake callback immediately.
    """

    WAKE_KEYWORDS: tuple[str, ...] = (
        "포코야",
        "코코야",
        "포코향",
        "포코아",
        "뽀코야",
        "뽀코",
        "포코",
        "헤이포코",
        "헤이 포코",
        "오케이포코",
        "오케이 포코",
        "안녕포코",
        "안녕 포코",
        "hey poco",
        "poco ya",
        "poco box",
        "pocobox",
    )

    def __init__(
        self,
        stt_url: str | None = None,
        enabled: bool = True,
        on_wake: Callable[[str, str, float, float, float], None] | None = None,
    ) -> None:
        raw_url = stt_url if stt_url is not None else os.environ.get("LOCAL_STT_URL", "http://127.0.0.1:8766")
        self.stt_url = raw_url.rstrip("/") if raw_url else ""
        self.enabled = enabled and bool(self.stt_url)
        self.on_wake = on_wake
        self._queue: queue.Queue[tuple[bytes, float, float]] = queue.Queue(maxsize=1)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._verifying = False

        if self.enabled:
            self._thread = threading.Thread(
                target=self._worker_loop,
                daemon=True,
                name="local-wake-verifier",
            )
            self._thread.start()

    @classmethod
    def match_wake_phrase(cls, text: str) -> str | None:
        if not text:
            return None
        cleaned = re.sub(r"[\s.,!?~:;\-_]+", "", text.lower())
        for kw in cls.WAKE_KEYWORDS:
            ckw = re.sub(r"[\s.,!?~:;\-_]+", "", kw.lower())
            if ckw in cleaned:
                return "poco ya" if any(k in kw for k in ("포코", "코코", "뽀코", "poco")) else kw
        return None

    def submit(self, pcm_bytes: bytes, peak_rms: float, ambient_rms: float) -> bool:
        """Submit candidate speech audio for local STT verification without blocking the audio loop."""
        if not self.enabled or self._stop_event.is_set() or not pcm_bytes:
            return False
        try:
            self._queue.put_nowait((pcm_bytes, peak_rms, ambient_rms))
            return True
        except queue.Full:
            return False

    def close(self) -> None:
        self._stop_event.set()
        try:
            self._queue.put_nowait((b"", 0.0, 0.0))
        except queue.Full:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.3)
        self._thread = None

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                pcm_bytes, peak_rms, amb_rms = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if self._stop_event.is_set() or not pcm_bytes:
                break

            self._verifying = True
            try:
                wav_bytes = build_wav_bytes(pcm_bytes, sample_rate=SAMPLE_RATE, channels=1)
                req = urllib.request.Request(
                    f"{self.stt_url}/transcribe?language=ko",
                    data=wav_bytes,
                    headers={"Content-Type": "audio/wav"},
                )
                t0 = time.monotonic()
                with urllib.request.urlopen(req, timeout=2.5) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                stt_ms = (time.monotonic() - t0) * 1000.0
                text = str(res.get("text", "")).strip()
                matched = self.match_wake_phrase(text)
                if matched and self.on_wake is not None:
                    log.info(
                        "Local SenseVoice wake triggered: phrase='%s' text=%r (stt=%.1fms peak=%.1f amb=%.1f)",
                        matched,
                        text,
                        stt_ms,
                        peak_rms,
                        amb_rms,
                    )
                    self.on_wake(matched, text, stt_ms, peak_rms, amb_rms)
            except Exception as exc:
                log.debug("Local wake verifier STT notice: %s", exc)
            finally:
                self._verifying = False


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


class ContinuousVoiceListener:
    """Continuously monitors PulseAudio mic with AEC3 + anti-aliased Bandpass + local Vosk wake-word."""

    def __init__(
        self,
        on_utterance: Callable[[bytes], None],
        is_paused_fn: Callable[[], bool] | None = None,
        on_state_change: Callable[[str, float], None] | None = None,
        is_music_playing_fn: Callable[[], bool] | None = None,
        on_wake_detected: Callable[[str], None] | None = None,
        on_wake_utterance: Callable[[bytes | None, str], None] | None = None,
        is_chime_playing_fn: Callable[[], bool] | None = None,
        telemetry: VoiceTelemetryLogger | None = None,
    ) -> None:
        self.on_utterance = on_utterance
        self.is_paused_fn = is_paused_fn or (lambda: False)
        self.on_state_change = on_state_change
        self.is_music_playing_fn = is_music_playing_fn or (lambda: False)
        self.on_wake_detected = on_wake_detected
        self.on_wake_utterance = on_wake_utterance
        self.is_chime_playing_fn = is_chime_playing_fn or (lambda: False)
        self.telemetry = telemetry

        # Quiet-room thresholds (on cleaned 16kHz audio)
        self.min_threshold = float(os.environ.get("VOICE_VAD_THRESHOLD", "40.0"))
        self.speech_multiplier = float(os.environ.get("VOICE_VAD_MULTIPLIER", "3.0"))
        # Playback-aware thresholds (when LMS is actively playing music)
        self.playing_min_threshold = float(os.environ.get("VOICE_PLAYING_VAD_THRESHOLD", "180.0"))
        self.playing_multiplier = float(os.environ.get("VOICE_PLAYING_MULTIPLIER", "2.1"))

        self.onset_chunks = max(2, int(float(os.environ.get("VOICE_ONSET_SEC", "0.2")) * 1000 // CHUNK_MS))
        self.silence_chunks = max(4, int(float(os.environ.get("VOICE_SILENCE_SEC", "0.9")) * 1000 // CHUNK_MS))
        self.wake_silence_chunks = max(4, int(float(os.environ.get("VOICE_WAKE_SILENCE_SEC", "0.7")) * 1000 // CHUNK_MS))
        self.max_chunks = max(15, int(float(os.environ.get("VOICE_MAX_SEC", "5.0")) * 1000 // CHUNK_MS))
        self.wake_max_chunks = max(20, int(float(os.environ.get("VOICE_WAKE_MAX_SEC", "4.8")) * 1000 // CHUNK_MS))
        self.wake_no_speech_chunks = max(15, int(float(os.environ.get("VOICE_WAKE_TIMEOUT_SEC", "3.5")) * 1000 // CHUNK_MS))
        self.preroll_chunks = max(2, int(float(os.environ.get("VOICE_PREROLL_SEC", "0.5")) * 1000 // CHUNK_MS))
        self.cooldown_sec = float(os.environ.get("VOICE_COOLDOWN_SEC", "1.8"))
        self.source = os.environ.get("VOICE_PULSE_SOURCE") or None

        # DSP, decoupled AEC3 & local wake-word configuration
        self.mic_channel = os.environ.get("VOICE_MIC_CHANNEL", "right").strip().lower()
        self.use_rnnoise = _env_bool("VOICE_USE_RNNOISE", True)
        self.rnnoise_on_wake = _env_bool("VOICE_RNNOISE_ON_WAKE", False)
        self.use_bandpass = _env_bool("VOICE_USE_BANDPASS", True)
        self.use_aec3 = _env_bool("VOICE_USE_AEC3", True)
        self.rms_fallback_during_music = _env_bool("VOICE_RMS_FALLBACK_DURING_MUSIC", False)
        self.wake_min_rms = float(os.environ.get("VOICE_WAKE_MIN_RMS", "35.0"))
        self.wake_snr_ratio = float(os.environ.get("VOICE_WAKE_SNR_RATIO", "1.08"))
        self.wake_delta_rms = float(os.environ.get("VOICE_WAKE_DELTA_RMS", "25.0"))
        # Periodic Vosk recognizer reset interval during music playback so
        # continuous music audio does not corrupt the recognizer state.
        self.wake_music_reset_sec = float(os.environ.get("VOICE_WAKE_MUSIC_RESET_SEC", "8.0"))
        self._wake_music_reset_at: float | None = None

        self.bandpass_low_hz = float(os.environ.get("VOICE_BANDPASS_LOW_HZ", "150.0"))
        self.bandpass_high_hz = float(os.environ.get("VOICE_BANDPASS_HIGH_HZ", "6000.0"))

        self.rnnoise = RNNoiseProcessor() if self.use_rnnoise else None
        self.decimator_wake = AntiAliasDecimator48kTo16k()
        self.decimator_stt = AntiAliasDecimator48kTo16k()
        self.wake_bandpass = (
            SpeechBandpassFilter(sample_rate=SAMPLE_RATE, low_hz=self.bandpass_low_hz, high_hz=self.bandpass_high_hz)
            if self.use_bandpass
            else None
        )
        self.stt_bandpass = (
            SpeechBandpassFilter(sample_rate=SAMPLE_RATE, low_hz=self.bandpass_low_hz, high_hz=self.bandpass_high_hz)
            if self.use_bandpass
            else None
        )

        self.aec3 = WebRTCAEC3Processor(sample_rate=SAMPLE_RATE) if self.use_aec3 else None
        self.vis_client = SqueezeliteVisClient(target_rate=SAMPLE_RATE) if self.use_aec3 else None
        self.wake_spotter = VoskWakeWordSpotter(sample_rate=SAMPLE_RATE)
        self.rolling_recorder = (
            RollingAudioRecorder() if _env_bool("VOICE_ROLLING_RECORDER", True) else None
        )

        self.stt_use_raw_bandpass = _env_bool("VOICE_STT_USE_RAW_BANDPASS", True)
        self.use_local_stt_wake = _env_bool("VOICE_LOCAL_STT_WAKE", True)
        self.local_wake_verifier = (
            LocalWakeVerifier(
                stt_url=os.environ.get("LOCAL_STT_URL", "http://127.0.0.1:8766"),
                enabled=self.use_local_stt_wake,
                on_wake=self._on_wake_from_verifier,
            )
            if self.use_local_stt_wake
            else None
        )
        self._pending_stt_wake: dict[str, Any] | None = None
        self._stt_ring: collections.deque[bytes] = collections.deque(maxlen=20)  # 2.0s @ 100ms
        self._speech_streak: int = 0
        self._silence_streak: int = 0
        self._candidate_peak: float = 0.0

        self.state: str = "stopped"  # stopped | waiting | wake | recording | processing | paused
        self.ambient_rms: float = 20.0
        self.wake_ambient_rms: float = 20.0
        self.current_rms: float = 0.0
        self.raw_peak: int = 0
        self.raw_clip_ratio: float = 0.0
        self._clip_warn_until: float = 0.0
        self.music_playing: bool = False
        self.last_speech_at: float | None = None
        self.last_wake_phrase: str | None = None
        self.last_wake_meta: dict = {}
        self.false_wake_rejected_count: int = 0
        self._last_clip_log_at: float = 0.0
        self._wake_rms_window: collections.deque[float] = collections.deque(maxlen=20)  # 2.0s @ 100ms (covers full utterance before Kaldi is_final)
        self._wake_clip_ring: collections.deque[bytes] = collections.deque(maxlen=20)  # 2.0s @ 100ms

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_music_check = 0.0

    @property
    def rnnoise_active(self) -> bool:
        return bool(self.rnnoise and self.rnnoise.available)

    @property
    def aec3_available(self) -> bool:
        return bool(self.aec3 and self.aec3.available)

    @property
    def aec3_active(self) -> bool:
        return bool(self.aec3 and self.aec3.active)

    @property
    def wake_engine_name(self) -> str:
        return "vosk" if self.wake_spotter.available else "rms"

    @property
    def _use_48k_stereo(self) -> bool:
        return self.rnnoise_active or self.aec3_available or self.mic_channel in ("left", "right")

    def _set_state(self, new_state: str, rms: float = 0.0) -> None:
        changed = new_state != self.state
        self.state = new_state
        if changed and self.on_state_change is not None:
            try:
                self.on_state_change(new_state, rms)
            except Exception:
                pass

    def notify_ambient_rms(self, rms_mean: float) -> None:
        """Immediately raise the noise floor when an utterance turned out to be background music/noise."""
        if rms_mean > self.ambient_rms:
            self.ambient_rms = min(2500.0, max(self.ambient_rms, rms_mean * 1.15))
            log.info("Adapted ambient_rms floor upward to %.1f (music/noise rejection)", self.ambient_rms)

    def _on_wake_from_verifier(
        self, phrase: str, transcript: str, stt_ms: float, peak_rms: float, amb_rms: float
    ) -> None:
        self._pending_stt_wake = {
            "phrase": phrase,
            "transcript": transcript,
            "stt_ms": stt_ms,
            "peak": peak_rms,
            "amb": amb_rms,
            "time": time.monotonic(),
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="continuous-voice", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._set_state("stopped")
        if self.rolling_recorder is not None:
            self.rolling_recorder.close()
        if self.local_wake_verifier is not None:
            self.local_wake_verifier.close()
        if self.vis_client is not None:
            self.vis_client.close()
        if self.aec3 is not None:
            self.aec3.close()

    def current_threshold(self) -> float:
        if self.music_playing:
            return max(self.playing_min_threshold, self.ambient_rms * self.playing_multiplier)
        return max(self.min_threshold, self.ambient_rms * self.speech_multiplier)

    def should_allow_rms_fallback(self) -> bool:
        """During music playback with local wake-word active, suppress RMS spikes by default."""
        if not self.music_playing:
            return True
        if not self.wake_spotter.available:
            return True
        return self.rms_fallback_during_music

    def initial_wake_cmd_floor(self) -> float:
        """Estimate initial post-duck background RMS floor right after wake-word fires."""
        if self.music_playing:
            return max(15.0, min(self.ambient_rms * 0.45, 160.0))
        return max(15.0, min(self.ambient_rms, 80.0))

    def wake_cmd_threshold(self, wake_cmd_floor: float, speech_seen: bool) -> float:
        """Dynamic post-duck command threshold so ducked music (~60-100 RMS) never stalls VAD until 4.8s max."""
        mult = 1.45 if speech_seen else 1.85
        return max(self.min_threshold, 35.0, wake_cmd_floor * mult)

    def update_wake_cmd_floor(self, wake_cmd_floor: float, rms: float, is_speech: bool) -> float:
        """Rapidly adapt post-duck floor downward as LMS volume drops, slowly upward on non-speech."""
        capped = min(rms, 600.0)
        if capped < wake_cmd_floor:
            return wake_cmd_floor * 0.45 + capped * 0.55
        if not is_speech:
            return wake_cmd_floor * 0.90 + capped * 0.10
        return wake_cmd_floor

    def passes_wake_energy_gate(self, recent_peak_rms: float) -> bool:
        """Reject faint reverb/music false wake hits that do not rise above floor energy."""
        effective_min_rms = self.wake_min_rms
        if self.music_playing and self.aec3_active and self.aec3.echo_reduction_db >= 1.5:
            effective_min_rms = min(self.wake_min_rms, 25.0)
        if recent_peak_rms < effective_min_rms:
            return False
        if self.music_playing:
            # During loud playback, speech adds non-coherent power to the music floor.
            # Require either an SNR ratio above ambient OR a small absolute delta so
            # normal speaking volume can trigger the wake word even over music.
            # When AEC3 has cancelled speaker echo, the gate is looser because the
            # music floor in the wake path is already suppressed.
            snr_req = self.wake_snr_ratio
            delta_req = self.wake_delta_rms
            if self.aec3 is not None and self.aec3.active and self.aec3.echo_reduction_db >= 1.5:
                snr_req = min(self.wake_snr_ratio, 1.01)
                delta_req = min(self.wake_delta_rms, 8.0)
            if (
                recent_peak_rms < self.wake_ambient_rms * snr_req
                and (recent_peak_rms - self.wake_ambient_rms) < delta_req
            ):
                return False
        return True

    def status_dict(self) -> dict:
        now = time.monotonic()
        return {
            "continuous_listen": self._thread is not None and self._thread.is_alive(),
            "listening_state": self.state,
            "music_playing": self.music_playing,
            "ambient_rms": round(self.ambient_rms, 1),
            "current_rms": round(self.current_rms, 1),
            "active_threshold": round(self.current_threshold(), 1),
            "quiet_threshold": self.min_threshold,
            "playing_threshold": self.playing_min_threshold,
            "multiplier": self.playing_multiplier,
            "raw_peak": self.raw_peak,
            "raw_clip_ratio": round(self.raw_clip_ratio, 4),
            "clip_warning": now < self._clip_warn_until,
            "rnnoise_enabled": self.rnnoise_active,
            "rnnoise_on_wake": self.rnnoise_on_wake,
            "aec3_enabled": self.aec3_available,
            "aec3_active": self.aec3_active,
            "aec3_reduction_db": round(self.aec3.echo_reduction_db, 1) if self.aec3 else 0.0,
            "vis_connected": bool(self.vis_client and self.vis_client.connected),
            "vis_rate": self.vis_client.current_rate if self.vis_client else 0,
            "bandpass_enabled": self.wake_bandpass is not None,
            "bandpass_range": f"{int(self.bandpass_low_hz)}-{int(self.bandpass_high_hz)}Hz",
            "rms_fallback_during_music": self.rms_fallback_during_music,
            "false_wake_rejected_count": self.false_wake_rejected_count,
            "wake_engine": self.wake_engine_name,
            "mic_channel": self.mic_channel,
            "wake_snr_ratio": round(self.wake_snr_ratio, 2),
            "wake_delta_rms": round(self.wake_delta_rms, 1),
            "wake_min_rms": round(self.wake_min_rms, 1),
            "wake_music_reset_sec": self.wake_music_reset_sec,
            "music_confirm_chunks": self.wake_spotter.music_confirm_chunks if self.wake_spotter else 2,
            "music_min_conf": round(self.wake_spotter.music_min_confidence, 2) if self.wake_spotter else 0.65,
            "last_wake_phrase": self.last_wake_phrase,
            "last_speech_at": self.last_speech_at,
            "stt_use_raw_bandpass": self.stt_use_raw_bandpass,
            "local_stt_wake": bool(self.local_wake_verifier and self.local_wake_verifier.enabled),
            "rolling_recorder": self.rolling_recorder.status_dict() if self.rolling_recorder else None,
        }

    def _refresh_music_state(self, now: float) -> None:
        if self.vis_client is not None and self.vis_client.connected:
            self.music_playing = self.vis_client.is_playing
            return
        if now - self._last_music_check >= 2.0:
            self._last_music_check = now
            try:
                self.music_playing = bool(self.is_music_playing_fn())
            except Exception:
                pass

    def _spawn_parecord(self) -> subprocess.Popen:
        env = os.environ.copy()
        env.setdefault("XDG_RUNTIME_DIR", "/run/user/10000")
        env.setdefault("PULSE_SERVER", "unix:/run/user/10000/pulse/native")
        rate = RNNOISE_SAMPLE_RATE if self._use_48k_stereo else SAMPLE_RATE
        channels = 2 if self._use_48k_stereo else 1
        latency_ms = 40 if self._use_48k_stereo else 100
        cmd = [
            "parecord",
            "--raw",
            "--format=s16le",
            f"--rate={rate}",
            f"--channels={channels}",
            f"--latency-msec={latency_ms}",
        ]
        if self.source:
            cmd.extend(["--device", self.source])
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)

    def _read_exact(self, stream, expected: int) -> bytes:
        buf = bytearray()
        while len(buf) < expected:
            part = stream.read(expected - len(buf))
            if not part:
                return b""
            buf.extend(part)
        return bytes(buf)

    def _read_clean_16k_chunk(self, proc: subprocess.Popen) -> tuple[bytes, bytes, bytes]:
        """Read 100ms of audio in 20ms sub-chunks and return (raw_pcm16, wake_pcm16, stt_pcm16)."""
        assert proc.stdout is not None
        if not self._use_48k_stereo:
            raw = self._read_exact(proc.stdout, CHUNK_BYTES)
            if not raw:
                return b"", b"", b""
            peak, clip_ratio = measure_pcm_peak_and_clip(raw)
            self._update_peak_stats(peak, clip_ratio)
            out = self.wake_bandpass.process_pcm(raw) if self.wake_bandpass is not None else raw
            return raw, out, out

        raw_parts: list[bytes] = []
        wake_parts: list[bytes] = []
        stt_parts: list[bytes] = []
        max_peak = 0
        max_clip = 0.0

        for _ in range(SUBCHUNKS_PER_CHUNK):
            raw_sub = self._read_exact(proc.stdout, SUBCHUNK_48K_STEREO_BYTES)
            if not raw_sub:
                return b"", b"", b""

            mono48 = extract_mono_channel(raw_sub, self.mic_channel)
            peak, clip_ratio = measure_pcm_peak_and_clip(mono48)
            if peak > max_peak:
                max_peak = peak
            if clip_ratio > max_clip:
                max_clip = clip_ratio

            # Anti-aliased 48k -> 16k decimation BEFORE bandpass & AEC3
            clean16 = self.decimator_wake.process_48k_to_16k(mono48)
            raw_parts.append(clean16)

            # Decoupled WebRTC AEC3 using Squeezelite VISEXPORT shm reference (zero DAC impact)
            ref_stereo = (
                self.vis_client.pull_16k_stereo(SUBCHUNK_16K_FRAMES)
                if self.vis_client is not None
                else None
            )
            if self.aec3 is not None and self.aec3.available:
                aec16 = self.aec3.process_chunk(clean16, ref_stereo)
            else:
                aec16 = clean16

            wake_sub = self.wake_bandpass.process_pcm(aec16) if self.wake_bandpass is not None else aec16

            if self.stt_use_raw_bandpass:
                # Anti-aliased decimated 16k mono with bandpass (150-6000Hz) bypasses AEC3
                # double-talk residual suppression so STT receives 100% undistorted speech phonemes
                stt_sub = self.stt_bandpass.process_pcm(clean16) if self.stt_bandpass is not None else clean16
            elif self.aec3_active:
                # AEC3 already subtracted music; keep STT path crisp without RNNoise vocal distortion
                stt_sub = wake_sub
            elif self.rnnoise is not None and self.rnnoise.available:
                denoised48, _ = self.rnnoise.process_48k_pcm(mono48)
                denoised16 = self.decimator_stt.process_48k_to_16k(denoised48)
                stt_sub = self.stt_bandpass.process_pcm(denoised16) if self.stt_bandpass is not None else denoised16
                if self.rnnoise_on_wake:
                    wake_sub = stt_sub
            else:
                stt_sub = wake_sub

            wake_parts.append(wake_sub)
            stt_parts.append(stt_sub)

        self._update_peak_stats(max_peak, max_clip)
        return b"".join(raw_parts), b"".join(wake_parts), b"".join(stt_parts)

    def _update_peak_stats(self, peak: int, clip_ratio: float) -> None:
        self.raw_peak = peak
        self.raw_clip_ratio = clip_ratio
        if peak >= 30000:
            now = time.monotonic()
            self._clip_warn_until = now + 2.5
            if self.state == "waiting" and self.telemetry is not None and (now - self._last_clip_log_at) >= 30.0:
                self._last_clip_log_at = now
                self.telemetry.record(
                    "clip_detected",
                    raw_peak=peak,
                    clip_ratio=clip_ratio,
                    music_playing=self.music_playing,
                )

    def _build_wake_telemetry_meta(self, phrase: str, wake_peak: float, wake_ev: dict | None = None) -> dict:
        amb = max(self.wake_ambient_rms, 1.0)
        meta: dict = {
            "phrase": phrase,
            "music_playing": self.music_playing,
            "wake_peak_rms": round(wake_peak, 1),
            "wake_ambient_rms": round(self.wake_ambient_rms, 1),
            "snr_ratio": round(wake_peak / amb, 3),
            "raw_peak": self.raw_peak,
            "clip_ratio": round(self.raw_clip_ratio, 4),
            "aec3_active": self.aec3_active,
            "aec3_reduction_db": round(self.aec3.echo_reduction_db, 1) if self.aec3 else 0.0,
        }
        if wake_ev:
            for k in ("is_final", "confidence", "min_word_conf", "streak", "text"):
                if wake_ev.get(k) is not None:
                    meta[k] = wake_ev[k]
        return meta

    def _run_loop(self) -> None:
        log.info(
            "Starting Voice 2.1 listener (channel=%s aec3=%s vis=%s rnnoise=%s(wake=%s) bp=%d-%dHz wake=%s rms_in_music=%s)",
            self.mic_channel,
            self.aec3_available,
            bool(self.vis_client and self.vis_client.connected),
            self.rnnoise_active,
            self.rnnoise_on_wake,
            int(self.bandpass_low_hz),
            int(self.bandpass_high_hz),
            self.wake_engine_name,
            self.rms_fallback_during_music,
        )
        while not self._stop_event.is_set():
            proc = self._spawn_parecord()
            self.decimator_wake.reset()
            self.decimator_stt.reset()
            if self.wake_bandpass is not None:
                self.wake_bandpass.reset()
            if self.stt_bandpass is not None:
                self.stt_bandpass.reset()
            self.wake_spotter.reset()

            preroll: collections.deque[bytes] = collections.deque(maxlen=self.preroll_chunks)
            captured: list[bytes] = []
            recording = False
            wake_mode = False
            wake_phrase = ""
            wake_speech_seen = False
            wake_cmd_floor = 25.0
            consecutive_speech = 0
            consecutive_silence = 0
            cooldown_until = 0.0
            self._set_state("waiting")

            try:
                while not self._stop_event.is_set():
                    raw_data, wake_data, stt_data = self._read_clean_16k_chunk(proc)
                    if not wake_data:
                        break

                    if self.rolling_recorder is not None and self.rolling_recorder.enabled:
                        self.rolling_recorder.feed(
                            raw_data,
                            wake_data,
                            is_music_playing=self.music_playing,
                            aec3_db=self.aec3.echo_reduction_db if self.aec3 else 0.0,
                        )

                    if self.is_paused_fn():
                        self._set_state("paused")
                        recording = False
                        wake_mode = False
                        captured.clear()
                        consecutive_speech = 0
                        preroll.clear()
                        continue

                    now = time.monotonic()
                    self._refresh_music_state(now)
                    rms = chunk_rms(stt_data)
                    wake_rms = chunk_rms(wake_data)
                    self.current_rms = rms
                    self._wake_rms_window.append(wake_rms)
                    self._wake_clip_ring.append(wake_data)
                    self._stt_ring.append(stt_data)

                    # During cooldown, keep tracking ambient_rms cleanly
                    if now < cooldown_until:
                        self._set_state("waiting")
                        preroll.append(stt_data)
                        capped = min(rms, 2500.0)
                        alpha = 0.15 if capped > self.ambient_rms else 0.06
                        self.ambient_rms = self.ambient_rms * (1.0 - alpha) + capped * alpha
                        wcap = min(wake_rms, 2500.0)
                        self.wake_ambient_rms = self.wake_ambient_rms * 0.90 + wcap * 0.10
                        continue

                    threshold = self.current_threshold()

                    if not recording:
                        self._set_state("waiting")
                        preroll.append(stt_data)

                        # Trigger Path 1B: Local SenseVoice STT wake verifier (handles Korean "포코야", "코코야", etc.)
                        if self._pending_stt_wake is not None and self.on_wake_utterance is not None:
                            w_info = self._pending_stt_wake
                            self._pending_stt_wake = None
                            if (now - w_info["time"]) < 2.5:
                                hit = w_info["phrase"]
                                transcript = w_info["transcript"]
                                recent_peak = w_info.get("peak", rms)
                                wake_ev = {
                                    "phrase": hit,
                                    "source": "local_sensevoice",
                                    "transcript": transcript,
                                    "confidence": 0.98,
                                    "status": "triggered",
                                }
                                meta = self._build_wake_telemetry_meta(hit, recent_peak, wake_ev)
                                clip_path = None
                                if self.telemetry is not None:
                                    clip_wav = pcm_to_wav_bytes(b"".join(self._wake_clip_ring))
                                    clip_path = self.telemetry.save_wake_clip(clip_wav, "pending", meta)
                                    self.telemetry.record("wake_triggered", clip_path=clip_path, **meta)
                                self.last_wake_meta = {**meta, "clip_path": clip_path}
                                log.info(
                                    "SenseVoice wake-word triggered: '%s' from %r (rms=%.1f peak=%.1f amb=%.1f music=%s)",
                                    hit,
                                    transcript,
                                    rms,
                                    recent_peak,
                                    self.wake_ambient_rms,
                                    self.music_playing,
                                )
                                self.last_wake_phrase = hit
                                self.last_speech_at = time.time()
                                wake_phrase = hit
                                wake_mode = True
                                recording = True
                                wake_speech_seen = False
                                wake_cmd_floor = self.initial_wake_cmd_floor()
                                consecutive_silence = 0
                                captured = list(preroll)[-2:]
                                self._set_state("wake", rms)
                                if self.on_wake_detected is not None:
                                    try:
                                        self.on_wake_detected(hit)
                                    except Exception as exc:
                                        log.error("on_wake_detected hook error: %s", exc)
                                self._set_state("recording", rms)
                                continue

                        # Candidate speech detection for local STT wake verifier during music
                        if self.music_playing and self.local_wake_verifier is not None:
                            speech_floor = max(self.wake_min_rms, self.wake_ambient_rms * 1.60)
                            if rms >= speech_floor:
                                self._speech_streak += 1
                                self._silence_streak = 0
                                if rms > self._candidate_peak:
                                    self._candidate_peak = rms
                            else:
                                if self._speech_streak > 0:
                                    self._silence_streak += 1

                            if self._speech_streak >= 2 and (self._silence_streak >= 2 or self._speech_streak >= 12):
                                slice_len = min(len(self._stt_ring), self._speech_streak + 6)
                                pcm_slice = b"".join(list(self._stt_ring)[-slice_len:])
                                self.local_wake_verifier.submit(pcm_slice, self._candidate_peak, self.wake_ambient_rms)
                                self._speech_streak = 0
                                self._silence_streak = 0
                                self._candidate_peak = 0.0

                        # Trigger Path 1: Local streaming Vosk wake-word spotter on AEC3 + anti-aliased audio
                        # During music, always feed audio to Vosk so it can detect wake words.
                        # The energy gate (passes_wake_energy_gate) filters music-only false positives.
                        # is_silent is only used to allow Vosk to reset its recognizer state during
                        # quiet moments in the music, preventing state corruption from continuous audio.
                        if self.music_playing:
                            # Periodic reset during music playback so lyrics do not corrupt state,
                            # but never reset while an utterance is actively in progress.
                            now = time.monotonic()
                            if (
                                self._wake_music_reset_at is None
                                or (
                                    now - self._wake_music_reset_at >= self.wake_music_reset_sec
                                    and not getattr(self.wake_spotter, "_has_active_partial", False)
                                    and getattr(self.wake_spotter, "_partial_streak", 0) == 0
                                )
                            ):
                                self.wake_spotter.reset()
                                self._wake_music_reset_at = now
                            is_silent = wake_rms < max(18.0, self.wake_ambient_rms * 1.15)
                        else:
                            is_silent = wake_rms < max(self.min_threshold, self.wake_ambient_rms * 1.4)
                        hit = (
                            self.wake_spotter.accept_pcm16k(
                                wake_data,
                                is_silent=is_silent,
                                music_playing=self.music_playing,
                            )
                            if self.wake_spotter.available
                            else None
                        )
                        wake_ev = self.wake_spotter.last_event
                        recent_peak = max(self._wake_rms_window) if self._wake_rms_window else wake_rms

                        # Log low-confidence rejections from Vosk for statistical analysis
                        if hit is None and wake_ev and wake_ev.get("status") == "rejected_conf":
                            self.false_wake_rejected_count += 1
                            if self.telemetry is not None:
                                meta = self._build_wake_telemetry_meta(str(wake_ev.get("phrase", "")), recent_peak, wake_ev)
                                clip_wav = pcm_to_wav_bytes(b"".join(self._wake_clip_ring))
                                clip_path = self.telemetry.save_wake_clip(clip_wav, "rejected", {**meta, "reject_reason": "conf"})
                                self.telemetry.record("wake_rejected_conf", clip_path=clip_path, **meta)

                        if hit and self.on_wake_utterance is not None:
                            if not self.passes_wake_energy_gate(recent_peak):
                                self.false_wake_rejected_count += 1
                                log.info(
                                    "Rejected weak wake false-positive '%s' (peak=%.1f wake_amb=%.1f music=%s)",
                                    hit,
                                    recent_peak,
                                    self.wake_ambient_rms,
                                    self.music_playing,
                                )
                                if self.telemetry is not None:
                                    meta = self._build_wake_telemetry_meta(hit, recent_peak, wake_ev)
                                    clip_wav = pcm_to_wav_bytes(b"".join(self._wake_clip_ring))
                                    clip_path = self.telemetry.save_wake_clip(clip_wav, "rejected", {**meta, "reject_reason": "energy"})
                                    self.telemetry.record("wake_rejected_energy", clip_path=clip_path, **meta)
                                hit = None
                        if hit and self.on_wake_utterance is not None:
                            meta = self._build_wake_telemetry_meta(hit, recent_peak, wake_ev)
                            clip_path = None
                            if self.telemetry is not None:
                                clip_wav = pcm_to_wav_bytes(b"".join(self._wake_clip_ring))
                                clip_path = self.telemetry.save_wake_clip(clip_wav, "pending", meta)
                                self.telemetry.record("wake_triggered", clip_path=clip_path, **meta)
                            self.last_wake_meta = {**meta, "clip_path": clip_path}
                            log.info(
                                "Local wake-word triggered: '%s' (rms=%.1f wake_peak=%.1f wake_amb=%.1f music=%s aec3=%.1fdB)",
                                hit,
                                rms,
                                recent_peak,
                                self.wake_ambient_rms,
                                self.music_playing,
                                self.aec3.echo_reduction_db if self.aec3 else 0.0,
                            )
                            self.last_wake_phrase = hit
                            self.last_speech_at = time.time()
                            wake_phrase = hit
                            wake_mode = True
                            recording = True
                            wake_speech_seen = False
                            wake_cmd_floor = self.initial_wake_cmd_floor()
                            consecutive_silence = 0
                            # Keep last 2 chunks (~0.2s) in case user speaks continuously without pausing
                            captured = list(preroll)[-2:]
                            self._set_state("wake", rms)
                            if self.on_wake_detected is not None:
                                try:
                                    self.on_wake_detected(hit)
                                except Exception as exc:
                                    log.error("on_wake_detected hook error: %s", exc)
                            self._set_state("recording", rms)
                            continue

                        # Trigger Path 2: Adaptive RMS speech-spike fallback
                        # Disabled during active music playback when local wake-word is running so
                        # musical crescendos / drums never hijack the listener or waste cloud STT calls.
                        if self.should_allow_rms_fallback() and rms >= threshold:
                            consecutive_speech += 1
                            if consecutive_speech >= self.onset_chunks:
                                recording = True
                                wake_mode = False
                                self._set_state("recording", rms)
                                self.last_speech_at = time.time()
                                captured = list(preroll)
                                consecutive_silence = 0
                        else:
                            consecutive_speech = 0
                            capped = min(rms, 2500.0)
                            alpha = 0.12 if capped > self.ambient_rms else 0.06
                            self.ambient_rms = self.ambient_rms * (1.0 - alpha) + capped * alpha
                            wcap = min(wake_rms, 2500.0)
                            walpha = 0.12 if wcap > self.wake_ambient_rms else 0.06
                            self.wake_ambient_rms = self.wake_ambient_rms * (1.0 - walpha) + wcap * walpha
                    else:
                        captured.append(stt_data)
                        if wake_mode:
                            if self.is_chime_playing_fn():
                                # Prompt chime is playing out of internal speaker adjacent to mic.
                                # Discard chime chunks from command capture so STT and VAD are not corrupted.
                                captured.clear()
                                wake_speech_seen = False
                                consecutive_silence = 0
                                continue

                            # Dynamic post-duck command VAD: adapts to actual ducked room/speaker floor
                            # so utterances end cleanly ~0.7s after speech instead of hitting 4.8s max.
                            cmd_threshold = self.wake_cmd_threshold(wake_cmd_floor, wake_speech_seen)
                            is_cmd_speech = rms >= cmd_threshold
                            wake_cmd_floor = self.update_wake_cmd_floor(wake_cmd_floor, rms, is_cmd_speech)
                            if is_cmd_speech:
                                wake_speech_seen = True
                                consecutive_silence = 0
                            elif wake_speech_seen:
                                consecutive_silence += 1

                            done_silence = wake_speech_seen and consecutive_silence >= self.wake_silence_chunks
                            done_timeout = (not wake_speech_seen) and len(captured) >= self.wake_no_speech_chunks
                            done_max = len(captured) >= self.wake_max_chunks
                            if done_silence or done_timeout or done_max:
                                if wake_speech_seen and captured:
                                    # Trim excess trailing silence (keeping ~200ms padding) for crisp low-latency STT
                                    if consecutive_silence > 2:
                                        excess = consecutive_silence - 2
                                        trimmed_chunks = captured[:-excess] if len(captured) > excess else captured
                                    else:
                                        trimmed_chunks = captured
                                    wav_bytes = pcm_to_wav_bytes(b"".join(trimmed_chunks))
                                else:
                                    wav_bytes = None
                                phrase = wake_phrase
                                recording = False
                                wake_mode = False
                                captured.clear()
                                consecutive_speech = 0
                                consecutive_silence = 0
                                preroll.clear()
                                self.wake_spotter.reset()
                                self._set_state("processing", rms)
                                try:
                                    assert self.on_wake_utterance is not None
                                    self.on_wake_utterance(wav_bytes, phrase)
                                except Exception as exc:
                                    log.error("Wake utterance handler error: %s", exc)
                                cooldown_until = time.monotonic() + self.cooldown_sec
                                self._set_state("waiting")
                        else:
                            # Keep feeding local Vosk wake spotter during the first 1.5s of RMS recording
                            # so "포코야" still triggers instant Auto-Ducking even if RMS onset fired on syllable 1!
                            if self.wake_spotter.available and self.on_wake_utterance is not None and len(captured) <= 15:
                                hit = self.wake_spotter.accept_pcm16k(
                                    wake_data,
                                    is_silent=False,
                                    music_playing=self.music_playing,
                                )
                                if hit:
                                    log.info("Local wake-word triggered mid-onset: '%s' (rms=%.1f)", hit, rms)
                                    self.last_wake_phrase = hit
                                    self.last_speech_at = time.time()
                                    wake_phrase = hit
                                    wake_mode = True
                                    wake_speech_seen = False
                                    wake_cmd_floor = self.initial_wake_cmd_floor()
                                    consecutive_silence = 0
                                    captured = captured[-2:]
                                    self._set_state("wake", rms)
                                    if self.on_wake_detected is not None:
                                        try:
                                            self.on_wake_detected(hit)
                                        except Exception as exc:
                                            log.error("on_wake_detected hook error: %s", exc)
                                    self._set_state("recording", rms)
                                    continue

                            sustain_threshold = threshold * 0.78
                            if rms >= sustain_threshold:
                                consecutive_silence = 0
                            else:
                                consecutive_silence += 1

                            if consecutive_silence >= self.silence_chunks or len(captured) >= self.max_chunks:
                                wav_bytes = pcm_to_wav_bytes(b"".join(captured))
                                recording = False
                                captured.clear()
                                consecutive_speech = 0
                                consecutive_silence = 0
                                preroll.clear()
                                self.wake_spotter.reset()
                                self._set_state("processing", rms)
                                try:
                                    self.on_utterance(wav_bytes)
                                except Exception as exc:
                                    log.error("Continuous utterance handler error: %s", exc)
                                cooldown_until = time.monotonic() + self.cooldown_sec
                                self._set_state("waiting")
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    proc.kill()

            if not self._stop_event.is_set():
                log.warning("parecord stream ended; restarting in 2s...")
                self._set_state("waiting")
                time.sleep(2.0)
