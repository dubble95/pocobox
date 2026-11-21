"""Semantic intent matcher and dual-root intent classifier for PocoBox.

Provides sub-millisecond, offline natural language command classification
handling colloquial phrasing, particles, word-order variations, and typos.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from .aliases import expand_tidal_queries, strip_wake_prefix
from .intent_schema import IntentAction, VoiceIntent
from .jamo_util import jamo_phonetic_similarity

# Semantic dual-root triggers: (entity_pattern, action_verb_pattern)
# Both patterns must match (or one if the other is empty).
_SEMANTIC_INTENT_PATTERNS: list[tuple[IntentAction, list[tuple[str, str]]]] = [
    (
        IntentAction.NOW_PLAYING,
        [
            (r"(?:노래|음악|곡|트랙|가수|아티스트|타이틀|제목|곡명)", r"(?:뭐|누구|알려|어떤|무엇|뭔지|궁금|이름)"),
            (r"(?:지금|현재|이거|그거|요거|방금)", r"(?:노래|음악|곡|트랙|나오|틀|흘러)"),
            (r"(?:지금|현재)", r"(?:뭐\s*(?:나와|틀었|재생)|무슨\s*(?:곡|노래|음악))"),
            (r"(?:what|who)", r"(?:song|track|music|playing|singing|artist|title)"),
            (r"(?:current|now)", r"(?:song|track|music|playing)"),
        ],
    ),
    (
        IntentAction.PAUSE,
        [
            (r"(?:음악|노래|재생)", r"(?:멈춰|꺼|정지|일시정지|그만|스톱|중단|쉬어)"),
            (r"(?:잠깐|잠시)", r"(?:멈춰|정지|쉬|스톱)"),
            (r"(?:그만\s*틀|노래\s*그만|음악\s*그만)", r""),
            (r"(?:pause|stop)", r"(?:music|song|playback)?"),
        ],
    ),
    (
        IntentAction.VOLUME_DOWN,
        [
            (r"(?:소리|볼륨|음량|사운드)", r"(?:줄여|내려|작게|낮춰|더\s*줄|더\s*내|더\s*작)"),
            (r"(?:더|조금|살짝|많이|확|좀)", r"(?:작게|줄여|내려|낮춰)"),
            (r"(?:시끄러|귀\s*아파|너무\s*커)", r"(?:줄여|작게|낮춰|내려)"),
            (r"(?:시끄러|귀\s*아파)", r""),
            (r"(?:quieter|softer|lower|decrease|turn\s*down)", r"(?:volume|sound|music)?"),
        ],
    ),
    (
        IntentAction.VOLUME_UP,
        [
            (r"(?:소리|볼륨|음량|사운드)", r"(?:키워|올려|크게|높여|더\s*키|더\s*올|더\s*크)"),
            (r"(?:더|조금|살짝|많이|확|좀)", r"(?:크게|키워|올려|높여)"),
            (r"(?:소리가\s*작|너무\s*작)", r"(?:키워|올려|크게)"),
            (r"(?:louder|increase|raise|turn\s*up)", r"(?:volume|sound|music)?"),
        ],
    ),
    (
        IntentAction.MUTE,
        [
            (r"(?:소리)", r"(?:꺼\s*줘|꺼줘|끄\s*기|뮤트|음소거)"),
            (r"(?:음소거|뮤트|조용히\s*해)", r""),
            (r"(?:mute)", r"(?:volume|audio|sound|music)?"),
        ],
    ),
    (
        IntentAction.RESUME,
        [
            (r"(?:다시|계속|이어서)", r"(?:틀어|재생|켜|플레이)"),
            (r"(?:음악|노래)", r"(?:다시\s*틀|계속\s*틀|켜\s*줘|틀어\s*줘|재생)"),
            (r"(?:play|resume|continue)", r"(?:music|song|playback)?"),
        ],
    ),
    (
        IntentAction.NEXT,
        [
            (r"(?:다음|다음\s*곡|다음\s*노래|다음\s*트랙)", r"(?:틀어|넘겨|재생|들려|가|패스)?"),
            (r"(?:노래|곡)", r"(?:넘겨|스킵|패스)"),
            (r"(?:스킵|패스|넘겨줘|넘겨)", r""),
            (r"(?:next|skip)", r"(?:song|track|music)?"),
        ],
    ),
    (
        IntentAction.PREVIOUS,
        [
            (r"(?:이전|전\s*곡|이전\s*노래|앞\s*곡|아까\s*(?:그\s*)?곡)", r"(?:틀어|돌려|재생|들려|가)?"),
            (r"(?:노래|곡)", r"(?:되돌려|뒤로)"),
            (r"(?:되돌려|뒤로\s*가)", r""),
            (r"(?:previous|back)", r"(?:song|track|music)?"),
        ],
    ),
    (
        IntentAction.PLAY_MIX,
        [
            (r"(?:마이믹스|my\s*mix|타이달\s*믹스|tidal\s*mix)", r""),
            (r"(?:아무\s*노래|아무\s*음악|아무거나|신나는\s*거|추천\s*곡|추천\s*노래)", r"(?:틀어|재생|들려)"),
            (r"(?:랜덤으로|알아서)", r"(?:틀어|재생|들려)"),
            (r"(?:surprise\s+me|play\s+something|play\s+my\s+mix)", r""),
        ],
    ),
    (
        IntentAction.WEATHER,
        [
            (r"(?:날씨|일기\s*예보|기상\s*예보|기온|온도|미세먼지|초미세먼지|대기\s*질|대기질)", r"(?:어때|몇|알려|와|내려|추|더|좋|나쁨)"),
            (r"(?:오늘|내일|모레|지금|현재|밖에)", r"(?:비|눈|비와|눈와|비 와|눈 와)"),
            (r"(?:우산)", r"(?:챙겨|필요|가져|들고)"),
            (r"(?:weather|forecast)", r"(?:today|tomorrow|rain|snow|cold|hot)"),
        ],
    ),
]

_INTENT_EXEMPLARS: dict[IntentAction, list[str]] = {
    IntentAction.NOW_PLAYING: [
        "지금 나오는 노래 뭐야", "지금 음악 뭐야", "지금 노래 뭐야", "지금 나오는 거 뭐야",
        "지금 틀고 있는 음악 뭐야", "이 음악 뭐야", "이 노래 뭐야", "이거 무슨 노래야",
        "무슨 음악이야", "무슨 노래 나와", "노래 제목 뭐야", "음악 제목 알려줘",
        "가수가 누구야", "누구 노래야", "현재 재생 중인 곡", "이 노래 제목이 뭔지 알려줄래",
        "현재 곡 알려줘", "곡명이 뭐야", "노래 이름이 뭐야",
        "what is playing", "what song is this", "current song", "now playing", "who is singing",
    ],
    IntentAction.PAUSE: [
        "음악 멈춰줘", "노래 멈춰", "음악 꺼줘", "노래 꺼", "음악 정지",
        "일시정지해줘", "잠깐 멈춰", "그만 틀어", "노래 그만 틀어",
        "pause", "stop playback", "stop the music",
    ],
    IntentAction.RESUME: [
        "다시 틀어줘", "계속 틀어줘", "음악 틀어줘", "노래 틀어줘", "음악 틀줘",
        "음악 켜줘", "노래 켜", "다시 재생해줘", "이어서 틀어줘",
        "play", "resume", "continue music",
    ],
    IntentAction.NEXT: [
        "다음 곡으로 넘겨줘", "다음 노래", "다음 거 틀어줘", "노래 넘겨줘",
        "스킵해줘", "노래 넘겨", "다음 트랙 틀어줘",
        "next song", "skip this track", "next track",
    ],
    IntentAction.PREVIOUS: [
        "이전 곡으로 돌아가", "이전 노래 틀어줘", "아까 그 노래 틀어줘", "앞 곡 틀어줘",
        "되돌려줘", "이전 트랙으로 가줘",
        "previous song", "go back", "previous track",
    ],
    IntentAction.VOLUME_UP: [
        "소리 좀 키워줘", "볼륨 올려줘", "더 크게 해줘", "소리 키워",
        "소리 좀 올려", "소리가 너무 작아", "볼륨 좀 높여줘",
        "turn volume up", "louder", "make it louder",
    ],
    IntentAction.VOLUME_DOWN: [
        "소리 좀 줄여줘", "볼륨 낮춰줘", "더 작게 해줘", "소리 줄여",
        "소리 조금만 줄여", "소리가 너무 커", "볼륨 좀 낮춰줘", "소리 너무 시끄러",
        "turn volume down", "quieter", "make it quieter",
    ],
    IntentAction.PLAY_MIX: [
        "타이달 마이믹스 틀어줘", "마이 믹스 틀어줘", "아무 노래나 틀어줘",
        "추천곡 틀어줘", "신나는 거 아무거나 틀어줘", "랜덤으로 틀어줘",
        "surprise me", "play something", "play my mix",
    ],
    IntentAction.WEATHER: [
        "오늘 날씨 어때", "내일 날씨 어때", "지금 날씨 어때", "날씨 알려줘",
        "지금 몇 도야", "오늘 기온 어때", "내일 비 와?", "오늘 눈 와?",
        "우산 챙겨야 해?", "미세먼지 어때", "대기질 어때", "초미세먼지 나쁨?",
        "부산 날씨 알려줘", "서울 날씨 어때", "판교 내일 날씨 어때",
        "what's the weather", "will it rain today", "weather forecast",
    ],
}


def _char_ngrams(s: str, n: int = 2) -> set[str]:
    clean = re.sub(r"[^\w]", "", s)
    if len(clean) < n:
        return {clean} if clean else set()
    return {clean[i : i + n] for i in range(len(clean) - n + 1)}


def _semantic_similarity(query: str, target: str) -> float:
    q_words = set(re.sub(r"[^\w\s]", "", query).split())
    t_words = set(re.sub(r"[^\w\s]", "", target).split())
    w_jac = len(q_words & t_words) / max(1, len(q_words | t_words))

    q_ng2 = _char_ngrams(query, 2)
    t_ng2 = _char_ngrams(target, 2)
    ng2_jac = len(q_ng2 & t_ng2) / max(1, len(q_ng2 | t_ng2))

    q_ng3 = _char_ngrams(query, 3)
    t_ng3 = _char_ngrams(target, 3)
    ng3_jac = len(q_ng3 & t_ng3) / max(1, len(q_ng3 | t_ng3))

    seq = difflib.SequenceMatcher(None, query, target).ratio()
    base_score = 0.35 * ng2_jac + 0.25 * ng3_jac + 0.20 * w_jac + 0.20 * seq

    # Boost Korean phonetic similarity via Jamo decomposition if Korean characters present
    if re.search(r"[가-힣]", query) or re.search(r"[가-힣]", target):
        j_sim = jamo_phonetic_similarity(query, target)
        return round(0.55 * base_score + 0.45 * j_sim, 4)
    return base_score


def parse_semantic_command(raw_text: str, *, threshold: float = 0.58) -> VoiceIntent | None:
    """Classify natural language voice command into VoiceIntent using dual-root and exemplar similarity.

    Returns VoiceIntent if confidence >= threshold, or None if intent is ambiguous/unknown.
    Execution latency is < 1 millisecond.
    """
    if not raw_text or not raw_text.strip():
        return None

    text, wake_detected = strip_wake_prefix(raw_text)
    clean = text.strip(" .!?,~")
    if not clean:
        return None

    low = clean.lower()

    # Step 1: Check dual-root semantic rules (fastest & highly discriminative)
    for act, rule_pairs in _SEMANTIC_INTENT_PATTERNS:
        for p1, p2 in rule_pairs:
            m1 = bool(re.search(p1, low)) if p1 else True
            m2 = bool(re.search(p2, low)) if p2 else True
            if m1 and m2:
                # Refine volume delta if volume action
                delta: int | None = None
                if act == IntentAction.VOLUME_UP:
                    delta = 5 if re.search(r"(조금|살짝|약간)", low) else (20 if re.search(r"(많이|확|너무)", low) else 10)
                elif act == IntentAction.VOLUME_DOWN:
                    delta = -5 if re.search(r"(조금|살짝|약간)", low) else (-20 if re.search(r"(많이|확|너무)", low) else -10)

                return VoiceIntent(
                    action=act,
                    raw_text=raw_text,
                    delta=delta,
                    confidence=0.92,
                    wake_word_detected=wake_detected,
                    provider="semantic-dual-root",
                )

    # Step 2: N-gram exemplar similarity matching
    best_act: IntentAction | None = None
    best_score = 0.0

    for act, exemplars in _INTENT_EXEMPLARS.items():
        for ex in exemplars:
            score = _semantic_similarity(low, ex)
            if score > best_score:
                best_score = score
                best_act = act

    if best_act is not None and best_score >= threshold:
        delta = None
        if best_act == IntentAction.VOLUME_UP:
            delta = 5 if re.search(r"(조금|살짝|약간)", low) else 10
        elif best_act == IntentAction.VOLUME_DOWN:
            delta = -5 if re.search(r"(조금|살짝|약간)", low) else -10

        return VoiceIntent(
            action=best_act,
            raw_text=raw_text,
            delta=delta,
            confidence=round(best_score, 2),
            wake_word_detected=wake_detected,
            provider="semantic-exemplar",
        )

    return None
