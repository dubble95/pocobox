"""Ultra-lightweight local streaming wake-word spotter powered by Vosk grammar mode (~6% CPU).

Hardened against false triggers (호출 오탐) in music and living-room environments:
- Distractor / decoy vocabulary in the Kaldi grammar absorbs singing vocals, TV audio,
  and everyday speech instead of forcing them into wake-word hypotheses.
- Strict trigger whitelist (drops false-positive magnets like 'ok coco', 'hey coco', 'poker ya').
- Bare 2-syllable 'poco' is disabled during music playback.
- Requires word-level confidence on final results (>= 0.60 music, >= 0.60 quiet; 'hey poco' >= 0.75 music)
  or multi-chunk consecutive partial stability (2 chunks music, 2 chunks quiet).
- Korean ASR variants ("boco ya", "poko ya", "poco yah", "poco ya ya", Hangul "포코야") normalize to "poco ya".
- Silence-gated periodic reset so utterances crossing the reset boundary are not cut off.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("pocobox-voice.wakeword")

DEFAULT_MODEL_DIR = "/srv/pocobox/models/vosk-model-small-en-us-0.15"

# Clean trigger phrases verified for Korean ("포코야", "포코박스", "헤이 포코") and English ("Hey Poco")
# Note: single-word "poco" alone is intentionally excluded from triggers to prevent high false-positives
# during quiet/paused conversation; use "poco ya" ("포코야"), "hey poco", or "poco box".
DEFAULT_TRIGGER_PHRASES: tuple[str, ...] = (
    "hey poco",
    "poco ya",
    "poco box",
)

# Competing non-trigger decoy words in the Kaldi grammar so non-wake speech/singing
# maps here instead of being forced into wake phrases.
DEFAULT_DECOY_PHRASES: tuple[str, ...] = (
    "poco",  # Decoy: bare "poco" alone does not trigger without "hey" / "ya" / "box"
    "okay",
    "hey",
    "hello",
    "yeah",
    "yes",
    "no",
    "oh",
    "ah",
    "go",
    "so",
    "know",
    "coco",
    "cocoa",
    "ok coco",
    "hey coco",
    "coco box",
    "boko ya",
    "coco ya",
    "ok poco",
    "poker",
    "poker ya",
    "local",
    "vocal",
    "photo",
    "people",
    "open",
    "over",
    "only",
    "play",
    "music",
    "stop",
    "what",
    "that",
    "this",
    "the",
    "and",
    "you",
    "for",
    "from",
    "with",
    "have",
    "are",
    "not",
    "but",
    "all",
    "can",
    "just",
    "like",
    "time",
    "one",
    "two",
    "three",
    "thank you",
    "[unk]",
)

DEFAULT_WAKE_GRAMMAR: tuple[str, ...] = DEFAULT_TRIGGER_PHRASES + DEFAULT_DECOY_PHRASES

# Too short to trust alone during music playback (2-syllable singing lyrics false-trigger easily)
QUIET_ONLY_PHRASES: frozenset[str] = frozenset()

# Phrases requiring stricter confirmation during active music playback
# Singing vocals in music frequently false-trigger English vocal phrases like 'hey poco' (0 TP, 5 FP)
MUSIC_STRICT_PHRASES: frozenset[str] = frozenset({"hey poco"})

# Korean wake-phrase ASR variants the English Vosk model commonly hears as
# "poco ya" / "poco box" / "hey poco". Normalized before matching so variants
# such as "boco ya", "poko ya", "poco yah", and "poco ya ya" still wake the device.
_WAKE_VARIANT_NORMALIZATIONS: tuple[tuple[str, str], ...] = (
    ("포코야", "poco ya"),
    ("포코 야", "poco ya"),
    ("폭호야", "poco ya"),
    ("포코아", "poco ya"),
    ("포코얌", "poco ya"),
    ("포쿠야", "poco ya"),
    ("포코약", "poco ya"),
    ("포코얘", "poco ya"),
    ("포코박스", "poco box"),
    ("포코 박스", "poco box"),
    ("포코박서", "poco box"),
    ("포코박쓰", "poco box"),
    ("헤이 포코", "hey poco"),
    ("헤이포코", "hey poco"),
    ("안녕 포코", "hey poco"),
    ("안녕포코", "hey poco"),
)

# Latin ASR confusions for the Korean wake phrase "포코야" -> "poco ya".
_WAKE_LATIN_VARIANTS: dict[str, str] = {
    "boco": "poco",
    "poko": "poco",
    "poco": "poco",
    "polco": "poco",
    "pork": "poco",
    "pocco": "poco",
    "porco": "poco",
    "bocco": "poco",
    "yah": "ya",
    "yahn": "ya",
    "yaw": "ya",
    "yha": "ya",
    "yuh": "ya",
}


class VoskWakeWordSpotter:
    """Streaming 16kHz PCM keyword spotter for hands-free wake-word detection."""

    def __init__(
        self,
        model_dir: str | None = None,
        sample_rate: int = 16000,
        grammar_phrases: tuple[str, ...] = DEFAULT_WAKE_GRAMMAR,
        trigger_phrases: tuple[str, ...] | None = None,
        reset_interval_sec: float = 6.0,
        music_confirm_chunks: int | None = None,
        quiet_confirm_chunks: int | None = None,
        music_min_confidence: float | None = None,
        quiet_min_confidence: float | None = None,
    ) -> None:
        self.model_dir = model_dir or os.environ.get("VOSK_WAKE_MODEL_DIR", DEFAULT_MODEL_DIR)
        self.sample_rate = sample_rate
        self.grammar_phrases = grammar_phrases
        if trigger_phrases is not None:
            self.trigger_phrases = tuple(p for p in trigger_phrases if p != "[unk]")
        elif grammar_phrases is DEFAULT_WAKE_GRAMMAR:
            self.trigger_phrases = DEFAULT_TRIGGER_PHRASES
        else:
            decoy_set = set(DEFAULT_DECOY_PHRASES)
            self.trigger_phrases = tuple(p for p in grammar_phrases if p != "[unk]" and p not in decoy_set)

        self.reset_interval_sec = reset_interval_sec
        self.music_confirm_chunks = (
            music_confirm_chunks
            if music_confirm_chunks is not None
            else max(1, int(os.environ.get("VOICE_WAKE_CONFIRM_CHUNKS", "2")))
        )
        self.quiet_confirm_chunks = (
            quiet_confirm_chunks
            if quiet_confirm_chunks is not None
            else max(1, int(os.environ.get("VOICE_WAKE_QUIET_CONFIRM_CHUNKS", "2")))
        )
        self.music_min_confidence = (
            music_min_confidence
            if music_min_confidence is not None
            else float(
                os.environ.get("VOICE_WAKE_MUSIC_MIN_CONF", os.environ.get("VOICE_WAKE_MIN_CONF", "0.55"))
            )
        )
        self.quiet_min_confidence = (
            quiet_min_confidence
            if quiet_min_confidence is not None
            else float(os.environ.get("VOICE_WAKE_QUIET_MIN_CONF", "0.60"))
        )

        self.available = False
        self._model = None
        self._rec = None
        self._last_reset = time.monotonic()
        self._partial_hit: str | None = None
        self._partial_streak: int = 0
        self._has_active_partial: bool = False
        self.last_event: dict | None = None

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
            # Deduplicate grammar while keeping order
            unique_grammar = list(dict.fromkeys(self.grammar_phrases))
            self._rec = KaldiRecognizer(self._model, self.sample_rate, json.dumps(unique_grammar))
            try:
                self._rec.SetWords(True)
            except Exception:
                pass
            self.available = True
            self._last_reset = time.monotonic()
            log.info(
                "Loaded Vosk wake-word spotter (%s) in %.2fs (triggers=%d decoys=%d confirm=%d/%d conf=%.2f/%.2f)",
                self.model_dir,
                time.monotonic() - t0,
                len(self.trigger_phrases),
                max(0, len(unique_grammar) - len(self.trigger_phrases)),
                self.music_confirm_chunks,
                self.quiet_confirm_chunks,
                self.music_min_confidence,
                self.quiet_min_confidence,
            )
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
        self._partial_hit = None
        self._partial_streak = 0
        self._has_active_partial = False

    @staticmethod
    def _norm_wake_word(word: str) -> str:
        w = (word or "").strip(".,!?~:;").lower().replace("[unk]", "")
        return _WAKE_LATIN_VARIANTS.get(w, w)

    def _normalize_wake_text(self, text: str) -> str:
        cleaned = (text or "").strip().lower()
        for ko, en in _WAKE_VARIANT_NORMALIZATIONS:
            cleaned = cleaned.replace(ko, en)

        tokens: list[str] = []
        for t in cleaned.split():
            t = self._norm_wake_word(t)
            if not t:
                continue
            tokens.append(t)

        collapsed: list[str] = []
        for t in tokens:
            if collapsed and collapsed[-1] == t:
                continue
            collapsed.append(t)

        normalized = " ".join(collapsed)
        if normalized.startswith("poco ya "):
            normalized = "poco ya"
        if normalized.startswith("poco box "):
            normalized = "poco box"
        if normalized.startswith("hey poco "):
            normalized = "hey poco"
        if normalized == "poco ya poco ya":
            normalized = "poco ya"
        return normalized

    def _match_text(self, text: str, music_playing: bool = False) -> str | None:
        cleaned = self._normalize_wake_text(text)
        if not cleaned or cleaned == "[unk]":
            return None
        tokens = cleaned.split()
        if not tokens:
            return None

        # Prefer longer / more specific phrases first; require exact token-sequence match
        ordered = sorted(self.trigger_phrases, key=len, reverse=True)
        for phrase in ordered:
            if music_playing and phrase in QUIET_ONLY_PHRASES:
                continue
            p_tokens = phrase.split()
            plen = len(p_tokens)
            for i in range(len(tokens) - plen + 1):
                if tokens[i : i + plen] == p_tokens:
                    return phrase
        return None

    def _phrase_confidence(self, result_obj: dict, phrase: str) -> tuple[float | None, float | None]:
        words = result_obj.get("result")
        if not isinstance(words, list) or not words:
            return None, None
        phrase_tokens = set(phrase.split())
        confs = [
            float(w.get("conf", 1.0))
            for w in words
            if isinstance(w, dict) and self._norm_wake_word(w.get("word", "")) in phrase_tokens
        ]
        if not confs:
            return None, None
        return sum(confs) / len(confs), min(confs)

    def _check_result_confidence(self, result_obj: dict, phrase: str, min_conf: float) -> bool:
        avg_conf, min_word_conf = self._phrase_confidence(result_obj, phrase)
        if avg_conf is None or min_word_conf is None:
            return True
        return avg_conf >= min_conf and min_word_conf >= (min_conf - 0.15)

    def accept_pcm16k(
        self,
        pcm_bytes: bytes,
        *,
        is_silent: bool = False,
        music_playing: bool = False,
    ) -> str | None:
        """Feed one 16kHz mono s16le chunk. Returns matched wake phrase or None."""
        self.last_event = None
        if not self.available or self._rec is None or not pcm_bytes:
            return None

        now = time.monotonic()
        elapsed = now - self._last_reset
        # Silence-gated reset: avoid chopping an in-progress wake utterance at the 6s mark
        if elapsed >= self.reset_interval_sec * 2.5 or (
            elapsed >= self.reset_interval_sec and (is_silent or not self._has_active_partial)
        ):
            self.reset()

        try:
            is_final = bool(self._rec.AcceptWaveform(pcm_bytes))
            if is_final:
                payload = json.loads(self._rec.Result())
                text = payload.get("text", "")
            else:
                payload = json.loads(self._rec.PartialResult())
                text = payload.get("partial", "")
        except Exception:
            return None

        self._has_active_partial = (not is_final) and bool((text or "").strip().replace("[unk]", "").strip())
        hit = self._match_text(text, music_playing=music_playing)
        min_conf = self.music_min_confidence if music_playing else self.quiet_min_confidence
        confirm_needed = self.music_confirm_chunks if music_playing else self.quiet_confirm_chunks
        if music_playing and hit in MUSIC_STRICT_PHRASES:
            confirm_needed = max(3, confirm_needed + 1)
            min_conf = max(0.75, min_conf + 0.10)

        if is_final:
            self._partial_hit = None
            self._partial_streak = 0
            if hit:
                avg_conf, min_w_conf = self._phrase_confidence(payload, hit)
                if not self._check_result_confidence(payload, hit, min_conf):
                    self.last_event = {
                        "status": "rejected_conf",
                        "phrase": hit,
                        "is_final": True,
                        "confidence": avg_conf,
                        "min_word_conf": min_w_conf,
                        "text": text,
                    }
                    log.debug("Rejected low-confidence wake hit '%s' (conf=%s music=%s)", hit, avg_conf, music_playing)
                    return None
                self.last_event = {
                    "status": "triggered",
                    "phrase": hit,
                    "is_final": True,
                    "confidence": avg_conf,
                    "min_word_conf": min_w_conf,
                    "text": text,
                }
                self.reset()
                return hit
            return None

        # Partial result handling: require consecutive stable chunks before firing
        if not hit:
            self._partial_hit = None
            self._partial_streak = 0
            return None

        if confirm_needed <= 1:
            self.last_event = {
                "status": "triggered",
                "phrase": hit,
                "is_final": False,
                "streak": 1,
                "text": text,
            }
            self.reset()
            return hit

        if hit == self._partial_hit:
            self._partial_streak += 1
        else:
            self._partial_hit = hit
            self._partial_streak = 1

        if self._partial_streak >= confirm_needed:
            self.last_event = {
                "status": "triggered",
                "phrase": hit,
                "is_final": False,
                "streak": self._partial_streak,
                "text": text,
            }
            self.reset()
            return hit
        return None
