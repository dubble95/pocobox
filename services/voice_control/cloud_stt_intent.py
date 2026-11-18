"""Hybrid Cloud STT + Intent Parser (Gemini Audio / OpenAI Whisper+LLM) with instant local rule fast-path."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
import uuid
from typing import Any

from .aliases import expand_tidal_queries, strip_wake_prefix
from .intent_schema import IntentAction, VoiceIntent
from .rule_parser import parse_command

SYSTEM_PROMPT = """You are the voice command interpreter for PocoBox, a Korean/English smart music player backed by Lyrion Music Server (LMS) and TIDAL.

The device wake word is "포코" / "포코야" / "포코박스" / "헤이 포코" / "Hey Poco" (phonetic variants like "포쿠야" or "쿠쿠야" at the start of a music command also count as wake_word_detected=true).

Listen to the audio (or read the text) and return ONLY a valid JSON object matching this exact schema:
{
  "transcript": "<exact spoken text>",
  "wake_word_detected": true,
  "action": "play_search | play_mix | play_favorite | pause | resume | next | previous | volume_set | volume_up | volume_down | mute | unmute | now_playing | unknown",
  "query": "<primary TIDAL-friendly search query, using official English/canonical artist & song titles when better indexed on TIDAL, e.g. 'IU Through the Night'>",
  "fallback_queries": ["<alternative query 1>", "<original Korean query>"],
  "search_type": "track | artist | album | playlist | any",
  "volume": null,
  "delta": null,
  "confidence": 0.95
}

Rules:
- Strip the wake word ("포코야", "포코박스", "Hey Poco", etc.) from "query" and set "wake_word_detected": true if addressed to PocoBox, otherwise false.
- For Korean artists/songs, put the official TIDAL English/canonical name in "query" and Korean/shorter variants in "fallback_queries" (e.g. "포코야 아이유 밤편지 틀어줘" -> query: "IU Through the Night", fallback_queries: ["밤편지", "IU"]).
- For playback controls (pause, resume, next, previous, volume_set/up/down, mute, unmute, now_playing), set query to "".
- For volume_up use positive delta (default 10), for volume_down use negative delta (default -10), for volume_set set volume 0..100.
- If the utterance is background music lyrics, TV chatter, noise, silence, or unrelated to music control, return action="unknown" and wake_word_detected=false.
- Return pure JSON only, no markdown fences."""


def _parse_json_response(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    return json.loads(cleaned.strip())


def _intent_from_llm_payload(payload: dict[str, Any], provider: str, raw_fallback: str = "") -> VoiceIntent:
    transcript = str(payload.get("transcript") or raw_fallback or "").strip()
    action_str = str(payload.get("action") or "unknown").strip().lower()
    query = str(payload.get("query") or "").strip()
    fallbacks = [str(x).strip() for x in (payload.get("fallback_queries") or []) if str(x).strip()]

    # Enrich with local alias expansion if query is Korean
    if query and action_str == "play_search":
        for extra in expand_tidal_queries(query):
            if extra.lower() != query.lower() and extra.lower() not in {f.lower() for f in fallbacks}:
                fallbacks.append(extra)

    _, prefix_wake = strip_wake_prefix(transcript)
    wake_detected = bool(payload.get("wake_word_detected", False)) or prefix_wake

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
    )


class HybridVoiceInterpreter:
    """Combines ultra-fast local rule parsing with Gemini or OpenAI cloud audio/text understanding."""

    def __init__(
        self,
        provider: str | None = None,
        gemini_api_key: str | None = None,
        gemini_model: str | None = None,
        openai_api_key: str | None = None,
        openai_stt_model: str | None = None,
        openai_chat_model: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.gemini_api_key = gemini_api_key if gemini_api_key is not None else os.environ.get("GEMINI_API_KEY", "").strip()
        self.gemini_model = gemini_model or os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
        self.openai_api_key = openai_api_key if openai_api_key is not None else os.environ.get("OPENAI_API_KEY", "").strip()
        self.openai_stt_model = openai_stt_model or os.environ.get("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")
        self.openai_chat_model = openai_chat_model or os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")
        self.timeout = timeout

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

    def interpret_text(self, text: str, use_cloud_fallback: bool = True) -> VoiceIntent:
        """Fast-path local rule parsing first; optionally escalate complex/unknown text to cloud LLM."""
        local_intent = parse_command(text)
        if local_intent.action != IntentAction.UNKNOWN and local_intent.confidence >= 0.9:
            return local_intent

        if not use_cloud_fallback or self.provider == "local":
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
        """Send recorded WAV audio to configured cloud provider (Gemini single-shot or OpenAI STT+fast-path)."""
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
            "Cloud STT API key not configured. Set GEMINI_API_KEY or OPENAI_API_KEY in /etc/pocobox/voice.env "
            "(or use text command testing via `pocobox-voice say \"...\"`)."
        )

    def _gemini_audio_intent(self, wav_bytes: bytes) -> VoiceIntent:
        b64 = base64.b64encode(wav_bytes).decode("ascii")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_api_key}"
        body = {
            "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"inline_data": {"mime_type": "audio/wav", "data": b64}},
                        {"text": "Transcribe and interpret this music voice command as JSON."},
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }
        data = self._http_json(url, body)
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return _intent_from_llm_payload(_parse_json_response(text), provider="gemini-audio")

    def _gemini_text_intent(self, text: str) -> VoiceIntent:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_api_key}"
        body = {
            "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {
                "temperature": 0.1,
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
                {"role": "system", "content": SYSTEM_PROMPT},
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

    def _http_json(
        self,
        url: str,
        payload: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
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
            raise RuntimeError(f"Cloud API HTTP {exc.code}: {detail}") from exc
