"""Typed schema for PocoBox voice intents and execution results."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class IntentAction(str, Enum):
    PLAY_SEARCH = "play_search"
    PLAY_MIX = "play_mix"
    PLAY_FAVORITE = "play_favorite"
    PAUSE = "pause"
    RESUME = "resume"
    NEXT = "next"
    PREVIOUS = "previous"
    VOLUME_SET = "volume_set"
    VOLUME_UP = "volume_up"
    VOLUME_DOWN = "volume_down"
    MUTE = "mute"
    UNMUTE = "unmute"
    NOW_PLAYING = "now_playing"
    ANSWER_QA = "answer_qa"
    START_QUIZ = "start_quiz"
    STOP_QUIZ = "stop_quiz"
    UNKNOWN = "unknown"


VALID_SEARCH_TYPES = {"track", "artist", "album", "playlist", "any"}


@dataclass
class VoiceIntent:
    action: IntentAction
    raw_text: str = ""
    query: str = ""
    fallback_queries: list[str] = field(default_factory=list)
    search_type: str = "any"
    volume: int | None = None
    delta: int | None = None
    confidence: float = 1.0
    provider: str = "rule"
    wake_word_detected: bool = False
    language: str = "ko"
    answer: str = ""
    quiz_topic: str = ""
    quiz_count: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.action, str):
            try:
                self.action = IntentAction(self.action)
            except ValueError:
                self.action = IntentAction.UNKNOWN
        if self.search_type not in VALID_SEARCH_TYPES:
            self.search_type = "any"
        if self.volume is not None:
            self.volume = max(0, min(100, int(self.volume)))
        # Deduplicate fallback_queries preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for q in self.fallback_queries:
            q_clean = q.strip()
            if q_clean and q_clean.lower() not in seen:
                seen.add(q_clean.lower())
                deduped.append(q_clean)
        self.fallback_queries = deduped

    def all_queries(self) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()
        for q in [self.query, *self.fallback_queries]:
            q_clean = q.strip()
            if q_clean and q_clean.lower() not in seen:
                seen.add(q_clean.lower())
                candidates.append(q_clean)
        return candidates

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["action"] = self.action.value
        return d


@dataclass
class ExecutionResult:
    ok: bool
    action: str
    message: str
    intent: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
