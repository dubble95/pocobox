"""Korean/English TTS playback for PocoBox Q&A and spoken responses.

Supports Microsoft Edge Neural TTS (`edge-tts`: 선희/인준/현수 등 자연스러운 한국어 목소리 + 속도/음높이 조절)
with automatic zero-dependency fallback to Google Translate TTS.
Always routes audio explicitly to POCO F1's built-in Speaker sink so Squeezelite's
exclusive USB DAC ALSA stream is never disturbed.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request

from .feedback import pulse_env, resolve_internal_speaker_sink

log = logging.getLogger("pocobox-voice.tts")

AVAILABLE_VOICES: tuple[dict[str, str], ...] = (
    {"id": "ko-KR-SunHiNeural", "label": "선희 (한국어 여성 · 추천)", "engine": "edge"},
    {"id": "ko-KR-InJoonNeural", "label": "인준 (한국어 남성 · 차분함)", "engine": "edge"},
    {"id": "ko-KR-HyunsuMultilingualNeural", "label": "현수 (한/영 다국어 남성)", "engine": "edge"},
    {"id": "en-US-JennyNeural", "label": "Jenny (English Female)", "engine": "edge"},
    {"id": "en-US-GuyNeural", "label": "Guy (English Male)", "engine": "edge"},
    {"id": "google", "label": "Google 기본 TTS (한국어 여성)", "engine": "google"},
)

VALID_VOICE_IDS: frozenset[str] = frozenset(v["id"] for v in AVAILABLE_VOICES)


def edge_tts_available() -> bool:
    try:
        import edge_tts  # type: ignore  # noqa: F401
        return True
    except Exception:
        return False


def default_tts_voice() -> str:
    env_voice = os.environ.get("TTS_VOICE", "").strip()
    if env_voice in VALID_VOICE_IDS:
        return env_voice
    return "ko-KR-SunHiNeural" if edge_tts_available() else "google"


def default_english_tts_voice() -> str:
    env_voice = os.environ.get("QUIZ_EN_VOICE", "").strip()
    if env_voice in VALID_VOICE_IDS:
        return env_voice
    return "en-US-JennyNeural" if edge_tts_available() else "google"


def format_edge_rate(rate_pct: int) -> str:
    clamped = max(-30, min(30, int(rate_pct)))
    return f"{clamped:+d}%"


def format_edge_pitch(pitch_hz: int) -> str:
    clamped = max(-15, min(15, int(pitch_hz)))
    return f"{clamped:+d}Hz"


def _detect_tts_lang(text: str) -> str:
    if re.search(r"[가-힣]", text):
        return "ko"
    return "en"


def clean_for_speech(text: str) -> str:
    """Strip markdown symbols and excessive whitespace for smooth spoken TTS."""
    s = re.sub(r"[*_`#~•]+", " ", text or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def split_tts_chunks(text: str, max_chars: int = 160) -> list[str]:
    """Split text into natural sentence chunks <= max_chars for TTS synthesis."""
    cleaned = clean_for_speech(text)
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]

    parts = re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s+|(?<=요\.)\s+", cleaned)
    chunks: list[str] = []
    cur = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) > max_chars:
            subparts = re.split(r",\s*", part)
            for sp in subparts:
                sp = sp.strip()
                while len(sp) > max_chars:
                    cut = sp.rfind(" ", 0, max_chars)
                    if cut <= 0:
                        cut = max_chars
                    if cur:
                        chunks.append(cur)
                        cur = ""
                    chunks.append(sp[:cut].strip())
                    sp = sp[cut:].strip()
                if sp:
                    if cur and len(cur) + 1 + len(sp) <= max_chars:
                        cur = f"{cur}, {sp}"
                    else:
                        if cur:
                            chunks.append(cur)
                        cur = sp
        else:
            if cur and len(cur) + 1 + len(part) <= max_chars:
                cur = f"{cur} {part}"
            else:
                if cur:
                    chunks.append(cur)
                cur = part
    if cur:
        chunks.append(cur)
    return chunks


def fetch_tts_mp3(chunk: str, lang: str = "ko", timeout: float = 8.0) -> bytes:
    url = "https://translate.google.com/translate_tts?" + urllib.parse.urlencode({
        "ie": "UTF-8",
        "client": "tw-ob",
        "tl": lang,
        "q": chunk,
    })
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Linux; PocoBox)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_edge_tts_mp3(
    text: str,
    voice: str = "ko-KR-SunHiNeural",
    rate_pct: int = 0,
    pitch_hz: int = 0,
    timeout: float = 12.0,
) -> bytes:
    """Synthesize MP3 audio in memory via Microsoft Edge Neural TTS."""
    import edge_tts  # type: ignore

    rate_str = format_edge_rate(rate_pct)
    pitch_str = format_edge_pitch(pitch_hz)

    async def _synth() -> bytes:
        comm = edge_tts.Communicate(text, voice=voice, rate=rate_str, pitch=pitch_str)
        buf = bytearray()
        async for chunk in comm.stream():
            if chunk.get("type") == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)

    return asyncio.run(asyncio.wait_for(_synth(), timeout=timeout))


def _synthesize_chunk_mp3(chunk: str, lang: str, voice: str, rate_pct: int, pitch_hz: int) -> bytes:
    if voice != "google" and edge_tts_available():
        try:
            data = fetch_edge_tts_mp3(chunk, voice=voice, rate_pct=rate_pct, pitch_hz=pitch_hz)
            if data:
                return data
        except Exception as exc:
            log.warning("Edge TTS failed (voice=%s), falling back to Google TTS: %s", voice, exc)
    return fetch_tts_mp3(chunk, lang=lang)


def synthesize_pcm(
    text: str,
    lang: str,
    voice: str,
    rate_pct: int,
    pitch_hz: int,
) -> bytes:
    """Synthesize text into 24kHz mono s16le PCM bytes (best-effort, returns b'' on failure)."""
    max_chars = 600 if (voice != "google" and edge_tts_available()) else 160
    chunks = split_tts_chunks(text, max_chars=max_chars)
    if not chunks:
        return b""
    pcm_parts: list[bytes] = []
    for chunk in chunks:
        try:
            mp3_bytes = _synthesize_chunk_mp3(chunk, lang, voice, rate_pct, pitch_hz)
            dec = subprocess.run(
                ["mpg123", "-q", "-s", "-r", "24000", "--mono", "-"],
                input=mp3_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10.0,
                check=True,
            )
            if dec.stdout:
                pcm_parts.append(dec.stdout)
        except Exception as exc:
            log.warning("TTS synthesis failed for chunk %r: %s", chunk[:30], exc)
            break
    return b"".join(pcm_parts)


def play_pcm(pcm_bytes: bytes, sink: str | None = None, env: dict[str, str] | None = None) -> tuple[bool, str | None]:
    """Play 24kHz mono s16le PCM via PulseAudio built-in speaker sink."""
    if not pcm_bytes:
        return False, sink
    pa_env = env if env is not None else pulse_env()
    cur_sink = sink if sink is not None else resolve_internal_speaker_sink(pa_env)
    cmd = ["paplay", "--raw", "--rate=24000", "--channels=1", "--format=s16le"]
    if cur_sink:
        cmd.append(f"--device={cur_sink}")
    try:
        res = subprocess.run(
            cmd,
            input=pcm_bytes,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=pa_env,
            timeout=25.0,
            check=False,
        )
        if res.returncode != 0 and cur_sink:
            cur_sink = resolve_internal_speaker_sink(pa_env, force_refresh=True)
            retry_cmd = ["paplay", "--raw", "--rate=24000", "--channels=1", "--format=s16le"]
            if cur_sink:
                retry_cmd.append(f"--device={cur_sink}")
            res = subprocess.run(
                retry_cmd,
                input=pcm_bytes,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=pa_env,
                timeout=25.0,
                check=False,
            )
        return res.returncode == 0, cur_sink
    except Exception as exc:
        log.warning("TTS playback failed: %s", exc)
        return False, cur_sink


def speak_text(
    text: str,
    lang: str | None = None,
    *,
    voice: str | None = None,
    rate_pct: int | None = None,
    pitch_hz: int | None = None,
    enabled: bool | None = None,
) -> bool:
    """Speak text aloud via POCO F1 built-in speaker (best-effort, never raises). Returns True if played."""
    if enabled is False:
        return False
    if enabled is None and os.environ.get("ENABLE_TTS", "1").strip().lower() in ("0", "false", "no"):
        return False

    chosen_voice = voice if (voice in VALID_VOICE_IDS) else default_tts_voice()
    chosen_rate = int(rate_pct) if rate_pct is not None else int(os.environ.get("TTS_RATE_PCT", "0"))
    chosen_pitch = int(pitch_hz) if pitch_hz is not None else int(os.environ.get("TTS_PITCH_HZ", "0"))
    chosen_lang = lang or os.environ.get("TTS_LANG") or _detect_tts_lang(text)

    pcm = synthesize_pcm(text, chosen_lang, chosen_voice, chosen_rate, chosen_pitch)
    if not pcm:
        return False
    env = pulse_env()
    sink = resolve_internal_speaker_sink(env)
    ok, _ = play_pcm(pcm, sink=sink, env=env)
    return ok


def speak_segments(
    segments: list[tuple[str, str]],
    *,
    ko_voice: str | None = None,
    en_voice: str | None = None,
    ko_rate_pct: int | None = None,
    en_rate_pct: int = -8,
    slow_en_rate_pct: int = -18,
    pitch_hz: int | None = None,
    enabled: bool | None = None,
) -> bool:
    """Speak a sequence of bilingual TTS segments: ('ko'|'en'|'en_slow'|'pause', value).

    Pre-synthesizes all text segments in parallel so switching between native Korean
    and native English neural voices plays back seamlessly with zero network gaps.
    """
    if enabled is False:
        return False
    if enabled is None and os.environ.get("ENABLE_TTS", "1").strip().lower() in ("0", "false", "no"):
        return False
    if not segments:
        return False

    chosen_ko = ko_voice if (ko_voice in VALID_VOICE_IDS) else default_tts_voice()
    if chosen_ko.startswith("en-"):
        chosen_ko = "ko-KR-SunHiNeural" if edge_tts_available() else "google"
    chosen_en = en_voice if (en_voice in VALID_VOICE_IDS) else default_english_tts_voice()
    if chosen_en.startswith("ko-") and chosen_en != "ko-KR-HyunsuMultilingualNeural":
        chosen_en = default_english_tts_voice()
    resolved_ko_rate = int(ko_rate_pct) if ko_rate_pct is not None else int(os.environ.get("TTS_RATE_PCT", "0"))
    resolved_pitch = int(pitch_hz) if pitch_hz is not None else int(os.environ.get("TTS_PITCH_HZ", "0"))

    # Build synthesis specs so we can prefetch PCM concurrently while preserving exact playback order
    tasks: list[tuple[int, str, str, str, int, int]] = []
    for idx, (role, val) in enumerate(segments):
        if role == "pause":
            continue
        text = (val or "").strip()
        if not text:
            continue
        if role in ("en", "en_slow"):
            rate = slow_en_rate_pct if role == "en_slow" else en_rate_pct
            tasks.append((idx, text, "en", chosen_en, rate, 0))
        else:
            tasks.append((idx, text, "ko", chosen_ko, resolved_ko_rate, resolved_pitch))

    pcm_by_idx: dict[int, bytes] = {}
    if tasks:
        workers = min(4, len(tasks))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {
                pool.submit(synthesize_pcm, text, lang, voice, rate, pitch): idx
                for idx, text, lang, voice, rate, pitch in tasks
            }
            for fut, idx in futs.items():
                try:
                    pcm_by_idx[idx] = fut.result()
                except Exception as exc:
                    log.warning("Parallel TTS segment %d failed: %s", idx, exc)
                    pcm_by_idx[idx] = b""

    env = pulse_env()
    sink = resolve_internal_speaker_sink(env)
    played_any = False
    for idx, (role, val) in enumerate(segments):
        if role == "pause":
            try:
                sec = max(0.05, min(2.0, float(val)))
                time.sleep(sec)
            except Exception:
                time.sleep(0.25)
            continue
        pcm = pcm_by_idx.get(idx, b"")
        if not pcm:
            continue
        ok, sink = play_pcm(pcm, sink=sink, env=env)
        if ok:
            played_any = True
    return played_any
