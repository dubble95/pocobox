"""Korean/English TTS playback for PocoBox Q&A and spoken responses.

Supports Microsoft Edge Neural TTS (`edge-tts`: 선희/인준/현수 등 자연스러운 한국어 목소리 + 속도/음높이 조절)
with automatic zero-dependency fallback to Google Translate TTS.
Always routes audio explicitly to POCO F1's built-in Speaker sink so Squeezelite's
exclusive USB DAC ALSA stream is never disturbed.
"""

from __future__ import annotations

import array
import asyncio
import concurrent.futures
import io
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import wave

from .feedback import pulse_env, resolve_internal_speaker_sink

log = logging.getLogger("pocobox-voice.tts")

AVAILABLE_VOICES: tuple[dict[str, str], ...] = (
    {"id": "ko-KR-SunHiNeural", "label": "선희 (한국어 여성 · 추천)", "engine": "edge"},
    {"id": "ko-KR-InJoonNeural", "label": "인준 (한국어 남성 · 차분함)", "engine": "edge"},
    {"id": "ko-KR-HyunsuMultilingualNeural", "label": "현수 (한/영 다국어 남성)", "engine": "edge"},
    {"id": "en-US-JennyNeural", "label": "Jenny (English Female)", "engine": "edge"},
    {"id": "en-US-GuyNeural", "label": "Guy (English Male)", "engine": "edge"},
    {"id": "google", "label": "Google 기본 TTS (한국어 여성)", "engine": "google"},
    {"id": "kokoro-en", "label": "Kokoro (로컬 고음질 영어 · 오프라인)", "engine": "kokoro"},
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


def resolve_bilingual_voices(voice: str | None = None, en_voice: str | None = None) -> tuple[str, str]:
    """Resolve paired (korean_voice, english_voice) for seamless mixed-language TTS."""
    base = voice if (voice in VALID_VOICE_IDS) else default_tts_voice()
    explicit_en = en_voice if (en_voice in VALID_VOICE_IDS) else os.environ.get("TTS_EN_VOICE", "").strip()
    if explicit_en not in VALID_VOICE_IDS:
        explicit_en = ""

    if explicit_en == "kokoro-en":
        return (base if base != "kokoro-en" else default_tts_voice()), explicit_en
    if base == "kokoro-en":
        return default_tts_voice(), base

    if base == "google" or not edge_tts_available():
        return "google", "google"
    if base == "ko-KR-HyunsuMultilingualNeural":
        # Microsoft MultilingualNeural natively handles inline Korean/English code-switching
        if explicit_en and explicit_en != base:
            return base, explicit_en
        return base, base

    if base == "ko-KR-InJoonNeural":
        return base, (explicit_en or "en-US-GuyNeural")
    if base == "ko-KR-SunHiNeural":
        return base, (explicit_en or default_english_tts_voice())
    if base == "en-US-GuyNeural":
        return "ko-KR-InJoonNeural", base
    if base == "en-US-JennyNeural":
        return "ko-KR-SunHiNeural", base
    return base, (explicit_en or default_english_tts_voice())


def format_edge_rate(rate_pct: int) -> str:
    clamped = max(-30, min(30, int(rate_pct)))
    return f"{clamped:+d}%"


def format_edge_pitch(pitch_hz: int) -> str:
    clamped = max(-15, min(15, int(pitch_hz)))
    return f"{clamped:+d}Hz"


_LATIN_CHARS = r"A-Za-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F\u1E00-\u1EFF"
_LATIN_LETTER_RE = re.compile(rf"[{_LATIN_CHARS}]")
_HANGUL_RE = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")

# Measurement/SI units attached directly to digits in Korean prose (e.g. 15°C, 3m/s, 10km, 48kHz)
_KO_ATTACHED_UNIT_RE = re.compile(
    rf"(?<![{_LATIN_CHARS}])"
    r"\d+(?:\.\d+)?"
    r"(?:\s*°\s*[CFcf]|(?:km/h|m/s|km|cm|mm|kg|mg|ml|ms|khz|mhz|ghz|hz|gb|mb|kb|tb|db|fps|bpm))"
    rf"(?![{_LATIN_CHARS}0-9])",
    re.IGNORECASE,
)

# Individual Latin/English word atom (handles apostrophes, hyphens, sharps, initials, abbreviations, 24K/GPT-4o)
_LATIN_ATOM_RE = re.compile(
    rf"(?:(?<![가-힣0-9{_LATIN_CHARS}])\d+(?:\.\d+)?(?=[{_LATIN_CHARS}]))?"
    rf"[{_LATIN_CHARS}]+"
    rf"(?:['’\-/#+&.]?[{_LATIN_CHARS}0-9]+)*"
    r"(?:[#]|(?<![.])\.)?",
)

# Korean counters immediately following a number (prevent absorbing "2024년", "9번", "1악장" into English spans)
_KO_COUNTER_AFTER_NUM_RE = re.compile(
    r"^(?:년|월|일|시|분|초|번째|번|개|명|살|도|원|곡|회|층|위|호|대|권|장|잔|병|마리|가지|배|차|편|화|기|절|악장|퍼센트)"
)

_LEADING_EN_NUM_RE = re.compile(r"(?:^|(?<=[\s(\[\"'“‘:,]))(\d+(?:\.\s*|\s+))$")
_TRAILING_EN_NUM_RE = re.compile(r"^([\s:-]+\d+(?:[.:]\d+)?(?:st|nd|rd|th)?)")
_HAS_SPEAKABLE_RE = re.compile(rf"[가-힣ㄱ-ㅎㅏ-ㅣ0-9{_LATIN_CHARS}]")
_EN_ABBREV_END_RE = re.compile(r"\b(?:no|op|mr|mrs|ms|dr|st|feat|ft|vs|pt|vol|jr|sr|prof|[a-z])\.$", re.IGNORECASE)


def _detect_tts_lang(text: str) -> str:
    if _HANGUL_RE.search(text):
        return "ko"
    if _LATIN_LETTER_RE.search(text):
        return "en"
    return "ko"


def clean_for_speech(text: str) -> str:
    """Strip markdown symbols and excessive whitespace for smooth spoken TTS.

    Preserves musical sharp signs attached to note names (e.g. 'C# Minor', 'F# Major').
    """
    s = re.sub(r"[*_`~•]+", " ", text or "")
    s = re.sub(r"(?<![A-Ga-g])#+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _boundary_pause_ms(raw_boundary: str, prev_text: str = "") -> int:
    """Compute a natural micro-pause in ms between adjacent bilingual segments."""
    if any(ch in raw_boundary for ch in ".!?。"):
        return 170
    if prev_text and prev_text[-1] in ".!?。" and not _EN_ABBREV_END_RE.search(prev_text):
        return 170
    if any(ch in raw_boundary for ch in ",:;-—–()[]"):
        return 100
    if prev_text and prev_text[-1] in ",:;-—–":
        return 100
    if not raw_boundary or not raw_boundary[0].isspace():
        return 20
    return 55


def _strip_unmatched_boundary_brackets(text: str, *, side: str) -> tuple[str, bool]:
    """Strip stray unmatched brackets or quotes at the boundary facing an adjacent English span."""
    stripped_bracket = False
    if side == "right":
        while text:
            ch = text[-1]
            if ch in "([“‘":
                closer = { "(": ")", "[": "]", "“": "”", "‘": "’" }[ch]
                if text.count(ch) > text.count(closer):
                    text = text[:-1].rstrip()
                    if ch in "([":
                        stripped_bracket = True
                    continue
            elif ch in "\"'":
                if (text.count(ch) % 2) == 1:
                    text = text[:-1].rstrip()
                    continue
            break
    elif side == "left":
        while text:
            ch = text[0]
            if ch in ")]”’":
                opener = { ")": "(", "]": "[", "”": "“", "’": "‘" }[ch]
                if text.count(ch) > text.count(opener):
                    text = text[1:].lstrip()
                    if ch in ")]":
                        stripped_bracket = True
                    continue
            elif ch in "\"'":
                if (text.count(ch) % 2) == 1:
                    text = text[1:].lstrip()
                    continue
            break
    return text, stripped_bracket


def _split_bilingual_plan(text: str) -> list[tuple[str, str, int]]:
    """Split mixed Korean/English text into [(lang, text, gap_after_ms), ...].

    Keeps Korean units (15°C, 3m/s, 10km) and single-letter Korean labels (A팀, B형) in Korean,
    while grouping multi-word English/European titles, numbers, and symbols (e.g.
    'Rachmaninoff: Piano Concerto No. 2 in C# Minor, Op. 18: 1. Moderato', 'ROSÉ & Bruno Mars',
    '7 Rings') into native English segments.
    """
    cleaned = clean_for_speech(text)
    if not cleaned:
        return []

    has_ko = bool(_HANGUL_RE.search(cleaned))
    has_latin = bool(_LATIN_LETTER_RE.search(cleaned))
    if not has_latin:
        return [("ko", cleaned, 0)]
    if not has_ko:
        return [("en", cleaned, 0)]

    # Identify measurement units attached directly to digits so they stay in Korean
    unit_spans = [m.span() for m in _KO_ATTACHED_UNIT_RE.finditer(cleaned)]

    def _inside_unit(start: int, end: int) -> bool:
        return any(us <= start and end <= ue for us, ue in unit_spans)

    raw_atoms: list[tuple[int, int]] = []
    for m in _LATIN_ATOM_RE.finditer(cleaned):
        s, e = m.span()
        if _inside_unit(s, e):
            continue
        raw_atoms.append((s, e))

    if not raw_atoms:
        return [("ko", cleaned, 0)]

    # Merge adjacent Latin spans whenever the text between them contains no Hangul
    merged: list[list[int]] = [[raw_atoms[0][0], raw_atoms[0][1]]]
    for s, e in raw_atoms[1:]:
        prev_e = merged[-1][1]
        between = cleaned[prev_e:s]
        if not _HANGUL_RE.search(between):
            merged[-1][1] = e
        else:
            merged.append([s, e])

    # Expand spans to absorb adjacent bare numbers (e.g. '7 Rings', 'No. 1', 'Remastered 2023')
    # and close any matching parenthesis/bracket opened inside the span
    en_spans: list[tuple[int, int]] = []
    for idx, (s, e) in enumerate(merged):
        prev_limit = en_spans[-1][1] if en_spans else 0
        # Leading bare number (not attached to Hangul)
        prefix_slice = cleaned[prev_limit:s]
        lm = _LEADING_EN_NUM_RE.search(prefix_slice)
        if lm:
            s -= len(lm.group(1))

        # Trailing bare number (unless followed by a Korean counter like 년/번/악장)
        next_limit = merged[idx + 1][0] if (idx + 1 < len(merged)) else len(cleaned)
        suffix_slice = cleaned[e:next_limit]
        tm = _TRAILING_EN_NUM_RE.match(suffix_slice)
        if tm:
            num_part = tm.group(1)
            after_num = suffix_slice[len(num_part):]
            if not _KO_COUNTER_AFTER_NUM_RE.match(after_num):
                e += len(num_part)

        # Balance closing ')' / ']' / quotes if opener is inside the English span
        span_txt = cleaned[s:e]
        while e < next_limit and cleaned[e] in ")]”’\"'":
            ch = cleaned[e]
            if ch in ")]”’":
                opener = {")": "(", "]": "[", "”": "“", "’": "‘"}[ch]
                if span_txt.count(opener) > span_txt.count(ch):
                    e += 1
                    span_txt = cleaned[s:e]
                    continue
            elif ch in "\"'":
                if (span_txt.count(ch) % 2) == 1:
                    e += 1
                    span_txt = cleaned[s:e]
                    continue
            break

        # Require at least 2 Latin letters to avoid jarring single-letter switches (A팀, B형, K팝)
        if len(_LATIN_LETTER_RE.findall(cleaned[s:e])) >= 2:
            en_spans.append((s, e))

    if not en_spans:
        return [("ko", cleaned, 0)]

    # Assemble alternating Korean and English segments with natural boundary gaps
    plan: list[list[object]] = []  # [lang, text, gap_after_ms]
    cursor = 0
    for s, e in en_spans:
        ko_raw = cleaned[cursor:s]
        if ko_raw:
            lead_punct_zone = ""
            ko_work = ko_raw
            if plan:
                # Trim leading stray closers / punctuation that follow the previous English segment
                ko_work, had_closer = _strip_unmatched_boundary_brackets(ko_work.lstrip(), side="left")
                if had_closer:
                    lead_punct_zone += ")"
                m_lead = re.match(r"^([,:;.!?。\-—–\s]+)", ko_work)
                if m_lead:
                    lead_punct_zone += m_lead.group(1)
                    ko_work = ko_work[m_lead.end():]
                # Preserve exact whitespace info when no leading punct was stripped
                boundary_for_prev = lead_punct_zone if lead_punct_zone else ko_raw[:1]
                prev_gap = _boundary_pause_ms(boundary_for_prev, str(plan[-1][1]))
                plan[-1][2] = max(int(plan[-1][2]), prev_gap)

            ko_work_r = ko_work.rstrip()
            ko_work_r, had_opener = _strip_unmatched_boundary_brackets(ko_work_r, side="right")
            ko_clean = ko_work_r.strip()
            if ko_clean and _HAS_SPEAKABLE_RE.search(ko_clean):
                trail_boundary = ("(" if had_opener else "") + ko_raw[len(ko_raw.rstrip()):]
                gap_before_en = _boundary_pause_ms(trail_boundary, ko_clean)
                plan.append(["ko", ko_clean, gap_before_en])
            elif plan and had_opener:
                plan[-1][2] = max(int(plan[-1][2]), 100)

        en_text = cleaned[s:e].strip()
        if en_text:
            plan.append(["en", en_text, 0])
        cursor = e

    # Trailing Korean text after the last English span
    tail_raw = cleaned[cursor:]
    if tail_raw:
        lead_punct_zone = ""
        tail_work = tail_raw
        if plan:
            tail_work, had_closer = _strip_unmatched_boundary_brackets(tail_work.lstrip(), side="left")
            if had_closer:
                lead_punct_zone += ")"
            m_lead = re.match(r"^([,:;.!?。\-—–\s]+)", tail_work)
            if m_lead:
                lead_punct_zone += m_lead.group(1)
                tail_work = tail_work[m_lead.end():]
            boundary_for_prev = lead_punct_zone if lead_punct_zone else tail_raw[:1]
            prev_gap = _boundary_pause_ms(boundary_for_prev, str(plan[-1][1]))
            plan[-1][2] = max(int(plan[-1][2]), prev_gap)
        tail_clean = tail_work.strip()
        if tail_clean and _HAS_SPEAKABLE_RE.search(tail_clean):
            plan.append(["ko", tail_clean, 0])

    if not plan:
        return [("ko", cleaned, 0)]

    # Coalesce any adjacent same-language entries (e.g. if a punctuation-only gap was skipped)
    coalesced: list[tuple[str, str, int]] = []
    for lang, seg_text, gap_ms in plan:
        s_lang = str(lang)
        s_text = str(seg_text)
        s_gap = int(gap_ms)
        if coalesced and coalesced[-1][0] == s_lang:
            prev_lang, prev_text, _ = coalesced[-1]
            coalesced[-1] = (prev_lang, f"{prev_text} {s_text}", s_gap)
        else:
            coalesced.append((s_lang, s_text, s_gap))

    # Last segment does not need trailing gap
    if coalesced:
        last_lang, last_text, _ = coalesced[-1]
        coalesced[-1] = (last_lang, last_text, 0)
    return coalesced


def split_bilingual_segments(text: str) -> list[tuple[str, str]]:
    """Split text into [(lang, text), ...] segments for native Korean/English pronunciation."""
    return [(lang, seg_text) for lang, seg_text, _ in _split_bilingual_plan(text)]


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


def fetch_kokoro_tts_wav(
    text: str,
    *,
    sid: int = 0,
    speed: float = 1.0,
    url: str | None = None,
    timeout: float = 15.0,
) -> bytes:
    """Synthesize 24kHz mono PCM16 WAV audio via local Kokoro TTS microservice (127.0.0.1:8766/tts)."""
    endpoint = url or os.environ.get("LOCAL_TTS_URL", "http://127.0.0.1:8766/tts")
    payload = json.dumps({"text": text, "sid": sid, "speed": speed}).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Kokoro TTS HTTP {resp.status}")
        return resp.read()


def _decode_wav_pcm16_bytes(wav_bytes: bytes) -> bytes:
    """Extract raw PCM bytes from a 24kHz mono PCM16 WAV container."""
    if not wav_bytes:
        return b""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            return wf.readframes(wf.getnframes())
    except Exception as exc:
        log.warning("Failed to decode Kokoro WAV container: %s", exc)
        return b""


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
    if voice == "kokoro-en" or voice.startswith("kokoro"):
        try:
            wav_bytes = fetch_kokoro_tts_wav(text)
            if wav_bytes:
                pcm = _decode_wav_pcm16_bytes(wav_bytes)
                if pcm:
                    return pcm
        except Exception as exc:
            log.warning("Kokoro local TTS failed for %r, falling back to edge/google: %s", text[:30], exc)
            fallback_voice = default_english_tts_voice() if lang == "en" else default_tts_voice()
            if fallback_voice == "kokoro-en":
                fallback_voice = "en-US-JennyNeural" if edge_tts_available() else "google"
            return synthesize_pcm(text, lang, fallback_voice, rate_pct, pitch_hz)

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


def _silence_pcm(ms: int, sample_rate: int = 24000) -> bytes:
    """Generate silent 24kHz mono s16le PCM bytes of duration ms."""
    if ms <= 0:
        return b""
    samples = int(round(sample_rate * ms / 1000.0))
    return b"\x00\x00" * samples


def _trim_pcm_silence(
    pcm: bytes,
    sample_rate: int = 24000,
    peak_threshold: int = 180,
    pad_start_ms: int = 20,
    pad_end_ms: int = 30,
) -> bytes:
    """Trim leading and trailing MP3/TTS silence from 24kHz mono s16le PCM while preserving consonants."""
    frame_samples = max(1, sample_rate // 100)  # 10ms frame
    frame_bytes = frame_samples * 2
    if len(pcm) < frame_bytes * 2 or (len(pcm) % 2) != 0:
        return pcm

    samples = array.array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()

    total_samples = len(samples)
    first_active: int | None = None
    last_active: int | None = None
    neg_threshold = -peak_threshold

    for i in range(0, total_samples, frame_samples):
        chunk = samples[i : i + frame_samples]
        if chunk and (max(chunk) >= peak_threshold or min(chunk) <= neg_threshold):
            first_active = i
            break

    if first_active is None:
        return pcm

    last_start = ((total_samples - 1) // frame_samples) * frame_samples
    for i in range(last_start, first_active - 1, -frame_samples):
        chunk = samples[i : i + frame_samples]
        if chunk and (max(chunk) >= peak_threshold or min(chunk) <= neg_threshold):
            last_active = i
            break

    if last_active is None:
        return pcm

    pad_start = (sample_rate * pad_start_ms) // 1000
    pad_end = (sample_rate * pad_end_ms) // 1000
    start_idx = max(0, first_active - pad_start)
    end_idx = min(total_samples, last_active + frame_samples + pad_end)
    return pcm[start_idx * 2 : end_idx * 2]


def synthesize_bilingual_pcm(
    text: str,
    *,
    voice: str | None = None,
    en_voice: str | None = None,
    rate_pct: int = 0,
    pitch_hz: int = 0,
) -> bytes:
    """Synthesize Korean/English text into one seamless 24kHz mono s16le PCM stream.

    English phrases (including song titles, artists, classical opus numbers, and accents)
    are synthesized concurrently with a native English neural voice and stitched together
    with trimmed MP3 padding and natural micro-pauses.
    """
    ko_voice, resolved_en_voice = resolve_bilingual_voices(voice, en_voice=en_voice)
    if ko_voice == resolved_en_voice and ko_voice != "google":
        return synthesize_pcm(text, _detect_tts_lang(text), ko_voice, rate_pct, pitch_hz)

    plan = _split_bilingual_plan(text)
    if not plan:
        return b""

    if len(plan) == 1:
        seg_lang, seg_text, _ = plan[0]
        seg_voice = resolved_en_voice if seg_lang == "en" else ko_voice
        return synthesize_pcm(seg_text, seg_lang, seg_voice, rate_pct, pitch_hz)

    pcm_by_idx: dict[int, bytes] = {}
    workers = min(4, len(plan))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {}
        for idx, (seg_lang, seg_text, _gap_ms) in enumerate(plan):
            seg_voice = resolved_en_voice if seg_lang == "en" else ko_voice
            futs[pool.submit(synthesize_pcm, seg_text, seg_lang, seg_voice, rate_pct, pitch_hz)] = idx
        for fut, idx in futs.items():
            try:
                pcm_by_idx[idx] = fut.result()
            except Exception as exc:
                log.warning("Bilingual TTS segment %d failed: %s", idx, exc)
                pcm_by_idx[idx] = b""

    out_parts: list[bytes] = []
    last_nonempty_idx = -1
    for idx in range(len(plan)):
        if pcm_by_idx.get(idx):
            last_nonempty_idx = idx

    for idx, (_seg_lang, _seg_text, gap_after_ms) in enumerate(plan):
        raw_pcm = pcm_by_idx.get(idx, b"")
        if not raw_pcm:
            continue
        trimmed = _trim_pcm_silence(raw_pcm)
        if not trimmed:
            continue
        out_parts.append(trimmed)
        if idx < last_nonempty_idx and gap_after_ms > 0:
            out_parts.append(_silence_pcm(gap_after_ms))

    return b"".join(out_parts)


def speak_text(
    text: str,
    lang: str | None = None,
    *,
    voice: str | None = None,
    en_voice: str | None = None,
    rate_pct: int | None = None,
    pitch_hz: int | None = None,
    enabled: bool | None = None,
    bilingual: bool = True,
) -> bool:
    """Speak text aloud via POCO F1 built-in speaker (best-effort, never raises). Returns True if played."""
    if enabled is False:
        return False
    if enabled is None and os.environ.get("ENABLE_TTS", "1").strip().lower() in ("0", "false", "no"):
        return False

    chosen_voice = voice if (voice in VALID_VOICE_IDS) else default_tts_voice()
    chosen_rate = int(rate_pct) if rate_pct is not None else int(os.environ.get("TTS_RATE_PCT", "0"))
    chosen_pitch = int(pitch_hz) if pitch_hz is not None else int(os.environ.get("TTS_PITCH_HZ", "0"))
    env_lang = os.environ.get("TTS_LANG", "").strip()

    if bilingual and lang is None and not env_lang:
        pcm = synthesize_bilingual_pcm(
            text,
            voice=chosen_voice,
            en_voice=en_voice,
            rate_pct=chosen_rate,
            pitch_hz=chosen_pitch,
        )
    else:
        chosen_lang = lang or env_lang or _detect_tts_lang(text)
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
