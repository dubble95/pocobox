"""Ultra-lightweight local streaming wake-word spotter powered by Vosk grammar mode (~6% CPU)."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("pocobox-voice.wakeword")

DEFAULT_MODEL_DIR = "/srv/pocobox/models/vosk-model-small-en-us-0.15"

# Phonetic matches verified against Korean TTS ("포코야", "포코박스", "헤이 포코") and English ("Hey Poco")
DEFAULT_WAKE_GRAMMAR: tuple[str, ...] = (
    "hey poco",
    "ok poco",
    "poco",
    "poco ya",
    "poco box",
    "hey coco",
    "ok coco",
    "coco ya",
    "coco box",
    "poker ya",
    "boko ya",
    "[unk]",
)

# Phrases that qualify as a wake-word trigger (exclude bare "[unk]" and filler words)
DEFAULT_TRIGGER_PHRASES: tuple[str, ...] = tuple(p for p in DEFAULT_WAKE_GRAMMAR if p != "[unk]")


class VoskWakeWordSpotter:
    """Streaming 16kHz PCM keyword spotter for hands-free wake-word detection."""

    def __init__(
        self,
        model_dir: str | None = None,
        sample_rate: int = 16000,
        grammar_phrases: tuple[str, ...] = DEFAULT_WAKE_GRAMMAR,
        reset_interval_sec: float = 6.0,
    ) -> None:
        self.model_dir = model_dir or os.environ.get("VOSK_WAKE_MODEL_DIR", DEFAULT_MODEL_DIR)
        self.sample_rate = sample_rate
        self.grammar_phrases = grammar_phrases
        self.trigger_phrases = tuple(p for p in grammar_phrases if p != "[unk]")
        self.reset_interval_sec = reset_interval_sec

        self.available = False
        self._model = None
        self._rec = None
        self._last_reset = time.monotonic()

        if os.environ.get("VOICE_WAKE_ENGINE", "vosk").strip().lower() in ("0", "off", "none", "rms"):
            log.info("Local Vosk wake-word engine disabled via VOICE_WAKE_ENGINE")
            return

        if not Path(self.model_dir).is_dir():
            log.info("Vosk wake-word model not found at %s; using RMS fallback.", self.model_dir)
            return

        try:
            from vosk import KaldiRecognizer, Model, SetLogLevel  # type: ignore

            SetLogLevel(-1)
            t0 = time.monotonic()
            self._model = Model(self.model_dir)
            self._rec = KaldiRecognizer(self._model, self.sample_rate, json.dumps(list(self.grammar_phrases)))
            self.available = True
            self._last_reset = time.monotonic()
            log.info("Loaded Vosk wake-word spotter (%s) in %.2fs", self.model_dir, time.monotonic() - t0)
        except Exception as exc:
            log.warning("Failed to initialize Vosk wake-word spotter: %s", exc)
            self.available = False

    def reset(self) -> None:
        if self._rec is not None:
            try:
                self._rec.Reset()
            except Exception:
                pass
        self._last_reset = time.monotonic()

    def _match_text(self, text: str) -> str | None:
        cleaned = (text or "").strip().lower()
        if not cleaned or cleaned == "[unk]":
            return None
        tokens = [t for t in cleaned.split() if t != "[unk]"]
        joined = " ".join(tokens)
        if not joined:
            return None
        for phrase in self.trigger_phrases:
            if phrase in joined:
                return phrase
        return None

    def accept_pcm16k(self, pcm_bytes: bytes) -> str | None:
        """Feed one 16kHz mono s16le chunk. Returns matched wake phrase or None."""
        if not self.available or self._rec is None or not pcm_bytes:
            return None

        now = time.monotonic()
        if now - self._last_reset >= self.reset_interval_sec:
            self.reset()

        try:
            if self._rec.AcceptWaveform(pcm_bytes):
                text = json.loads(self._rec.Result()).get("text", "")
            else:
                text = json.loads(self._rec.PartialResult()).get("partial", "")
        except Exception:
            return None

        hit = self._match_text(text)
        if hit:
            self.reset()
            return hit
        return None
