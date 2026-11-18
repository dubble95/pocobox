"""Always-on 24/7 hands-free microphone listener with RNNoise DSP, local Vosk wake-word, and auto-ducking hooks."""

from __future__ import annotations

import collections
import logging
import os
import subprocess
import threading
import time
from typing import Callable

from .audio_capture import CHUNK_BYTES, CHUNK_MS, SAMPLE_RATE, chunk_rms, pcm_to_wav_bytes
from .audio_dsp import (
    RNNOISE_SAMPLE_RATE,
    RNNoiseProcessor,
    SpeechBandpassFilter,
    decimate_48k_to_16k,
    extract_mono_channel,
)
from .wakeword import VoskWakeWordSpotter

log = logging.getLogger("pocobox-voice.continuous")

CHUNK_48K_STEREO_BYTES = (RNNOISE_SAMPLE_RATE * CHUNK_MS // 1000) * 4  # 100ms @ 48kHz, 2ch, s16le = 19200 bytes


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


class ContinuousVoiceListener:
    """Continuously monitors PulseAudio mic with RNNoise + Bandpass DSP + local Vosk wake-word spotting."""

    def __init__(
        self,
        on_utterance: Callable[[bytes], None],
        is_paused_fn: Callable[[], bool] | None = None,
        on_state_change: Callable[[str, float], None] | None = None,
        is_music_playing_fn: Callable[[], bool] | None = None,
        on_wake_detected: Callable[[str], None] | None = None,
        on_wake_utterance: Callable[[bytes | None, str], None] | None = None,
    ) -> None:
        self.on_utterance = on_utterance
        self.is_paused_fn = is_paused_fn or (lambda: False)
        self.on_state_change = on_state_change
        self.is_music_playing_fn = is_music_playing_fn or (lambda: False)
        self.on_wake_detected = on_wake_detected
        self.on_wake_utterance = on_wake_utterance

        # Quiet-room thresholds (on RNNoise + bandpass cleaned 16kHz audio)
        self.min_threshold = float(os.environ.get("VOICE_VAD_THRESHOLD", "40.0"))
        self.speech_multiplier = float(os.environ.get("VOICE_VAD_MULTIPLIER", "3.0"))
        # Playback-aware thresholds (when LMS is actively playing music)
        self.playing_min_threshold = float(os.environ.get("VOICE_PLAYING_VAD_THRESHOLD", "180.0"))
        self.playing_multiplier = float(os.environ.get("VOICE_PLAYING_MULTIPLIER", "2.1"))

        self.onset_chunks = max(2, int(float(os.environ.get("VOICE_ONSET_SEC", "0.2")) * 1000 // CHUNK_MS))
        self.silence_chunks = max(4, int(float(os.environ.get("VOICE_SILENCE_SEC", "0.9")) * 1000 // CHUNK_MS))
        self.max_chunks = max(15, int(float(os.environ.get("VOICE_MAX_SEC", "5.0")) * 1000 // CHUNK_MS))
        self.wake_max_chunks = max(20, int(float(os.environ.get("VOICE_WAKE_MAX_SEC", "4.8")) * 1000 // CHUNK_MS))
        self.wake_no_speech_chunks = max(15, int(float(os.environ.get("VOICE_WAKE_TIMEOUT_SEC", "3.5")) * 1000 // CHUNK_MS))
        self.preroll_chunks = max(2, int(float(os.environ.get("VOICE_PREROLL_SEC", "0.5")) * 1000 // CHUNK_MS))
        self.cooldown_sec = float(os.environ.get("VOICE_COOLDOWN_SEC", "1.8"))
        self.source = os.environ.get("VOICE_PULSE_SOURCE") or None

        # DSP & local wake-word configuration
        self.mic_channel = os.environ.get("VOICE_MIC_CHANNEL", "right").strip().lower()
        self.use_rnnoise = _env_bool("VOICE_USE_RNNOISE", True)
        self.use_bandpass = _env_bool("VOICE_USE_BANDPASS", True)
        self.rnnoise = RNNoiseProcessor() if self.use_rnnoise else None
        self.bandpass = SpeechBandpassFilter(sample_rate=SAMPLE_RATE, low_hz=250.0, high_hz=3500.0) if self.use_bandpass else None
        self.wake_spotter = VoskWakeWordSpotter(sample_rate=SAMPLE_RATE)

        self.state: str = "stopped"  # stopped | waiting | wake | recording | processing | paused
        self.ambient_rms: float = 20.0
        self.current_rms: float = 0.0
        self.music_playing: bool = False
        self.last_speech_at: float | None = None
        self.last_wake_phrase: str | None = None

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_music_check = 0.0

    @property
    def rnnoise_active(self) -> bool:
        return bool(self.rnnoise and self.rnnoise.available)

    @property
    def wake_engine_name(self) -> str:
        return "vosk" if self.wake_spotter.available else "rms"

    @property
    def _use_48k_stereo(self) -> bool:
        return self.rnnoise_active or self.mic_channel in ("left", "right")

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

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="continuous-voice", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._set_state("stopped")

    def current_threshold(self) -> float:
        if self.music_playing:
            return max(self.playing_min_threshold, self.ambient_rms * self.playing_multiplier)
        return max(self.min_threshold, self.ambient_rms * self.speech_multiplier)

    def status_dict(self) -> dict:
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
            "rnnoise_enabled": self.rnnoise_active,
            "bandpass_enabled": self.bandpass is not None,
            "wake_engine": self.wake_engine_name,
            "mic_channel": self.mic_channel,
            "last_wake_phrase": self.last_wake_phrase,
            "last_speech_at": self.last_speech_at,
        }

    def _refresh_music_state(self, now: float) -> None:
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
        cmd = [
            "parecord",
            "--raw",
            "--format=s16le",
            f"--rate={rate}",
            f"--channels={channels}",
            "--latency-msec=100",
        ]
        if self.source:
            cmd.extend(["--device", self.source])
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)

    def _read_clean_16k_chunk(self, proc: subprocess.Popen) -> bytes:
        assert proc.stdout is not None
        expected = CHUNK_48K_STEREO_BYTES if self._use_48k_stereo else CHUNK_BYTES
        buf = bytearray()
        while len(buf) < expected:
            part = proc.stdout.read(expected - len(buf))
            if not part:
                return b""
            buf.extend(part)
        raw = bytes(buf)

        if self._use_48k_stereo:
            mono48 = extract_mono_channel(raw, self.mic_channel)
            if self.rnnoise is not None and self.rnnoise.available:
                mono48, _ = self.rnnoise.process_48k_pcm(mono48)
            pcm16 = decimate_48k_to_16k(mono48)
        else:
            pcm16 = raw

        if self.bandpass is not None:
            pcm16 = self.bandpass.process_pcm(pcm16)
        return pcm16

    def _run_loop(self) -> None:
        log.info(
            "Starting Voice 2.0 listener (channel=%s rnnoise=%s bandpass=%s wake=%s quiet=%.0f*%.1f playing=%.0f*%.1f)",
            self.mic_channel,
            self.rnnoise_active,
            self.bandpass is not None,
            self.wake_engine_name,
            self.min_threshold,
            self.speech_multiplier,
            self.playing_min_threshold,
            self.playing_multiplier,
        )
        while not self._stop_event.is_set():
            proc = self._spawn_parecord()
            if self.bandpass is not None:
                self.bandpass.reset()
            self.wake_spotter.reset()

            preroll: collections.deque[bytes] = collections.deque(maxlen=self.preroll_chunks)
            captured: list[bytes] = []
            recording = False
            wake_mode = False
            wake_phrase = ""
            wake_speech_seen = False
            consecutive_speech = 0
            consecutive_silence = 0
            cooldown_until = 0.0
            self._set_state("waiting")

            try:
                while not self._stop_event.is_set():
                    data = self._read_clean_16k_chunk(proc)
                    if not data:
                        break

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
                    rms = chunk_rms(data)
                    self.current_rms = rms

                    # During cooldown, keep tracking ambient_rms cleanly
                    if now < cooldown_until:
                        self._set_state("waiting")
                        preroll.append(data)
                        capped = min(rms, 2500.0)
                        alpha = 0.15 if capped > self.ambient_rms else 0.06
                        self.ambient_rms = self.ambient_rms * (1.0 - alpha) + capped * alpha
                        continue

                    threshold = self.current_threshold()

                    if not recording:
                        self._set_state("waiting")
                        preroll.append(data)

                        # Trigger Path 1: Local streaming Vosk wake-word spotter (works even during music!)
                        hit = self.wake_spotter.accept_pcm16k(data) if self.wake_spotter.available else None
                        if hit and self.on_wake_utterance is not None:
                            log.info("Local wake-word triggered: '%s' (rms=%.1f ambient=%.1f)", hit, rms, self.ambient_rms)
                            self.last_wake_phrase = hit
                            self.last_speech_at = time.time()
                            wake_phrase = hit
                            wake_mode = True
                            recording = True
                            wake_speech_seen = False
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

                        # Trigger Path 2: Adaptive RMS speech-spike fallback (1-breath command over RNNoise floor)
                        if rms >= threshold:
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
                    else:
                        captured.append(data)
                        if wake_mode:
                            # Music is ducked to ~18%, so use sensitive quiet-room command threshold
                            cmd_threshold = max(self.min_threshold, 35.0)
                            if rms >= cmd_threshold:
                                wake_speech_seen = True
                                consecutive_silence = 0
                            elif wake_speech_seen:
                                consecutive_silence += 1

                            done_silence = wake_speech_seen and consecutive_silence >= self.silence_chunks
                            done_timeout = (not wake_speech_seen) and len(captured) >= self.wake_no_speech_chunks
                            done_max = len(captured) >= self.wake_max_chunks
                            if done_silence or done_timeout or done_max:
                                wav_bytes = pcm_to_wav_bytes(b"".join(captured)) if wake_speech_seen else None
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
                                hit = self.wake_spotter.accept_pcm16k(data)
                                if hit:
                                    log.info("Local wake-word triggered mid-onset: '%s' (rms=%.1f)", hit, rms)
                                    self.last_wake_phrase = hit
                                    self.last_speech_at = time.time()
                                    wake_phrase = hit
                                    wake_mode = True
                                    wake_speech_seen = False
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
