"""Bounded, local-first Korean ASR correction learning for PocoBox.

Only repeated, phonetically similar music corrections are promoted. The learner
never sends transcripts off-device and never participates in blind quiz STT.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .aliases import normalize_asr_text
from .jamo_util import jamo_phonetic_similarity

log = logging.getLogger("pocobox-voice.adaptive_learning")

DEFAULT_LEARNING_PATH = "/srv/pocobox/learning/korean_corrections.json"
_SCHEMA_VERSION = 1
_MAX_PHRASE_CHARS = 80


def _clean_phrase(text: str) -> str:
    return re.sub(r"\s+", " ", normalize_asr_text(text or "").strip(" .!?,~"))


class KoreanAdaptiveLearner:
    """Thread-safe persistent lexicon built from confirmed Korean voice corrections.

    A correction stays pending until the same source/target pair is confirmed
    twice. This avoids turning a one-off song preference change into an ASR rule.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        enabled: bool | None = None,
        max_entries: int | None = None,
        required_confirmations: int = 2,
        min_similarity: float = 0.68,
    ) -> None:
        if enabled is None:
            enabled = os.environ.get("VOICE_ADAPTIVE_LEARNING", "1").strip().lower() not in ("0", "false", "no", "off")
        self.enabled = bool(enabled)
        env_path = os.environ.get("VOICE_LEARNING_PATH", DEFAULT_LEARNING_PATH).strip()
        self.path = Path(path) if path is not None else Path(env_path)
        if max_entries is None:
            max_entries = int(os.environ.get("VOICE_LEARNING_MAX_ENTRIES", "200"))
        self.max_entries = max(1, min(500, max_entries))
        self.required_confirmations = max(2, required_confirmations)
        self.min_similarity = max(0.5, min(0.95, min_similarity))
        self._active: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._applications = 0
        self._last_write_ok = True
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.enabled or not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("learning store must be a JSON object")
            if payload.get("version") != _SCHEMA_VERSION:
                raise ValueError("unsupported learning schema")
            active = self._validated_entries(payload.get("active"), active=True)
            pending = self._validated_entries(payload.get("pending"), active=False)
            with self._lock:
                self._active = active
                self._pending = pending
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            log.warning("Ignoring invalid Korean learning store %s: %s", self.path, exc)

    def _validated_entries(self, value: Any, *, active: bool) -> dict[str, dict[str, Any]]:
        if not isinstance(value, dict):
            return {}
        validated: dict[str, dict[str, Any]] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not isinstance(item, dict):
                continue
            source = _clean_phrase(str(item.get("source") or ""))
            target = _clean_phrase(str(item.get("target") or ""))
            if not self._valid_pair(source, target) or key != source.lower():
                continue
            confirmations = item.get("confirmations", 0)
            if not isinstance(confirmations, int):
                continue
            if not 1 <= confirmations <= self.required_confirmations:
                continue
            if active and confirmations < self.required_confirmations:
                continue
            if not active and confirmations >= self.required_confirmations:
                continue
            validated[key] = {
                "source": source,
                "target": target,
                "confirmations": confirmations,
                "updated_at": float(item.get("updated_at") or 0.0),
            }
        return dict(sorted(validated.items(), key=lambda pair: pair[1]["updated_at"], reverse=True)[: self.max_entries])

    @staticmethod
    def _valid_pair(source: str, target: str) -> bool:
        return bool(
            source
            and target
            and source.lower() != target.lower()
            and 2 <= len(source) <= _MAX_PHRASE_CHARS
            and 2 <= len(target) <= _MAX_PHRASE_CHARS
            and not any(ord(ch) < 32 for ch in source + target)
        )

    def record_correction(self, source: str, target: str) -> str:
        """Record a correction and return disabled/rejected/pending/active."""
        if not self.enabled:
            return "disabled"
        clean_source = _clean_phrase(source)
        clean_target = _clean_phrase(target)
        if not self._valid_pair(clean_source, clean_target):
            return "rejected"
        if jamo_phonetic_similarity(clean_source, clean_target) < self.min_similarity:
            return "rejected"

        key = clean_source.lower()
        now = time.time()
        with self._lock:
            current = self._active.get(key)
            if current and current["target"].lower() == clean_target.lower():
                current["confirmations"] = min(self.required_confirmations, int(current["confirmations"]) + 1)
                current["updated_at"] = now
                self._save_locked()
                return "active"

            candidate = self._pending.get(key)
            confirmations = 1
            if candidate and candidate["target"].lower() == clean_target.lower():
                confirmations = int(candidate["confirmations"]) + 1

            entry = {
                "source": clean_source,
                "target": clean_target,
                "confirmations": confirmations,
                "updated_at": now,
            }
            if confirmations >= self.required_confirmations:
                self._pending.pop(key, None)
                self._active[key] = entry
                self._prune_locked(self._active, self.max_entries)
                status = "active"
            else:
                self._pending[key] = entry
                self._prune_locked(self._pending, self.max_entries)
                status = "pending"
            self._save_locked()
            return status

    @staticmethod
    def _prune_locked(entries: dict[str, dict[str, Any]], max_entries: int) -> None:
        while len(entries) > max_entries:
            oldest = min(entries, key=lambda key: float(entries[key].get("updated_at") or 0.0))
            del entries[oldest]

    def apply(self, text: str) -> str:
        """Apply at most one longest confirmed correction to a recognition transcript."""
        original = text or ""
        if not self.enabled or not original:
            return original
        with self._lock:
            if not self._active:
                return original
            cleaned = _clean_phrase(original)
            entries = sorted(self._active.values(), key=lambda item: len(item["source"]), reverse=True)
            for item in entries:
                pattern = re.compile(re.escape(item["source"]), re.IGNORECASE)
                if pattern.search(cleaned):
                    self._applications += 1
                    return pattern.sub(item["target"], cleaned)
        return original

    def forget(self, source: str) -> bool:
        """Remove a learned or pending source phrase; useful for local curation."""
        key = _clean_phrase(source).lower()
        with self._lock:
            removed = self._active.pop(key, None) is not None
            removed = self._pending.pop(key, None) is not None or removed
            if removed:
                self._save_locked()
            return removed

    def learned_entries(self) -> list[dict[str, Any]]:
        """Return copies of confirmed rules for regression review/export."""
        with self._lock:
            return [dict(item) for item in self._active.values()]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "active_rules": len(self._active),
                "pending_rules": len(self._pending),
                "applications": self._applications,
                "required_confirmations": self.required_confirmations,
                "last_write_ok": self._last_write_ok,
            }

    def _save_locked(self) -> None:
        payload = {
            "version": _SCHEMA_VERSION,
            "active": self._active,
            "pending": self._pending,
        }
        temp_path: str | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_path = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, self.path)
            self._last_write_ok = True
        except OSError as exc:
            self._last_write_ok = False
            log.warning("Cannot persist Korean learning store %s: %s", self.path, exc)
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except OSError:
                    pass
