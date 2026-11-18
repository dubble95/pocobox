#!/usr/bin/env python3
"""PocoBox Always-On Hybrid Voice Control & Now-Playing Display Daemon."""

from __future__ import annotations

import json
import logging
import os
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
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
from voice_control.continuous_listener import ContinuousVoiceListener  # noqa: E402
from voice_control.display_ui import DISPLAY_HTML  # noqa: E402
from voice_control.executor import VoiceExecutor  # noqa: E402
from voice_control.feedback import play_chime  # noqa: E402
from voice_control.intent_schema import ExecutionResult, IntentAction  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("pocobox-voice")

_EV_KEY = 1
_KEY_VOLUMEUP = 115
_INPUT_EVENT_FMT = "qqHHi"
_INPUT_EVENT_SIZE = struct.calcsize(_INPUT_EVENT_FMT)
SQUEEZELITE_ENV = Path("/etc/pocobox/squeezelite.env")


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _read_audio_output_label() -> str:
    if not SQUEEZELITE_ENV.exists():
        return "🔊 내장 스피커"
    for line in SQUEEZELITE_ENV.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("SQUEEZELITE_OUTPUT="):
            out = line.split("=", 1)[1].strip()
            if "CARD=" in out:
                card = out.split("CARD=", 1)[1].split(",", 1)[0]
                return f"🎧 USB DAC ({card})"
            return "🔊 내장 스피커"
    return "🔊 내장 스피커"


class VoiceController:
    def __init__(self) -> None:
        self.lms = LMSClient(
            host=os.environ.get("LMS_HOST", "127.0.0.1"),
            port=int(os.environ.get("LMS_PORT", "9000")),
        )
        self.player_name = os.environ.get("PLAYER_NAME", "pocobox")
        self.executor = VoiceExecutor(self.lms, player_name=self.player_name)
        self.interpreter = HybridVoiceInterpreter()
        self.require_wake_word = _env_bool("REQUIRE_WAKE_WORD", True)
        self.continuous_enabled = _env_bool("CONTINUOUS_LISTEN", True)

        self._listen_lock = threading.Lock()
        self._manual_busy = False
        self._last_nokey_warn = 0.0
        self.last_result: dict[str, Any] | None = None

        self.continuous_listener: ContinuousVoiceListener | None = None
        if self.continuous_enabled:
            self.continuous_listener = ContinuousVoiceListener(
                on_utterance=self.handle_continuous_wav,
                is_paused_fn=lambda: self._manual_busy,
            )

    def start_background_listeners(self) -> None:
        if self.continuous_listener is not None:
            self.continuous_listener.start()

    def handle_text(self, text: str, dry_run: bool = False, use_cloud: bool = True) -> ExecutionResult:
        intent = self.interpreter.interpret_text(text, use_cloud_fallback=use_cloud)
        res = self.executor.execute(intent, dry_run=dry_run)
        self.last_result = res.to_dict()
        log.info("TEXT '%s' -> action=%s ok=%s msg=%s", text, res.action, res.ok, res.message)
        return res

    def handle_wav(self, wav_bytes: bytes, dry_run: bool = False, enforce_wake_word: bool = False) -> ExecutionResult:
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

        if enforce_wake_word and self.require_wake_word and not intent.wake_word_detected:
            log.info("Ignored ambient speech without wake word: '%s'", intent.raw_text)
            return ExecutionResult(
                ok=False,
                action=IntentAction.UNKNOWN.value,
                message="호출어('포코야')가 없어 무시되었습니다.",
                intent=intent.to_dict(),
                details={"vad": metrics.__dict__, "ignored_no_wake_word": True},
            )

        res = self.executor.execute(intent, dry_run=dry_run)
        res.details["vad"] = metrics.__dict__
        self.last_result = res.to_dict()
        log.info("AUDIO (%.2fs) -> '%s' action=%s ok=%s", metrics.duration_sec, intent.raw_text, res.action, res.ok)
        return res

    def handle_continuous_wav(self, wav_bytes: bytes) -> None:
        if self.interpreter.provider == "local":
            now = time.monotonic()
            if now - self._last_nokey_warn > 60:
                log.warning("Speech detected in always-on mode, but no cloud STT API key is configured.")
                self._last_nokey_warn = now
            return
        res = self.handle_wav(wav_bytes, dry_run=False, enforce_wake_word=True)
        if res.ok:
            play_chime("ok")

    def listen_once(self, dry_run: bool = False) -> ExecutionResult:
        if not self._listen_lock.acquire(blocking=False):
            return ExecutionResult(ok=False, action="busy", message="이미 음성 명령을 듣는 중입니다.")
        self._manual_busy = True
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
            res = self.handle_wav(wav_bytes, dry_run=dry_run, enforce_wake_word=False)
            play_chime("ok" if res.ok else "error")
            return res
        finally:
            self._manual_busy = False
            self._listen_lock.release()

    def display_control(self, action: str) -> dict[str, Any]:
        player_id = self.lms.resolve_player_id(self.player_name)
        if action == "toggle":
            self.lms.toggle_pause(player_id)
        elif action == "next":
            self.lms.next_track(player_id)
        elif action == "prev":
            self.lms.prev_track(player_id)
        elif action == "vol_up":
            self.lms.adjust_volume(player_id, 5)
        elif action == "vol_down":
            self.lms.adjust_volume(player_id, -5)
        else:
            return {"ok": False, "error": f"Unknown control action: {action}"}
        return {"ok": True, "action": action}

    def display_state(self) -> dict[str, Any]:
        np: dict[str, Any] = {}
        try:
            pid = self.lms.resolve_player_id(self.player_name)
            np = self.lms.now_playing(pid)
        except Exception:
            pass
        voice_info = (
            self.continuous_listener.status_dict()
            if self.continuous_listener is not None
            else {"continuous_listen": False, "listening_state": "stopped"}
        )
        voice_info["provider"] = self.interpreter.provider
        voice_info["require_wake_word"] = self.require_wake_word
        voice_info["last_result"] = self.last_result
        return {
            "ok": True,
            "now_playing": np,
            "audio_output_label": _read_audio_output_label(),
            "voice": voice_info,
        }

    def fetch_cover_art(self) -> tuple[bytes, str] | None:
        try:
            pid = self.lms.resolve_player_id(self.player_name)
        except Exception:
            pid = "02:70:6f:63:6f:01"
        url = f"http://{self.lms.host}:{self.lms.port}/music/current/cover.jpg?player={pid}"
        try:
            with urllib.request.urlopen(url, timeout=4.0) as resp:
                return resp.read(), resp.headers.get("Content-Type", "image/jpeg")
        except Exception:
            return None

    def health(self) -> dict[str, Any]:
        lms_ok = False
        players: list[dict[str, Any]] = []
        try:
            players = self.lms.list_players()
            lms_ok = True
        except Exception:
            pass
        cont = (
            self.continuous_listener.status_dict()
            if self.continuous_listener is not None
            else {"continuous_listen": False, "listening_state": "stopped"}
        )
        return {
            "ok": True,
            "provider": self.interpreter.provider,
            "gemini_model": self.interpreter.gemini_model,
            "gemini_configured": bool(self.interpreter.gemini_api_key),
            "openai_configured": bool(self.interpreter.openai_api_key),
            "require_wake_word": self.require_wake_word,
            **cont,
            "lms_connected": lms_ok,
            "players": [
                {"name": p.get("name"), "playerid": p.get("playerid"), "connected": p.get("connected")}
                for p in players
            ],
            "last_result": self.last_result,
        }


def start_hw_button_watcher(controller: VoiceController) -> None:
    """Trigger manual voice listen when physical Volume-Up button is held >= 0.7s."""
    if not _env_bool("ENABLE_HW_BUTTON", True):
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
                    if value == 1:
                        pressed_at = time.monotonic()
                        triggered = False
                    elif value == 2 and pressed_at is not None and not triggered:
                        if time.monotonic() - pressed_at >= hold_sec:
                            triggered = True
                            threading.Thread(target=controller.listen_once, daemon=True).start()
                    elif value == 0:
                        if pressed_at is not None and not triggered and (time.monotonic() - pressed_at >= hold_sec):
                            threading.Thread(target=controller.listen_once, daemon=True).start()
                        pressed_at = None
                        triggered = False

    threading.Thread(target=_watch, daemon=True).start()


def make_handler(controller: VoiceController) -> type[BaseHTTPRequestHandler]:
    class VoiceHTTPHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            # Suppress noisy 1Hz /display/state poll logs
            if args and isinstance(args[0], str) and "/display/state" in args[0]:
                return
            log.info("HTTP %s - " + fmt, self.address_string(), *args)

        def _send_json(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_bytes(self, code: int, data: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/health", "/status"):
                self._send_json(200, controller.health())
                return
            if path in ("/", "/display"):
                self._send_bytes(200, DISPLAY_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/display/state":
                self._send_json(200, controller.display_state())
                return
            if path == "/display/cover":
                art = controller.fetch_cover_art()
                if art is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self._send_bytes(200, art[0], art[1])
                return
            self._send_json(404, {"ok": False, "error": "Not found"})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(length) if length > 0 else b""
            path = self.path.split("?", 1)[0]

            if path == "/display/control":
                try:
                    data = json.loads(body.decode("utf-8")) if body else {}
                    res = controller.display_control(str(data.get("action", "")))
                    self._send_json(200 if res.get("ok") else 400, res)
                except Exception as exc:
                    self._send_json(500, {"ok": False, "error": str(exc)})
                return

            if path == "/voice/text":
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

            if path == "/voice/audio":
                dry_run = "dry_run=1" in self.path or "dry_run=true" in self.path
                enforce_wake = "wake=1" in self.path or "wake=true" in self.path
                res = controller.handle_wav(body, dry_run=dry_run, enforce_wake_word=enforce_wake)
                self._send_json(200 if res.ok else 422, res.to_dict())
                return

            if path == "/voice/listen":
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
    controller.start_background_listeners()
    start_hw_button_watcher(controller)
    server = ThreadingHTTPServer((host, port), make_handler(controller))
    log.info(
        "PocoBox Voice & Display daemon on http://%s:%d (provider=%s continuous=%s wake_required=%s)",
        host,
        port,
        controller.interpreter.provider,
        controller.continuous_enabled,
        controller.require_wake_word,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if controller.continuous_listener:
            controller.continuous_listener.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
