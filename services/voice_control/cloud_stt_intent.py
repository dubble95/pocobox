"""Hybrid Local-First (SenseVoice INT8) + Cloud STT/Intent Parser with instant offline rule fast-path."""

from __future__ import annotations

import base64
import datetime
import json
import logging
import os
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
  "action": "play_search | play_mix | play_favorite | pause | resume | next | previous | volume_set | volume_up | volume_down | mute | unmute | now_playing | answer_qa | start_quiz | stop_quiz | unknown",
  "query": "<primary TIDAL-friendly search query for play_search, else empty string>",
  "fallback_queries": ["<alternative query 1>", "<original Korean query>"],
  "search_type": "track | artist | album | playlist | any",
  "volume": null,
  "delta": null,
  "answer": "<for answer_qa ONLY: concise, friendly spoken Korean answer in 1-2 short sentences (plain text, no markdown/bullets/emojis); else empty string>",
  "confidence": 0.95
}

Rules:
- Strip the wake word ("포코야", "포코박스", "Hey Poco", etc.) from "query" and set "wake_word_detected": true ONLY if the speaker actually said the wake word.
- Use action="answer_qa" when the user asks a general question, trivia, math, definition, recommendation, music/artist info, greeting, or time/date question (e.g. "콜드플레이가 어떤 밴드야?", "프랑스 수도가 어디야?", "38 곱하기 17은?", "저녁 메뉴 추천해줘", "내일 무슨 요일이야?"). Keep "answer" short, accurate, and natural to read aloud (1-2 sentences, Korean unless the user asked in English).
- Use action="start_quiz" when the user asks to start an English quiz or English listening quiz (e.g. "영어 퀴즈 시작해줘", "영어 문제 내줘", "동물 영어 퀴즈 하자", "English quiz"). Use action="stop_quiz" when they ask to stop/end the quiz.
- If the speaker did NOT specify a song/artist/genre/mix/question (e.g. only said "포코야" or vague "이 노래 틀어줘"), return action="unknown" unless they clearly said "다시 틀어줘" (resume) or "아무거나 틀어줘" (play_mix).
- For Korean artists/songs, put the official TIDAL English/canonical name in "query" and Korean/shorter variants in "fallback_queries" (e.g. "포코야 아이유 밤편지 틀어줘" -> query: "IU Through the Night", fallback_queries: ["밤편지", "IU"]).
- For playback controls (pause, resume, next, previous, volume_set/up/down, mute, unmute, now_playing), set query and answer to "".
- For volume_up use positive delta (default 10), for volume_down use negative delta (default -10), for volume_set set volume 0..100.
- Return pure JSON only, no markdown fences."""


def _build_system_prompt() -> str:
    now = datetime.datetime.now(tz=_KST)
    wd = _KO_WEEKDAYS[now.weekday()]
    return (
        SYSTEM_PROMPT
        + f"\n\nCurrent local time (Korea Standard Time, KST): {now.strftime('%Y-%m-%d %H:%M')} ({wd})."
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
    fallbacks = [str(x).strip() for x in (payload.get("fallback_queries") or []) if str(x).strip()]

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
        cloud_fallback: bool | None = None,
    ) -> None:
        self.gemini_api_key = gemini_api_key if gemini_api_key is not None else os.environ.get("GEMINI_API_KEY", "").strip()
        self.gemini_model = gemini_model or os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
        self.openai_api_key = openai_api_key if openai_api_key is not None else os.environ.get("OPENAI_API_KEY", "").strip()
        self.openai_stt_model = openai_stt_model or os.environ.get("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")
        self.openai_chat_model = openai_chat_model or os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")
        self.timeout = timeout

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
        if cloud_fallback is not None:
            self.cloud_fallback = bool(cloud_fallback)
        else:
            self.cloud_fallback = os.environ.get("VOICE_CLOUD_FALLBACK", "1").strip().lower() not in ("0", "false", "no", "off")

        self._session = _requests.Session() if _requests is not None else None
        self._local_health_cache: tuple[float, dict[str, Any]] | None = None

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

        if not use_cloud_fallback or not self.cloud_fallback or not self.has_cloud_api():
            return local_intent

        try:
            if self.provider == "gemini" and self.gemini_api_key:
                return self._gemini_text_intent(text)
            if self.provider == "openai" and self.openai_api_key:
                return self._openai_text_intent(text)
        except Exception:
            pass
        return local_intent

    def interpret_audio(self, wav_bytes: bytes) -> VoiceIntent:
        """Local-first SenseVoice STT + offline rule parser (~0.3s); optional cloud fallback for Q&A/unknown."""
        if self.local_stt_url:
            try:
                stt = self.transcribe_local_audio(wav_bytes)
                transcript = str(stt.get("text") or "").strip()
                if stt.get("is_music_only") or not transcript:
                    return VoiceIntent(
                        action=IntentAction.UNKNOWN,
                        raw_text="",
                        confidence=0.0,
                        provider="sensevoice-local",
                        wake_word_detected=False,
                    )

                fast = parse_command(transcript)
                if fast.action != IntentAction.UNKNOWN and fast.confidence >= 0.9:
                    fast.provider = "sensevoice+rule"
                    return fast

                # Bare wake-word utterance ("포코야") -> return immediately without any cloud call
                stripped, has_wake = strip_wake_prefix(transcript)
                if has_wake and not stripped.strip(" .!?,~"):
                    fast.provider = "sensevoice+rule"
                    return fast

                # Unknown/complex command or Q&A: escalate text-only to cloud LLM if enabled
                if self.cloud_fallback and self.has_cloud_api():
                    try:
                        if self.provider == "gemini" and self.gemini_api_key:
                            cloud_intent = self._gemini_text_intent(transcript)
                            cloud_intent.provider = "sensevoice+gemini-text"
                            return cloud_intent
                        if self.provider == "openai" and self.openai_api_key:
                            cloud_intent = self._openai_text_intent(transcript)
                            cloud_intent.provider = "sensevoice+openai-text"
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
        data = self._http_json(url, body)
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return _intent_from_llm_payload(_parse_json_response(text), provider="gemini-audio")

    def _gemini_text_intent(self, text: str) -> VoiceIntent:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_api_key}"
        body = {
            "system_instruction": {"parts": [{"text": _build_system_prompt()}]},
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }
        data = self._http_json(url, body)
        out = data["candidates"][0]["content"]["parts"][0]["text"]
        return _intent_from_llm_payload(_parse_json_response(out), provider="gemini-text", raw_fallback=text)

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
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_api_key}"
                body = {
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"},
                }
                data = self._http_json(url, body)
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
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_api_key}"
                body = {
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.7, "responseMimeType": "application/json"},
                }
                data = self._http_json(url, body)
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
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)

        max_attempts = 2
        for attempt in range(max_attempts):
            if self._session is not None:
                try:
                    resp = self._session.post(url, json=payload, headers=headers, timeout=self.timeout)
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
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
                if exc.code in (429, 500, 502, 503, 504) and attempt + 1 < max_attempts:
                    log.warning("Cloud API HTTP %d (attempt %d/%d); retrying...", exc.code, attempt + 1, max_attempts)
                    time.sleep(0.35)
                    continue
                raise RuntimeError(f"Cloud API HTTP {exc.code}: {detail}") from exc
        raise RuntimeError("Cloud API request failed after retries.")
