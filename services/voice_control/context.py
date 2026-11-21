"""Dialogue State Tracking (DST) and context-aware slot inheritance for PocoBox voice pipeline."""

from __future__ import annotations

import re
import time
from typing import Any

from .aliases import (
    expand_tidal_queries,
    is_classical_movement_work,
    is_classical_work,
    strip_wake_prefix,
)
from .intent_schema import IntentAction, VoiceIntent

_SINO_MVT_MAP: dict[str, int] = {
    "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9, "십": 10,
}


def _parse_movement_number(text: str) -> int | None:
    if not text:
        return None
    m_ko = re.search(r"(?:제\s*)?([일이삼사오육칠팔구십]|\d{1,2})\s*악장", text)
    if m_ko:
        val = m_ko.group(1)
        if val.isdigit():
            return int(val)
        if val in _SINO_MVT_MAP:
            return _SINO_MVT_MAP[val]
    m_en = re.search(r"(?:movement|mvt\.?)\s*(\d{1,2})", text, re.IGNORECASE)
    if m_en:
        return int(m_en.group(1))
    return None

# Correction cues
_CORRECTION_PREFIX_RE = re.compile(
    r"^(?:아니(?:고|\s*그게\s*아니라|\s*그거\s*말고)?|그거\s*말고|말고)\s*",
    re.IGNORECASE,
)

# Relative music continuation cues (e.g. "다른 거", "딴 노래", "다른 노래")
_ANOTHER_SONG_RE = re.compile(
    r"^(?:다른\s*(?:곡|노래|음악|거|트랙)|딴\s*(?:곡|노래|음악|거)|다른\s*걸로|딴\s*걸로)(?:\s+(?:틀어|재생|들려|플레이|켜).*)?$",
    re.IGNORECASE,
)

# Relative volume delta continuation cues (e.g. "조금 더", "더", "한번 더", "살짝 더")
_RELATIVE_VOLUME_RE = re.compile(
    r"^(?:더|조금\s*더|살짝\s*더|많이\s*더|한\s*번\s*더|한번\s*더)(?:\s*(?:작게|크게|줄여|올려|내려|키워|낮춰))?$",
    re.IGNORECASE,
)

# Weather follow-up cues (e.g. "내일은?", "비 와?", "우산 필요해?", "주말은?")
_WEATHER_FOLLOWUP_RE = re.compile(
    r"^(?:내일은|모레는|주말은|오늘\s*밤은|오후는|비\s*와\??|눈\s*와\??|우산\s*필요해\??|기온은\??|많이\s*추워\??|많이\s*더워\??|미세먼지는\??)$",
    re.IGNORECASE,
)


class ConversationContext:
    """Tracks multi-turn voice interaction dialogue turns with slot inheritance."""

    def __init__(self, expiry_sec: float = 45.0) -> None:
        self.expiry_sec = expiry_sec
        self.last_action: str | None = None
        self.last_query: str | None = None
        self.last_display_query: str | None = None
        self.last_track_title: str | None = None
        self.last_artist: str | None = None
        self.last_search_type: str | None = None
        self.last_location: str | None = None
        self.last_volume_action: str | None = None
        self.last_volume_delta: int | None = None
        self.last_classical_work: str | None = None
        self.last_timestamp: float = 0.0
        self.turns: list[dict[str, str]] = []

    def record_turn(
        self,
        role: str,
        text: str,
        *,
        action: str | None = None,
        query: str | None = None,
        display_query: str | None = None,
        track_title: str | None = None,
        artist: str | None = None,
        search_type: str | None = None,
        location: str | None = None,
        volume_delta: int | None = None,
    ) -> None:
        now = time.time()
        if self.last_timestamp and (now - self.last_timestamp > self.expiry_sec):
            self.clear_slots()
        self.last_timestamp = now

        if action:
            self.last_action = action
            if action in (IntentAction.VOLUME_UP.value, IntentAction.VOLUME_DOWN.value):
                self.last_volume_action = action
                if volume_delta is not None:
                    self.last_volume_delta = volume_delta
            elif action == IntentAction.WEATHER.value and location:
                self.last_location = location

        if query:
            self.last_query = query
        if display_query:
            self.last_display_query = display_query
        if track_title:
            self.last_track_title = track_title
            if is_classical_work(track_title) or is_classical_movement_work(track_title):
                self.last_classical_work = track_title
        if artist:
            self.last_artist = artist
        if search_type:
            self.last_search_type = search_type

        self.turns.append({"role": role, "text": text})
        if len(self.turns) > 6:
            self.turns = self.turns[-6:]

    def clear_slots(self) -> None:
        """Clear all conversation context slots and turns."""
        self.turns.clear()
        self.last_action = None
        self.last_query = None
        self.last_display_query = None
        self.last_track_title = None
        self.last_artist = None
        self.last_search_type = None
        self.last_location = None
        self.last_volume_action = None
        self.last_volume_delta = None
        self.last_classical_work = None
        self.last_timestamp = 0.0

    def is_active(self) -> bool:
        return bool(self.turns) and (time.time() - self.last_timestamp <= self.expiry_sec)

    def is_recent_music_playback(self) -> bool:
        if not self.is_active():
            return False
        return self.last_action in ("play_search", "play_mix", "play_favorite", "resume")

    def resolve_contextual_followup(self, raw_text: str) -> VoiceIntent | None:
        """Resolve a follow-up or correction utterance using conversation slot inheritance.

        Returns VoiceIntent if successfully resolved from context, or None.
        Runs offline in < 0.5 milliseconds.
        """
        if not self.is_active():
            return None

        clean_text, wake_detected = strip_wake_prefix(raw_text)
        clean_text = clean_text.strip(" .!?,~")
        if not clean_text:
            return None

        low = clean_text.lower()

        # -------------------------------------------------------------
        # 1. Volume Continuation ("조금 더", "더 작게", "더 크게", "한번 더")
        # -------------------------------------------------------------
        if self.last_volume_action and _RELATIVE_VOLUME_RE.match(clean_text):
            is_up = "크게" in low or "올려" in low or "키워" in low
            is_down = "작게" in low or "줄여" in low or "내려" in low or "낮춰" in low

            action = IntentAction.VOLUME_UP if is_up else (IntentAction.VOLUME_DOWN if is_down else None)
            if action is None:
                action = IntentAction(self.last_volume_action)

            base_delta = abs(self.last_volume_delta or 10)
            if "조금" in low or "살짝" in low:
                delta_val = 5
            elif "많이" in low:
                delta_val = 20
            else:
                delta_val = base_delta

            final_delta = delta_val if action == IntentAction.VOLUME_UP else -delta_val
            return VoiceIntent(
                action=action,
                raw_text=raw_text,
                delta=final_delta,
                confidence=0.95,
                provider="context-volume-inheritance",
                wake_word_detected=wake_detected,
            )

        # -------------------------------------------------------------
        # 2. Weather Follow-up ("내일은?", "비 와?", "우산 필요해?")
        # -------------------------------------------------------------
        if self.last_action == IntentAction.WEATHER.value and _WEATHER_FOLLOWUP_RE.match(clean_text):
            inherited_loc = self.last_location or ""
            combined_query = f"{inherited_loc} {clean_text}".strip()
            return VoiceIntent(
                action=IntentAction.WEATHER,
                raw_text=raw_text,
                query=inherited_loc,
                confidence=0.95,
                provider="context-weather-inheritance",
                wake_word_detected=wake_detected,
            )

        # -------------------------------------------------------------
        # 3. Classical Movement Continuation (e.g. "1악장만", "2악장 틀어줘")
        # -------------------------------------------------------------
        mvt_num = _parse_movement_number(clean_text)
        if mvt_num is not None and (self.last_classical_work or (self.last_track_title and is_classical_work(self.last_track_title))):
            work_name = self.last_classical_work or self.last_track_title or ""
            expanded = expand_tidal_queries(f"{work_name} Movement {mvt_num}")
            primary = expanded[0] if expanded else f"{work_name} Movement {mvt_num}"
            return VoiceIntent(
                action=IntentAction.PLAY_SEARCH,
                raw_text=raw_text,
                query=primary,
                fallback_queries=expanded[1:] if len(expanded) > 1 else [],
                display_query=f"{work_name} {mvt_num}악장",
                search_type="classical",
                confidence=0.96,
                provider="context-classical-movement-inheritance",
                wake_word_detected=wake_detected,
            )

        # -------------------------------------------------------------
        # 4. Music Continuation / Correction ("다른 거", "그거 말고 밤편지")
        # -------------------------------------------------------------
        if self.is_recent_music_playback():
            # 4a. "다른 노래", "딴 거 틀어줘" -> inherit artist
            if _ANOTHER_SONG_RE.match(clean_text) and self.last_artist:
                expanded = expand_tidal_queries(self.last_artist)
                primary = expanded[0] if expanded else self.last_artist
                return VoiceIntent(
                    action=IntentAction.PLAY_SEARCH,
                    raw_text=raw_text,
                    query=primary,
                    fallback_queries=expanded[1:] if len(expanded) > 1 else [],
                    display_query=f"{self.last_artist} 다른 노래",
                    search_type="artist",
                    confidence=0.94,
                    provider="context-artist-another-song",
                    wake_word_detected=wake_detected,
                )

            # 4b. Correction: "그거 말고 밤편지", "아니 밤편지"
            if _CORRECTION_PREFIX_RE.search(clean_text):
                corrected_target = _CORRECTION_PREFIX_RE.sub("", clean_text).strip(" .!?,~")
                if corrected_target:
                    # If user specified a track and we have the artist, combine them
                    artist_prefix = self.last_artist if (self.last_artist and self.last_artist not in corrected_target) else ""
                    combined = f"{artist_prefix} {corrected_target}".strip() if artist_prefix else corrected_target
                    expanded = expand_tidal_queries(combined)
                    primary = expanded[0] if expanded else combined
                    return VoiceIntent(
                        action=IntentAction.PLAY_SEARCH,
                        raw_text=raw_text,
                        query=primary,
                        fallback_queries=expanded[1:] if len(expanded) > 1 else [],
                        display_query=combined,
                        search_type="track",
                        confidence=0.95,
                        provider="context-correction-slot-inheritance",
                        wake_word_detected=wake_detected,
                    )

        return None
