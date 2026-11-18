"""Lightweight Korean/English TTS playback for PocoBox Q&A answers.

Synthesizes natural spoken audio via Google Translate TTS and plays raw 24kHz mono PCM
through PulseAudio (POCO F1 speaker) via mpg123 + paplay so Squeezelite's exclusive
USB DAC ALSA stream is never disturbed.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import urllib.parse
import urllib.request

log = logging.getLogger("pocobox-voice.tts")


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
            # Split long single clause on commas or spaces
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


def speak_text(text: str, lang: str | None = None) -> bool:
    """Speak text aloud via PulseAudio (best-effort, never raises). Returns True if played."""
    if os.environ.get("ENABLE_TTS", "1").strip().lower() in ("0", "false", "no"):
        return False

    chunks = split_tts_chunks(text)
    if not chunks:
        return False

    chosen_lang = lang or os.environ.get("TTS_LANG") or _detect_tts_lang(text)
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", "/run/user/10000")
    env.setdefault("PULSE_SERVER", "unix:/run/user/10000/pulse/native")

    played_any = False
    for chunk in chunks:
        try:
            mp3_bytes = fetch_tts_mp3(chunk, lang=chosen_lang)
            dec = subprocess.run(
                ["mpg123", "-q", "-s", "-r", "24000", "--mono", "-"],
                input=mp3_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10.0,
                check=True,
            )
            if not dec.stdout:
                continue
            res = subprocess.run(
                ["paplay", "--raw", "--rate=24000", "--channels=1", "--format=s16le"],
                input=dec.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                timeout=20.0,
                check=False,
            )
            if res.returncode == 0:
                played_any = True
        except Exception as exc:
            log.warning("TTS playback failed for chunk %r: %s", chunk[:30], exc)
            break
    return played_any
