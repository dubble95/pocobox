"""Always-on 24/7 hands-free microphone listener with playback-aware adaptive noise floor."""

from __future__ import annotations

import collections
import logging
import os
import subprocess
import threading
import time
from typing import Callable

from .audio_capture import CHUNK_BYTES, CHUNK_MS, SAMPLE_RATE, chunk_rms, pcm_to_wav_bytes

log = logging.getLogger("pocobox-voice.continuous")


class ContinuousVoiceListener:
    """Continuously monitors PulseAudio mic and emits WAV utterances when speech spikes above ambient/music."""

    def __init__(
        self,
        on_utterance: Callable[[bytes], None],
        is_paused_fn: Callable[[], bool] | None = None,
        on_state_change: Callable[[str, float], None] | None = None,
        is_music_playing_fn: Callable[[], bool] | None = None,
    ) -> None:
        self.on_utterance = on_utterance
        self.is_paused_fn = is_paused_fn or (lambda: False)
        self.on_state_change = on_state_change
        self.is_music_playing_fn = is_music_playing_fn or (lambda: False)

        # Quiet-room thresholds (when music is stopped/paused)
        self.min_threshold = float(os.environ.get("VOICE_VAD_THRESHOLD", "50.0"))
        self.speech_multiplier = float(os.environ.get("VOICE_VAD_MULTIPLIER", "3.5"))
        # Playback-aware thresholds (when LMS is actively playing music)
        self.playing_min_threshold = float(os.environ.get("VOICE_PLAYING_VAD_THRESHOLD", "260.0"))
        self.playing_multiplier = float(os.environ.get("VOICE_PLAYING_MULTIPLIER", "2.6"))

        self.onset_chunks = max(2, int(float(os.environ.get("VOICE_ONSET_SEC", "0.2")) * 1000 // CHUNK_MS))
        self.silence_chunks = max(4, int(float(os.environ.get("VOICE_SILENCE_SEC", "0.9")) * 1000 // CHUNK_MS))
        self.max_chunks = max(15, int(float(os.environ.get("VOICE_MAX_SEC", "5.0")) * 1000 // CHUNK_MS))
        self.preroll_chunks = max(2, int(float(os.environ.get("VOICE_PREROLL_SEC", "0.5")) * 1000 // CHUNK_MS))
        self.cooldown_sec = float(os.environ.get("VOICE_COOLDOWN_SEC", "2.0"))
        self.source = os.environ.get("VOICE_PULSE_SOURCE") or None

        self.state: str = "stopped"  # stopped | waiting | recording | processing | paused
        self.ambient_rms: float = 20.0
        self.current_rms: float = 0.0
        self.music_playing: bool = False
        self.last_speech_at: float | None = None

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_music_check = 0.0

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
            # Jump floor quickly toward music level so the same song stops re-triggering
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
        cmd = [
            "parecord",
            "--raw",
            "--format=s16le",
            f"--rate={SAMPLE_RATE}",
            "--channels=1",
            "--latency-msec=100",
        ]
        if self.source:
            cmd.extend(["--device", self.source])
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)

    def _run_loop(self) -> None:
        log.info(
            "Starting playback-aware always-on voice listener (quiet=%.0f*%.1f playing=%.0f*%.1f)",
            self.min_threshold,
            self.speech_multiplier,
            self.playing_min_threshold,
            self.playing_multiplier,
        )
        while not self._stop_event.is_set():
            proc = self._spawn_parecord()
            assert proc.stdout is not None
            preroll: collections.deque[bytes] = collections.deque(maxlen=self.preroll_chunks)
            captured: list[bytes] = []
            recording = False
            consecutive_speech = 0
            consecutive_silence = 0
            cooldown_until = 0.0
            self._set_state("waiting")

            try:
                while not self._stop_event.is_set():
                    data = proc.stdout.read(CHUNK_BYTES)
                    if not data:
                        break

                    if self.is_paused_fn():
                        self._set_state("paused")
                        recording = False
                        captured.clear()
                        consecutive_speech = 0
                        preroll.clear()
                        continue

                    now = time.monotonic()
                    self._refresh_music_state(now)
                    rms = chunk_rms(data)
                    self.current_rms = rms

                    # During cooldown, ALWAYS track ambient_rms so playing music updates the floor!
                    if now < cooldown_until:
                        self._set_state("waiting")
                        preroll.append(data)
                        capped = min(rms, 2500.0)
                        alpha = 0.15 if capped > self.ambient_rms else 0.05
                        self.ambient_rms = self.ambient_rms * (1.0 - alpha) + capped * alpha
                        continue

                    threshold = self.current_threshold()

                    if not recording:
                        self._set_state("waiting")
                        preroll.append(data)
                        if rms >= threshold:
                            consecutive_speech += 1
                            if consecutive_speech >= self.onset_chunks:
                                recording = True
                                self._set_state("recording", rms)
                                self.last_speech_at = time.time()
                                captured = list(preroll)
                                consecutive_silence = 0
                        else:
                            consecutive_speech = 0
                            capped = min(rms, 2500.0)
                            # Track upward quickly if music just got louder, decay gently when room gets quiet
                            alpha = 0.12 if capped > self.ambient_rms else 0.06
                            self.ambient_rms = self.ambient_rms * (1.0 - alpha) + capped * alpha
                    else:
                        captured.append(data)
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
