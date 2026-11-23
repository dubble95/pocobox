"""Background verifier that re-analyzes saved command audio clips to detect
recognition errors after the fact and feeds corrections back into the ASR
normalization table for continuous improvement.

Scans the command_clips directory for WAV + JSON metadata pairs, re-interprets
each WAV through the HybridVoiceInterpreter, and compares the re-interpreted
action against the originally recorded action. When a mismatch is detected and
the re-interpretation is confident, the misheard transcript is recorded as a
correction in the Korean adaptive learner so future recognitions are improved.

Designed to run as a low-priority background thread on the PocoBox device so it
does not interfere with real-time voice handling.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from .adaptive_learning import KoreanAdaptiveLearner
from .cloud_stt_intent import HybridVoiceInterpreter
from .intent_schema import IntentAction
from .telemetry import VoiceTelemetryLogger

log = logging.getLogger("pocobox-voice.bg-verifier")

DEFAULT_SCAN_INTERVAL_SEC = 60.0
DEFAULT_MAX_CLIPS_PER_SCAN = 20
DEFAULT_MIN_AGE_SEC = 30.0  # Don't re-verify clips still being written
DEFAULT_LEARNING_PATH = "/srv/pocobox/learning/korean_corrections.json"
# Only record corrections when re-interpretation confidence is at least this high
MIN_REINTERP_CONFIDENCE = 0.90
# Only record corrections when transcripts are meaningfully different
MIN_TRANSCRIPT_DIFF_RATIO = 0.3


class BackgroundVerifier:
    """Periodically re-verifies saved command clips and feeds corrections back
    into the ASR normalization table for continuous recognition improvement."""

    def __init__(
        self,
        interpreter: HybridVoiceInterpreter,
        telemetry: VoiceTelemetryLogger | None = None,
        clips_dir: str | Path | None = None,
        learning_path: str | Path | None = None,
        scan_interval_sec: float = DEFAULT_SCAN_INTERVAL_SEC,
        max_clips_per_scan: int = DEFAULT_MAX_CLIPS_PER_SCAN,
        min_age_sec: float = DEFAULT_MIN_AGE_SEC,
        enabled: bool | None = None,
    ) -> None:
        self.interpreter = interpreter
        self.telemetry = telemetry
        if clips_dir is not None:
            self.clips_dir = Path(clips_dir)
        elif telemetry is not None:
            self.clips_dir = telemetry.command_clips_dir
        else:
            self.clips_dir = Path(
                os.environ.get("VOICE_COMMAND_CLIP_DIR", "/srv/pocobox/logs/command_clips")
            )
        self.learning_path = Path(
            learning_path or os.environ.get("VOICE_LEARNING_PATH", DEFAULT_LEARNING_PATH)
        )
        self.learner = KoreanAdaptiveLearner(path=self.learning_path)
        self.scan_interval_sec = scan_interval_sec
        self.max_clips_per_scan = max_clips_per_scan
        self.min_age_sec = min_age_sec
        if enabled is not None:
            self.enabled = bool(enabled)
        else:
            self.enabled = (
                os.environ.get("VOICE_BG_VERIFIER_ENABLED", "1").strip().lower()
                not in ("0", "false", "no", "off")
            )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._verified: set[str] = set()  # clip stems already verified
        self._lock = threading.Lock()
        self.stats: dict[str, int] = {
            "scans": 0,
            "clips_verified": 0,
            "mismatches": 0,
            "corrections_recorded": 0,
            "skipped_recent": 0,
            "errors": 0,
        }

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self.enabled:
            log.info("Background verifier disabled via VOICE_BG_VERIFIER_ENABLED")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="bg-verifier", daemon=True)
        self._thread.start()
        log.info(
            "Background verifier started (interval=%.0fs, max_clips=%d, min_age=%.0fs, dir=%s, learning=%s)",
            self.scan_interval_sec,
            self.max_clips_per_scan,
            self.min_age_sec,
            self.clips_dir,
            self.learning_path,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception as exc:
                log.warning("Background verifier scan error: %s", exc)
                with self._lock:
                    self.stats["errors"] += 1
            self._stop.wait(self.scan_interval_sec)

    def _should_record_correction(
        self,
        recorded_transcript: str,
        re_transcript: str,
        re_confidence: float,
        re_ok: bool,
        recorded_ok: bool,
    ) -> bool:
        """Decide whether a mismatch warrants recording an ASR correction."""
        if not re_ok or re_confidence < MIN_REINTERP_CONFIDENCE:
            return False
        if not recorded_transcript or not re_transcript:
            return False
        if recorded_transcript.strip().lower() == re_transcript.strip().lower():
            return False
        # Avoid recording trivial differences (punctuation, spacing)
        r_clean = re.sub(r"[^\w\s]", "", recorded_transcript).strip().lower()
        re_clean = re.sub(r"[^\w\s]", "", re_transcript).strip().lower()
        if r_clean == re_clean:
            return False
        # Require meaningful difference (not just a trailing particle)
        max_len = max(len(r_clean), len(re_clean))
        if max_len == 0:
            return False
        diff_ratio = abs(len(r_clean) - len(re_clean)) / max_len
        if diff_ratio < MIN_TRANSCRIPT_DIFF_RATIO:
            return False
        return True

    def scan_once(self) -> dict[str, int]:
        """Run one verification scan over recent command clips."""
        if not self.clips_dir.exists():
            return {"scanned": 0, "verified": 0, "mismatches": 0, "corrections_recorded": 0}

        now = time.time()
        cutoff = now - self.min_age_sec

        # Gather JSON metadata files, newest first
        try:
            json_files = sorted(
                self.clips_dir.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return {"scanned": 0, "verified": 0, "mismatches": 0, "corrections_recorded": 0}

        scanned = 0
        verified = 0
        mismatches = 0
        corrections_recorded = 0
        skipped_recent = 0

        for jf in json_files:
            if scanned >= self.max_clips_per_scan:
                break
            stem = jf.stem
            with self._lock:
                if stem in self._verified:
                    continue

            # Skip clips still being written
            try:
                mtime = jf.stat().st_mtime
            except OSError:
                continue
            if mtime > cutoff:
                skipped_recent += 1
                continue

            wav_path = jf.with_suffix(".wav")
            if not wav_path.exists():
                continue

            # Read recorded metadata
            try:
                meta = json.loads(jf.read_text(encoding="utf-8"))
            except Exception:
                continue

            recorded_action = str(meta.get("action", "unknown"))
            recorded_ok = bool(meta.get("ok", False))
            recorded_transcript = str(meta.get("transcript", ""))

            # Re-interpret the audio
            try:
                wav_bytes = wav_path.read_bytes()
            except OSError:
                continue

            try:
                intent = self.interpreter.interpret_audio(wav_bytes, enforce_wake_word=False)
            except Exception as exc:
                log.debug("Re-interpretation failed for %s: %s", stem, exc)
                with self._lock:
                    self.stats["errors"] += 1
                with self._lock:
                    self._verified.add(stem)
                continue

            re_action = intent.action.value
            re_ok = intent.action != IntentAction.UNKNOWN
            re_confidence = intent.confidence
            re_provider = intent.provider
            re_transcript = intent.raw_text or ""

            is_mismatch = (
                recorded_action != re_action
                or (recorded_ok and not re_ok)
                or (not recorded_ok and re_ok)
            )

            with self._lock:
                self._verified.add(stem)
                self.stats["clips_verified"] += 1
                verified += 1

            if is_mismatch:
                mismatches += 1
                with self._lock:
                    self.stats["mismatches"] += 1

                # Record correction for feedback loop when re-interpretation is confident
                if self._should_record_correction(
                    recorded_transcript, re_transcript, re_confidence, re_ok, recorded_ok
                ):
                    status = self.learner.record_correction(recorded_transcript, re_transcript)
                    if status in ("active", "pending"):
                        corrections_recorded += 1
                        with self._lock:
                            self.stats["corrections_recorded"] += 1
                        log.info(
                            "Feedback correction recorded: %r -> %r (status=%s)",
                            recorded_transcript,
                            re_transcript,
                            status,
                        )

                if self.telemetry is not None:
                    self.telemetry.record(
                        "command_verification_mismatch",
                        clip_stem=stem,
                        recorded_action=recorded_action,
                        recorded_ok=recorded_ok,
                        re_action=re_action,
                        re_ok=re_ok,
                        re_confidence=round(re_confidence, 4),
                        re_provider=re_provider,
                        recorded_transcript=recorded_transcript,
                        re_transcript=re_transcript,
                        music_playing=bool(meta.get("music_playing", False)),
                        verified_at=round(now, 3),
                    )
                log.warning(
                    "Verification mismatch for %s: recorded=%s(ok=%s) re=%s(ok=%s, conf=%.2f)",
                    stem,
                    recorded_action,
                    recorded_ok,
                    re_action,
                    re_ok,
                    re_confidence,
                )

            scanned += 1

        with self._lock:
            self.stats["scans"] += 1
            self.stats["skipped_recent"] += skipped_recent

        log.debug(
            "Verification scan: scanned=%d verified=%d mismatches=%d corrections=%d skipped_recent=%d",
            scanned,
            verified,
            mismatches,
            corrections_recorded,
            skipped_recent,
        )
        return {
            "scanned": scanned,
            "verified": verified,
            "mismatches": mismatches,
            "corrections_recorded": corrections_recorded,
        }

    def stats_dict(self) -> dict[str, Any]:
        with self._lock:
            stats = dict(self.stats)
        stats["learner"] = self.learner.stats()
        return stats


def _cli() -> int:
    import argparse

    from .cloud_stt_intent import HybridVoiceInterpreter
    from .telemetry import VoiceTelemetryLogger

    parser = argparse.ArgumentParser(description="Run PocoBox background verifier once")
    parser.add_argument("--clips-dir", default=None)
    parser.add_argument("--learning-path", default=None)
    parser.add_argument("--max-clips", type=int, default=DEFAULT_MAX_CLIPS_PER_SCAN)
    args = parser.parse_args()

    interp = HybridVoiceInterpreter()
    tel = VoiceTelemetryLogger()
    verifier = BackgroundVerifier(
        interpreter=interp,
        telemetry=tel,
        clips_dir=args.clips_dir,
        learning_path=args.learning_path,
        max_clips_per_scan=args.max_clips,
    )
    result = verifier.scan_once()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
