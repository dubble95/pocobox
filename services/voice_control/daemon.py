#!/usr/bin/env python3
"""PocoBox Hybrid Voice Control Daemon (HTTP API + Hardware Long-Press Trigger + LMS/TIDAL)."""

from __future__ import annotations

import json
import logging
import os
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# Ensure local packages are importable in both repo and /srv/pocobox deployments
_HERE = Path(__file__).resolve().parent
for _p in (str(_HERE.parent), str(_HERE.parent / "lms_client"), "/srv/pocobox/bin"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lms_client import LMSClient  # noqa: E402
from voice_control.audio_capture import analyze_wav_bytes, record_utterance_wav, trim_silence_wav  # noqa: E402
from voice_control.cloud_stt_intent import HybridVoiceInterpreter  # noqa: E402
from voice_control.executor import VoiceExecutor  # noqa: E402
from voice_control.feedback import play_chime  # noqa: E402
from voice_control.intent_schema import ExecutionResult, IntentAction  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("pocobox-voice")

# Linux input_event struct on 64-bit aarch64: tv_sec(q), tv_usec(q), type(H), code(H), value(i)
_EV_KEY = 1
_KEY_VOLUMEUP = 115
_INPUT_EVENT_FMT = "qqHHi"
_INPUT_EVENT_SIZE = struct.calcsize(_INPUT_EVENT_FMT)


class VoiceController:
    def __init__(self) -> None:
        self.lms = LMSClient(
            host=os.environ.get("LMS_HOST", "127.0.0.1"),
            port=int(os.environ.get("LMS_PORT", "9000")),
        )
        self.player_name = os.environ.get("PLAYER_NAME", "pocobox")
        self.executor = VoiceExecutor(self.lms, player_name=self.player_name)
        self.interpreter = HybridVoiceInterpreter()
        self._listen_lock = threading.Lock()
        self.last_result: dict[str, Any] | None = None

    def handle_text(self, text: str, dry_run: bool = False, use_cloud: bool = True) -> ExecutionResult:
        intent = self.interpreter.interpret_text(text, use_cloud_fallback=use_cloud)
        res = self.executor.execute(intent, dry_run=dry_run)
        self.last_result = res.to_dict()
        log.info("TEXT '%s' -> action=%s ok=%s msg=%s", text, res.action, res.ok, res.message)
        return res

    def handle_wav(self, wav_bytes: bytes, dry_run: bool = False) -> ExecutionResult:
        metrics = analyze_wav_bytes(wav_bytes)
        if not metrics.has_speech:
            res = ExecutionResult(
                ok=False,
                action=IntentAction.UNKNOWN.value,
                message="음성이 감지되지 않았습니다.",
                details={"vad": metrics.__dict__},
            )
            self.last_result = res.to_dict()
            return res

        trimmed = trim_silence_wav(wav_bytes)
        try:
            intent = self.interpreter.interpret_audio(trimmed)
        except Exception as exc:
            res = ExecutionResult(
                ok=False,
                action=IntentAction.UNKNOWN.value,
                message=str(exc),
                details={"vad": metrics.__dict__},
            )
            self.last_result = res.to_dict()
            return res

        res = self.executor.execute(intent, dry_run=dry_run)
        res.details["vad"] = metrics.__dict__
        self.last_result = res.to_dict()
        log.info("AUDIO (%ss) -> '%s' action=%s ok=%s", metrics.duration_sec, intent.raw_text, res.action, res.ok)
        return res

    def listen_once(self, dry_run: bool = False) -> ExecutionResult:
        if not self._listen_lock.acquire(blocking=False):
            return ExecutionResult(ok=False, action="busy", message="이미 음성 명령을 듣는 중입니다.")
        try:
            play_chime("listen")
            wav_bytes, metrics = record_utterance_wav(
                max_duration_sec=float(os.environ.get("VOICE_MAX_SEC", "8.0")),
                silence_timeout_sec=float(os.environ.get("VOICE_SILENCE_SEC", "1.2")),
                min_threshold=float(os.environ.get("VOICE_VAD_THRESHOLD", "350.0")),
                source=os.environ.get("VOICE_PULSE_SOURCE") or None,
            )
            if not metrics.has_speech:
                play_chime("error")
                res = ExecutionResult(
                    ok=False,
                    action=IntentAction.UNKNOWN.value,
                    message="음성이 감지되지 않았습니다.",
                    details={"vad": metrics.__dict__},
                )
                self.last_result = res.to_dict()
                return res
            res = self.handle_wav(wav_bytes, dry_run=dry_run)
            play_chime("ok" if res.ok else "error")
            return res
        finally:
            self._listen_lock.release()

    def health(self) -> dict[str, Any]:
        lms_ok = False
        players: list[dict[str, Any]] = []
        try:
            players = self.lms.list_players()
            lms_ok = True
        except Exception:
            pass
        return {
            "ok": True,
            "provider": self.interpreter.provider,
            "gemini_configured": bool(self.interpreter.gemini_api_key),
            "openai_configured": bool(self.interpreter.openai_api_key),
            "lms_connected": lms_ok,
            "players": [
                {"name": p.get("name"), "playerid": p.get("playerid"), "connected": p.get("connected")}
                for p in players
            ],
            "last_result": self.last_result,
        }


def start_hw_button_watcher(controller: VoiceController) -> None:
    """Trigger voice listen when physical Volume-Up button is held >= 0.7s."""
    if os.environ.get("ENABLE_HW_BUTTON", "1").strip().lower() in ("0", "false", "no"):
        return
    dev_path = os.environ.get("HW_BUTTON_DEVICE", "/dev/input/event2")
    hold_sec = float(os.environ.get("HW_BUTTON_HOLD_SEC", "0.7"))

    def _watch() -> None:
        if not os.path.exists(dev_path):
            log.info("HW button device %s not found; skipping button watcher.", dev_path)
            return
        try:
            f = open(dev_path, "rb")
        except PermissionError:
            log.warning("No permission to read %s; HW button trigger disabled.", dev_path)
            return

        log.info("Watching %s (long-press Volume-Up >= %.1fs triggers voice listen)", dev_path, hold_sec)
        pressed_at: float | None = None
        triggered = False
        with f:
            while True:
                raw = f.read(_INPUT_EVENT_SIZE)
                if len(raw) < _INPUT_EVENT_SIZE:
                    break
                _, _, ev_type, code, value = struct.unpack(_INPUT_EVENT_FMT, raw)
                if ev_type == _EV_KEY and code == _KEY_VOLUMEUP:
                    if value == 1:  # key down
                        pressed_at = time.monotonic()
                        triggered = False
                    elif value == 2 and pressed_at is not None and not triggered:  # key repeat
                        if time.monotonic() - pressed_at >= hold_sec:
                            triggered = True
                            threading.Thread(target=controller.listen_once, daemon=True).start()
                    elif value == 0:  # key up
                        if pressed_at is not None and not triggered and (time.monotonic() - pressed_at >= hold_sec):
                            threading.Thread(target=controller.listen_once, daemon=True).start()
                        pressed_at = None
                        triggered = False

    threading.Thread(target=_watch, daemon=True).start()


def make_handler(controller: VoiceController) -> type[BaseHTTPRequestHandler]:
    class VoiceHTTPHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            log.info("HTTP %s - " + fmt, self.address_string(), *args)

        def _send_json(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/health", "/status"):
                self._send_json(200, controller.health())
                return
            self._send_json(404, {"ok": False, "error": "Not found"})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(length) if length > 0 else b""

            if self.path.startswith("/voice/text"):
                try:
                    data = json.loads(body.decode("utf-8")) if body else {}
                except json.JSONDecodeError:
                    self._send_json(400, {"ok": False, "error": "Invalid JSON"})
                    return
                res = controller.handle_text(
                    str(data.get("text", "")),
                    dry_run=bool(data.get("dry_run", False)),
                    use_cloud=bool(data.get("use_cloud", True)),
                )
                self._send_json(200 if res.ok else 422, res.to_dict())
                return

            if self.path.startswith("/voice/audio"):
                dry_run = "dry_run=1" in self.path or "dry_run=true" in self.path
                res = controller.handle_wav(body, dry_run=dry_run)
                self._send_json(200 if res.ok else 422, res.to_dict())
                return

            if self.path.startswith("/voice/listen"):
                dry_run = "dry_run=1" in self.path or "dry_run=true" in self.path
                res = controller.listen_once(dry_run=dry_run)
                self._send_json(200 if res.ok else 422, res.to_dict())
                return

            self._send_json(404, {"ok": False, "error": "Not found"})

    return VoiceHTTPHandler


def main() -> int:
    host = os.environ.get("VOICE_HTTP_HOST", "0.0.0.0")
    port = int(os.environ.get("VOICE_HTTP_PORT", "8765"))
    controller = VoiceController()
    start_hw_button_watcher(controller)
    server = ThreadingHTTPServer((host, port), make_handler(controller))
    log.info("PocoBox Voice Control daemon listening on http://%s:%d (provider=%s)", host, port, controller.interpreter.provider)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
