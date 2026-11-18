"""Always-on 24/7 hands-free microphone listener with pre-roll ring buffer and adaptive VAD."""

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
    """Continuously monitors PulseAudio mic and emits WAV utterances when speech is detected."""

    def __init__(
        self,
        on_utterance: Callable[[bytes], None],
        is_paused_fn: Callable[[], bool] | None = None,
    ) -> None:
        self.on_utterance = on_utterance
        self.is_paused_fn = is_paused_fn or (lambda: False)

        self.min_threshold = float(os.environ.get("VOICE_VAD_THRESHOLD", "350.0"))
        self.speech_multiplier = float(os.environ.get("VOICE_VAD_MULTIPLIER", "2.3"))
        self.onset_chunks = max(2, int(float(os.environ.get("VOICE_ONSET_SEC", "0.3")) * 1000 // CHUNK_MS))
        self.silence_chunks = max(4, int(float(os.environ.get("VOICE_SILENCE_SEC", "1.0")) * 1000 // CHUNK_MS))
        self.max_chunks = max(15, int(float(os.environ.get("VOICE_MAX_SEC", "7.0")) * 1000 // CHUNK_MS))
        self.preroll_chunks = max(2, int(float(os.environ.get("VOICE_PREROLL_SEC", "0.5")) * 1000 // CHUNK_MS))
        self.cooldown_sec = float(os.environ.get("VOICE_COOLDOWN_SEC", "1.5"))
        self.source = os.environ.get("VOICE_PULSE_SOURCE") or None

        self.state: str = "stopped"  # stopped | waiting | recording | processing | paused
        self.ambient_rms: float = 120.0
        self.current_rms: float = 0.0
        self.last_speech_at: float | None = None

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="continuous-voice", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self.state = "stopped"

    def status_dict(self) -> dict:
        return {
            "continuous_listen": self._thread is not None and self._thread.is_alive(),
            "listening_state": self.state,
            "ambient_rms": round(self.ambient_rms, 1),
            "current_rms": round(self.current_rms, 1),
            "last_speech_at": self.last_speech_at,
        }

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
            "Starting always-on voice listener (min_thresh=%.0f mult=%.1f preroll=%d onset=%d silence=%d)",
            self.min_threshold,
            self.speech_multiplier,
            self.preroll_chunks,
            self.onset_chunks,
            self.silence_chunks,
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
            self.state = "waiting"

            try:
                while not self._stop_event.is_set():
                    data = proc.stdout.read(CHUNK_BYTES)
                    if not data:
                        break

                    if self.is_paused_fn():
                        self.state = "paused"
                        recording = False
                        captured.clear()
                        consecutive_speech = 0
                        preroll.clear()
                        continue

                    now = time.monotonic()
                    rms = chunk_rms(data)
                    self.current_rms = rms

                    if now < cooldown_until:
                        self.state = "waiting"
                        preroll.append(data)
                        continue

                    threshold = max(self.min_threshold, self.ambient_rms * self.speech_multiplier)

                    if not recording:
                        self.state = "waiting"
                        preroll.append(data)
                        if rms >= threshold:
                            consecutive_speech += 1
                            if consecutive_speech >= self.onset_chunks:
                                recording = True
                                self.state = "recording"
                                self.last_speech_at = time.time()
                                captured = list(preroll)
                                consecutive_silence = 0
                        else:
                            consecutive_speech = 0
                            # Smoothly track background noise floor (capped so loud music doesn't blind mic forever)
                            capped = min(rms, 1200.0)
                            self.ambient_rms = self.ambient_rms * 0.94 + capped * 0.06
                    else:
                        captured.append(data)
                        # Use slightly lower sustain threshold so soft endings of Korean words aren't clipped
                        sustain_threshold = max(self.min_threshold * 0.8, self.ambient_rms * (self.speech_multiplier * 0.75))
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
                            self.state = "processing"
                            try:
                                self.on_utterance(wav_bytes)
                            except Exception as exc:
                                log.error("Continuous utterance handler error: %s", exc)
                            cooldown_until = time.monotonic() + self.cooldown_sec
                            self.state = "waiting"
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    proc.kill()

            if not self._stop_event.is_set():
                log.warning("parecord stream ended; restarting in 2s...")
                self.state = "waiting"
                time.sleep(2.0)
