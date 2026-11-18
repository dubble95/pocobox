"""Fast offline Korean + English rule-based voice command parser for PocoBox."""

from __future__ import annotations

import re

from .aliases import (
    ARTIST_ALIASES,
    GENRE_MOOD_ALIASES,
    TRACK_ALIASES,
    expand_tidal_queries,
    normalize_asr_text,
    parse_korean_number,
    strip_wake_prefix,
)
from .intent_schema import IntentAction, VoiceIntent

# Trailing command verbs/phrases to strip from play queries (Korean & English)
_KO_PLAY_SUFFIX_RE = re.compile(
    r"(?:"
    r"\s+(?:좀|한번|한 번|바로|지금|빨리|계속)?"
    r"(?:\s*(?:노래|음악|곡|트랙|앨범|플레이리스트|믹스|메들리))?"
    r"(?:\s*(?:좀|한번|한 번))?"
    r"\s*(?:틀어\s*줘|틀어\s*주세요|틀어\s*줄래|틀어|재생해\s*줘|재생해\s*주세요|재생해|재생|들려\s*줘|들려\s*주세요|들려|켜\s*줘|켜줘|플레이\s*해\s*줘|플레이해줘|검색해서\s*틀어\s*줘|검색해\s*줘|찾아\s*줘|찾아서\s*틀어\s*줘)"
    r")$",
    re.IGNORECASE,
)

_EN_PLAY_PREFIX_RE = re.compile(
    r"^(?:please\s+)?(?:play|put\s+on|search\s+(?:and\s+play\s+)?|find\s+(?:and\s+play\s+)?|listen\s+to)\s+",
    re.IGNORECASE,
)

_EN_PLAY_SUFFIX_RE = re.compile(
    r"\s+(?:on\s+tidal|from\s+tidal|please|for\s+me|now)$",
    re.IGNORECASE,
)


def _detect_language(text: str) -> str:
    has_ko = bool(re.search(r"[가-힣]", text))
    has_en = bool(re.search(r"[a-zA-Z]", text))
    if has_ko and has_en:
        return "ko-en"
    if has_ko:
        return "ko"
    return "en"


def parse_command(raw_text: str) -> VoiceIntent:
    """Parse Korean/English natural-language music command into a typed VoiceIntent."""
    if not raw_text or not raw_text.strip():
        return VoiceIntent(action=IntentAction.UNKNOWN, raw_text=raw_text or "", confidence=0.0)

    normalized = normalize_asr_text(raw_text)
    text, wake_detected = strip_wake_prefix(normalized)
    text = text.strip(" .!?,~")
    lang = _detect_language(text or normalized)

    if not text:
        return VoiceIntent(
            action=IntentAction.UNKNOWN,
            raw_text=raw_text,
            confidence=0.0,
            wake_word_detected=wake_detected,
            language=lang,
        )

    low = text.lower()

    # 1. NOW PLAYING queries
    if re.search(
        r"(지금\s*(나오는|재생\s*중인|듣고\s*있는)\s*(노래|음악|곡)|이\s*(노래|곡)\s*(뭐야|제목|누구)|무슨\s*(노래|곡)|현재\s*곡|what'?s\s+playing|what\s+song\s+is\s+this|current\s+(song|track)|now\s+playing)",
        low,
    ):
        return VoiceIntent(
            action=IntentAction.NOW_PLAYING,
            raw_text=raw_text,
            confidence=0.98,
            wake_word_detected=wake_detected,
            language=lang,
        )

    # 2. MUTE / UNMUTE
    if re.search(r"(음소거\s*(해제|풀어|꺼)|소리\s*(다시\s*)?켜|unmute)", low):
        return VoiceIntent(
            action=IntentAction.UNMUTE,
            raw_text=raw_text,
            confidence=0.98,
            wake_word_detected=wake_detected,
            language=lang,
        )
    if re.search(r"(음소거|뮤트|소리\s*꺼|조용히\s*해|^mute$|mute\s+(the\s+)?(music|audio|sound|volume))", low):
        return VoiceIntent(
            action=IntentAction.MUTE,
            raw_text=raw_text,
            confidence=0.98,
            wake_word_detected=wake_detected,
            language=lang,
        )

    # 3. VOLUME controls (check before pause/play so "소리 키워줘" doesn't misroute)
    if re.search(r"(볼륨|소리|음량|volume|louder|quieter|softer)", low):
        num = parse_korean_number(low)
        # Check relative up/down with explicit amount (e.g., "볼륨 10 올려줘") vs absolute set ("볼륨 50으로")
        is_up = bool(re.search(r"(올려|키워|크게|높여|더\s*크게|\+|up|louder|raise|increase|higher)", low))
        is_down = bool(re.search(r"(내려|줄여|작게|낮춰|더\s*작게|-|down|quieter|softer|lower|decrease)", low))

        if is_up:
            delta = num if (num is not None and 1 <= num <= 50) else (
                20 if re.search(r"(많이|확|크게|much|lot)", low) else (
                    5 if re.search(r"(조금|살짝|약간|bit|little)", low) else 10
                )
            )
            return VoiceIntent(
                action=IntentAction.VOLUME_UP,
                raw_text=raw_text,
                delta=delta,
                confidence=0.96,
                wake_word_detected=wake_detected,
                language=lang,
            )

        if is_down:
            delta = num if (num is not None and 1 <= num <= 50) else (
                20 if re.search(r"(많이|확|크게|much|lot)", low) else (
                    5 if re.search(r"(조금|살짝|약간|bit|little)", low) else 10
                )
            )
            return VoiceIntent(
                action=IntentAction.VOLUME_DOWN,
                raw_text=raw_text,
                delta=-delta,
                confidence=0.96,
                wake_word_detected=wake_detected,
                language=lang,
            )

        if num is not None and 0 <= num <= 100:
            return VoiceIntent(
                action=IntentAction.VOLUME_SET,
                raw_text=raw_text,
                volume=num,
                confidence=0.98,
                wake_word_detected=wake_detected,
                language=lang,
            )

    # Handle implicit volume commands without word "볼륨" (e.g. "조금만 줄여줘", "좀 키워줘")
    if re.fullmatch(r"(?:좀|조금|조금만|살짝|많이|확)?\s*(?:키워\s*줘|키워|올려\s*줘|올려)", low):
        delta = 5 if re.search(r"(조금|살짝)", low) else (20 if re.search(r"(많이|확)", low) else 10)
        return VoiceIntent(
            action=IntentAction.VOLUME_UP,
            raw_text=raw_text,
            delta=delta,
            confidence=0.9,
            wake_word_detected=wake_detected,
            language=lang,
        )
    if re.fullmatch(r"(?:좀|조금|조금만|살짝|많이|확)?\s*(?:줄여\s*줘|줄여|내려\s*줘|내려)", low):
        delta = 5 if re.search(r"(조금|살짝)", low) else (20 if re.search(r"(많이|확)", low) else 10)
        return VoiceIntent(
            action=IntentAction.VOLUME_DOWN,
            raw_text=raw_text,
            delta=-delta,
            confidence=0.9,
            wake_word_detected=wake_detected,
            language=lang,
        )

    # 4. NEXT / PREVIOUS track
    if re.search(
        r"(다음\s*(곡|노래|트랙|거)|넘겨\s*줘|넘겨|스킵|넥스트|^next(?:\s+(?:song|track))?$|^skip(?:\s+(?:this\s+)?(?:track|song))?$)",
        low,
    ):
        return VoiceIntent(
            action=IntentAction.NEXT,
            raw_text=raw_text,
            confidence=0.98,
            wake_word_detected=wake_detected,
            language=lang,
        )

    if re.search(
        r"(이전\s*(곡|노래|트랙|거)|앞\s*(곡|노래)|아까\s*(그\s*)?(곡|노래)|전\s*곡|되돌려|^previous(\s+(song|track))?$|^prev$|^back$|go\s+back)",
        low,
    ):
        return VoiceIntent(
            action=IntentAction.PREVIOUS,
            raw_text=raw_text,
            confidence=0.97,
            wake_word_detected=wake_detected,
            language=lang,
        )

    # 5. PAUSE / STOP
    if re.search(
        r"(^(?:잠깐\s*)?(?:멈춰|멈춰\s*줘|멈춰줘|정지|일시정지|스톱|스탑|그만|꺼\s*줘|꺼줘|노래\s*꺼|음악\s*꺼|음악\s*멈춰|노래\s*멈춰|잠시\s*멈춰)(?:\s*줘|\s*주세요)?$|^pause(\s+(the\s+)?(music|song|playback))?$|^stop(\s+(the\s+)?(music|song|playback))?$)",
        low,
    ):
        return VoiceIntent(
            action=IntentAction.PAUSE,
            raw_text=raw_text,
            confidence=0.98,
            wake_word_detected=wake_detected,
            language=lang,
        )

    # 6. RESUME (generic play with no query)
    if re.fullmatch(
        r"(?:다시\s*(?:틀어\s*줘|틀어|재생해\s*줘|재생|켜\s*줘)|계속\s*(?:틀어\s*줘|틀어|재생해\s*줘|재생)|이어서\s*(?:틀어\s*줘|틀어|재생해\s*줘|재생)|재생\s*(?:해\s*줘|해)?|음악\s*(?:틀어\s*줘|켜\s*줘|재생)|노래\s*(?:틀어\s*줘|켜\s*줘|재생)|resume(?:\s+(?:music|playback))?|continue(?:\s+(?:music|playback))?|play)",
        low,
    ):
        return VoiceIntent(
            action=IntentAction.RESUME,
            raw_text=raw_text,
            confidence=0.95,
            wake_word_detected=wake_detected,
            language=lang,
        )

    # 7. TIDAL MY MIX / RANDOM ("마이믹스 틀어줘", "아무거나 틀어줘", "추천곡 틀어줘")
    if re.search(
        r"(마이믹스|my\s*mix|타이달\s*믹스|tidal\s*mix|아무\s*(?:노래|음악|거나)\s*(?:틀어|재생)|추천\s*(?:곡|노래|음악)|play\s+something|surprise\s+me)",
        low,
    ):
        return VoiceIntent(
            action=IntentAction.PLAY_MIX,
            raw_text=raw_text,
            query="My Mix",
            confidence=0.95,
            wake_word_detected=wake_detected,
            language=lang,
        )

    # 8. FAVORITES ("즐겨찾기 틀어줘", "좋아하는 노래 틀어줘")
    if re.search(r"(즐겨찾기|페이버릿|favorites?|my\s+favorites?)", low):
        fav_q = re.sub(
            r"(즐겨찾기|페이버릿|favorites?|에서|중에|틀어\s*줘|틀어|재생해\s*줘|재생|play)",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()
        return VoiceIntent(
            action=IntentAction.PLAY_FAVORITE,
            raw_text=raw_text,
            query=fav_q,
            confidence=0.94,
            wake_word_detected=wake_detected,
            language=lang,
        )

    # 9. PLAY SEARCH (Korean or English music search command)
    has_ko_play_intent = bool(_KO_PLAY_SUFFIX_RE.search(text))
    has_en_play_intent = bool(_EN_PLAY_PREFIX_RE.search(text))

    if has_ko_play_intent or has_en_play_intent:
        # Infer search_type from phrasing BEFORE stripping descriptors
        search_type = "any"
        if re.search(r"(앨범|album)", low):
            search_type = "album"
        elif re.search(r"(플레이리스트|믹스|playlist|mix)", low):
            search_type = "playlist"
        elif re.search(r"(아티스트|가수|artist)", low):
            search_type = "artist"

        extracted = text
        if has_en_play_intent:
            extracted = _EN_PLAY_PREFIX_RE.sub("", extracted)
            extracted = _EN_PLAY_SUFFIX_RE.sub("", extracted)
        if has_ko_play_intent:
            # Strip explicit "타이달에서 " / "유튜브에서 " prefix if present
            extracted = re.sub(r"^(?:타이달|tidal)에서\s*", "", extracted, flags=re.IGNORECASE)
            extracted = _KO_PLAY_SUFFIX_RE.sub("", extracted)

        # Clean leftover particles/filler words ("아이유의 노래" -> "아이유", "잔나비의 주저하는 연인들을 위해")
        extracted = re.sub(r"\s+(?:노래|음악|곡|앨범|플레이리스트|트랙|좀|한번|한 번)$", "", extracted).strip()
        extracted = re.sub(r"^(?:노래|음악|songs?\s+by|music\s+by|tracks?\s+by)\s+", "", extracted, flags=re.IGNORECASE).strip()
        # Handle Korean possessive "의" after known artist (e.g. "아이유의 밤편지" -> "아이유 밤편지")
        extracted = re.sub(r"([가-힣a-zA-Z0-9]+)의\s+", r"\1 ", extracted).strip()

        if not extracted:
            return VoiceIntent(
                action=IntentAction.RESUME,
                raw_text=raw_text,
                confidence=0.85,
                wake_word_detected=wake_detected,
                language=lang,
            )

        # Refine search_type heuristically when user didn't specify album/playlist
        if search_type == "any":
            if extracted in GENRE_MOOD_ALIASES or any(
                extracted.endswith(g) for g in ("재즈", "클래식", "피아노", "로파이", "팝송", "발라드", "힙합", "어쿠스틱", "보사노바")
            ):
                search_type = "playlist"
            elif extracted in ARTIST_ALIASES and not any(t in extracted for t in TRACK_ALIASES):
                search_type = "artist"
            elif any(t in extracted for t in TRACK_ALIASES):
                search_type = "track"
            elif re.search(r"(노래|음악)\s*(?:틀어|재생|들려)", text) and len(extracted.split()) == 1:
                search_type = "artist"
            else:
                search_type = "track"

        expanded = expand_tidal_queries(extracted)
        primary = expanded[0] if expanded else extracted
        fallbacks = expanded[1:] if len(expanded) > 1 else []

        return VoiceIntent(
            action=IntentAction.PLAY_SEARCH,
            raw_text=raw_text,
            query=primary,
            fallback_queries=fallbacks,
            search_type=search_type,
            confidence=0.92,
            wake_word_detected=wake_detected,
            language=lang,
        )

    # Not recognized as a music command (e.g. "오늘 날씨 어때", "배고파")
    return VoiceIntent(
        action=IntentAction.UNKNOWN,
        raw_text=raw_text,
        confidence=0.0,
        wake_word_detected=wake_detected,
        language=lang,
    )
