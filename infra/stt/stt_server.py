#!/usr/bin/env python3
"""Ultra-fast local SenseVoice-Small INT8 HTTP STT microservice for PocoBox (via sherpa-onnx)."""

from __future__ import annotations

import io
import json
import logging
import os
import threading
import time
import urllib.parse
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np
import sherpa_onnx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("pocobox-stt")


def _load_wav_mono_float32(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    """Decode PCM16 WAV bytes into mono float32 samples in [-1.0, 1.0]."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)

    if sampwidth != 2:
        raise ValueError(f"Unsupported WAV sample width: {sampwidth * 8}-bit (expected 16-bit PCM)")
    if not raw:
        return np.zeros(0, dtype=np.float32), rate

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


def _strip_sensevoice_tag(val: str) -> str:
    return (val or "").strip().strip("<|>").strip()


class SenseVoiceEngine:
    """Thread-safe wrapper around sherpa-onnx SenseVoice OfflineRecognizer."""

    def __init__(
        self,
        model_dir: str,
        *,
        language: str = "auto",
        use_itn: bool = True,
        num_threads: int = 4,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.default_language = language
        self.use_itn = use_itn
        self.num_threads = max(1, num_threads)
        self._lock = threading.Lock()
        self._recognizers: dict[str, sherpa_onnx.OfflineRecognizer] = {}

        model_file = self.model_dir / "model.int8.onnx"
        if not model_file.is_file():
            model_file = self.model_dir / "model.onnx"
        tokens_file = self.model_dir / "tokens.txt"
        if not model_file.is_file() or not tokens_file.is_file():
            raise FileNotFoundError(f"SenseVoice model/tokens not found in {self.model_dir}")

        self.model_path = str(model_file)
        self.tokens_path = str(tokens_file)

        t0 = time.perf_counter()
        self._recognizers[self.default_language] = self._create_recognizer(self.default_language)
        self.load_sec = round(time.perf_counter() - t0, 3)
        log.info(
            "Loaded SenseVoice (%s, lang=%s, threads=%d, itn=%s) in %.2fs",
            model_file.name,
            self.default_language,
            self.num_threads,
            self.use_itn,
            self.load_sec,
        )

    def _create_recognizer(self, language: str) -> sherpa_onnx.OfflineRecognizer:
        return sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=self.model_path,
            tokens=self.tokens_path,
            language=language,
            use_itn=self.use_itn,
            num_threads=self.num_threads,
            debug=False,
        )

    def _get_recognizer(self, language: str) -> sherpa_onnx.OfflineRecognizer:
        lang = (language or self.default_language).strip().lower()
        if lang not in ("auto", "ko", "en", "zh", "ja", "yue"):
            lang = self.default_language
        rec = self._recognizers.get(lang)
        if rec is None:
            rec = self._create_recognizer(lang)
            self._recognizers[lang] = rec
        return rec

    def transcribe_wav(self, wav_bytes: bytes, *, language: str | None = None) -> dict[str, Any]:
        samples, sample_rate = _load_wav_mono_float32(wav_bytes)
        duration_sec = len(samples) / float(sample_rate) if sample_rate else 0.0
        if len(samples) == 0:
            return {
                "ok": True,
                "text": "",
                "lang": "",
                "event": "",
                "emotion": "",
                "is_music_only": False,
                "duration_sec": 0.0,
                "latency_ms": 0.0,
            }

        t0 = time.perf_counter()
        with self._lock:
            rec = self._get_recognizer(language or self.default_language)
            stream = rec.create_stream()
            stream.accept_waveform(sample_rate, samples)
            rec.decode_stream(stream)
            res = stream.result
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)

        text = str(getattr(res, "text", "") or "").strip()
        lang_tag = _strip_sensevoice_tag(str(getattr(res, "lang", "") or ""))
        event_tag = _strip_sensevoice_tag(str(getattr(res, "event", "") or ""))
        emotion_tag = _strip_sensevoice_tag(str(getattr(res, "emotion", "") or ""))

        is_music_only = (event_tag.upper() in ("BGM", "MUSIC", "SINGING") and not text) or (
            lang_tag.lower() == "nospeech"
        )
        return {
            "ok": True,
            "text": text,
            "lang": lang_tag,
            "event": event_tag,
            "emotion": emotion_tag,
            "is_music_only": is_music_only,
            "duration_sec": round(duration_sec, 3),
            "latency_ms": latency_ms,
        }


class SherpaTtsEngine:
    """Thread-safe wrapper around sherpa-onnx OfflineTts (supports Kokoro and VITS)."""

    def __init__(
        self,
        model_dir: str,
        *,
        num_threads: int = 4,
        engine_name: str | None = None,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.num_threads = max(1, num_threads)
        self._lock = threading.Lock()
        self._tts: sherpa_onnx.OfflineTts | None = None
        self.available = False
        self.engine_type = "none"
        self.load_sec = 0.0

        if not self.model_dir.is_dir():
            log.info("TTS model directory %s does not exist (disabled)", self.model_dir)
            return

        model_file = self.model_dir / "model.int8.onnx"
        if not model_file.is_file():
            model_file = self.model_dir / "model.onnx"
        voices_file = self.model_dir / "voices.bin"
        tokens_file = self.model_dir / "tokens.txt"
        lexicon_file = self.model_dir / "lexicon-us-en.txt"
        if not lexicon_file.is_file():
            lexicon_file = self.model_dir / "lexicon-en-us.txt"
        if not lexicon_file.is_file():
            lexicon_file = self.model_dir / "lexicon.txt"

        if not (model_file.is_file() and tokens_file.is_file()):
            log.info("TTS model/tokens not found in %s (TTS disabled)", self.model_dir)
            return

        try:
            if voices_file.is_file():
                # Kokoro model architecture
                cfg = sherpa_onnx.OfflineTtsConfig(
                    model=sherpa_onnx.OfflineTtsModelConfig(
                        kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                            model=str(model_file),
                            voices=str(voices_file),
                            tokens=str(tokens_file),
                            lexicon=str(lexicon_file) if lexicon_file.is_file() else "",
                            length_scale=1.0,
                        ),
                        num_threads=self.num_threads,
                    )
                )
                self.engine_type = engine_name or "kokoro"
            else:
                # VITS / Piper-compatible architecture
                vits_kwargs: dict[str, Any] = {
                    "model": str(model_file),
                    "tokens": str(tokens_file),
                    "length_scale": 1.0,
                }
                if lexicon_file.is_file():
                    vits_kwargs["lexicon"] = str(lexicon_file)
                data_dir = self.model_dir / "espeak-ng-data"
                if data_dir.is_dir():
                    vits_kwargs["data_dir"] = str(data_dir)

                cfg = sherpa_onnx.OfflineTtsConfig(
                    model=sherpa_onnx.OfflineTtsModelConfig(
                        vits=sherpa_onnx.OfflineTtsVitsModelConfig(**vits_kwargs),
                        num_threads=self.num_threads,
                    )
                )
                self.engine_type = engine_name or "vits"

            if not cfg.validate():
                log.warning("TTS config validation failed for %s", self.model_dir)
                return

            t0 = time.perf_counter()
            self._tts = sherpa_onnx.OfflineTts(cfg)
            self.load_sec = round(time.perf_counter() - t0, 3)
            self.available = True
            log.info(
                "Loaded %s TTS (%s, threads=%d) in %.2fs",
                self.engine_type,
                model_file.name,
                self.num_threads,
                self.load_sec,
            )
        except Exception as exc:
            log.warning("Failed to initialize TTS from %s: %s", self.model_dir, exc)

    def generate_wav(self, text: str, *, sid: int = 0, speed: float = 1.0) -> bytes:
        if not self.available or self._tts is None:
            raise RuntimeError(f"{self.engine_type} TTS engine is not available")
        cleaned = text.strip()
        if not cleaned:
            return b""
        with self._lock:
            audio = self._tts.generate(cleaned, sid=sid, speed=speed)
        samples = np.array(audio.samples, dtype=np.float32)
        int16_samples = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(audio.sample_rate)
            wf.writeframes(int16_samples.tobytes())
        return buf.getvalue()


# Alias for backwards compatibility
KokoroTtsEngine = SherpaTtsEngine


def build_handler(
    engine: SenseVoiceEngine,
    tts_engine: SherpaTtsEngine | None = None,
    tts_ko_engine: SherpaTtsEngine | None = None,
) -> type[BaseHTTPRequestHandler]:
    class STTHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            log.debug("HTTP %s - " + fmt, self.address_string(), *args)

        def _send_json(self, code: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path in ("/", "/health"):
                info: dict[str, Any] = {
                    "ok": True,
                    "engine": "sensevoice-int8",
                    "model_dir": str(engine.model_dir),
                    "default_language": engine.default_language,
                    "num_threads": engine.num_threads,
                    "load_sec": engine.load_sec,
                    "tts_available": bool(tts_engine and tts_engine.available),
                    "tts_ko_available": bool(tts_ko_engine and tts_ko_engine.available),
                }
                if tts_engine and tts_engine.available:
                    info["tts_engine"] = tts_engine.engine_type
                    info["tts_model_dir"] = str(tts_engine.model_dir)
                    info["tts_load_sec"] = tts_engine.load_sec
                if tts_ko_engine and tts_ko_engine.available:
                    info["tts_ko_engine"] = tts_ko_engine.engine_type
                    info["tts_ko_model_dir"] = str(tts_ko_engine.model_dir)
                    info["tts_ko_load_sec"] = tts_ko_engine.load_sec
                self._send_json(200, info)
                return
            self._send_json(404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/tts":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    raw_body = self.rfile.read(length) if length > 0 else b"{}"
                    data = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                    text = str(data.get("text", "")).strip()
                    sid = int(data.get("sid", 0))
                    speed = float(data.get("speed", 1.0))
                    lang = str(data.get("lang", "en")).strip().lower()

                    active_engine = tts_ko_engine if (lang == "ko" and tts_ko_engine and tts_ko_engine.available) else tts_engine
                    if not active_engine or not active_engine.available:
                        self._send_json(503, {"ok": False, "error": f"TTS engine ({lang}) not available"})
                        return

                    t0 = time.perf_counter()
                    wav_bytes = active_engine.generate_wav(text, sid=sid, speed=speed)
                    gen_ms = round((time.perf_counter() - t0) * 1000.0, 1)
                    log.info("%s TTS (%.0fms, %d bytes) -> %r", active_engine.engine_type, gen_ms, len(wav_bytes), text[:40])
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Content-Length", str(len(wav_bytes)))
                    self.send_header("X-Latency-Ms", str(gen_ms))
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    self.wfile.write(wav_bytes)
                except Exception as exc:
                    log.warning("Local TTS error: %s", exc)
                    self._send_json(400, {"ok": False, "error": str(exc)})
                return

            if parsed.path != "/transcribe":
                self._send_json(404, {"ok": False, "error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                wav_bytes = self.rfile.read(length) if length > 0 else b""
                qs = urllib.parse.parse_qs(parsed.query)
                lang = (qs.get("language", [""])[0] or "").strip() or None
                out = engine.transcribe_wav(wav_bytes, language=lang)
                log.info(
                    "STT (%.2fs, lang=%s event=%s) -> %.0fms %r",
                    out["duration_sec"],
                    out["lang"] or lang or engine.default_language,
                    out["event"] or "-",
                    out["latency_ms"],
                    out["text"],
                )
                self._send_json(200, out)
            except Exception as exc:
                log.warning("Transcribe error: %s", exc)
                self._send_json(400, {"ok": False, "error": str(exc)})

    return STTHandler


def main() -> None:
    host = os.environ.get("STT_HOST", "127.0.0.1")
    port = int(os.environ.get("STT_PORT", "8766"))
    model_dir = os.environ.get(
        "STT_MODEL_DIR",
        "/models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17",
    )
    language = os.environ.get("STT_LANG", "ko")
    use_itn = os.environ.get("STT_USE_ITN", "1").strip().lower() not in ("0", "false", "no")
    threads = int(os.environ.get("STT_THREADS", "4"))
    tts_model_dir = os.environ.get("TTS_MODEL_DIR", "/models/kokoro-int8-en-v0_19")
    tts_ko_model_dir = os.environ.get("TTS_KO_MODEL_DIR", "/models/vits-mimic3-ko_KO-kss_low")

    engine = SenseVoiceEngine(
        model_dir,
        language=language,
        use_itn=use_itn,
        num_threads=threads,
    )
    tts_engine = SherpaTtsEngine(tts_model_dir, num_threads=threads, engine_name="kokoro-int8-en")
    tts_ko_engine = (
        SherpaTtsEngine(tts_ko_model_dir, num_threads=threads, engine_name="vits-korean")
        if Path(tts_ko_model_dir).is_dir()
        else None
    )
    server = ThreadingHTTPServer((host, port), build_handler(engine, tts_engine, tts_ko_engine))
    log.info("PocoBox SenseVoice local STT + Sherpa TTS server listening on http://%s:%d", host, port)
    server.serve_forever()
    log.info("PocoBox SenseVoice local STT + Kokoro TTS server listening on http://%s:%d", host, port)
    server.serve_forever()


if __name__ == "__main__":
    main()
