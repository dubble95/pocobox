"""Hybrid Local-First (SenseVoice INT8) + Cloud STT/Intent Parser with instant offline rule fast-path."""

from __future__ import annotations

import base64
import datetime
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

try:
    import requests as _requests  # type: ignore
except Exception:  # pragma: no cover
    _requests = None

from .aliases import expand_tidal_queries, strip_wake_prefix
from .english_quiz import QuizQuestion, classify_quiz_command, is_local_answer_match, parse_quiz_options
from .intent_schema import IntentAction, VoiceIntent
from .rule_parser import parse_command
from .weather import get_default_location

log = logging.getLogger("pocobox-voice.stt")

_KST = datetime.timezone(datetime.timedelta(hours=9))
_KO_WEEKDAYS = ("월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일")

SYSTEM_PROMPT = """You are the voice assistant and music controller for PocoBox, a Korean/English smart speaker backed by Lyrion Music Server (LMS) and TIDAL.

CRITICAL ANTI-HALLUCINATION RULE:
The microphone is located near music speakers. Many audio clips contain ONLY background singing, song lyrics, instrumental music, TV audio, or ambient noise.
- NEVER invent or hallucinate the wake word ("포코", "포코야") or a command/question if nobody clearly spoke it!
- Song vocals/singing are NOT voice commands or questions. If the audio is singing/music without a clear spoken human request addressed to PocoBox, set "is_music_only": true, "wake_word_detected": false, "action": "unknown", "confidence": 0.0.

The device wake word is ONLY "포코" / "포코야" / "포코박스" / "헤이 포코" / "Hey Poco".

Listen to the audio (or read the text) and return ONLY a valid JSON object matching this exact schema:
{
  "transcript": "<exact spoken words only; empty string if only instrumental/noise>",
  "is_music_only": false,
  "wake_word_detected": true,
  "action": "play_search | play_mix | play_favorite | pause | resume | next | previous | volume_set | volume_up | volume_down | mute | unmute | now_playing | answer_qa | weather | start_quiz | stop_quiz | unknown",
  "query": "<primary TIDAL-friendly search query for play_search, or requested city/region for weather (empty string for default device location), else empty string>",
  "display_query": "<for play_search ONLY: natural corrected Korean/spoken song or artist name to read aloud in confirmation (fixing obvious ASR typos like '잘코스키 피아노 협주 일번' -> '차이코프스키 피아노 협주곡 1번'), else empty string>",
  "fallback_queries": ["<alternative query 1>", "<original Korean query>"],
  "search_type": "track | artist | album | playlist | any",
  "volume": null,
  "delta": null,
  "answer": "<for answer_qa ONLY: concise, friendly spoken Korean answer in 1-2 short sentences (plain text, no markdown/bullets/emojis); else empty string>",
  "confidence": 0.95
}

Rules:
- Strip the wake word ("포코야", "포코박스", "Hey Poco", etc.) from "query" and set "wake_word_detected": true ONLY if the speaker actually said the wake word.
- Use action="weather" whenever the user asks about weather, temperature, rain, snow, or umbrella (e.g. "오늘 날씨 어때?", "내일 비 와?", "부산 날씨 알려줘"). Put ONLY the explicitly requested city/region in "query" (or "" if they did not specify a region). NEVER guess or invent weather data in "answer".
- Use action="answer_qa" when the user asks a general question, trivia, math, definition, recommendation, music/artist info, greeting, or time/date question (e.g. "콜드플레이가 어떤 밴드야?", "프랑스 수도가 어디야?", "38 곱하기 17은?", "저녁 메뉴 추천해줘", "내일 무슨 요일이야?"). Keep "answer" short, accurate, and natural to read aloud (1-2 sentences, Korean unless the user asked in English).
- Use action="start_quiz" when the user asks to start an English quiz or English listening quiz (e.g. "영어 퀴즈 시작해줘", "영어 문제 내줘", "동물 영어 퀴즈 하자", "English quiz"). Use action="stop_quiz" when they ask to stop/end the quiz.
- If the speaker did NOT specify a song/artist/genre/mix/question (e.g. only said "포코야" or vague "이 노래 틀어줘"), return action="unknown" unless they clearly said "다시 틀어줘" (resume) or "아무거나 틀어줘" (play_mix).
- For Korean artists/songs/classical works, ALWAYS fix obvious speech-recognition (ASR) mis-hearings (e.g. "잘코스키 피아노 협주 일번 틀어줘" -> query: "Tchaikovsky Piano Concerto No. 1", display_query: "차이코프스키 피아노 협주곡 1번"; "쇼핑 녹턴 틀어줘" -> query: "Chopin Nocturne", display_query: "쇼팽 녹턴"). Put the official TIDAL English/canonical name in "query", the natural spoken Korean title in "display_query", and Korean/shorter variants in "fallback_queries" (e.g. "포코야 아이유 밤편지 틀어줘" -> query: "IU Through the Night", display_query: "아이유 밤편지", fallback_queries: ["밤편지", "IU"]).
- For playback controls (pause, resume, next, previous, volume_set/up/down, mute, unmute, now_playing), set query and answer to "".
- For volume_up use positive delta (default 10), for volume_down use negative delta (default -10), for volume_set set volume 0..100.
- Return pure JSON only, no markdown fences."""


def _build_system_prompt() -> str:
    now = datetime.datetime.now(tz=_KST)
    wd = _KO_WEEKDAYS[now.weekday()]
    loc = get_default_location()
    return (
        SYSTEM_PROMPT
        + f"\n\nCurrent local time (Korea Standard Time, KST): {now.strftime('%Y-%m-%d %H:%M')} ({wd})."
        + f"\nDevice default location: {loc.name} ({loc.en_name}, South Korea). For any location-dependent question where the user did not specify another city, assume {loc.name} (never default to Seoul unless the user explicitly mentions Seoul)."
    )


def _parse_json_response(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    return json.loads(cleaned.strip())


def _intent_from_llm_payload(payload: dict[str, Any], provider: str, raw_fallback: str = "") -> VoiceIntent:
    transcript = str(payload.get("transcript") or raw_fallback or "").strip()
    is_music_only = bool(payload.get("is_music_only", False))
    if is_music_only:
        return VoiceIntent(
            action=IntentAction.UNKNOWN,
            raw_text="",
            confidence=0.0,
            provider=provider,
            wake_word_detected=False,
        )

    action_str = str(payload.get("action") or "unknown").strip().lower()
    query = str(payload.get("query") or "").strip()
    display_query = str(payload.get("display_query") or "").strip()
    fallbacks = [str(x).strip() for x in (payload.get("fallback_queries") or []) if str(x).strip()]

    # Guard against LLM now_playing hallucinations on random chatter or statements
    if action_str == IntentAction.NOW_PLAYING.value:
        now_playing_cues = (
            "뭐", "무슨", "누구", "제목", "현재", "지금", "이 곡", "이 노래", "어떤 곡", "어떤 노래",
            "what", "playing", "song", "title", "track",
        )
        if not any(cue in transcript.lower() for cue in now_playing_cues):
            action_str = IntentAction.UNKNOWN.value

    # Guard against SLM / LLM play_search hallucinations on random chatter
    if action_str == IntentAction.PLAY_SEARCH.value:
        play_cues = (
            "틀어", "재생", "들려", "노래", "음악", "곡", "앨범", "아티스트", "가수", "플레이", "찾아", "선곡",
            "play", "music", "song", "track", "album", "artist", "listen",
        )
        has_play_cue = any(c in transcript.lower() for c in play_cues)
        alias_matches = False
        if not has_play_cue and query:
            expanded = expand_tidal_queries(query)
            if expanded and expanded[0].lower() != query.lower():
                alias_matches = True
        if not has_play_cue and not alias_matches:
            action_str = IntentAction.UNKNOWN.value

    # Enrich with local alias expansion if query is Korean
    if query and action_str == "play_search":
        for extra in expand_tidal_queries(query):
            if extra.lower() != query.lower() and extra.lower() not in {f.lower() for f in fallbacks}:
                fallbacks.append(extra)

    _, prefix_wake = strip_wake_prefix(transcript)
    # Require either real wake prefix in transcript OR explicit wake_word_detected with high confidence
    wake_detected = prefix_wake or (
        bool(payload.get("wake_word_detected", False))
        and any(w in transcript.lower() for w in ("포코", "포쿠", "쿠쿠", "poco"))
    )

    quiz_topic = ""
    quiz_count: int | None = None
    if action_str == IntentAction.START_QUIZ.value:
        quiz_topic, quiz_count = parse_quiz_options(transcript or query)

    return VoiceIntent(
        action=IntentAction(action_str) if action_str in {a.value for a in IntentAction} else IntentAction.UNKNOWN,
        raw_text=transcript,
        query=query,
        display_query=display_query,
        fallback_queries=fallbacks,
        search_type=str(payload.get("search_type") or "any").lower(),
        volume=payload.get("volume"),
        delta=payload.get("delta"),
        confidence=float(payload.get("confidence", 0.9)),
        provider=provider,
        wake_word_detected=wake_detected,
        answer=str(payload.get("answer") or "").strip(),
        quiz_topic=quiz_topic,
        quiz_count=quiz_count,
    )


class GeminiQuotaTracker:
    """Tracks and limits daily and per-minute Gemini API calls to prevent quota exhaustion."""

    def __init__(
        self,
        daily_limit: int | None = None,
        max_rpm: int | None = None,
    ) -> None:
        self.daily_limit = (
            daily_limit
            if daily_limit is not None
            else int(os.environ.get("GEMINI_DAILY_LIMIT", "150"))
        )
        self.max_rpm = (
            max_rpm
            if max_rpm is not None
            else int(os.environ.get("GEMINI_MAX_RPM", "10"))
        )
        self._lock = threading.Lock()
        self._today_date: datetime.date = datetime.datetime.now(tz=_KST).date()
        self._daily_calls: int = 0
        self._minute_calls: list[float] = []
        self._total_calls: int = 0
        self._cache_hits: int = 0
        self._skipped_ambient: int = 0
        self._rate_limited_count: int = 0

    def _rollover_if_needed_unlocked(self, now_dt: datetime.datetime) -> None:
        today = now_dt.date()
        if today != self._today_date:
            log.info(
                "GeminiQuotaTracker: daily rollover %s -> %s (yesterday calls: %d)",
                self._today_date,
                today,
                self._daily_calls,
            )
            self._today_date = today
            self._daily_calls = 0

    def can_call(self) -> tuple[bool, str]:
        """Check if a Gemini call is permitted under RPM and daily limits."""
        with self._lock:
            now = time.time()
            now_dt = datetime.datetime.fromtimestamp(now, tz=_KST)
            self._rollover_if_needed_unlocked(now_dt)

            # Check daily limit (0 or negative means unlimited)
            if self.daily_limit > 0 and self._daily_calls >= self.daily_limit:
                self._rate_limited_count += 1
                return False, f"daily_limit_exceeded ({self._daily_calls}/{self.daily_limit})"

            # Prune sliding 60-second window
            cutoff = now - 60.0
            self._minute_calls = [t for t in self._minute_calls if t > cutoff]
            if self.max_rpm > 0 and len(self._minute_calls) >= self.max_rpm:
                self._rate_limited_count += 1
                return False, f"rpm_limit_exceeded ({len(self._minute_calls)}/{self.max_rpm})"

            return True, ""

    def record_call(self) -> None:
        """Record an executed Gemini API call."""
        with self._lock:
            now = time.time()
            now_dt = datetime.datetime.fromtimestamp(now, tz=_KST)
            self._rollover_if_needed_unlocked(now_dt)
            self._daily_calls += 1
            self._total_calls += 1
            self._minute_calls.append(now)

    def record_cache_hit(self) -> None:
        with self._lock:
            self._cache_hits += 1

    def record_skipped_ambient(self) -> None:
        with self._lock:
            self._skipped_ambient += 1

    def stats(self) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            now_dt = datetime.datetime.fromtimestamp(now, tz=_KST)
            self._rollover_if_needed_unlocked(now_dt)
            cutoff = now - 60.0
            rpm_current = len([t for t in self._minute_calls if t > cutoff])
            remaining = max(0, self.daily_limit - self._daily_calls) if self.daily_limit > 0 else 9999
            return {
                "daily_calls": self._daily_calls,
                "daily_limit": self.daily_limit,
                "daily_remaining": remaining,
                "current_rpm": rpm_current,
                "max_rpm": self.max_rpm,
                "total_calls": self._total_calls,
                "cache_hits": self._cache_hits,
                "skipped_ambient": self._skipped_ambient,
                "rate_limited_count": self._rate_limited_count,
                "date_kst": self._today_date.isoformat(),
            }


class IntentTTLCache:
    """Thread-safe in-memory TTL cache for voice intent results to avoid redundant cloud calls."""

    def __init__(self, ttl_sec: float = 1800.0, max_entries: int = 100) -> None:
        self.ttl_sec = ttl_sec
        self.max_entries = max_entries
        self._cache: dict[str, tuple[float, VoiceIntent]] = {}
        self._lock = threading.Lock()

    def _normalize_key(self, text: str) -> str:
        clean = re.sub(r"[^\w\s]", "", text or "").strip().lower()
        clean, _ = strip_wake_prefix(clean)
        return " ".join(clean.split())

    def get(self, text: str) -> VoiceIntent | None:
        key = self._normalize_key(text)
        if not key:
            return None
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            ts, intent = entry
            if time.time() - ts > self.ttl_sec:
                del self._cache[key]
                return None
            return intent.clone()

    def put(self, text: str, intent: VoiceIntent) -> None:
        key = self._normalize_key(text)
        if not key or intent.action == IntentAction.UNKNOWN:
            return
        with self._lock:
            if len(self._cache) >= self.max_entries:
                oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][0])
                del self._cache[oldest_key]
            self._cache[key] = (time.time(), intent.clone())

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


_QUESTION_INDICATORS = (
    "?", "뭐", "무슨", "어디", "언제", "왜", "어떻게", "몇", "누구", "얼마", "어때",
    "알려줘", "알려", "가르쳐", "설명해", "말해줘", "추천", "날씨", "기온", "비 와", "비와", "눈 와", "눈와",
    "우산", "미세먼지", "시차", "환율", "수도", "역사", "뜻", "의미", "계산", "곱하기",
    "더하기", "빼기", "나누기", "맞아", "틀려", "상태",
    "what", "where", "when", "why", "how", "who", "which", "weather", "tell me", "explain",
)

_MUSIC_INTENT_INDICATORS = (
    "틀어", "재생", "들려", "노래", "음악", "곡", "앨범", "아티스트", "가수", "플레이",
    "찾아", "선곡", "play", "music", "song", "track", "album", "artist", "listen",
)

_QUIZ_INTENT_INDICATORS = (
    "퀴즈", "quiz", "문제",
)

_REQUEST_VERB_ENDINGS = (
    "해줘", "해 줘", "해봐", "해 봐", "줘요", "부탁해", "하자", "할래",
)


def is_meaningful_cloud_candidate(text: str) -> bool:
    """Check if an utterance has clear intent markers (question, music, quiz, or request).

    Prevents escalating household chatter, TV noise fragments, or random statements
    to Gemini/OpenAI cloud APIs.
    """
    clean = re.sub(r"[^\w\s\?]", " ", text or "").strip().lower()
    stripped, has_wake = strip_wake_prefix(clean)
    content = stripped.strip()
    if not content or len(content) < 2:
        return False
    if any(q in content for q in _QUESTION_INDICATORS):
        return True
    if any(m in content for m in _MUSIC_INTENT_INDICATORS):
        return True
    if any(qz in content for qz in _QUIZ_INTENT_INDICATORS):
        return True
    if any(content.endswith(req) for req in _REQUEST_VERB_ENDINGS):
        return True
    expanded = expand_tidal_queries(content)
    if expanded and expanded[0].lower() != content.lower():
        return True
    return False


class HybridVoiceInterpreter:
    """Combines local SenseVoice INT8 STT + offline rule fast-path with optional Gemini/OpenAI fallback."""

    def __init__(
        self,
        provider: str | None = None,
        gemini_api_key: str | None = None,
        gemini_model: str | None = None,
        openai_api_key: str | None = None,
        openai_stt_model: str | None = None,
        openai_chat_model: str | None = None,
        timeout: float = 15.0,
        local_stt_url: str | None = None,
        local_stt_language: str | None = None,
        local_stt_timeout: float | None = None,
        local_llm_url: str | None = None,
        enable_local_llm: bool | None = None,
        local_llm_timeout: float | None = None,
        cloud_fallback: bool | None = None,
        gemini_daily_limit: int | None = None,
        gemini_max_rpm: int | None = None,
        cache_ttl_sec: float | None = None,
        telemetry: Any | None = None,
    ) -> None:
        self.gemini_api_key = gemini_api_key if gemini_api_key is not None else os.environ.get("GEMINI_API_KEY", "").strip()
        self.gemini_model = gemini_model or os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
        self.openai_api_key = openai_api_key if openai_api_key is not None else os.environ.get("OPENAI_API_KEY", "").strip()
        self.openai_stt_model = openai_stt_model or os.environ.get("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")
        self.openai_chat_model = openai_chat_model or os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")
        self.timeout = timeout

        self.quota_tracker = GeminiQuotaTracker(daily_limit=gemini_daily_limit, max_rpm=gemini_max_rpm)
        self.intent_cache = IntentTTLCache(ttl_sec=cache_ttl_sec or 1800.0)
        self.telemetry = telemetry

        raw_local_url = local_stt_url if local_stt_url is not None else os.environ.get("LOCAL_STT_URL", "")
        self.local_stt_url = raw_local_url.strip().rstrip("/")
        self.local_stt_language = (
            local_stt_language if local_stt_language is not None else os.environ.get("LOCAL_STT_LANGUAGE", "ko")
        ).strip().lower() or "ko"
        self.local_stt_timeout = (
            float(local_stt_timeout)
            if local_stt_timeout is not None
            else float(os.environ.get("LOCAL_STT_TIMEOUT", "5.0"))
        )
        raw_local_llm = local_llm_url if local_llm_url is not None else os.environ.get("LOCAL_LLM_URL", "http://127.0.0.1:8767")
        self.local_llm_url = raw_local_llm.strip().rstrip("/")
        if enable_local_llm is not None:
            self.enable_local_llm = bool(enable_local_llm)
        else:
            self.enable_local_llm = os.environ.get("ENABLE_LOCAL_LLM", "1").strip().lower() not in ("0", "false", "no", "off")
        self.local_llm_timeout = (
            float(local_llm_timeout)
            if local_llm_timeout is not None
            else float(os.environ.get("LOCAL_LLM_TIMEOUT", "3.5"))
        )
        if cloud_fallback is not None:
            self.cloud_fallback = bool(cloud_fallback)
        else:
            self.cloud_fallback = os.environ.get("VOICE_CLOUD_FALLBACK", "1").strip().lower() not in ("0", "false", "no", "off")

        self._session = _requests.Session() if _requests is not None else None
        self._local_health_cache: tuple[float, dict[str, Any]] | None = None
        self._local_llm_health_cache: tuple[float, dict[str, Any]] | None = None

        requested = (provider or os.environ.get("VOICE_PROVIDER", "auto")).strip().lower()
        if requested == "auto":
            if self.gemini_api_key:
                self.provider = "gemini"
            elif self.openai_api_key:
                self.provider = "openai"
            else:
                self.provider = "local"
        else:
            self.provider = requested

    def has_cloud_api(self) -> bool:
        return bool(
            (self.provider == "gemini" and self.gemini_api_key)
            or (self.provider == "openai" and self.openai_api_key)
        )

    def can_transcribe_audio(self) -> bool:
        """True if local SenseVoice STT is configured or a cloud provider is active."""
        return bool(self.local_stt_url) or self.provider != "local"

    def set_telemetry(self, telemetry: Any) -> None:
        self.telemetry = telemetry

    def local_stt_health(self, force_refresh: bool = False) -> dict[str, Any]:
        """Check local SenseVoice STT HTTP server health (cached for 10s)."""
        if not self.local_stt_url:
            return {"configured": False, "available": False, "url": ""}
        now = time.monotonic()
        if not force_refresh and self._local_health_cache is not None and (now - self._local_health_cache[0]) < 10.0:
            return self._local_health_cache[1]
        url = f"{self.local_stt_url}/health"
        info: dict[str, Any] = {"configured": True, "available": False, "url": self.local_stt_url}
        try:
            if self._session is not None:
                resp = self._session.get(url, timeout=1.5)
                if resp.status_code == 200:
                    data = resp.json()
                    info["available"] = bool(data.get("ok", True))
                    info["engine"] = data.get("engine", "sensevoice")
                    info["language"] = data.get("default_language", self.local_stt_language)
            else:
                with urllib.request.urlopen(url, timeout=1.5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    info["available"] = bool(data.get("ok", True))
                    info["engine"] = data.get("engine", "sensevoice")
                    info["language"] = data.get("default_language", self.local_stt_language)
        except Exception:
            info["available"] = False
        self._local_health_cache = (now, info)
        return info

    def local_llm_health(self, force_refresh: bool = False) -> dict[str, Any]:
        """Check local llama-server SLM HTTP server health (cached for 10s)."""
        if not self.enable_local_llm or not self.local_llm_url:
            return {"configured": False, "available": False, "url": ""}
        now = time.monotonic()
        if not force_refresh and self._local_llm_health_cache is not None and (now - self._local_llm_health_cache[0]) < 10.0:
            return self._local_llm_health_cache[1]
        url = f"{self.local_llm_url}/health"
        info: dict[str, Any] = {"configured": True, "available": False, "url": self.local_llm_url}
        try:
            if self._session is not None:
                resp = self._session.get(url, timeout=1.2)
                if resp.status_code == 200:
                    data = resp.json()
                    info["available"] = (data.get("status") == "ok" or bool(data.get("ok", True)))
                    info["model"] = "qwen2.5-0.5b-instruct"
            else:
                with urllib.request.urlopen(url, timeout=1.2) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    info["available"] = (data.get("status") == "ok" or bool(data.get("ok", True)))
                    info["model"] = "qwen2.5-0.5b-instruct"
        except Exception:
            info["available"] = False
        self._local_llm_health_cache = (now, info)
        return info

    def _local_llm_text_intent(self, text: str) -> VoiceIntent | None:
        """Parse natural language command using local llama-server (Qwen2.5-0.5B-Instruct)."""
        if not self.enable_local_llm or not self.local_llm_url:
            return None
        url = f"{self.local_llm_url}/v1/chat/completions"
        system_content = (
            "You are the voice intent parser for PocoBox music player. "
            "Output JSON only: {\"action\": \"play_search\"|\"play_mix\"|\"now_playing\"|\"pause\"|\"resume\"|\"next\"|\"previous\"|\"volume_up\"|\"volume_down\"|\"answer_qa\"|\"unknown\", "
            "\"query\": \"...\", \"answer\": \"...\"}.\n"
            "Rules:\n"
            "- play_search: user asks to play music/song (~틀어줘, ~재생해, play ~).\n"
            "- now_playing: user asks what song is playing (지금 무슨 노래야, 이 곡 뭐야).\n"
            "- answer_qa: user asks a factual question (수도가 어디야, 날씨 어때).\n"
            "- unknown: non-command statements, chat, or irrelevant speech."
        )
        payload = {
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": text},
            ],
            "temperature": 0.0,
            "max_tokens": 40,
        }
        try:
            data = self._http_json(url, payload, timeout=self.local_llm_timeout)
            choices = data.get("choices")
            if not choices:
                return None
            content = choices[0]["message"]["content"]
            parsed = _parse_json_response(content)
            intent = _intent_from_llm_payload(parsed, provider="local-slm-qwen0.5b", raw_fallback=text)
            if intent.action != IntentAction.UNKNOWN:
                return intent
        except Exception as exc:
            log.debug("Local SLM intent parsing failed (%s): %s", text, exc)
        return None

    def transcribe_local_audio(self, wav_bytes: bytes, *, language: str | None = None) -> dict[str, Any]:
        """Transcribe WAV bytes via the local SenseVoice-Small INT8 HTTP server (~0.15-0.35s)."""
        if not self.local_stt_url:
            raise RuntimeError("LOCAL_STT_URL is not configured.")
        lang = (language or self.local_stt_language or "ko").strip().lower()
        url = f"{self.local_stt_url}/transcribe?{urllib.parse.urlencode({'language': lang})}"
        headers = {"Content-Type": "audio/wav"}
        if self._session is not None:
            resp = self._session.post(url, data=wav_bytes, headers=headers, timeout=self.local_stt_timeout)
            if resp.status_code != 200:
                raise RuntimeError(f"Local STT HTTP {resp.status_code}: {resp.text[:200]}")
            return resp.json()
        req = urllib.request.Request(url, data=wav_bytes, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.local_stt_timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def interpret_text(self, text: str, use_cloud_fallback: bool = True) -> VoiceIntent:
        """Fast-path local rule parsing first; optionally escalate complex/unknown text to cloud LLM."""
        local_intent = parse_command(text)
        if local_intent.action != IntentAction.UNKNOWN and local_intent.confidence >= 0.9:
            return local_intent

        clean = re.sub(r"[^\w\s]", "", text or "").strip()
        stripped_content, _ = strip_wake_prefix(clean)
        stripped_content = stripped_content.strip()
        if not stripped_content or len(stripped_content) < 2 or stripped_content in ("어", "응", "야", "음", "아", "네"):
            return local_intent

        # Tier 3: Local SLM (Qwen2.5-0.5B via llama-server on 127.0.0.1:8767, ~1.2s on-device)
        if self.enable_local_llm and self.local_llm_url:
            try:
                slm_intent = self._local_llm_text_intent(text)
                if slm_intent is not None and slm_intent.action != IntentAction.UNKNOWN:
                    if local_intent.wake_word_detected and not slm_intent.wake_word_detected:
                        slm_intent.wake_word_detected = True
                    return slm_intent
            except Exception as exc:
                log.debug("Local SLM text fallback failed (%r): %s", text, exc)

        if not use_cloud_fallback or not self.cloud_fallback or not self.has_cloud_api():
            return local_intent

        # Intent Gating: Do not escalate random statements or fragments to cloud LLM
        if not is_meaningful_cloud_candidate(text):
            self.quota_tracker.record_skipped_ambient()
            if self.telemetry:
                self.telemetry.record(
                    "gemini_skipped_ambient",
                    transcript=text,
                    reason="no_intent_cues",
                )
            return local_intent

        try:
            cloud_intent: VoiceIntent | None = None
            if self.provider == "gemini" and self.gemini_api_key:
                cloud_intent = self._gemini_text_intent(text)
            elif self.provider == "openai" and self.openai_api_key:
                cloud_intent = self._openai_text_intent(text)
            if cloud_intent is not None and (
                cloud_intent.action != IntentAction.UNKNOWN or local_intent.action == IntentAction.UNKNOWN
            ):
                if local_intent.wake_word_detected and not cloud_intent.wake_word_detected:
                    cloud_intent.wake_word_detected = True
                return cloud_intent
        except Exception:
            pass
        return local_intent

    def interpret_audio(self, wav_bytes: bytes, *, enforce_wake_word: bool = False) -> VoiceIntent:
        """Local-first SenseVoice STT + offline rule parser (~0.3s); optional cloud fallback for Q&A/unknown."""
        if self.local_stt_url:
            try:
                stt = self.transcribe_local_audio(wav_bytes)
                transcript = str(stt.get("text") or "").strip()
                clean_text = re.sub(r"[^\w\s]", "", transcript).strip()
                if stt.get("is_music_only") or not clean_text:
                    return VoiceIntent(
                        action=IntentAction.UNKNOWN,
                        raw_text="",
                        confidence=0.0,
                        provider="sensevoice-local",
                        wake_word_detected=False,
                    )

                stripped, has_wake = strip_wake_prefix(transcript)
                has_wake_cue = has_wake or any(w in transcript.lower() for w in ("포코", "포쿠", "쿠쿠", "poco"))

                # CRITICAL: If wake word is required and missing, drop immediately!
                # Prevents local SLM and cloud Gemini from running on background room chatter.
                if enforce_wake_word and not has_wake_cue:
                    return VoiceIntent(
                        action=IntentAction.UNKNOWN,
                        raw_text=transcript,
                        confidence=0.0,
                        provider="sensevoice-local",
                        wake_word_detected=False,
                    )

                fast = parse_command(transcript)
                if fast.action != IntentAction.UNKNOWN and fast.confidence >= 0.85:
                    fast.provider = "sensevoice+rule"
                    return fast

                # Bare wake-word utterance ("포코야") -> return immediately without any cloud call
                clean_stripped = re.sub(r"[^\w\s]", "", stripped).strip()
                if has_wake and not clean_stripped:
                    fast.provider = "sensevoice+rule"
                    return fast

                # Skip cloud/SLM fallback for empty punctuation or short ambient filler sounds (< 2 chars or simple filler)
                if not clean_stripped or len(clean_stripped) < 2 or clean_stripped in ("어", "응", "야", "음", "아", "네"):
                    fast.provider = "sensevoice-local"
                    return fast

                # Tier 3: Local SLM (Qwen2.5-0.5B via llama-server on 127.0.0.1:8767, ~1.2s on-device)
                if self.enable_local_llm and self.local_llm_url:
                    try:
                        slm_intent = self._local_llm_text_intent(transcript)
                        if slm_intent is not None and slm_intent.action != IntentAction.UNKNOWN:
                            slm_intent.provider = "sensevoice+local-slm"
                            if fast.wake_word_detected and not slm_intent.wake_word_detected:
                                slm_intent.wake_word_detected = True
                            return slm_intent
                    except Exception as exc:
                        log.debug("Local SLM audio fallback failed after STT (%r): %s", transcript, exc)

                # Intent Gating: Do not escalate random noise/fragment to Gemini unless it has intent cues!
                if not is_meaningful_cloud_candidate(transcript):
                    self.quota_tracker.record_skipped_ambient()
                    if self.telemetry:
                        self.telemetry.record(
                            "gemini_skipped_ambient",
                            transcript=transcript,
                            reason="no_intent_cues",
                        )
                    log.info("Skipped cloud LLM for ambient utterance without intent cues: %r", transcript)
                    fast.provider = "sensevoice-local"
                    return fast

                # Tier 4: Unknown/complex command or Q&A: escalate text-only to cloud LLM if enabled
                if self.cloud_fallback and self.has_cloud_api():
                    try:
                        cloud_intent: VoiceIntent | None = None
                        if self.provider == "gemini" and self.gemini_api_key:
                            cloud_intent = self._gemini_text_intent(transcript)
                            cloud_intent.provider = "sensevoice+gemini-text"
                        elif self.provider == "openai" and self.openai_api_key:
                            cloud_intent = self._openai_text_intent(transcript)
                            cloud_intent.provider = "sensevoice+openai-text"
                        if cloud_intent is not None and (
                            cloud_intent.action != IntentAction.UNKNOWN or fast.action == IntentAction.UNKNOWN
                        ):
                            if fast.wake_word_detected and not cloud_intent.wake_word_detected:
                                cloud_intent.wake_word_detected = True
                            return cloud_intent
                    except Exception as exc:
                        log.warning("Cloud text fallback failed after local STT (%r): %s", transcript, exc)

                fast.provider = "sensevoice-local"
                return fast
            except Exception as exc:
                log.warning("Local SenseVoice STT unavailable (%s); falling back to cloud audio.", exc)
                if not self.has_cloud_api():
                    raise RuntimeError(f"Local STT error ({self.local_stt_url}): {exc}") from exc

        if self.provider == "gemini" and self.gemini_api_key:
            return self._gemini_audio_intent(wav_bytes)
        if self.provider == "openai" and self.openai_api_key:
            transcript = self._openai_transcribe(wav_bytes)
            if not transcript:
                return VoiceIntent(action=IntentAction.UNKNOWN, raw_text="", confidence=0.0, provider="openai")
            # Ultra-fast local rule fast-path avoids a 2nd LLM round-trip for clear commands!
            fast = parse_command(transcript)
            if fast.action != IntentAction.UNKNOWN and fast.confidence >= 0.9:
                fast.provider = "openai-stt+rule"
                return fast
            return self._openai_text_intent(transcript)
        raise RuntimeError(
            "Neither LOCAL_STT_URL nor a cloud STT API key is configured. "
            "Set LOCAL_STT_URL or GEMINI_API_KEY / OPENAI_API_KEY in /etc/pocobox/voice.env."
        )

    def _gemini_audio_intent(self, wav_bytes: bytes) -> VoiceIntent:
        allowed, reason = self.quota_tracker.can_call()
        if not allowed:
            log.warning("Gemini audio call blocked by quota tracker (%s)", reason)
            stats = self.quota_tracker.stats()
            if self.telemetry:
                self.telemetry.record(
                    "gemini_quota_blocked",
                    reason=reason,
                    daily_calls=stats["daily_calls"],
                    daily_limit=stats["daily_limit"],
                )
            return VoiceIntent(
                action=IntentAction.UNKNOWN,
                raw_text="",
                confidence=0.0,
                provider="gemini-quota-blocked",
            )

        b64 = base64.b64encode(wav_bytes).decode("ascii")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_api_key}"
        body = {
            "system_instruction": {"parts": [{"text": _build_system_prompt()}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"inline_data": {"mime_type": "audio/wav", "data": b64}},
                        {"text": "Transcribe and interpret this voice command or simple question as JSON."},
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }
        t0 = time.perf_counter()
        data = self._http_json(url, body)
        call_latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        self.quota_tracker.record_call()
        stats = self.quota_tracker.stats()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        intent = _intent_from_llm_payload(_parse_json_response(text), provider="gemini-audio")

        if self.telemetry:
            self.telemetry.record(
                "gemini_audio_call",
                query=intent.raw_text,
                action=intent.action.value,
                latency_ms=call_latency_ms,
                daily_calls=stats["daily_calls"],
                daily_remaining=stats["daily_remaining"],
            )

        return intent

    def _gemini_text_intent(self, text: str) -> VoiceIntent:
        cached = self.intent_cache.get(text)
        if cached is not None:
            self.quota_tracker.record_cache_hit()
            cached.provider = "gemini-cache"
            if self.telemetry:
                self.telemetry.record(
                    "gemini_cache_hit",
                    query=text,
                    action=cached.action.value,
                )
            log.info("Gemini cache hit for %r -> action=%s", text, cached.action.value)
            return cached

        allowed, reason = self.quota_tracker.can_call()
        if not allowed:
            log.warning("Gemini API call blocked by quota tracker (%s): %r", reason, text)
            stats = self.quota_tracker.stats()
            if self.telemetry:
                self.telemetry.record(
                    "gemini_quota_blocked",
                    reason=reason,
                    query=text,
                    daily_calls=stats["daily_calls"],
                    daily_limit=stats["daily_limit"],
                )
            answer_msg = (
                "오늘 클라우드 질문 한도에 도달하여 답변할 수 없습니다."
                if "daily" in reason
                else "요청이 너무 많아 잠시 후 다시 시도해주세요."
            )
            return VoiceIntent(
                action=IntentAction.ANSWER_QA if any(q in text for q in ("?", "어때", "알려줘", "뭐", "몇")) else IntentAction.UNKNOWN,
                raw_text=text,
                answer=answer_msg,
                confidence=0.5,
                provider="gemini-quota-blocked",
                wake_word_detected=True,
            )

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_api_key}"
        body = {
            "system_instruction": {"parts": [{"text": _build_system_prompt()}]},
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }
        t0 = time.perf_counter()
        data = self._http_json(url, body)
        call_latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        self.quota_tracker.record_call()
        stats = self.quota_tracker.stats()
        out = data["candidates"][0]["content"]["parts"][0]["text"]
        intent = _intent_from_llm_payload(_parse_json_response(out), provider="gemini-text", raw_fallback=text)

        if self.telemetry:
            self.telemetry.record(
                "gemini_api_call",
                query=text,
                action=intent.action.value,
                latency_ms=call_latency_ms,
                daily_calls=stats["daily_calls"],
                daily_remaining=stats["daily_remaining"],
                current_rpm=stats["current_rpm"],
            )

        if intent.action != IntentAction.UNKNOWN:
            self.intent_cache.put(text, intent)

        return intent

    def _openai_transcribe(self, wav_bytes: bytes) -> str:
        boundary = f"----PocoBoxBoundary{uuid.uuid4().hex}"
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{self.openai_stt_model}\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"language\"\r\n\r\nko\r\n".encode(),
            (
                f"--{boundary}\r\n"
                "Content-Disposition: form-data; name=\"file\"; filename=\"command.wav\"\r\n"
                "Content-Type: audio/wav\r\n\r\n"
            ).encode()
            + wav_bytes
            + b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
        payload = b"".join(parts)
        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/transcriptions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.openai_api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            res = json.loads(resp.read().decode("utf-8"))
        return str(res.get("text", "")).strip()

    def _openai_text_intent(self, text: str) -> VoiceIntent:
        body = {
            "model": self.openai_chat_model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _build_system_prompt()},
                {"role": "user", "content": text},
            ],
        }
        data = self._http_json(
            "https://api.openai.com/v1/chat/completions",
            body,
            extra_headers={"Authorization": f"Bearer {self.openai_api_key}"},
        )
        out = data["choices"][0]["message"]["content"]
        return _intent_from_llm_payload(_parse_json_response(out), provider="openai", raw_fallback=text)

    def transcribe_blind_audio(self, wav_bytes: bytes) -> str:
        """Strictly literal Korean/English STT with ZERO quiz-answer context (local SenseVoice first)."""
        if self.local_stt_url:
            try:
                stt = self.transcribe_local_audio(wav_bytes)
                if stt.get("is_music_only"):
                    return ""
                return str(stt.get("text") or "").strip().strip(".,!?")
            except Exception as exc:
                log.warning("Local STT failed during quiz blind transcription (%s); falling back to cloud.", exc)
                if not self.has_cloud_api():
                    raise RuntimeError(f"Local STT error ({self.local_stt_url}): {exc}") from exc

        if self.provider == "gemini" and self.gemini_api_key:
            b64 = base64.b64encode(wav_bytes).decode("ascii")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_api_key}"
            sys_prompt = (
                "You are a strictly literal Korean/English speech-to-text transcriber.\n"
                "Rules:\n"
                "- Output ONLY JSON: {\"transcript\": \"<exact words spoken, or empty string if silence/noise>\"}\n"
                "- Transcribe EXACTLY what the speaker actually said in Korean or English, even if it is nonsense, gibberish, or unrelated words.\n"
                "- NEVER guess, invent, or autocorrect unclear speech into a meaningful word.\n"
                "- If there is only background noise, coughing, breathing, or silence, return {\"transcript\": \"\"}."
            )
            body = {
                "system_instruction": {"parts": [{"text": sys_prompt}]},
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"inline_data": {"mime_type": "audio/wav", "data": b64}},
                            {"text": "Transcribe the spoken Korean or English words verbatim as JSON."},
                        ],
                    }
                ],
                "generationConfig": {
                    "temperature": 0.0,
                    "responseMimeType": "application/json",
                },
            }
            data = self._http_json(url, body)
            raw = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = _parse_json_response(raw)
            return str(parsed.get("transcript") or "").strip()
        if self.provider == "openai" and self.openai_api_key:
            return self._openai_transcribe(wav_bytes)
        raise RuntimeError("Cloud STT API key not configured for voice quiz.")

    def transcribe_and_grade_quiz_audio(
        self,
        wav_bytes: bytes,
        *,
        english_text: str,
        korean_meaning: str,
        accepted_answers: tuple[str, ...] | list[str],
        kind: str = "story_quiz",
    ) -> dict[str, Any]:
        """2-step anti-hallucination quiz audio pipeline: blind literal STT first, then objective grading."""
        transcript = self.transcribe_blind_audio(wav_bytes)
        if not transcript:
            return {"transcript": "", "command": "none", "is_correct": None}

        cmd = classify_quiz_command(transcript)
        if cmd is not None:
            return {"transcript": transcript, "command": cmd, "is_correct": None}

        # Instant local exact/synonym check first (avoids unnecessary 2nd LLM call when clearly right)
        probe_q = QuizQuestion(
            id="_probe",
            category="all",
            kind=kind,
            english_text=english_text,
            korean_meaning=korean_meaning,
            accepted_answers=tuple(accepted_answers),
            hint_ko="",
            explanation_ko="",
        )
        if is_local_answer_match(transcript, probe_q):
            return {"transcript": transcript, "command": "answer", "is_correct": True}

        # Strict semantic fallback ONLY on the blind transcript (never leaks answer into STT!)
        is_correct = self.grade_quiz_text_answer(
            transcript,
            english_text=english_text,
            korean_meaning=korean_meaning,
            accepted_answers=accepted_answers,
            kind=kind,
        )
        return {"transcript": transcript, "command": "answer", "is_correct": bool(is_correct)}

    def grade_quiz_text_answer(
        self,
        user_text: str,
        *,
        english_text: str,
        korean_meaning: str,
        accepted_answers: tuple[str, ...] | list[str],
        kind: str = "story_quiz",
    ) -> bool | None:
        """Strict semantic LLM grading fallback on a blind transcript when local matching did not match."""
        if not user_text.strip() or self.provider == "local":
            return None
        prompt = (
            "You strictly grade a user's answer to an English listening quiz.\n"
            "Rules:\n"
            "- Return is_correct=true ONLY if the user's answer clearly names or directly means the EXACT expected answer (in Korean or English, allowing natural synonyms or polite Korean endings).\n"
            "- Return is_correct=false for wrong answers, random words, gibberish, vague guesses, or anything that does not actually mean the expected answer.\n"
            f"English question: {english_text}\n"
            f"Expected answer: {korean_meaning} (accepted: {', '.join(accepted_answers)})\n"
            f"User's spoken transcript: {user_text}\n"
            'Return ONLY JSON: {"is_correct": true} or {"is_correct": false}'
        )
        try:
            if self.provider == "gemini" and self.gemini_api_key:
                allowed, reason = self.quota_tracker.can_call()
                if not allowed:
                    log.warning("Gemini quiz grading blocked by quota (%s)", reason)
                    return None
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_api_key}"
                body = {
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"},
                }
                data = self._http_json(url, body)
                self.quota_tracker.record_call()
                parsed = _parse_json_response(data["candidates"][0]["content"]["parts"][0]["text"])
                return bool(parsed.get("is_correct")) if isinstance(parsed.get("is_correct"), bool) else None
            if self.provider == "openai" and self.openai_api_key:
                body = {
                    "model": self.openai_chat_model,
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": prompt}],
                }
                data = self._http_json(
                    "https://api.openai.com/v1/chat/completions",
                    body,
                    extra_headers={"Authorization": f"Bearer {self.openai_api_key}"},
                )
                parsed = _parse_json_response(data["choices"][0]["message"]["content"])
                return bool(parsed.get("is_correct")) if isinstance(parsed.get("is_correct"), bool) else None
        except Exception:
            return None
        return None

    def generate_kids_quiz_questions(
        self,
        topic: str = "all",
        count: int = 5,
        exclude_english: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Optionally generate fresh kids/beginner English listening questions via Gemini/OpenAI."""
        if self.provider == "local":
            return []
        excluded = ", ".join((exclude_english or [])[:25]) or "none"
        prompt = (
            f"Create {count} fun, engaging English listening quiz questions for Korean elementary/beginner-intermediate (A2) learners.\n"
            f"Topic: {topic}\n"
            f"Avoid these already-used English texts: {excluded}\n"
            "Each 'english_text' MUST be 2 to 3 natural, vivid English sentences (a short mini-story, description, or riddle) ending with a clear question (e.g. 'What animal am I?', 'Where did he go?', 'What am I?').\n"
            "Provide both English and Korean in 'korean_meaning' (e.g. 'Umbrella (우산)') and include both English and Korean variants in 'accepted_answers'.\n"
            "Keep 'hint_ko' and 'explanation_ko' in warm, encouraging Korean (1-2 short sentences, no markdown).\n"
            'Return ONLY JSON: {"questions": [{"kind": "story_quiz", "category": "...", "english_text": "...", "korean_meaning": "...", "accepted_answers": ["..."], "hint_ko": "...", "explanation_ko": "..."}]}'
        )
        try:
            if self.provider == "gemini" and self.gemini_api_key:
                allowed, reason = self.quota_tracker.can_call()
                if not allowed:
                    log.warning("Gemini quiz question generation blocked by quota (%s)", reason)
                    return []
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_api_key}"
                body = {
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.7, "responseMimeType": "application/json"},
                }
                data = self._http_json(url, body)
                self.quota_tracker.record_call()
                parsed = _parse_json_response(data["candidates"][0]["content"]["parts"][0]["text"])
                qs = parsed.get("questions")
                return qs if isinstance(qs, list) else []
            if self.provider == "openai" and self.openai_api_key:
                body = {
                    "model": self.openai_chat_model,
                    "temperature": 0.7,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": prompt}],
                }
                data = self._http_json(
                    "https://api.openai.com/v1/chat/completions",
                    body,
                    extra_headers={"Authorization": f"Bearer {self.openai_api_key}"},
                )
                parsed = _parse_json_response(data["choices"][0]["message"]["content"])
                qs = parsed.get("questions")
                return qs if isinstance(qs, list) else []
        except Exception:
            return []
        return []

    def _http_json(
        self,
        url: str,
        payload: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)

        req_timeout = timeout if timeout is not None else self.timeout
        max_attempts = 2
        for attempt in range(max_attempts):
            if self._session is not None:
                try:
                    resp = self._session.post(url, json=payload, headers=headers, timeout=req_timeout)
                except Exception as exc:
                    if attempt + 1 < max_attempts:
                        time.sleep(0.35)
                        continue
                    raise RuntimeError(f"Cloud API network error: {exc}") from exc
                if resp.status_code in (429, 500, 502, 503, 504) and attempt + 1 < max_attempts:
                    log.warning("Cloud API HTTP %d (attempt %d/%d); retrying...", resp.status_code, attempt + 1, max_attempts)
                    time.sleep(0.35)
                    continue
                if resp.status_code >= 400:
                    raise RuntimeError(f"Cloud API HTTP {resp.status_code}: {resp.text[:300]}")
                return resp.json()

            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=req_timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
                if exc.code in (429, 500, 502, 503, 504) and attempt + 1 < max_attempts:
                    log.warning("Cloud API HTTP %d (attempt %d/%d); retrying...", exc.code, attempt + 1, max_attempts)
                    time.sleep(0.35)
                    continue
                raise RuntimeError(f"Cloud API HTTP {exc.code}: {detail}") from exc
        raise RuntimeError("Cloud API request failed after retries.")
