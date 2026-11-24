#!/usr/bin/env python3
"""PocoBox Always-On Hybrid Voice Control & Now-Playing Display Daemon with Live SSE Subtitles."""

from __future__ import annotations

import datetime
import json
import logging
import os
import queue
import shutil
import struct
import subprocess
import sys
import threading
import time
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
from voice_control.audio_capture import (  # noqa: E402
    analyze_wav_bytes,
    normalize_wav_gain,
    record_utterance_wav,
    trim_silence_wav,
)
from voice_control.audio_dsp import filter_wav_speech_band  # noqa: E402
from voice_control.cloud_stt_intent import ConversationContext, HybridVoiceInterpreter  # noqa: E402
from voice_control.continuous_listener import ContinuousVoiceListener  # noqa: E402
from voice_control.dashboard_ui import DASHBOARD_HTML  # noqa: E402
from voice_control.display_ui import DISPLAY_HTML, DISPLAY_MANIFEST, UI_VERSION  # noqa: E402
from voice_control.english_quiz import (  # noqa: E402
    DEFAULT_QUIZ_COUNT,
    EnglishQuizSession,
    QuizTurnResponse,
    TOPIC_LABELS,
    classify_quiz_command,
    generate_infinite_quiz_questions,
    is_local_answer_match,
    question_from_dict,
)
from voice_control.executor import (  # noqa: E402
    VoiceExecutor,
    format_command_ack,
    format_command_failure,
    format_now_playing_response,
    is_dac_mode,
)
from voice_control.feedback import (  # noqa: E402
    enable_usb_dac_pulseaudio,
    get_internal_speaker_volume,
    is_usb_dac_pulseaudio_active,
    play_chime,
    release_usb_dac_pulseaudio,
    set_internal_speaker_volume,
)
from voice_control.intent_schema import ExecutionResult, IntentAction, VoiceIntent  # noqa: E402
from voice_control.background_verifier import BackgroundVerifier  # noqa: E402
from voice_control.telemetry import VoiceTelemetryLogger, record_command_result, accuracy_summary  # noqa: E402
from voice_control.tts import (  # noqa: E402
    AVAILABLE_VOICES,
    VALID_VOICE_IDS,
    default_english_tts_voice,
    default_tts_voice,
    edge_tts_available,
    speak_segments,
    speak_text,
)
from voice_control.weather import get_default_location  # noqa: E402
from voice_control.youtube import (  # noqa: E402
    YouTubePlaybackManager,
    get_kpop_playlist,
    search_youtube_videos,
)

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
AUDIO_STATE_FILE = Path("/etc/pocobox/audio-state.json")


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _read_audio_output_label() -> str:
    if AUDIO_STATE_FILE.exists():
        try:
            st = json.loads(AUDIO_STATE_FILE.read_text(encoding="utf-8"))
            aec = " · AEC" if st.get("aec") else ""
            if st.get("mode") == "usb_dac" and st.get("card_id"):
                return f"🎧 USB DAC ({st['card_id']}){aec}"
            return f"🔊 내장 스피커{aec}"
        except Exception:
            pass
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


_KST = datetime.timezone(datetime.timedelta(hours=9))


def _parse_systemd_timestamp_to_kst(ts_str: str) -> tuple[str, str, float | None]:
    """Parse systemd timestamp (e.g. 'Thu 2026-11-19 22:45:00 GMT') into (kst_str, relative_str, epoch)."""
    if not ts_str or ts_str.strip().lower() in ("n/a", "no", "none", "0"):
        return "-", "-", None
    parts = ts_str.strip().split()
    try:
        if len(parts) >= 4 and len(parts[0]) == 3 and not parts[0].isdigit():
            date_part, time_part = parts[1], parts[2]
        elif len(parts) >= 2:
            date_part, time_part = parts[0], parts[1]
        else:
            return ts_str, "-", None
        dt_naive = datetime.datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M:%S")
        dt_utc = dt_naive.replace(tzinfo=datetime.timezone.utc)
        dt_kst = dt_utc.astimezone(_KST)
        kst_str = dt_kst.strftime("%Y-%m-%d %H:%M:%S KST")
        now_kst = datetime.datetime.now(tz=_KST)
        diff_sec = (dt_kst - now_kst).total_seconds()
        abs_diff = abs(diff_sec)
        if abs_diff < 60:
            rel = "방금 전" if diff_sec <= 0 else "잠시 후"
        elif abs_diff < 3600:
            m = int(abs_diff // 60)
            rel = f"{m}분 전" if diff_sec <= 0 else f"{m}분 후"
        elif abs_diff < 86400:
            h = int(abs_diff // 3600)
            m = int((abs_diff % 3600) // 60)
            m_str = f" {m}분" if m > 0 else ""
            rel = f"{h}시간{m_str} 전" if diff_sec <= 0 else f"{h}시간{m_str} 후"
        else:
            d = int(abs_diff // 86400)
            h = int((abs_diff % 86400) // 3600)
            rel = f"{d}일 {h}시간 전" if diff_sec <= 0 else f"{d}일 {h}시간 후"
        return kst_str, rel, dt_kst.timestamp()
    except Exception:
        return ts_str, "-", None


def _parse_kv_output(text: str) -> dict[str, str]:
    res: dict[str, str] = {}
    for line in text.strip().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            res[k.strip()] = v.strip()
    return res


def _read_daily_quiz_schedule_status() -> dict[str, Any]:
    topic = os.environ.get("DAILY_QUIZ_TOPIC", "all")
    count_str = os.environ.get("DAILY_QUIZ_COUNT", "3")
    count = int(count_str) if count_str.isdigit() else DEFAULT_QUIZ_COUNT
    topic_label = TOPIC_LABELS.get(topic, topic)

    info: dict[str, Any] = {
        "ok": True,
        "configured": False,
        "timer_active": False,
        "timer_substate": "unknown",
        "schedule_label": "평일 07:50 / 주말 09:00 (KST)",
        "schedule_expr": "Mon..Fri *-*-* 07:50:00 Asia/Seoul / Sat,Sun *-*-* 09:00:00 Asia/Seoul",
        "next_run_kst": "-",
        "next_run_rel": "-",
        "last_run_kst": "-",
        "last_run_rel": "-",
        "last_result": "none",
        "last_exit_code": None,
        "last_error": "",
        "service_state": "unknown",
        "topic": topic,
        "topic_label": topic_label,
        "count": count,
        "voice_loop": True,
    }

    try:
        t_proc = subprocess.run(
            [
                "systemctl",
                "show",
                "pocobox-daily-quiz.timer",
                "--property=ActiveState,SubState,NextElapseUSecRealtime,LastTriggerUSec,Result,TimersCalendar",
            ],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        s_proc = subprocess.run(
            [
                "systemctl",
                "show",
                "pocobox-daily-quiz.service",
                "--property=ActiveState,SubState,Result,ExecMainExitCode,ExecMainExitTimestamp",
            ],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if t_proc.returncode == 0 and t_proc.stdout.strip():
            info["configured"] = True
            t_props = _parse_kv_output(t_proc.stdout)
            info["timer_active"] = (t_props.get("ActiveState") == "active")
            info["timer_substate"] = t_props.get("SubState", "unknown")
            next_raw = t_props.get("NextElapseUSecRealtime", "")
            kst, rel, _ = _parse_systemd_timestamp_to_kst(next_raw)
            info["next_run_kst"] = kst
            info["next_run_rel"] = rel
            last_raw = t_props.get("LastTriggerUSec", "")
            kst_last, rel_last, _ = _parse_systemd_timestamp_to_kst(last_raw)
            info["last_run_kst"] = kst_last
            info["last_run_rel"] = rel_last

            tc_entries: list[str] = []
            for ln in t_proc.stdout.splitlines():
                if ln.startswith("TimersCalendar=") and "OnCalendar=" in ln:
                    expr = ln.split("OnCalendar=", 1)[1].split(";", 1)[0].strip()
                    if expr not in tc_entries:
                        tc_entries.append(expr)
            if tc_entries:
                info["schedule_expr"] = " / ".join(tc_entries)
                has_weekday = any("Mon..Fri" in e for e in tc_entries)
                has_weekend = any("Sat,Sun" in e or "Sat" in e for e in tc_entries)
                if has_weekday and has_weekend:
                    info["schedule_label"] = "평일 07:50 / 주말 09:00 (KST)"
                elif has_weekend and not has_weekday:
                    info["schedule_label"] = "주말 09:00 (KST)"
                elif has_weekday:
                    info["schedule_label"] = "평일 07:50 (KST)"
                elif len(tc_entries) == 1 and "07:50" in tc_entries[0]:
                    info["schedule_label"] = "매일 07:50 (KST)"

        if s_proc.returncode == 0 and s_proc.stdout.strip():
            s_props = _parse_kv_output(s_proc.stdout)
            s_active = s_props.get("ActiveState", "unknown")
            s_res = s_props.get("Result", "unknown")
            exit_code_str = s_props.get("ExecMainExitCode", "")
            exit_code = int(exit_code_str) if exit_code_str.isdigit() else None
            info["service_state"] = s_active
            info["last_exit_code"] = exit_code

            if s_active == "failed" or s_res == "exit-code":
                info["last_result"] = "failed"
                try:
                    j_proc = subprocess.run(
                        ["journalctl", "-u", "pocobox-daily-quiz.service", "-n", "5", "--no-pager", "-o", "cat"],
                        capture_output=True,
                        text=True,
                        timeout=2.0,
                        check=False,
                    )
                    lines = [ln.strip() for ln in j_proc.stdout.splitlines() if ln.strip()]
                    for ln in reversed(lines):
                        if "curl:" in ln or "Failed to connect" in ln or "error" in ln.lower():
                            info["last_error"] = ln
                            break
                    if not info["last_error"] and lines:
                        info["last_error"] = lines[-1]
                except Exception:
                    info["last_error"] = f"종료 코드 {exit_code}" if exit_code is not None else "실행 실패"
            elif s_res == "success" or exit_code == 0:
                info["last_result"] = "success"
            elif info["last_run_kst"] != "-":
                info["last_result"] = "success"
    except Exception:
        # systemctl or journalctl not available (e.g. macOS local dev or container)
        pass

    return info


class EventHub:
    """Thread-safe Server-Sent Events pub/sub hub for instant live subtitles on the display."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: set[queue.Queue[str]] = set()

    def subscribe(self) -> queue.Queue[str]:
        q: queue.Queue[str] = queue.Queue(maxsize=32)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue[str]) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        payload = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(payload)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except Exception:
                    pass


class VoiceController:
    def __init__(self) -> None:
        self.lms = LMSClient(
            host=os.environ.get("LMS_HOST", "127.0.0.1"),
            port=int(os.environ.get("LMS_PORT", "9000")),
        )
        self.player_name = os.environ.get("PLAYER_NAME", "pocobox")
        self.enable_tidal_autoplay = _env_bool("ENABLE_TIDAL_AUTOPLAY", True)
        self.youtube = YouTubePlaybackManager()
        self.executor = VoiceExecutor(
            self.lms,
            player_name=self.player_name,
            auto_queue_radio=self.enable_tidal_autoplay,
            is_dac_mode_fn=self.is_dac_mode,
            youtube_handler=self.handle_youtube_intent,
            is_youtube_active_fn=lambda: self.youtube.active,
            switch_output_handler=self.handle_switch_output,
            ensure_player_running_fn=self._ensure_squeezelite_running,
        )
        self.telemetry = VoiceTelemetryLogger()
        self.interpreter = HybridVoiceInterpreter(telemetry=self.telemetry)
        self.require_wake_word = _env_bool("REQUIRE_WAKE_WORD", True)
        self.continuous_enabled = _env_bool("CONTINUOUS_LISTEN", True)
        self.vad_threshold = float(os.environ.get("VOICE_VAD_THRESHOLD", "40.0"))
        self.duck_enabled = _env_bool("VOICE_DUCK_ENABLED", True)
        self.dac_duck_enabled = _env_bool("VOICE_DAC_DUCK_ENABLED", True)
        self.duck_volume = max(5, min(60, int(os.environ.get("VOICE_DUCK_VOLUME", "18"))))
        self.use_bandpass = _env_bool("VOICE_USE_BANDPASS", True)

        # Built-in speaker volume, chime, and TTS voice settings (persisted across restarts)
        self._prefs_path = Path(os.environ.get("VOICE_PREFS_FILE", "/srv/pocobox/logs/voice_prefs.json"))
        self.enable_chime = _env_bool("ENABLE_CHIME", True)
        self._chime_playing: bool = False
        self.chime_volume = max(0.05, min(1.0, float(os.environ.get("VOICE_CHIME_VOLUME", "0.25"))))
        self.enable_tts = _env_bool("ENABLE_TTS", True)
        self.enable_command_ack = _env_bool("ENABLE_COMMAND_ACK", True)
        self.tts_voice = default_tts_voice()
        self.tts_rate_pct = max(-30, min(30, int(os.environ.get("TTS_RATE_PCT", "0"))))
        self.tts_pitch_hz = max(-15, min(15, int(os.environ.get("TTS_PITCH_HZ", "0"))))
        self.speaker_volume = get_internal_speaker_volume()
        self._default_speaker_config: dict[str, Any] = {
            "speaker_volume": 100,
            "enable_chime": self.enable_chime,
            "chime_volume_pct": int(round(self.chime_volume * 100)),
            "enable_tts": self.enable_tts,
            "enable_command_ack": self.enable_command_ack,
            "tts_voice": self.tts_voice,
            "tts_rate_pct": self.tts_rate_pct,
            "tts_pitch_hz": self.tts_pitch_hz,
        }
        self._load_voice_prefs()

        self.events = EventHub()
        self.quiz = EnglishQuizSession()
        self.quiz_en_voice = default_english_tts_voice()
        self.quiz_en_rate_pct = max(-30, min(10, int(os.environ.get("QUIZ_EN_RATE_PCT", "-8"))))
        self.quiz_listen_max_sec = float(os.environ.get("QUIZ_LISTEN_MAX_SEC", "7.0"))
        self.quiz_silence_sec = float(os.environ.get("QUIZ_SILENCE_SEC", "1.2"))
        self._quiz_loop_stop = threading.Event()
        self._quiz_loop_active = False
        self._quiz_thread: threading.Thread | None = None
        self._quiz_paused_lms: str | None = None

        self._listen_lock = threading.Lock()
        self._duck_lock = threading.Lock()
        self._duck_saved: tuple[str, int] | None = None
        self._manual_busy = False
        self._tts_speaking = False
        self._display_wake_until = 0.0
        self._last_nokey_warn = 0.0
        self.last_result: dict[str, Any] | None = None
        self.conversation_context = ConversationContext(expiry_sec=45.0)
        self._schedule_cache: dict[str, Any] | None = None
        self._schedule_cache_time: float = 0.0

        self.continuous_listener: ContinuousVoiceListener | None = None
        if self.continuous_enabled:
            self.continuous_listener = ContinuousVoiceListener(
                on_utterance=self.handle_continuous_wav,
                is_paused_fn=lambda: (self._manual_busy or self._tts_speaking or self._quiz_loop_active),
                on_state_change=self._on_listener_state,
                is_music_playing_fn=self._is_music_playing,
                on_wake_detected=self.handle_wake_detected,
                on_wake_utterance=self.handle_wake_utterance,
                is_chime_playing_fn=lambda: self._chime_playing,
                telemetry=self.telemetry,
            )

        self.background_verifier = BackgroundVerifier(
            interpreter=self.interpreter,
            telemetry=self.telemetry,
            clips_dir=self.telemetry.command_clips_dir,
        )

        self._default_runtime_config: dict[str, Any] = {
            "require_wake_word": self.require_wake_word,
            "duck_enabled": self.duck_enabled,
            "duck_volume": self.duck_volume,
            "quiet_threshold": self.vad_threshold,
            "playing_threshold": self.continuous_listener.playing_min_threshold if self.continuous_listener else float(os.environ.get("VOICE_PLAYING_VAD_THRESHOLD", "180.0")),
            "speech_multiplier": self.continuous_listener.speech_multiplier if self.continuous_listener else float(os.environ.get("VOICE_VAD_MULTIPLIER", "3.0")),
            "playing_multiplier": self.continuous_listener.playing_multiplier if self.continuous_listener else float(os.environ.get("VOICE_PLAYING_MULTIPLIER", "2.1")),
            "rms_fallback_during_music": self.continuous_listener.rms_fallback_during_music if self.continuous_listener else _env_bool("VOICE_RMS_FALLBACK_DURING_MUSIC", False),
            "aec3_delay_ms": (self.continuous_listener.aec3.delay_ms if (self.continuous_listener and self.continuous_listener.aec3) else int(os.environ.get("VOICE_AEC3_DELAY_MS", "150"))),
        }

    @property
    def rolling_recorder(self):
        if self.continuous_listener:
            return self.continuous_listener.rolling_recorder
        return None

    def _load_voice_prefs(self) -> None:
        if not self._prefs_path.exists():
            return
        try:
            data = json.loads(self._prefs_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        if "enable_chime" in data:
            self.enable_chime = bool(data["enable_chime"])
        if "chime_volume_pct" in data:
            self.chime_volume = max(0.05, min(1.0, float(data["chime_volume_pct"]) / 100.0))
        if "enable_tts" in data:
            self.enable_tts = bool(data["enable_tts"])
        if "enable_command_ack" in data:
            self.enable_command_ack = bool(data["enable_command_ack"])
        if "tts_voice" in data and str(data["tts_voice"]) in VALID_VOICE_IDS:
            self.tts_voice = str(data["tts_voice"])
        if "tts_rate_pct" in data:
            self.tts_rate_pct = max(-30, min(30, int(data["tts_rate_pct"])))
        if "tts_pitch_hz" in data:
            self.tts_pitch_hz = max(-15, min(15, int(data["tts_pitch_hz"])))
        if "speaker_volume" in data:
            self.speaker_volume = set_internal_speaker_volume(int(data["speaker_volume"]))

    def _save_voice_prefs(self) -> None:
        payload = {
            "speaker_volume": self.speaker_volume,
            "enable_chime": self.enable_chime,
            "chime_volume_pct": int(round(self.chime_volume * 100)),
            "enable_tts": self.enable_tts,
            "enable_command_ack": self.enable_command_ack,
            "tts_voice": self.tts_voice,
            "tts_rate_pct": self.tts_rate_pct,
            "tts_pitch_hz": self.tts_pitch_hz,
        }
        try:
            self._prefs_path.parent.mkdir(parents=True, exist_ok=True)
            self._prefs_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _play_chime(self, kind: str = "listen") -> None:
        if not self.enable_chime:
            return
        self._chime_playing = True
        try:
            play_chime(kind, volume=self.chime_volume, enabled=self.enable_chime)
        finally:
            self._chime_playing = False

    def _is_music_playing(self) -> bool:
        if self.youtube.active and self.youtube.playing:
            return True
        try:
            pid = self.lms.resolve_player_id(self.player_name)
            st = self.lms.player_status(pid)
            return st.get("mode") == "play"
        except Exception:
            return False

    def is_dac_mode(self) -> bool:
        return is_dac_mode(AUDIO_STATE_FILE)

    def _begin_duck(self) -> bool:
        """Immediately lower LMS music or YouTube volume while listening so 2-3m voice is crystal clear."""
        if self.youtube.active:
            self.events.publish({"type": "youtube_duck", "duck": True})
        if not self.duck_enabled:
            return False
        if self.is_dac_mode() and not self.dac_duck_enabled:
            return False
        with self._duck_lock:
            if self._duck_saved is not None:
                return True
            try:
                pid = self.lms.resolve_player_id(self.player_name)
                st = self.lms.player_status(pid)
                if st.get("mode") != "play":
                    return False
                cur_vol = int(float(st.get("mixer volume", 0)))
                if cur_vol <= self.duck_volume:
                    return False
                self._duck_saved = (pid, cur_vol)
                self.lms.set_volume(pid, self.duck_volume)
                log.info("Auto-ducked LMS volume %d%% -> %d%%", cur_vol, self.duck_volume)
                return True
            except Exception as exc:
                log.debug("Auto-duck skipped: %s", exc)
                self._duck_saved = None
                return False

    def _restore_duck(self, skip_restore: bool = False) -> None:
        """Restore pre-duck LMS volume unless the user explicitly changed volume."""
        if self.youtube.active:
            self.events.publish({"type": "youtube_duck", "duck": False})
        with self._duck_lock:
            saved = self._duck_saved
            self._duck_saved = None
        if saved is None or skip_restore:
            return
        pid, prev_vol = saved
        try:
            self.lms.set_volume(pid, prev_vol)
            log.info("Restored LMS volume to %d%%", prev_vol)
        except Exception as exc:
            log.warning("Failed to restore ducked volume to %d%%: %s", prev_vol, exc)

    def _wake_display(self, duration_sec: float = 15.0) -> None:
        if duration_sec <= 0:
            return
        target = time.monotonic() + float(duration_sec)
        if target > self._display_wake_until:
            self._display_wake_until = target

    def _cap_display_wake(self, max_remaining_sec: float) -> None:
        limit = time.monotonic() + max(0.0, float(max_remaining_sec))
        if self._display_wake_until > limit:
            self._display_wake_until = limit

    def _clear_display_wake(self) -> None:
        self._display_wake_until = 0.0

    def _update_display_wake_for_action(self, action: str) -> None:
        if action == IntentAction.PAUSE.value:
            self._cap_display_wake(3.0)
        elif self._is_spoken_action(action):
            self._wake_display(15.0)
        else:
            self._wake_display(10.0)

    def _on_listener_state(self, state: str, rms: float) -> None:
        self.events.publish({"type": "state", "state": state, "rms": round(rms, 1), "ts": time.time()})

    def handle_wake_detected(self, phrase: str) -> None:
        """Fired immediately when local Vosk spots '포코야'/'헤이 포코'."""
        self._wake_display(12.0)
        self._begin_duck()
        if self.enable_chime:
            self._chime_playing = True
        threading.Thread(target=self._play_chime, args=("listen",), daemon=True).start()
        self.events.publish({
            "type": "transcript",
            "text": f"🎤 포코야! 명령을 말씀하세요... ({phrase})",
            "wake": True,
            "ts": time.time(),
        })

    def _speak_answer(self, text: str, *, keep_awake_after_sec: float = 0.0) -> bool:
        """Speak Q&A / Now-Playing / command confirmation via TTS while pausing mic and keeping music ducked."""
        if not text or not self.enable_tts:
            return False
        already_ducked = self._duck_saved is not None
        did_duck = False if already_ducked else self._begin_duck()
        self._tts_speaking = True
        try:
            return bool(
                speak_text(
                    text,
                    voice=self.tts_voice,
                    rate_pct=self.tts_rate_pct,
                    pitch_hz=self.tts_pitch_hz,
                    enabled=self.enable_tts,
                )
            )
        finally:
            self._tts_speaking = False
            if keep_awake_after_sec > 0.0:
                self._wake_display(keep_awake_after_sec)
            if did_duck:
                self._restore_duck(skip_restore=False)

    def test_speaker(self, text: str | None = None) -> dict[str, Any]:
        """Play a test chime + TTS sample directly on the built-in speaker."""
        self._wake_display(8.0)
        sample = (text or "").strip() or f"안녕하세요, 포코박스입니다. 현재 내장 스피커 음량은 {self.speaker_volume}퍼센트입니다."
        self._play_chime("ok")
        spoken = False
        if self.enable_tts:
            self._tts_speaking = True
            try:
                spoken = speak_text(
                    sample,
                    voice=self.tts_voice,
                    rate_pct=self.tts_rate_pct,
                    pitch_hz=self.tts_pitch_hz,
                    enabled=True,
                )
            finally:
                self._tts_speaking = False
        return {
            "ok": True,
            "spoken": spoken,
            "voice": self.tts_voice,
            "speaker_volume": self.speaker_volume,
            "text": sample,
        }

    def _is_spoken_action(self, action: str) -> bool:
        return action in (IntentAction.ANSWER_QA.value, IntentAction.WEATHER.value, IntentAction.NOW_PLAYING.value)

    def _is_quiz_action(self, action: str) -> bool:
        return action in (IntentAction.START_QUIZ.value, IntentAction.STOP_QUIZ.value, "quiz_turn")

    def _is_volume_or_mute_action(self, action: str) -> bool:
        return action in (
            IntentAction.VOLUME_SET.value,
            IntentAction.VOLUME_UP.value,
            IntentAction.VOLUME_DOWN.value,
            IntentAction.MUTE.value,
            IntentAction.UNMUTE.value,
        )

    def _execute_intent_with_ack(
        self,
        intent: VoiceIntent,
        *,
        dry_run: bool = False,
    ) -> tuple[ExecutionResult, bool]:
        """Speak pre-execution confirmation before executing an actionable command."""
        ack_text = format_command_ack(intent, is_dac_mode=self.is_dac_mode())
        ack_spoken = False
        already_ducked = self._duck_saved is not None
        did_duck = False

        if not dry_run and ack_text and self.enable_tts and self.enable_command_ack:
            did_duck = False if already_ducked else self._begin_duck()
            self.events.publish({
                "type": "ack",
                "text": ack_text,
                "user_text": intent.raw_text,
                "action": intent.action.value,
                "ts": time.time(),
            })
            log.info("CMD ACK (%s): %s", intent.action.value, ack_text)
            ack_spoken = self._speak_answer(ack_text)

        try:
            res = self.executor.execute(intent, dry_run=dry_run)
            if ack_text:
                res.details["ack_text"] = ack_text
            res.details["ack_spoken"] = ack_spoken
            if not dry_run and ack_spoken and not res.ok:
                self._speak_answer(format_command_failure(res))
        finally:
            if did_duck:
                skip_restore = self._is_volume_or_mute_action(intent.action.value) and not res.details.get("volume_disabled")
                self._restore_duck(skip_restore=skip_restore)

        return res, ack_spoken

    def _pause_lms_for_quiz(self) -> None:
        if self._quiz_paused_lms is not None:
            return
        try:
            pid = self.lms.resolve_player_id(self.player_name)
            st = self.lms.player_status(pid)
            if st.get("mode") == "play":
                self.lms.pause(pid, 1)
                self._quiz_paused_lms = pid
                log.info("Paused LMS player %s for English listening quiz", pid)
        except Exception as exc:
            log.debug("LMS pause for quiz skipped: %s", exc)

    def _resume_lms_after_quiz(self) -> None:
        pid = self._quiz_paused_lms
        self._quiz_paused_lms = None
        if not pid:
            return
        try:
            self.lms.play(pid)
            log.info("Resumed LMS player %s after English listening quiz", pid)
        except Exception as exc:
            log.warning("Failed to resume LMS after quiz: %s", exc)

    def _publish_quiz_event(self, turn: QuizTurnResponse, *, user_text: str = "") -> None:
        self.events.publish({
            "type": "quiz",
            "event": turn.event,
            "active": turn.active,
            "header": turn.display_header,
            "text": turn.display_text,
            "status": turn.display_status,
            "user_text": user_text,
            "quiz": turn.state,
            "ts": time.time(),
        })

    def _speak_quiz_response(self, turn: QuizTurnResponse, *, play_ready_cue: bool = True) -> None:
        self._tts_speaking = True
        try:
            if turn.chime:
                self._play_chime(turn.chime)
            if turn.segments and self.enable_tts:
                speak_segments(
                    turn.segments,
                    ko_voice=self.tts_voice,
                    en_voice=self.quiz_en_voice,
                    ko_rate_pct=self.tts_rate_pct,
                    en_rate_pct=self.quiz_en_rate_pct,
                    slow_en_rate_pct=max(-30, self.quiz_en_rate_pct - 10),
                    pitch_hz=self.tts_pitch_hz,
                    enabled=self.enable_tts,
                )
            if play_ready_cue and turn.expect_response and turn.active:
                time.sleep(0.15)
                self._play_chime("quiz_prompt")
                self.events.publish({
                    "type": "quiz_ready",
                    "quiz": turn.state,
                    "ts": time.time(),
                })
        finally:
            self._tts_speaking = False

    def _quiz_turn_to_result(self, turn: QuizTurnResponse, action: str, raw_text: str = "") -> ExecutionResult:
        res = ExecutionResult(
            ok=turn.ok,
            action=action,
            message=turn.spoken_summary,
            intent={"action": action, "raw_text": raw_text},
            details={
                "quiz_event": turn.event,
                "quiz": turn.state,
                "display_header": turn.display_header,
                "display_text": turn.display_text,
                "display_status": turn.display_status,
            },
        )
        self.last_result = res.to_dict()
        return res

    def _maybe_generate_custom_quiz_questions(self, topic: str, count: int) -> list[Any] | None:
        want_llm = _env_bool("QUIZ_USE_LLM_GENERATION", True)
        recent_en = self.quiz.recent_english_texts()
        llm_results: list[Any] = []
        if want_llm and (self.interpreter.gemini_api_key or self.interpreter.openai_api_key):
            try:
                raw_list = self.interpreter.generate_kids_quiz_questions(
                    topic=topic,
                    count=count,
                    exclude_english=recent_en,
                )
                llm_results = [
                    q
                    for i, item in enumerate(raw_list)
                    if isinstance(item, dict) and (q := question_from_dict(item, i)) is not None
                ]
            except Exception as exc:
                log.warning("LLM dynamic quiz generation failed; falling back to procedural generator: %s", exc)

        if len(llm_results) >= count:
            return llm_results[:count]

        # Top up with infinite procedural generation so questions are ALWAYS dynamic and infinite
        needed = count - len(llm_results)
        exclude_set = {q.english_text.strip().lower() for q in llm_results} | {t.strip().lower() for t in recent_en}
        procedural = generate_infinite_quiz_questions(
            topic=topic,
            count=needed,
            exclude_texts=exclude_set,
        )
        combined = llm_results + procedural
        return combined[:count] if combined else None

    def start_quiz(
        self,
        topic: str = "all",
        count: int = DEFAULT_QUIZ_COUNT,
        *,
        voice_loop: bool = False,
        dry_run: bool = False,
        raw_text: str = "",
    ) -> ExecutionResult:
        # Stop any prior voice loop cleanly before starting a fresh session
        self._quiz_loop_stop.set()
        if not dry_run:
            self._pause_lms_for_quiz()

        custom_qs = None if dry_run else self._maybe_generate_custom_quiz_questions(topic, count)
        turn = self.quiz.start(topic=topic, count=count, custom_questions=custom_qs)
        self._publish_quiz_event(turn)
        res = self._quiz_turn_to_result(turn, IntentAction.START_QUIZ.value, raw_text=raw_text)

        if not dry_run and turn.ok and turn.active:
            if voice_loop:
                self._launch_quiz_voice_loop(turn)
            else:
                threading.Thread(target=self._speak_quiz_response, args=(turn,), daemon=True).start()
        elif not turn.active and not dry_run:
            self._resume_lms_after_quiz()
        return res

    def answer_quiz_text(
        self,
        text: str,
        *,
        dry_run: bool = False,
        use_cloud: bool = True,
        speak: bool = True,
    ) -> ExecutionResult:
        q = self.quiz.current_question()
        llm_is_correct: bool | None = None
        if q is not None and use_cloud and classify_quiz_command(text) is None and not is_local_answer_match(text, q):
            llm_is_correct = self.interpreter.grade_quiz_text_answer(
                text,
                english_text=q.english_text,
                korean_meaning=q.korean_meaning,
                accepted_answers=q.accepted_answers,
                kind=q.kind,
            )

        turn = self.quiz.handle_input(text, llm_is_correct=llm_is_correct)
        self._publish_quiz_event(turn, user_text=text)
        res = self._quiz_turn_to_result(turn, "quiz_turn", raw_text=text)

        if not turn.active:
            self._quiz_loop_stop.set()
            if not dry_run:
                self._wake_display(10.0)

        if not dry_run and speak:
            def _speak_and_cleanup() -> None:
                self._speak_quiz_response(turn)
                if not turn.active:
                    self._wake_display(10.0)
                    self._resume_lms_after_quiz()

            threading.Thread(target=_speak_and_cleanup, daemon=True).start()
        elif not turn.active and not dry_run:
            self._resume_lms_after_quiz()
        return res

    def stop_quiz(self, *, dry_run: bool = False, speak: bool = True, reason: str = "user") -> ExecutionResult:
        self._quiz_loop_stop.set()
        turn = self.quiz.stop(reason=reason)
        self._publish_quiz_event(turn)
        res = self._quiz_turn_to_result(turn, IntentAction.STOP_QUIZ.value)
        if not dry_run:
            self._wake_display(10.0)
        if not dry_run and speak:
            def _speak_and_resume() -> None:
                self._speak_quiz_response(turn)
                self._wake_display(10.0)
                self._resume_lms_after_quiz()

            threading.Thread(target=_speak_and_resume, daemon=True).start()
        elif not dry_run:
            self._resume_lms_after_quiz()
        return res

    def _launch_quiz_voice_loop(self, initial_turn: QuizTurnResponse) -> None:
        self._quiz_loop_stop.clear()
        self._quiz_loop_active = True
        t = threading.Thread(
            target=self._run_quiz_voice_loop,
            args=(initial_turn,),
            name="english-quiz-voice-loop",
            daemon=True,
        )
        self._quiz_thread = t
        t.start()

    def _run_quiz_voice_loop(self, initial_turn: QuizTurnResponse) -> None:
        """Hands-free conversational voice loop: speak English prompt -> listen -> grade -> repeat."""
        self._quiz_loop_active = True
        silent_streak = 0
        try:
            self._speak_quiz_response(initial_turn)
            while self.quiz.active and not self._quiz_loop_stop.is_set():
                q = self.quiz.current_question()
                if q is None:
                    break
                if not self.interpreter.can_transcribe_audio():
                    log.warning("Voice quiz loop paused: neither LOCAL_STT_URL nor cloud STT API key is configured.")
                    break

                # Ready chime ("quiz_prompt") was just played by _speak_quiz_response right before opening the mic
                self.events.publish({"type": "state", "state": "recording", "rms": 0.0, "ts": time.time()})
                wav_bytes, metrics = record_utterance_wav(
                    max_duration_sec=self.quiz_listen_max_sec,
                    silence_timeout_sec=self.quiz_silence_sec,
                    min_threshold=self.vad_threshold,
                    source=os.environ.get("VOICE_PULSE_SOURCE") or None,
                )
                if self._quiz_loop_stop.is_set() or not self.quiz.active:
                    break

                if not metrics.has_speech:
                    silent_streak += 1
                    if silent_streak == 1:
                        retry_turn = QuizTurnResponse(
                            ok=True,
                            event="repeat",
                            active=True,
                            chime=None,
                            segments=[
                                ("en", "Take your time. Let's listen again."),
                                ("pause", "0.25"),
                                ("en_slow", q.english_text),
                                ("pause", "0.2"),
                                ("en", "What is your answer?"),
                            ],
                            display_header=f"🎧 영어 리스닝 퀴즈 ({self.quiz.public_state()['question_num']}/{self.quiz.public_state()['total']} · ⭐ {self.quiz.score}점)",
                            display_text="🔁 Take your time! Listen again.",
                            display_status="🔔 신호음 후 대답하거나 '힌트', '패스', '그만'이라고 해보세요",
                            spoken_summary="천천히 다시 들려드릴게요!",
                            state=self.quiz.public_state(),
                            expect_response=True,
                        )
                        self._publish_quiz_event(retry_turn)
                        self._speak_quiz_response(retry_turn)
                        continue
                    timeout_turn = self.quiz.stop(reason="timeout")
                    self._publish_quiz_event(timeout_turn)
                    self._quiz_turn_to_result(timeout_turn, IntentAction.STOP_QUIZ.value)
                    self._speak_quiz_response(timeout_turn)
                    break

                self.events.publish({"type": "state", "state": "processing", "rms": 0.0, "ts": time.time()})
                prepared = filter_wav_speech_band(wav_bytes) if self.use_bandpass else wav_bytes
                trimmed = trim_silence_wav(prepared, threshold=max(20.0, self.vad_threshold * 0.7))
                boosted = normalize_wav_gain(trimmed)
                try:
                    stt = self.interpreter.transcribe_and_grade_quiz_audio(
                        boosted,
                        english_text=q.english_text,
                        korean_meaning=q.korean_meaning,
                        accepted_answers=q.accepted_answers,
                        kind=q.kind,
                    )
                except Exception as exc:
                    log.warning("Quiz audio STT failed: %s", exc)
                    silent_streak += 1
                    if silent_streak >= 2:
                        break
                    continue

                if self._quiz_loop_stop.is_set() or not self.quiz.active:
                    break

                transcript = str(stt.get("transcript") or "").strip()
                cmd = str(stt.get("command") or "").strip().lower() or None
                is_correct = stt.get("is_correct") if isinstance(stt.get("is_correct"), bool) else None

                if not transcript:
                    silent_streak += 1
                    if silent_streak == 1:
                        retry_turn = QuizTurnResponse(
                            ok=True,
                            event="repeat",
                            active=True,
                            chime=None,
                            segments=[
                                ("en", "Sorry, I didn't catch that. Let's listen again."),
                                ("pause", "0.25"),
                                ("en_slow", q.english_text),
                                ("pause", "0.2"),
                                ("en", "What is your answer?"),
                            ],
                            display_header=f"🎧 영어 리스닝 퀴즈 ({self.quiz.public_state()['question_num']}/{self.quiz.public_state()['total']} · ⭐ {self.quiz.score}점)",
                            display_text="🔁 잘 못 들었어요! 다시 한 번 말씀해 주세요",
                            display_status="🔔 신호음 후 대답하거나 '힌트', '패스', '그만'이라고 해보세요",
                            spoken_summary="잘 못 들었어요! 다시 한 번 들려드릴게요.",
                            state=self.quiz.public_state(),
                            expect_response=True,
                        )
                        self._publish_quiz_event(retry_turn)
                        self._speak_quiz_response(retry_turn)
                        continue
                    timeout_turn = self.quiz.stop(reason="timeout")
                    self._publish_quiz_event(timeout_turn)
                    self._quiz_turn_to_result(timeout_turn, IntentAction.STOP_QUIZ.value)
                    self._speak_quiz_response(timeout_turn)
                    break

                silent_streak = 0
                self.events.publish({"type": "transcript", "text": transcript, "wake": True, "ts": time.time()})
                turn = self.quiz.handle_input(transcript, llm_command=cmd, llm_is_correct=is_correct)
                self._publish_quiz_event(turn, user_text=transcript)
                self._quiz_turn_to_result(turn, "quiz_turn", raw_text=transcript)
                log.info("QUIZ VOICE '%s' -> event=%s active=%s score=%d", transcript, turn.event, turn.active, self.quiz.score)
                self._speak_quiz_response(turn)
                if not turn.active:
                    break
        except Exception as exc:
            log.error("Quiz voice loop error: %s", exc)
        finally:
            self._quiz_loop_active = False
            self._wake_display(10.0)
            if not self.quiz.active:
                self._resume_lms_after_quiz()
            self.events.publish({"type": "state", "state": "waiting", "rms": 0.0, "ts": time.time()})

    def handle_wake_utterance(self, wav_bytes: bytes | None, phrase: str) -> None:
        """Process the clean post-wake command captured while music was ducked."""
        skip_restore = False
        wake_meta = dict(self.continuous_listener.last_wake_meta) if self.continuous_listener else {"phrase": phrase}
        wake_meta.setdefault("phrase", phrase)
        clip_path = wake_meta.pop("clip_path", None)
        try:
            if wav_bytes is None:
                self._clear_display_wake()
                log.info("Wake word '%s' timed out with no follow-up speech.", phrase)
                new_clip = self.telemetry.relabel_wake_clip(clip_path, "fp", {"fp_reason": "timeout_no_speech"})
                self.telemetry.record("wake_timeout_false_alarm", clip_path=new_clip, **wake_meta)
                self.events.publish({
                    "type": "result",
                    "text": "",
                    "ok": False,
                    "action": IntentAction.UNKNOWN.value,
                    "message": "",
                    "ignored_no_wake": True,
                    "ts": time.time(),
                })
                return
            if not self.interpreter.can_transcribe_audio():
                log.warning("Wake command captured, but neither LOCAL_STT_URL nor cloud STT API key is configured.")
                return
            res = self.handle_wav(
                wav_bytes,
                dry_run=False,
                enforce_wake_word=False,
                apply_bandpass=False,  # already bandpass-filtered in ContinuousVoiceListener
                start_quiz_voice_loop=True,
            )
            transcript = (res.intent or {}).get("raw_text", "") if isinstance(res.intent, dict) else ""
            is_wake_only = (
                bool(res.details and res.details.get("wake_only"))
                or (isinstance(res.intent, dict) and bool(res.intent.get("wake_word_detected")))
            )
            cmd_clip = res.details.get("command_clip_path") if res.details else None
            extra_meta = {"transcript": transcript, "action": res.action}
            if cmd_clip:
                extra_meta["command_clip"] = cmd_clip

            if (res.ok and res.action != IntentAction.UNKNOWN.value) or is_wake_only:
                new_clip = self.telemetry.relabel_wake_clip(
                    clip_path, "tp", extra_meta
                )
                self.telemetry.record(
                    "wake_command_ok",
                    clip_path=new_clip,
                    command_clip=cmd_clip,
                    transcript=transcript,
                    action=res.action,
                    **wake_meta,
                )
            else:
                new_clip = self.telemetry.relabel_wake_clip(
                    clip_path, "fp", {**extra_meta, "fp_reason": "empty_or_unknown"}
                )
                self.telemetry.record(
                    "wake_command_empty",
                    clip_path=new_clip,
                    command_clip=cmd_clip,
                    transcript=transcript,
                    action=res.action,
                    **wake_meta,
                )
            if self._is_volume_or_mute_action(res.action):
                skip_restore = not res.details.get("volume_disabled")
            if self._is_quiz_action(res.action):
                pass  # quiz handler already speaks bilingual TTS + chime
            elif res.ok and (self._is_spoken_action(res.action) or res.details.get("spoken")):
                self._speak_answer(res.message, keep_awake_after_sec=15.0)
            elif res.ok and not res.details.get("ack_spoken"):
                self._play_chime("ok")
        finally:
            self._restore_duck(skip_restore=skip_restore)

    def _init_tidal_autoplay_bg(self) -> None:
        """Enable LMS Don't Stop The Music (PLUGIN_TIDAL_DSTM) on startup for continuous TIDAL radio."""
        for _ in range(5):
            try:
                pid = self.lms.resolve_player_id(self.player_name)
                if self.lms.ensure_tidal_dstm(pid):
                    log.info("Enabled LMS Don't Stop The Music (PLUGIN_TIDAL_DSTM) for %s", pid)
                return
            except Exception as exc:
                log.debug("Waiting for LMS to enable TIDAL autoplay: %s", exc)
                time.sleep(2.0)

    def _sync_dynamic_catalog_bg(self) -> None:
        """Periodically sync LMS favorites, playlists, and recently played items into DynamicMusicCatalog."""
        from voice_control.dynamic_catalog import get_dynamic_catalog
        catalog = get_dynamic_catalog()
        time.sleep(3.0)  # Short initial wait for LMS to be ready
        while True:
            try:
                pid = self.lms.resolve_player_id(self.player_name, timeout=1.5)
                if pid:
                    catalog.update_from_lms(self.lms, pid)
            except Exception as exc:
                log.debug("Dynamic catalog sync background error: %s", exc)
            time.sleep(300.0)

    def start_background_listeners(self) -> None:
        if self.enable_tidal_autoplay:
            threading.Thread(target=self._init_tidal_autoplay_bg, name="tidal-dstm-init", daemon=True).start()
        threading.Thread(target=self._sync_dynamic_catalog_bg, name="dynamic-catalog-sync", daemon=True).start()
        if self.continuous_listener is not None:
            self.continuous_listener.start()
        if self.background_verifier is not None:
            self.background_verifier.start()

    def handle_text(self, text: str, dry_run: bool = False, use_cloud: bool = True) -> ExecutionResult:
        self.events.publish({"type": "transcript", "text": text, "wake": True, "ts": time.time()})
        # If an English listening quiz is active, route utterances straight to the quiz session
        if self.quiz.active:
            local_intent = self.interpreter.interpret_text(text, use_cloud_fallback=False)
            if local_intent.action == IntentAction.START_QUIZ:
                return self.start_quiz(
                    topic=local_intent.quiz_topic or "all",
                    count=local_intent.quiz_count or DEFAULT_QUIZ_COUNT,
                    voice_loop=False,
                    dry_run=dry_run,
                    raw_text=text,
                )
            if local_intent.action == IntentAction.STOP_QUIZ:
                return self.stop_quiz(dry_run=dry_run)
            return self.answer_quiz_text(text, dry_run=dry_run, use_cloud=use_cloud)

        intent = self.interpreter.interpret_text(text, use_cloud_fallback=use_cloud, context=self.conversation_context)
        if intent.action == IntentAction.START_QUIZ:
            return self.start_quiz(
                topic=intent.quiz_topic or "all",
                count=intent.quiz_count or DEFAULT_QUIZ_COUNT,
                voice_loop=False,
                dry_run=dry_run,
                raw_text=intent.raw_text or text,
            )
        if intent.action == IntentAction.STOP_QUIZ:
            return self.stop_quiz(dry_run=dry_run)

        res, _ = self._execute_intent_with_ack(intent, dry_run=dry_run)
        if not dry_run:
            self._update_display_wake_for_action(res.action)
            if res.ok:
                loc = res.details.get("location") or (intent.query if res.action == IntentAction.WEATHER.value else None)
                trk_title = res.details.get("title") or res.details.get("track")
                art_name = res.details.get("artist")
                self.conversation_context.record_turn(
                    "user",
                    intent.raw_text or text,
                    action=res.action,
                    query=intent.query,
                    display_query=intent.display_query,
                    location=loc,
                    volume_delta=intent.delta,
                )
                self.conversation_context.record_turn(
                    "assistant",
                    res.message,
                    track_title=trk_title,
                    artist=art_name,
                    location=loc,
                )
                if trk_title or art_name:
                    try:
                        from voice_control.dynamic_catalog import get_dynamic_catalog
                        get_dynamic_catalog().add_track(trk_title or "", art_name or "", source="playback")
                    except Exception:
                        pass
        self.last_result = res.to_dict()
        self.events.publish({
            "type": "result",
            "text": intent.raw_text or text,
            "ok": res.ok,
            "action": res.action,
            "message": res.message,
            "ignored_no_wake": False,
            "ts": time.time(),
        })
        log.info("TEXT '%s' -> action=%s ok=%s msg=%s", text, res.action, res.ok, res.message)

        if self.telemetry is not None and not dry_run:
            record_command_result(
                self.telemetry,
                transcript=intent.raw_text or text,
                action=res.action,
                ok=res.ok,
                provider=intent.provider,
                confidence=intent.confidence,
                stt_ms=0.0,
                music_playing=False,
                wake_word_detected=intent.wake_word_detected,
                search_type=intent.search_type,
                query=intent.query,
                message=res.message,
                duration_sec=0.0,
                input_type="text",
            )
        if not dry_run and res.ok and (self._is_spoken_action(res.action) or res.details.get("spoken")):
            threading.Thread(
                target=self._speak_answer,
                args=(res.message,),
                kwargs={"keep_awake_after_sec": 15.0},
                daemon=True,
            ).start()
        return res

    def handle_wav(
        self,
        wav_bytes: bytes,
        dry_run: bool = False,
        enforce_wake_word: bool = False,
        apply_bandpass: bool = True,
        start_quiz_voice_loop: bool = False,
    ) -> ExecutionResult:
        prepared = filter_wav_speech_band(wav_bytes) if (apply_bandpass and self.use_bandpass) else wav_bytes
        metrics = analyze_wav_bytes(prepared, speech_threshold=self.vad_threshold)
        if not metrics.has_speech:
            res = ExecutionResult(
                ok=False,
                action=IntentAction.UNKNOWN.value,
                message="음성이 감지되지 않았습니다.",
                details={"vad": metrics.__dict__},
            )
            self.last_result = res.to_dict()
            self.events.publish({"type": "result", "text": "", "ok": False, "message": res.message, "ignored_no_wake": True})
            return res

        trimmed = trim_silence_wav(prepared, threshold=max(20.0, self.vad_threshold * 0.7))
        boosted = normalize_wav_gain(trimmed)

        # If an English quiz is currently active, evaluate the audio directly as a quiz answer!
        if self.quiz.active:
            q = self.quiz.current_question()
            if q is not None:
                try:
                    stt = self.interpreter.transcribe_and_grade_quiz_audio(
                        boosted,
                        english_text=q.english_text,
                        korean_meaning=q.korean_meaning,
                        accepted_answers=q.accepted_answers,
                        kind=q.kind,
                    )
                except Exception as exc:
                    return ExecutionResult(ok=False, action="quiz_turn", message=str(exc), details={"vad": metrics.__dict__})
                transcript = str(stt.get("transcript") or "").strip()
                cmd = str(stt.get("command") or "").strip().lower() or None
                is_correct = stt.get("is_correct") if isinstance(stt.get("is_correct"), bool) else None
                if not transcript:
                    return ExecutionResult(ok=False, action="quiz_turn", message="음성을 인식하지 못했습니다.", details={"vad": metrics.__dict__})
                self.events.publish({"type": "transcript", "text": transcript, "wake": True, "ts": time.time()})
                turn = self.quiz.handle_input(transcript, llm_command=cmd, llm_is_correct=is_correct)
                self._publish_quiz_event(turn, user_text=transcript)
                res = self._quiz_turn_to_result(turn, "quiz_turn", raw_text=transcript)
                res.details["vad"] = metrics.__dict__
                if not dry_run:
                    def _speak_and_maybe_resume() -> None:
                        self._speak_quiz_response(turn)
                        if not turn.active:
                            self._resume_lms_after_quiz()
                    threading.Thread(target=_speak_and_maybe_resume, daemon=True).start()
                elif not turn.active:
                    self._resume_lms_after_quiz()
                return res

        t_stt = time.perf_counter()
        try:
            intent = self.interpreter.interpret_audio(
                boosted,
                enforce_wake_word=(enforce_wake_word and self.require_wake_word),
                context=self.conversation_context,
            )
        except Exception as exc:
            res = ExecutionResult(
                ok=False,
                action=IntentAction.UNKNOWN.value,
                message=str(exc),
                details={"vad": metrics.__dict__},
            )
            self.last_result = res.to_dict()
            self.events.publish({"type": "result", "text": "", "ok": False, "message": res.message, "ignored_no_wake": False})
            return res
        stt_ms = round((time.perf_counter() - t_stt) * 1000.0, 1)

        if enforce_wake_word and self.require_wake_word and not intent.wake_word_detected:
            # Feed music/ambient RMS back into continuous listener so playing music stops re-triggering!
            if self.continuous_listener is not None:
                self.continuous_listener.notify_ambient_rms(metrics.rms_mean)
            self.telemetry.record(
                "rms_ignored_music",
                rms_mean=metrics.rms_mean,
                rms_peak=metrics.rms_peak,
                duration_sec=metrics.duration_sec,
                transcript=intent.raw_text,
                music_playing=bool(self.continuous_listener.music_playing) if self.continuous_listener else False,
            )
            # Save command clip for ignored ambient speech if transcript contains words
            if self.telemetry is not None and not dry_run and wav_bytes and len(intent.raw_text.strip()) >= 2:
                cmd_meta = {
                    "transcript": intent.raw_text,
                    "action": "ignored_speech",
                    "ok": False,
                    "message": "호출어('포코야')가 없어 무시되었습니다.",
                    "provider": intent.provider,
                    "confidence": intent.confidence,
                    "stt_ms": stt_ms,
                    "vad": metrics.__dict__,
                    "music_playing": bool(self.continuous_listener.music_playing) if self.continuous_listener else False,
                    "ignored_no_wake": True,
                }
                self.telemetry.save_command_clip(wav_bytes, "ignored_speech", cmd_meta)
            log.info("Ignored ambient/music audio (rms=%.1f): '%s'", metrics.rms_mean, intent.raw_text)
            self.events.publish({
                "type": "result",
                "text": "",
                "ok": False,
                "action": IntentAction.UNKNOWN.value,
                "message": "",
                "ignored_no_wake": True,
                "ts": time.time(),
            })
            return ExecutionResult(
                ok=False,
                action=IntentAction.UNKNOWN.value,
                message="호출어('포코야')가 없어 무시되었습니다.",
                intent=intent.to_dict(),
                details={"vad": metrics.__dict__, "ignored_no_wake_word": True},
            )

        # Immediately push recognized subtitle to the screen BEFORE TIDAL search runs!
        if intent.raw_text:
            self.events.publish({
                "type": "transcript",
                "text": intent.raw_text,
                "wake": True,
                "ts": time.time(),
            })

        # If user called ONLY the wake word ("포코야" / "헤이 포코") with no command yet,
        # immediately duck music, chime, and wait for the follow-up command!
        if (
            intent.wake_word_detected
            and intent.action == IntentAction.UNKNOWN
            and not dry_run
            and enforce_wake_word
        ):
            self._wake_display(12.0)
            log.info("Bare wake word '%s' recognized via cloud -> entering auto-duck follow-up listen", intent.raw_text)
            self.events.publish({
                "type": "transcript",
                "text": "🎤 네! 명령을 말씀하세요...",
                "wake": True,
                "ts": time.time(),
            })
            return ExecutionResult(
                ok=True,
                action="wake_only",
                message="네! 명령을 말씀하세요...",
                intent=intent.to_dict(),
                details={"vad": metrics.__dict__, "wake_only": True},
            )

        if intent.action == IntentAction.START_QUIZ:
            res = self.start_quiz(
                topic=intent.quiz_topic or "all",
                count=intent.quiz_count or DEFAULT_QUIZ_COUNT,
                voice_loop=(start_quiz_voice_loop and not dry_run),
                dry_run=dry_run,
                raw_text=intent.raw_text,
            )
            res.details["vad"] = metrics.__dict__
            return res

        if intent.action == IntentAction.STOP_QUIZ:
            res = self.stop_quiz(dry_run=dry_run)
            res.details["vad"] = metrics.__dict__
            return res

        res, _ = self._execute_intent_with_ack(intent, dry_run=dry_run)
        if not dry_run:
            self._update_display_wake_for_action(res.action)
            if res.ok:
                loc = res.details.get("location") or (intent.query if res.action == IntentAction.WEATHER.value else None)
                trk_title = res.details.get("title") or res.details.get("track")
                art_name = res.details.get("artist")
                self.conversation_context.record_turn(
                    "user",
                    intent.raw_text,
                    action=res.action,
                    query=intent.query,
                    display_query=intent.display_query,
                    location=loc,
                    volume_delta=intent.delta,
                )
                self.conversation_context.record_turn(
                    "assistant",
                    res.message,
                    track_title=trk_title,
                    artist=art_name,
                    location=loc,
                )
                if trk_title or art_name:
                    try:
                        from voice_control.dynamic_catalog import get_dynamic_catalog
                        get_dynamic_catalog().add_track(trk_title or "", art_name or "", source="playback")
                    except Exception:
                        pass
        res.details["vad"] = metrics.__dict__
        res.details["stt_ms"] = stt_ms
        res.details["stt_provider"] = intent.provider

        if self.telemetry is not None and not dry_run and wav_bytes:
            cmd_meta = {
                "transcript": intent.raw_text,
                "action": res.action,
                "ok": res.ok,
                "message": res.message,
                "provider": intent.provider,
                "confidence": intent.confidence,
                "stt_ms": stt_ms,
                "vad": metrics.__dict__,
                "music_playing": bool(self.continuous_listener.music_playing) if self.continuous_listener else False,
                "gemini_stats": self.interpreter.quota_tracker.stats() if self.interpreter else None,
            }
            cmd_clip = self.telemetry.save_command_clip(wav_bytes, res.action, cmd_meta)
            if cmd_clip:
                res.details["command_clip_path"] = cmd_clip
            record_command_result(
                self.telemetry,
                transcript=intent.raw_text,
                action=res.action,
                ok=res.ok,
                provider=intent.provider,
                confidence=intent.confidence,
                stt_ms=stt_ms,
                music_playing=bool(self.continuous_listener.music_playing) if self.continuous_listener else False,
                wake_word_detected=intent.wake_word_detected,
                search_type=intent.search_type,
                query=intent.query,
                message=res.message,
                duration_sec=metrics.duration_sec,
            )

        self.last_result = res.to_dict()
        self.events.publish({
            "type": "result",
            "text": intent.raw_text,
            "ok": res.ok,
            "action": res.action,
            "message": res.message,
            "ignored_no_wake": False,
            "ts": time.time(),
        })
        log.info(
            "AUDIO (%.2fs) [%s %.0fms] -> '%s' action=%s ok=%s",
            metrics.duration_sec,
            intent.provider,
            stt_ms,
            intent.raw_text,
            res.action,
            res.ok,
        )
        return res

    def handle_continuous_wav(self, wav_bytes: bytes) -> None:
        if not self.interpreter.can_transcribe_audio():
            now = time.monotonic()
            if now - self._last_nokey_warn > 60:
                log.warning("Speech detected in always-on mode, but neither LOCAL_STT_URL nor cloud STT API key is configured.")
                self._last_nokey_warn = now
            return
        # Audio from ContinuousVoiceListener is already RNNoise + bandpass filtered
        res = self.handle_wav(
            wav_bytes,
            dry_run=False,
            enforce_wake_word=True,
            apply_bandpass=False,
            start_quiz_voice_loop=True,
        )
        if res.action == "wake_only":
            # User said just "포코야" -> duck music to 18%, chime, and capture the command cleanly!
            self.listen_once(dry_run=False)
            return
        if self._is_quiz_action(res.action):
            return
        if res.ok and self._is_spoken_action(res.action):
            self._speak_answer(res.message, keep_awake_after_sec=15.0)
        elif res.ok and not res.details.get("ack_spoken"):
            self._play_chime("ok")

    def listen_once(self, dry_run: bool = False) -> ExecutionResult:
        if self._quiz_loop_active:
            if not self.quiz.active:
                for _ in range(25):
                    if not self._quiz_loop_active:
                        break
                    time.sleep(0.1)
            if self._quiz_loop_active:
                if self.quiz.active:
                    msg = "영어 퀴즈가 음성으로 진행 중입니다. 신호음 후 정답을 말씀해 주세요."
                else:
                    msg = "영어 퀴즈 마무리 중입니다. 잠시 후 다시 눌러주세요."
                return ExecutionResult(ok=False, action="busy", message=msg)
        if not self._listen_lock.acquire(blocking=False):
            return ExecutionResult(ok=False, action="busy", message="이미 음성을 듣고 있습니다. 편하게 말씀해 주세요.")
        self._manual_busy = True
        skip_restore = False
        try:
            self._begin_duck()
            self._play_chime("listen")
            self.events.publish({"type": "state", "state": "recording", "rms": 0.0, "ts": time.time()})
            wav_bytes, metrics = record_utterance_wav(
                max_duration_sec=float(os.environ.get("VOICE_MAX_SEC", "6.0")),
                silence_timeout_sec=float(os.environ.get("VOICE_SILENCE_SEC", "1.0")),
                min_threshold=self.vad_threshold,
                source=os.environ.get("VOICE_PULSE_SOURCE") or None,
            )
            if not metrics.has_speech:
                self._play_chime("error")
                res = ExecutionResult(
                    ok=False,
                    action=IntentAction.UNKNOWN.value,
                    message="음성이 감지되지 않았습니다.",
                    details={"vad": metrics.__dict__},
                )
                self.last_result = res.to_dict()
                self.events.publish({"type": "result", "text": "", "ok": False, "message": res.message, "ignored_no_wake": False})
                return res
            self.events.publish({"type": "state", "state": "processing", "rms": 0.0, "ts": time.time()})
            res = self.handle_wav(
                wav_bytes,
                dry_run=dry_run,
                enforce_wake_word=False,
                apply_bandpass=True,
                start_quiz_voice_loop=not dry_run,
            )
            if self._is_volume_or_mute_action(res.action):
                skip_restore = not res.details.get("volume_disabled")
            if self._is_quiz_action(res.action):
                pass
            elif not dry_run and res.ok and (self._is_spoken_action(res.action) or res.details.get("spoken")):
                self._speak_answer(res.message, keep_awake_after_sec=15.0)
            elif not dry_run and res.details.get("ack_spoken"):
                pass  # spoken pre-execution ack (and failure TTS if failed) already played
            else:
                self._play_chime("ok" if res.ok else "error")
            return res
        finally:
            self._restore_duck(skip_restore=skip_restore)
            self._manual_busy = False
            self.events.publish({"type": "state", "state": "waiting", "rms": 0.0, "ts": time.time()})
            self._listen_lock.release()

    def update_runtime_config(self, data: dict[str, Any]) -> dict[str, Any]:
        if data.get("reset_defaults"):
            d = self._default_runtime_config
            self.require_wake_word = bool(d["require_wake_word"])
            self.duck_enabled = bool(d["duck_enabled"])
            self.duck_volume = int(d["duck_volume"])
            self.vad_threshold = float(d["quiet_threshold"])
            if self.continuous_listener:
                self.continuous_listener.min_threshold = float(d["quiet_threshold"])
                self.continuous_listener.playing_min_threshold = float(d["playing_threshold"])
                self.continuous_listener.speech_multiplier = float(d["speech_multiplier"])
                self.continuous_listener.playing_multiplier = float(d["playing_multiplier"])
                self.continuous_listener.rms_fallback_during_music = bool(d["rms_fallback_during_music"])
                if self.continuous_listener.aec3:
                    self.continuous_listener.aec3.set_delay_ms(int(d["aec3_delay_ms"]))
                if "music_confirm_chunks" in d and self.continuous_listener.wake_spotter:
                    self.continuous_listener.wake_spotter.music_confirm_chunks = int(d["music_confirm_chunks"])
                if "music_min_conf" in d and self.continuous_listener.wake_spotter:
                    self.continuous_listener.wake_spotter.music_min_confidence = float(d["music_min_conf"])
            return {"ok": True, "reset": True, "voice": self.display_state()["voice"]}

        if data.get("reset_speaker_defaults"):
            sd = self._default_speaker_config
            self.speaker_volume = set_internal_speaker_volume(int(sd["speaker_volume"]))
            self.enable_chime = bool(sd["enable_chime"])
            self.chime_volume = max(0.05, min(1.0, float(sd["chime_volume_pct"]) / 100.0))
            self.enable_tts = bool(sd["enable_tts"])
            self.enable_command_ack = bool(sd["enable_command_ack"])
            self.tts_voice = str(sd["tts_voice"])
            self.tts_rate_pct = int(sd["tts_rate_pct"])
            self.tts_pitch_hz = int(sd["tts_pitch_hz"])
            self._save_voice_prefs()
            return {"ok": True, "reset": True, "voice": self.display_state()["voice"]}

        prefs_changed = False
        if "speaker_volume" in data:
            self.speaker_volume = set_internal_speaker_volume(int(float(data["speaker_volume"])))
            prefs_changed = True
        if "enable_chime" in data:
            self.enable_chime = bool(data["enable_chime"])
            prefs_changed = True
        if "chime_volume_pct" in data:
            self.chime_volume = max(0.05, min(1.0, float(data["chime_volume_pct"]) / 100.0))
            prefs_changed = True
        if "enable_tts" in data:
            self.enable_tts = bool(data["enable_tts"])
            prefs_changed = True
        if "enable_command_ack" in data:
            self.enable_command_ack = bool(data["enable_command_ack"])
            prefs_changed = True
        if "tts_voice" in data:
            v = str(data["tts_voice"]).strip()
            if v in VALID_VOICE_IDS:
                self.tts_voice = v
                prefs_changed = True
        if "tts_rate_pct" in data:
            self.tts_rate_pct = max(-30, min(30, int(float(data["tts_rate_pct"]))))
            prefs_changed = True
        if "tts_pitch_hz" in data:
            self.tts_pitch_hz = max(-15, min(15, int(float(data["tts_pitch_hz"]))))
            prefs_changed = True
        if prefs_changed:
            self._save_voice_prefs()

        if "require_wake_word" in data:
            self.require_wake_word = bool(data["require_wake_word"])
        if "duck_enabled" in data:
            self.duck_enabled = bool(data["duck_enabled"])
        if "duck_volume" in data:
            self.duck_volume = max(5, min(60, int(float(data["duck_volume"]))))
        if "quiet_threshold" in data:
            val = max(5.0, min(500.0, float(data["quiet_threshold"])))
            self.vad_threshold = val
            if self.continuous_listener:
                self.continuous_listener.min_threshold = val
        if "playing_threshold" in data and self.continuous_listener:
            self.continuous_listener.playing_min_threshold = max(5.0, min(600.0, float(data["playing_threshold"])))
        if "multiplier" in data and self.continuous_listener:
            m = max(1.2, min(8.0, float(data["multiplier"])))
            self.continuous_listener.speech_multiplier = m
            self.continuous_listener.playing_multiplier = m
        if "rms_fallback_during_music" in data and self.continuous_listener:
            self.continuous_listener.rms_fallback_during_music = bool(data["rms_fallback_during_music"])
        if "aec3_delay_ms" in data and self.continuous_listener and self.continuous_listener.aec3:
            self.continuous_listener.aec3.set_delay_ms(int(float(data["aec3_delay_ms"])))
        if "music_confirm_chunks" in data and self.continuous_listener and self.continuous_listener.wake_spotter:
            self.continuous_listener.wake_spotter.music_confirm_chunks = max(1, min(5, int(float(data["music_confirm_chunks"]))))
        if "music_min_conf" in data and self.continuous_listener and self.continuous_listener.wake_spotter:
            self.continuous_listener.wake_spotter.music_min_confidence = max(0.1, min(0.99, float(data["music_min_conf"])))
        if data.get("reset_ambient") and self.continuous_listener:
            self.continuous_listener.ambient_rms = 10.0
        return {"ok": True, "voice": self.display_state()["voice"]}

    def replay_audio_history(self, seconds: float = 30.0, channel: str = "clean") -> dict[str, Any]:
        """Replay recent audio through VoskWakeWordSpotter and SenseVoice STT for debugging."""
        rec = self.rolling_recorder
        if rec is None:
            return {"ok": False, "error": "Rolling audio recorder not available"}
        wav_bytes = rec.get_recent_wav(seconds=seconds, channel=channel)
        if not wav_bytes or len(wav_bytes) <= 44:
            return {"ok": False, "error": "No recent audio captured in buffer"}

        wake_hits: list[dict[str, Any]] = []
        if self.continuous_listener and self.continuous_listener.wake_spotter:
            from .wakeword import VoskWakeWordSpotter

            spotter = VoskWakeWordSpotter()
            pcm = wav_bytes[44:]
            for offset in range(0, len(pcm), 3200):
                chunk = pcm[offset : offset + 3200]
                if len(chunk) < 3200:
                    continue
                hit = spotter.accept_pcm16k(chunk, is_silent=False, music_playing=self._is_music_playing())
                if hit:
                    wake_hits.append(
                        {
                            "offset_sec": round(offset / 32000.0, 2),
                            "hit": hit,
                            "event": spotter.last_event,
                        }
                    )

        transcript = ""
        stt_ms = 0.0
        intent = None
        if self.interpreter:
            try:
                t0 = time.monotonic()
                intent = self.interpreter.interpret_audio(wav_bytes)
                stt_ms = round((time.monotonic() - t0) * 1000.0, 1)
                transcript = intent.raw_text
            except Exception as exc:
                transcript = f"STT error: {exc}"

        return {
            "ok": True,
            "duration_sec": seconds,
            "channel": channel,
            "wake_hits": wake_hits,
            "transcript": transcript,
            "intent_action": intent.action.value if intent else None,
            "stt_ms": stt_ms,
            "size_bytes": len(wav_bytes),
        }

    def display_control(self, action: str) -> dict[str, Any]:
        log.info("Display control action='%s'", action)
        if self.youtube.active:
            if action == "toggle":
                act = IntentAction.PAUSE if self.youtube.playing else IntentAction.RESUME
                res = self.handle_youtube_intent(VoiceIntent(action=act))
                self._wake_display(15.0)
                return {"ok": res.ok, "action": action, "message": res.message}
            if action == "next":
                res = self.handle_youtube_intent(VoiceIntent(action=IntentAction.NEXT))
                self._wake_display(15.0)
                return {"ok": res.ok, "action": action, "message": res.message}
            if action == "prev":
                res = self.handle_youtube_intent(VoiceIntent(action=IntentAction.PREVIOUS))
                self._wake_display(15.0)
                return {"ok": res.ok, "action": action, "message": res.message}
            if action in ("vol_up", "vol_down"):
                if self.is_dac_mode():
                    return {"ok": False, "error": "DAC 모드에서는 볼륨이 100%로 고정되어 있습니다.", "fixed": True}

        player_id = self.lms.resolve_player_id(self.player_name)
        log.info("Display control action='%s' (player=%s)", action, player_id)
        if action == "toggle":
            np = self.lms.now_playing(player_id)
            if (np.get("mode") or "").strip().lower() == "stop":
                self.lms.play(player_id)
            else:
                self.lms.toggle_pause(player_id)
        elif action == "next":
            self.executor._ensure_next_track_available(player_id)
            res = self.lms.next_track(player_id)
            log.info("Display control next_track result: %s", res)
        elif action == "prev":
            res = self.lms.prev_track(player_id)
            log.info("Display control prev_track result: %s", res)
        elif action in ("vol_up", "vol_down"):
            if self.is_dac_mode():
                return {"ok": False, "error": "DAC 모드에서는 볼륨이 100%로 고정되어 있습니다.", "fixed": True}
            delta = 5 if action == "vol_up" else -5
            self.lms.adjust_volume(player_id, delta)
        else:
            return {"ok": False, "error": f"Unknown control action: {action}"}
        self._wake_display(15.0)
        return {"ok": True, "action": action}

    def route_youtube_to_dac_if_available(self) -> str | None:
        """Route YouTube / browser audio to USB DAC if plugged in."""
        if not self.is_dac_mode():
            return None
        sink = enable_usb_dac_pulseaudio()
        if sink:
            log.info("Routed YouTube audio to USB DAC sink '%s'", sink)
        return sink

    def release_youtube_from_dac(self) -> bool:
        """Release USB DAC from PulseAudio back to direct ALSA for LMS/Squeezelite."""
        ok = release_usb_dac_pulseaudio()
        if ok:
            log.info("Released USB DAC from PulseAudio back to direct ALSA for Squeezelite")
        return ok

    def _ensure_squeezelite_running(self) -> bool:
        """Ensure Squeezelite audio service is running and PulseAudio DAC profile is released."""
        try:
            if self.youtube.active:
                self.youtube.stop()
            ok = release_usb_dac_pulseaudio()
            log.info("Ensured Squeezelite running (release_usb_dac_pulseaudio result: %s)", ok)
            return ok
        except Exception as exc:
            log.warning("Failed to ensure Squeezelite running: %s", exc)
            return False

    def handle_switch_output(self, intent: VoiceIntent, dry_run: bool = False) -> ExecutionResult:
        """Handle switching current audio output between USB DAC and internal speaker."""
        t0 = time.monotonic()
        target = (intent.query or "dac").strip().lower()
        if dry_run:
            return ExecutionResult(ok=True, action=intent.action, message=f"사운드 {target} 전환 확인(dry-run)")
        if target == "speaker":
            self.release_youtube_from_dac()
            return ExecutionResult(
                ok=True,
                action=intent.action,
                message="사운드를 내장 스피커로 출력합니다.",
                intent=intent.to_dict(),
                latency_ms=round((time.monotonic() - t0) * 1000.0, 1),
            )
        if self.youtube.active:
            sink = self.route_youtube_to_dac_if_available()
            if sink:
                msg = f"유튜브 사운드를 USB DAC({sink})으로 출력합니다."
                ok = True
            else:
                msg = "연결된 USB DAC을 찾지 못했거나 활성화하지 못했습니다."
                ok = False
        else:
            self.release_youtube_from_dac()
            msg = "사운드를 USB DAC으로 출력합니다."
            ok = self.is_dac_mode()
        return ExecutionResult(
            ok=ok,
            action=intent.action,
            message=msg,
            intent=intent.to_dict(),
            latency_ms=round((time.monotonic() - t0) * 1000.0, 1),
        )

    def handle_youtube_intent(self, intent: VoiceIntent, dry_run: bool = False) -> ExecutionResult:
        """Handle YouTube and continuous K-Pop music video streaming commands."""
        t0 = time.monotonic()
        act = intent.action

        if act == IntentAction.PLAY_YOUTUBE:
            query = (intent.query or "kpop").strip()
            disp_query = intent.display_query or ("K-POP 뮤직비디오" if query == "kpop" else f"{query} 뮤직비디오")
            if dry_run:
                return ExecutionResult(
                    ok=True,
                    action=act,
                    message=f"유튜브 '{disp_query}' 재생 확인(dry-run)",
                    intent=intent.to_dict(),
                    latency_ms=round((time.monotonic() - t0) * 1000.0, 1),
                )

            # Pause LMS playback so audio does not clash
            try:
                pid = self.lms.resolve_player_id(self.player_name)
                self.lms.pause(pid, 1)
            except Exception:
                pass

            # Route YouTube/browser audio through external USB DAC if present
            self.route_youtube_to_dac_if_available()

            # Search or retrieve playlist
            videos = search_youtube_videos(query, lms_client=self.lms, player_id=self.player_name)
            self.youtube.load_queue(videos, query=query, start_index=0)
            self._wake_display(30.0)

            # Broadcast to display kiosk via SSE
            self.events.publish({
                "type": "youtube_play",
                "query": query,
                "display_query": disp_query,
                "videos": videos,
                "index": 0,
                "ts": time.time(),
            })

            cur = self.youtube.current_video()
            cur_title = cur.get("title", "") if cur else disp_query
            return ExecutionResult(
                ok=True,
                action=act,
                message=f"유튜브 '{cur_title}' 재생 시작 (연속 재생 {len(videos)}곡)",
                intent=intent.to_dict(),
                details={"youtube": self.youtube.to_dict()},
                latency_ms=round((time.monotonic() - t0) * 1000.0, 1),
            )

        if act == IntentAction.STOP_YOUTUBE:
            if dry_run:
                return ExecutionResult(ok=True, action=act, message="유튜브 재생 종료 확인(dry-run)")
            self.youtube.stop()
            self.release_youtube_from_dac()
            self.events.publish({"type": "youtube_cmd", "cmd": "stop", "ts": time.time()})
            return ExecutionResult(
                ok=True,
                action=act,
                message="유튜브 재생을 종료합니다.",
                intent=intent.to_dict(),
                latency_ms=round((time.monotonic() - t0) * 1000.0, 1),
            )

        if act == IntentAction.QUEUE_NEXT:
            if dry_run:
                return ExecutionResult(ok=True, action=act, message="유튜브 다음 영상 예약 확인(dry-run)")
            query = intent.query or intent.display_query or "kpop"
            videos = search_youtube_videos(query, limit=5, lms_client=self.lms, player_id=self.player_name)
            if not videos:
                return ExecutionResult(ok=False, action=act, message=f"유튜브에서 '{query}' 영상을 찾지 못했습니다.")
            video = videos[0]
            pos = self.youtube.insert_next(video)
            title = video.get("title", query)
            self.events.publish({
                "type": "youtube_queue_insert",
                "video": video,
                "position": pos,
                "ts": time.time(),
            })
            return ExecutionResult(
                ok=True,
                action=act,
                message=f"유튜브 '{title}' 영상을 다음 순서로 예약했습니다.",
                intent=intent.to_dict(),
                details={"video": video, "position": pos},
                latency_ms=round((time.monotonic() - t0) * 1000.0, 1),
            )

        if act == IntentAction.PAUSE:
            if dry_run:
                return ExecutionResult(ok=True, action=act, message="유튜브 일시정지 확인(dry-run)")
            self.youtube.set_playing(False)
            self.events.publish({"type": "youtube_cmd", "cmd": "pause", "ts": time.time()})
            return ExecutionResult(ok=True, action=act, message="유튜브 영상을 일시정지했습니다.")

        if act == IntentAction.RESUME:
            if dry_run:
                return ExecutionResult(ok=True, action=act, message="유튜브 이어재생 확인(dry-run)")
            self.youtube.set_playing(True)
            self.events.publish({"type": "youtube_cmd", "cmd": "play", "ts": time.time()})
            return ExecutionResult(ok=True, action=act, message="유튜브 영상을 다시 재생합니다.")

        if act == IntentAction.NEXT:
            if dry_run:
                return ExecutionResult(ok=True, action=act, message="유튜브 다음 영상 확인(dry-run)")
            nxt = self.youtube.next_video()
            self.events.publish({"type": "youtube_cmd", "cmd": "next", "ts": time.time()})
            title = nxt.get("title", "다음 영상") if nxt else "다음 영상"
            return ExecutionResult(ok=True, action=act, message=f"다음 영상 '{title}'으로 넘어갑니다.")

        if act == IntentAction.PREVIOUS:
            if dry_run:
                return ExecutionResult(ok=True, action=act, message="유튜브 이전 영상 확인(dry-run)")
            prv = self.youtube.prev_video()
            self.events.publish({"type": "youtube_cmd", "cmd": "prev", "ts": time.time()})
            title = prv.get("title", "이전 영상") if prv else "이전 영상"
            return ExecutionResult(ok=True, action=act, message=f"이전 영상 '{title}'으로 돌아갑니다.")

        if act == IntentAction.NOW_PLAYING:
            info = self._youtube_now_playing()
            msg = format_now_playing_response(info, language=intent.language)
            return ExecutionResult(
                ok=True,
                action=act,
                message=msg,
                intent=intent.to_dict(),
                details={"now_playing": info, "youtube": self.youtube.to_dict()},
                latency_ms=round((time.monotonic() - t0) * 1000.0, 1),
            )

        return ExecutionResult(ok=False, action=act, message="지원하지 않는 유튜브 명령입니다.")

    def get_daily_quiz_schedule(self, force_refresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not force_refresh and self._schedule_cache is not None and (now - self._schedule_cache_time) < 10.0:
            return dict(self._schedule_cache)
        st = _read_daily_quiz_schedule_status()
        self._schedule_cache = st
        self._schedule_cache_time = now
        return dict(st)

    def _youtube_now_playing(self) -> dict[str, Any]:
        cur = self.youtube.current_video() or {}
        title = str(cur.get("title") or "YouTube 동영상").strip()
        artist = str(cur.get("artist") or "").strip()
        album = "YouTube"
        vid = str(cur.get("id") or "").strip()
        thumb = str(cur.get("thumbnail") or "").strip()
        if not thumb and vid:
            thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        mode = "play" if self.youtube.playing else "pause"
        return {
            "player_id": "youtube",
            "player_name": "YouTube",
            "mode": mode,
            "power": 1,
            "volume": self.youtube.volume,
            "digital_volume_control": 1,
            "use_volume_control": 1,
            "playlist_tracks": len(self.youtube.queue),
            "time": 0.0,
            "duration": float(cur.get("duration", 0) or 0),
            "title": title,
            "artist": artist,
            "album": album,
            "coverid": vid,
            "artwork_url": thumb,
            "category": cur.get("category", "kpop"),
            "url": cur.get("url", f"https://www.youtube.com/watch?v={vid}" if vid else ""),
        }

    def display_state(self) -> dict[str, Any]:
        np: dict[str, Any] = {}
        if self.youtube.active:
            np = self._youtube_now_playing()
        else:
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
        voice_info["local_stt"] = self.interpreter.local_stt_health()
        voice_info["local_slm"] = self.interpreter.local_llm_health()
        voice_info["require_wake_word"] = self.require_wake_word
        voice_info["duck_enabled"] = self.duck_enabled
        voice_info["duck_volume"] = self.duck_volume
        voice_info["speaker_volume"] = self.speaker_volume
        voice_info["enable_chime"] = self.enable_chime
        voice_info["chime_volume_pct"] = int(round(self.chime_volume * 100))
        voice_info["enable_tts"] = self.enable_tts
        voice_info["enable_command_ack"] = self.enable_command_ack
        voice_info["tts_voice"] = self.tts_voice
        voice_info["tts_rate_pct"] = self.tts_rate_pct
        voice_info["tts_pitch_hz"] = self.tts_pitch_hz
        voice_info["edge_tts_available"] = edge_tts_available()
        voice_info["available_voices"] = list(AVAILABLE_VOICES)
        voice_info["last_result"] = self.last_result

        music_playing = str(np.get("mode") or "").strip().lower() == "play"
        quiz_active = bool(self.quiz.active or self._quiz_loop_active)
        voice_active = bool(self._tts_speaking or self._manual_busy)
        yt_active = bool(self.youtube.active)
        wake_rem = max(0.0, self._display_wake_until - time.monotonic())
        keep_awake = bool(music_playing or quiz_active or voice_active or yt_active or wake_rem > 0.0)
        return {
            "ok": True,
            "ui_version": UI_VERSION,
            "now_playing": np,
            "display": {
                "music_playing": music_playing,
                "quiz_active": quiz_active,
                "voice_active": voice_active,
                "youtube_active": yt_active,
                "wake_remaining_sec": round(wake_rem, 2),
                "keep_awake": keep_awake,
            },
            "audio_output_label": _read_audio_output_label(),
            "audio_output_mode": "usb_dac" if self.is_dac_mode() else "speaker",
            "dac_fixed_volume": self.is_dac_mode(),
            "voice": voice_info,
            "youtube": self.youtube.to_dict(),
            "quiz": self.quiz.public_state(),
            "quiz_schedule": self.get_daily_quiz_schedule(force_refresh=False),
        }

    def fetch_cover_art(self) -> tuple[bytes, str] | None:
        if self.youtube.active:
            cur = self.youtube.current_video() or {}
            thumb = str(cur.get("thumbnail") or "").strip()
            vid = str(cur.get("id") or "").strip()
            candidates: list[str] = []
            if thumb:
                candidates.append(thumb)
            if vid:
                candidates.append(f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg")
                candidates.append(f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg")
            for url in candidates:
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (PocoBox)"})
                    with urllib.request.urlopen(req, timeout=4.0) as resp:
                        data = resp.read()
                        if data:
                            return data, resp.headers.get("Content-Type", "image/jpeg")
                except Exception as exc:
                    log.debug("Failed to fetch YouTube thumbnail (%s): %s", url, exc)
                    continue
            return None

        try:
            pid = self.lms.resolve_player_id(self.player_name)
        except Exception:
            pid = "02:70:6f:63:6f:01"
        candidates = [
            f"http://{self.lms.host}:{self.lms.port}/music/current/cover.jpg?player={pid}",
        ]
        try:
            np = self.lms.now_playing(pid)
            artwork_url = str(np.get("artwork_url") or "").strip()
            if artwork_url:
                if artwork_url.startswith("/"):
                    candidates.append(f"http://{self.lms.host}:{self.lms.port}{artwork_url}")
                elif artwork_url.startswith(("http://", "https://")):
                    candidates.append(artwork_url)
        except Exception:
            pass

        for url in candidates:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (PocoBox)"})
                with urllib.request.urlopen(req, timeout=4.0) as resp:
                    data = resp.read()
                    if data:
                        return data, resp.headers.get("Content-Type", "image/jpeg")
            except Exception:
                continue
        return None

    def rotate_display(self, mode: str = "next") -> dict[str, Any]:
        """Rotate local Wayland kiosk output via wlr-randr (or re-enable sensor auto-rotate)."""
        self._wake_display(15.0)
        valid_transforms = ("normal", "90", "180", "270")
        req_mode = (mode or "next").strip().lower()
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        lock_file = Path(runtime_dir) / "pocobox-rotation-lock"

        if req_mode == "auto":
            try:
                lock_file.unlink(missing_ok=True)
            except OSError:
                pass
            return {"ok": True, "mode": "auto"}

        wlr_randr = shutil.which("wlr-randr")
        wayland_sock = Path(runtime_dir) / os.environ.get("WAYLAND_DISPLAY", "wayland-0")
        if not wlr_randr or not wayland_sock.exists():
            return {"ok": False, "error": "Local Wayland kiosk display not available"}

        env = os.environ.copy()
        env["XDG_RUNTIME_DIR"] = runtime_dir
        env.setdefault("WAYLAND_DISPLAY", "wayland-0")

        try:
            probe = subprocess.run(
                [wlr_randr, "--json"],
                env=env,
                capture_output=True,
                text=True,
                timeout=3.0,
                check=False,
            )
            outputs = json.loads(probe.stdout) if probe.returncode == 0 and probe.stdout.strip() else []
        except Exception:
            outputs = []

        out_name = "DSI-1"
        current_transform = "normal"
        if outputs and isinstance(outputs, list):
            first = outputs[0]
            out_name = str(first.get("name") or out_name)
            current_transform = str(first.get("transform") or "normal").lower()

        if req_mode == "next":
            idx = valid_transforms.index(current_transform) if current_transform in valid_transforms else 0
            target = valid_transforms[(idx + 1) % len(valid_transforms)]
        elif req_mode in valid_transforms:
            target = req_mode
        else:
            return {"ok": False, "error": f"Invalid rotation mode: {mode}"}

        try:
            res = subprocess.run(
                [wlr_randr, "--output", out_name, "--transform", target],
                env=env,
                capture_output=True,
                text=True,
                timeout=3.0,
                check=False,
            )
            if res.returncode != 0:
                return {"ok": False, "error": res.stderr.strip() or "wlr-randr failed"}
            try:
                lock_file.write_text(target, encoding="utf-8")
            except OSError:
                pass
            return {"ok": True, "output": out_name, "transform": target}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

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
        local_stt = self.interpreter.local_stt_health()
        default_loc = get_default_location()
        return {
            "ok": True,
            "ui_version": UI_VERSION,
            "location": default_loc.name,
            "weather_zone": default_loc.kma_zone,
            "provider": self.interpreter.provider,
            "local_stt": local_stt,
            "local_slm": self.interpreter.local_llm_health(),
            "adaptive_learning": self.interpreter.learning_stats(),
            "gemini": self.interpreter.quota_tracker.stats() if self.interpreter else {},
            "gemini_model": self.interpreter.gemini_model,
            "gemini_configured": bool(self.interpreter.gemini_api_key),
            "openai_configured": bool(self.interpreter.openai_api_key),
            "require_wake_word": self.require_wake_word,
            "duck_enabled": self.duck_enabled,
            "duck_volume": self.duck_volume,
            **cont,
            "lms_connected": lms_ok,
            "players": [
                {"name": p.get("name"), "playerid": p.get("playerid"), "connected": p.get("connected")}
                for p in players
            ],
            "quiz": self.quiz.public_state(),
            "context": {
                "active": self.conversation_context.is_active(),
                "turns": len(self.conversation_context.turns),
                "last_action": self.conversation_context.last_action,
                "last_query": self.conversation_context.last_query,
                "last_track": self.conversation_context.last_track_title,
            },
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
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            if args and isinstance(args[0], str) and ("/display/state" in args[0] or "/display/events" in args[0]):
                return
            log.info("HTTP %s - " + fmt, self.address_string(), *args)

        def _send_json(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _send_bytes(self, code: int, data: bytes, content_type: str, head_only: bool = False) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            if not head_only:
                self.wfile.write(data)

        def do_HEAD(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/", "/display"):
                self._send_bytes(200, DISPLAY_HTML.encode("utf-8"), "text/html; charset=utf-8", head_only=True)
                return
            if path == "/display/manifest.webmanifest":
                self._send_bytes(200, DISPLAY_MANIFEST.encode("utf-8"), "application/manifest+json; charset=utf-8", head_only=True)
                return
            if path == "/dashboard":
                self._send_bytes(200, DASHBOARD_HTML.encode("utf-8"), "text/html; charset=utf-8", head_only=True)
                return
            if path == "/display/cover":
                art = controller.fetch_cover_art()
                if art is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    return
                self._send_bytes(200, art[0], art[1], head_only=True)
                return
            if path in ("/health", "/status"):
                body = json.dumps(controller.health(), ensure_ascii=False).encode("utf-8")
                self._send_bytes(200, body, "application/json; charset=utf-8", head_only=True)
                return
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/health", "/status"):
                self._send_json(200, controller.health())
                return
            if path in ("/", "/display"):
                self._send_bytes(200, DISPLAY_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/display/manifest.webmanifest":
                self._send_bytes(200, DISPLAY_MANIFEST.encode("utf-8"), "application/manifest+json; charset=utf-8")
                return
            if path == "/dashboard":
                self._send_bytes(200, DASHBOARD_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/display/state":
                self._send_json(200, controller.display_state())
                return
            if path == "/quiz/state":
                self._send_json(200, {"ok": True, "quiz": controller.quiz.public_state(), "topics": TOPIC_LABELS})
                return
            if path == "/quiz/schedule":
                self._send_json(200, controller.get_daily_quiz_schedule(force_refresh=True))
                return
            if path == "/youtube/kpop":
                cat = "all"
                if "?" in self.path:
                    import urllib.parse
                    qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    cat = qs.get("category", ["all"])[0]
                self._send_json(200, {"ok": True, "videos": get_kpop_playlist(cat)})
                return
            if path == "/youtube/search":
                q = ""
                if "?" in self.path:
                    import urllib.parse
                    qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    q = qs.get("q", [""])[0]
                self._send_json(200, {"ok": True, "videos": search_youtube_videos(q, lms_client=controller.lms, player_id=controller.player_name)})
                return
            if path == "/youtube/state":
                self._send_json(200, {"ok": True, "state": controller.youtube.to_dict()})
                return
            if path == "/learning/entries":
                learner = controller.interpreter.adaptive_learner if controller.interpreter else None
                entries = learner.learned_entries() if learner else []
                stats = learner.stats() if learner else {}
                self._send_json(200, {"ok": True, "entries": entries, "stats": stats})
                return
            if path == "/telemetry/stats":
                hours = 24.0
                if "hours=" in self.path:
                    try:
                        hours = float(self.path.split("hours=", 1)[1].split("&", 1)[0])
                    except ValueError:
                        hours = 24.0
                self._send_json(200, controller.telemetry.summarize(hours=hours))
                return
            if path == "/telemetry/accuracy":
                hours = 24.0
                if "hours=" in self.path:
                    try:
                        hours = float(self.path.split("hours=", 1)[1].split("&", 1)[0])
                    except ValueError:
                        hours = 24.0
                events = controller.telemetry.read_recent(limit=0, since_ts=time.time() - hours * 3600.0) if hours > 0 else controller.telemetry.read_recent(limit=0)
                self._send_json(200, {"ok": True, "accuracy": accuracy_summary(events, window_hours=hours)})
                return
            if path == "/telemetry/recent":
                limit = 50
                if "limit=" in self.path:
                    try:
                        limit = max(1, min(500, int(self.path.split("limit=", 1)[1].split("&", 1)[0])))
                    except ValueError:
                        limit = 50
                self._send_json(200, {"ok": True, "events": controller.telemetry.read_recent(limit=limit)})
                return
            if path == "/telemetry/gemini_stats":
                self._send_json(200, {
                    "ok": True,
                    "gemini": controller.interpreter.quota_tracker.stats() if controller.interpreter else {},
                    "cache_entries": len(controller.interpreter.intent_cache._cache) if controller.interpreter else 0,
                    "model": controller.interpreter.gemini_model if controller.interpreter else "",
                })
                return
            if path == "/telemetry/command_clips":
                limit = 50
                action_filter = None
                provider_filter = None
                if "?" in self.path:
                    import urllib.parse
                    qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    if "limit" in qs:
                        try:
                            limit = max(1, min(200, int(qs["limit"][0])))
                        except ValueError:
                            limit = 50
                    if "action" in qs:
                        action_filter = qs["action"][0]
                    if "provider" in qs:
                        provider_filter = qs["provider"][0]
                clips = (
                    controller.telemetry.list_command_clips(
                        limit=limit,
                        action_filter=action_filter,
                        provider_filter=provider_filter,
                    )
                    if controller.telemetry
                    else []
                )
                self._send_json(200, {"ok": True, "clips": clips})
                return
            if path == "/telemetry/command_clip":
                import urllib.parse
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                fname = qs.get("file", [""])[0]
                if not fname or "/" in fname or "\\" in fname or ".." in fname:
                    self._send_json(400, {"ok": False, "error": "Invalid file parameter"})
                    return
                if not controller.telemetry:
                    self._send_json(404, {"ok": False, "error": "Telemetry disabled"})
                    return
                target = controller.telemetry.command_clips_dir / fname
                if not target.exists() or not target.is_file():
                    self._send_json(404, {"ok": False, "error": "Clip not found"})
                    return
                ctype = "audio/wav" if target.suffix == ".wav" else "application/json; charset=utf-8"
                self._send_bytes(200, target.read_bytes(), ctype)
                return
            if path == "/verifier/stats":
                stats = controller.background_verifier.stats_dict() if controller.background_verifier else {}
                self._send_json(200, {"ok": True, "verifier": stats})
                return
            if path == "/verifier/scan":
                if controller.background_verifier:
                    result = controller.background_verifier.scan_once()
                    self._send_json(200, {"ok": True, "scan": result})
                else:
                    self._send_json(200, {"ok": False, "error": "verifier disabled"})
                return
            if path == "/telemetry/wake_clip":
                import urllib.parse
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                fname = qs.get("file", [""])[0]
                label = qs.get("label", ["pending"])[0]
                safe_label = "".join(c for c in label if c.isalnum() or c in ("_", "-"))
                if not fname or "/" in fname or "\\" in fname or ".." in fname:
                    self._send_json(400, {"ok": False, "error": "Invalid file parameter"})
                    return
                if not controller.telemetry:
                    self._send_json(404, {"ok": False, "error": "Telemetry disabled"})
                    return
                target = controller.telemetry.clips_dir / safe_label / fname
                if not target.exists() or not target.is_file():
                    self._send_json(404, {"ok": False, "error": "Clip not found"})
                    return
                ctype = "audio/wav" if target.suffix == ".wav" else "application/json; charset=utf-8"
                self._send_bytes(200, target.read_bytes(), ctype)
                return
            if path == "/display/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                q = controller.events.subscribe()
                try:
                    self.wfile.write(b": connected\n\n")
                    self.wfile.flush()
                    while True:
                        try:
                            msg = q.get(timeout=15.0)
                            self.wfile.write(msg.encode("utf-8"))
                            self.wfile.flush()
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    controller.events.unsubscribe(q)
                return
            if path == "/display/cover":
                art = controller.fetch_cover_art()
                if art is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    return
                self._send_bytes(200, art[0], art[1])
                return

            if path == "/debug/audio_history":
                rec = controller.rolling_recorder
                if rec is None:
                    self._send_json(200, {"ok": False, "message": "Rolling audio recorder not available."})
                else:
                    self._send_json(200, rec.status_dict())
                return

            if path == "/debug/audio_history/recent.wav":
                rec = controller.rolling_recorder
                if rec is None:
                    self._send_json(404, {"ok": False, "error": "Rolling audio recorder not available."})
                    return
                import urllib.parse

                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                try:
                    secs = float(qs.get("seconds", ["60"])[0])
                except ValueError:
                    secs = 60.0
                channel = qs.get("channel", ["both"])[0]
                wav_data = rec.get_recent_wav(seconds=secs, channel=channel)
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(wav_data)))
                self.send_header("Content-Disposition", 'inline; filename="pocobox_recent_audio.wav"')
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(wav_data)
                return

            self._send_json(404, {"ok": False, "error": "Not found"})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(length) if length > 0 else b""
            path = self.path.split("?", 1)[0]

            if path == "/dashboard/config":
                try:
                    data = json.loads(body.decode("utf-8")) if body else {}
                    res = controller.update_runtime_config(data)
                    self._send_json(200, res)
                except Exception as exc:
                    self._send_json(400, {"ok": False, "error": str(exc)})
                return

            if path == "/learning/forget":
                try:
                    data = json.loads(body.decode("utf-8")) if body else {}
                except json.JSONDecodeError:
                    self._send_json(400, {"ok": False, "error": "Invalid JSON"})
                    return
                src = str(data.get("source", "")).strip()
                if not src:
                    self._send_json(400, {"ok": False, "error": "Missing 'source' parameter"})
                    return
                learner = controller.interpreter.adaptive_learner if controller.interpreter else None
                if learner:
                    removed = learner.forget(src)
                    self._send_json(200, {"ok": True, "removed": removed, "source": src, "stats": learner.stats()})
                else:
                    self._send_json(400, {"ok": False, "error": "Learner disabled"})
                return

            if path == "/display/control":
                try:
                    data = json.loads(body.decode("utf-8")) if body else {}
                    res = controller.display_control(str(data.get("action", "")))
                    self._send_json(200 if res.get("ok") else 400, res)
                except Exception as exc:
                    self._send_json(500, {"ok": False, "error": str(exc)})
                return

            if path == "/display/rotate":
                try:
                    data = json.loads(body.decode("utf-8")) if body else {}
                    res = controller.rotate_display(str(data.get("mode", "next")))
                    self._send_json(200 if res.get("ok") else 400, res)
                except Exception as exc:
                    self._send_json(500, {"ok": False, "error": str(exc)})
                return

            if path == "/youtube/control":
                try:
                    data = json.loads(body.decode("utf-8")) if body else {}
                except json.JSONDecodeError:
                    self._send_json(400, {"ok": False, "error": "Invalid JSON"})
                    return
                act = str(data.get("action", "")).lower()
                if act in ("ensure_dac", "route_dac"):
                    sink = controller.route_youtube_to_dac_if_available()
                    self._send_json(200, {"ok": True, "sink": sink})
                    return
                if act == "sync":
                    if "videos" in data and isinstance(data["videos"], list) and data["videos"]:
                        controller.youtube.queue = list(data["videos"])
                    if "query" in data:
                        controller.youtube.query = str(data["query"])
                    if "active" in data:
                        controller.youtube.active = bool(data["active"])
                    if "playing" in data:
                        controller.youtube.set_playing(bool(data["playing"]))
                    idx = data.get("index")
                    if idx is not None:
                        try:
                            controller.youtube.set_index(int(idx))
                        except Exception:
                            pass
                    self._send_json(200, {"ok": True, "state": controller.youtube.to_dict()})
                    return
                if act == "load":
                    controller.route_youtube_to_dac_if_available()
                    q = data.get("query", "kpop")
                    vids = data.get("videos")
                    if not vids or not isinstance(vids, list):
                        vids = search_youtube_videos(q, lms_client=controller.lms, player_id=controller.player_name)
                    st_idx = int(data.get("index", 0))
                    controller.youtube.load_queue(vids, query=q, start_index=st_idx)
                    controller._wake_display(30.0)
                    controller.events.publish({
                        "type": "youtube_play",
                        "query": q,
                        "display_query": data.get("display_query") or q,
                        "videos": vids,
                        "index": st_idx,
                        "ts": time.time(),
                    })
                    self._send_json(200, {"ok": True, "state": controller.youtube.to_dict()})
                    return

                intent_map = {
                    "play": IntentAction.RESUME,
                    "pause": IntentAction.PAUSE,
                    "toggle": IntentAction.PAUSE if controller.youtube.playing else IntentAction.RESUME,
                    "next": IntentAction.NEXT,
                    "prev": IntentAction.PREVIOUS,
                    "stop": IntentAction.STOP_YOUTUBE,
                }
                mapped = intent_map.get(act)
                if not mapped:
                    self._send_json(400, {"ok": False, "error": f"Unknown youtube action: {act}"})
                    return
                res = controller.handle_youtube_intent(VoiceIntent(action=mapped))
                self._send_json(200 if res.ok else 422, res.to_dict())
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

            if path == "/voice/test_speaker":
                try:
                    data = json.loads(body.decode("utf-8")) if body else {}
                except json.JSONDecodeError:
                    data = {}
                res = controller.test_speaker(text=data.get("text"))
                self._send_json(200, res)
                return

            if path == "/quiz/start":
                try:
                    data = json.loads(body.decode("utf-8")) if body else {}
                except json.JSONDecodeError:
                    self._send_json(400, {"ok": False, "error": "Invalid JSON"})
                    return
                if bool(data.get("skip_if_active", False)) and controller.quiz.active:
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "action": IntentAction.START_QUIZ.value,
                            "skipped": True,
                            "message": "영어 퀴즈가 이미 진행 중입니다.",
                            "quiz": controller.quiz.public_state(),
                        },
                    )
                    return
                res = controller.start_quiz(
                    topic=str(data.get("topic") or "all"),
                    count=int(data.get("count") or DEFAULT_QUIZ_COUNT),
                    voice_loop=bool(data.get("voice_loop", False)),
                    dry_run=bool(data.get("dry_run", False)),
                )
                self._send_json(200 if res.ok else 422, res.to_dict())
                return

            if path == "/quiz/schedule/trigger":
                try:
                    data = json.loads(body.decode("utf-8")) if body else {}
                except json.JSONDecodeError:
                    data = {}
                sched = controller.get_daily_quiz_schedule(force_refresh=True)
                topic = str(data.get("topic") or sched.get("topic") or "all")
                count = int(data.get("count") or sched.get("count") or DEFAULT_QUIZ_COUNT)
                voice_loop = bool(data.get("voice_loop", True))
                dry_run = bool(data.get("dry_run", False))
                skip_if_active = bool(data.get("skip_if_active", False))

                if skip_if_active and controller.quiz.active:
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "action": IntentAction.START_QUIZ.value,
                            "skipped": True,
                            "message": "영어 퀴즈가 이미 진행 중입니다.",
                            "quiz": controller.quiz.public_state(),
                            "quiz_schedule": sched,
                        },
                    )
                    return
                res = controller.start_quiz(
                    topic=topic,
                    count=count,
                    voice_loop=voice_loop,
                    dry_run=dry_run,
                )
                out = res.to_dict()
                out["quiz_schedule"] = controller.get_daily_quiz_schedule(force_refresh=True)
                self._send_json(200 if res.ok else 422, out)
                return

            if path == "/quiz/answer":
                try:
                    data = json.loads(body.decode("utf-8")) if body else {}
                except json.JSONDecodeError:
                    self._send_json(400, {"ok": False, "error": "Invalid JSON"})
                    return
                res = controller.answer_quiz_text(
                    str(data.get("text", "")),
                    dry_run=bool(data.get("dry_run", False)),
                    use_cloud=bool(data.get("use_cloud", True)),
                )
                self._send_json(200 if res.ok else 422, res.to_dict())
                return

            if path == "/quiz/stop":
                try:
                    data = json.loads(body.decode("utf-8")) if body else {}
                except json.JSONDecodeError:
                    data = {}
                res = controller.stop_quiz(dry_run=bool(data.get("dry_run", False)))
                self._send_json(200, res.to_dict())
                return

            if path == "/debug/audio_history/replay":
                try:
                    data = json.loads(body.decode("utf-8")) if body else {}
                except json.JSONDecodeError:
                    data = {}
                secs = float(data.get("seconds", 30.0))
                channel = str(data.get("channel", "clean"))
                res = controller.replay_audio_history(seconds=secs, channel=channel)
                self._send_json(200 if res.get("ok") else 400, res)
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
        "PocoBox Voice & Display daemon on http://%s:%d (provider=%s local_stt=%s continuous=%s wake_required=%s)",
        host,
        port,
        controller.interpreter.provider,
        controller.interpreter.local_stt_url or "off",
        controller.continuous_enabled,
        controller.require_wake_word,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if controller.youtube.active:
            controller.release_youtube_from_dac()
        if controller.continuous_listener:
            controller.continuous_listener.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
