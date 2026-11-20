"""Executes structured VoiceIntent actions against Lyrion Music Server (LMS) + TIDAL."""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("pocobox-voice.executor")

# Support importing lms_client both from repo layout and deployed /srv/pocobox/bin
_THIS_DIR = Path(__file__).resolve().parent
for _p in (str(_THIS_DIR.parent / "lms_client"), "/srv/pocobox/bin"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lms_client import LMSClient  # noqa: E402
from .aliases import normalize_asr_text, strip_wake_prefix  # noqa: E402
from .intent_schema import ExecutionResult, IntentAction, VoiceIntent  # noqa: E402
from .weather import answer_weather_query, fetch_weather  # noqa: E402

_ACK_KO_PLAY_VERB_RE = re.compile(
    r"(?:\s+(?:좀|한번|한 번|바로|지금|빨리|계속))*"
    r"\s*(?:"
    r"검색해서\s*틀어\s*(?:줘|주세요|줄래)?|"
    r"찾아서\s*틀어\s*(?:줘|주세요|줄래)?|"
    r"검색해\s*(?:줘|주세요)?|"
    r"찾아\s*(?:줘|주세요)?|"
    r"플레이\s*해\s*(?:줘|주세요)?|"
    r"재생해\s*(?:줘|주세요)?|재생|"
    r"들려\s*(?:줘|주세요)?|들려|"
    r"틀어\s*(?:줘|주세요|줄래)?|틀어|"
    r"켜\s*(?:줘|주세요)?"
    r")$",
    re.IGNORECASE,
)
_ACK_EN_PLAY_PREFIX_RE = re.compile(
    r"^(?:please\s+)?(?:play|put\s+on|search\s+(?:and\s+play\s+)?|find\s+(?:and\s+play\s+)?|listen\s+to)\s+",
    re.IGNORECASE,
)
_ACK_EN_PLAY_SUFFIX_RE = re.compile(
    r"\s+(?:on\s+tidal|from\s+tidal|please|for\s+me|now)$",
    re.IGNORECASE,
)


def _korean_ro(n: int) -> str:
    """Return Korean directional particle '으로' or '로' matching Sino-Korean pronunciation of n."""
    # 0(영), 10/20/..90(십), 100(백), 3(삼), 6(육) -> 으로; 1(일), 2(이), 4(사), 5(오), 7(칠), 8(팔), 9(구) -> 로
    return "으로" if (abs(int(n)) % 10) in (0, 3, 6) else "로"


def extract_spoken_play_target(intent: VoiceIntent) -> str:
    """Extract a natural spoken target label from the user's utterance for PLAY_SEARCH confirmation."""
    display = (intent.display_query or "").strip()
    if display:
        return display
    raw = (intent.raw_text or "").strip()
    if raw:
        cleaned = normalize_asr_text(raw)
        cleaned, _ = strip_wake_prefix(cleaned)
        cleaned = cleaned.strip(" .!?,~\"'“”")
        cleaned = re.sub(r"^(?:타이달|tidal)에서\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = _ACK_EN_PLAY_PREFIX_RE.sub("", cleaned)
        cleaned = _ACK_EN_PLAY_SUFFIX_RE.sub("", cleaned)
        cleaned = _ACK_KO_PLAY_VERB_RE.sub("", cleaned)
        cleaned = re.sub(r"\s+(?:좀|한번|한 번|바로|지금|빨리|계속)$", "", cleaned).strip(" .!?,~\"'“”")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned and len(cleaned) <= 45:
            return cleaned
    return (intent.query or "").strip()


AUDIO_STATE_FILE = Path("/etc/pocobox/audio-state.json")


def is_dac_mode(state_file: Path | None = None) -> bool:
    """Return True if USB DAC output mode is currently active."""
    target = state_file or AUDIO_STATE_FILE
    if target.exists():
        try:
            st = json.loads(target.read_text(encoding="utf-8"))
            return st.get("mode") == "usb_dac"
        except Exception:
            pass
    return False


def format_command_ack(intent: VoiceIntent, is_dac_mode: bool = False) -> str | None:
    """Build a short, natural Korean spoken confirmation to announce BEFORE executing a command.

    Returns None for non-action / already-spoken intents (Q&A, weather, now_playing, quiz, unknown),
    or for volume commands when in DAC mode (where volume is fixed at 100%).
    """
    act = intent.action
    if is_dac_mode and act in (
        IntentAction.VOLUME_SET,
        IntentAction.VOLUME_UP,
        IntentAction.VOLUME_DOWN,
        IntentAction.MUTE,
        IntentAction.UNMUTE,
    ):
        return None
    if act == IntentAction.PLAY_SEARCH:
        target = extract_spoken_play_target(intent)
        return f"네, {target} 틀어드릴게요." if target else "네, 음악 틀어드릴게요."
    if act == IntentAction.PLAY_MIX:
        low = (intent.raw_text or "").lower()
        if any(k in low for k in ("마이믹스", "마이 믹스", "my mix", "타이달 믹스", "tidal mix")):
            return "네, 마이믹스 틀어드릴게요."
        return "네, 추천 음악 틀어드릴게요."
    if act == IntentAction.PLAY_FAVORITE:
        q = (intent.query or "").strip()
        return f"네, {q} 즐겨찾기 틀어드릴게요." if q else "네, 즐겨찾기 틀어드릴게요."
    if act == IntentAction.PLAY_YOUTUBE:
        target = intent.display_query or intent.query or "K-POP 뮤직비디오"
        if intent.query in ("kpop", "girlgroup", "boygroup", "classic"):
            return f"네, {target} 연속 재생을 시작할게요."
        return f"네, 유튜브에서 {target} 틀어드릴게요."
    if act == IntentAction.STOP_YOUTUBE:
        return "네, 유튜브 재생을 종료할게요."
    if act == IntentAction.SWITCH_OUTPUT:
        target = "내장 스피커" if intent.query == "speaker" else "USB DAC"
        return f"네, 사운드를 {target}으로 출력할게요."
    if act == IntentAction.PAUSE:
        return "네, 음악을 멈출게요."
    if act == IntentAction.RESUME:
        return "네, 음악을 다시 틀어드릴게요."
    if act == IntentAction.NEXT:
        return "네, 다음 곡으로 넘어갈게요."
    if act == IntentAction.PREVIOUS:
        return "네, 이전 곡으로 돌아갈게요."
    if act == IntentAction.VOLUME_SET:
        vol = intent.volume if intent.volume is not None else 50
        return f"네, 볼륨을 {vol}{_korean_ro(vol)} 맞출게요."
    if act == IntentAction.VOLUME_UP:
        return "네, 볼륨을 올릴게요."
    if act == IntentAction.VOLUME_DOWN:
        return "네, 볼륨을 줄일게요."
    if act == IntentAction.MUTE:
        return "네, 음소거할게요."
    if act == IntentAction.UNMUTE:
        return "네, 음소거를 해제할게요."
    return None


def format_command_failure(result: ExecutionResult) -> str:
    """Build a brief Korean spoken apology if execution fails after pre-execution confirmation."""
    msg = (result.message or "").strip()
    if "LMS 플레이어" in msg:
        return "죄송해요, 플레이어에 연결하지 못했어요."
    if "찾지 못했습니다" in msg:
        return "죄송해요, 요청하신 곡을 찾지 못했어요."
    return "죄송해요, 명령을 실행하지 못했어요."


_NP_HANGUL_RE = re.compile(r"[가-힣]")
_NP_LATIN_RE = re.compile(r"[A-Za-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F\u1E00-\u1EFF]")


def format_now_playing_response(info: dict, language: str = "ko") -> str:
    """Format a natural spoken Now-Playing response optimized for bilingual Korean/English TTS."""
    title = str(info.get("title") or "").strip()
    artist = str(info.get("artist") or "").strip()
    mode = str(info.get("mode") or "stop").strip().lower()
    lang = (language or "ko").strip().lower()

    if lang == "en":
        if not title:
            return "Nothing is currently playing."
        prefix = "Currently paused on" if mode == "pause" else "Now playing"
        if artist:
            return f"{prefix} {title}, by {artist}."
        return f"{prefix} {title}."

    if not title:
        return "현재 재생 중인 곡이 없습니다."
    prefix = "일시정지 중인 곡은" if mode == "pause" else "지금 나오는 곡은"
    if artist:
        if _NP_HANGUL_RE.search(artist) and not _NP_LATIN_RE.search(artist):
            return f"{prefix} {artist}의 {title}입니다."
        return f"{prefix} {title}, 아티스트는 {artist}입니다."
    return f"{prefix} {title}입니다."


class VoiceExecutor:
    def __init__(
        self,
        lms: LMSClient,
        player_name: str = "pocobox",
        weather_fetcher=None,
        *,
        auto_queue_radio: bool = False,
        radio_settle_sec: float = 0.6,
        is_dac_mode_fn: Callable[[], bool] | None = None,
        youtube_handler: Callable[[VoiceIntent, bool], ExecutionResult] | None = None,
        is_youtube_active_fn: Callable[[], bool] | None = None,
        switch_output_handler: Callable[[VoiceIntent, bool], ExecutionResult] | None = None,
    ) -> None:
        self.lms = lms
        self.player_name = player_name
        self._weather_fetcher = weather_fetcher or fetch_weather
        self.auto_queue_radio = bool(auto_queue_radio)
        self.radio_settle_sec = max(0.0, float(radio_settle_sec))
        self.is_dac_mode_fn = is_dac_mode_fn or is_dac_mode
        self.youtube_handler = youtube_handler
        self.is_youtube_active_fn = is_youtube_active_fn or (lambda: False)
        self.switch_output_handler = switch_output_handler
        self._radio_lock = threading.Lock()
        self._radio_gen = 0

    def _bump_radio_gen(self) -> int:
        with self._radio_lock:
            self._radio_gen += 1
            return self._radio_gen

    def _schedule_tidal_track_radio(self, player_id: str) -> None:
        if not self.auto_queue_radio:
            return
        if not hasattr(self.lms, "append_tidal_track_radio"):
            return
        gen = self._bump_radio_gen()

        def _worker() -> None:
            try:
                if self.radio_settle_sec > 0:
                    time.sleep(self.radio_settle_sec)
                with self._radio_lock:
                    if gen != self._radio_gen:
                        return
                if hasattr(self.lms, "ensure_tidal_dstm"):
                    try:
                        self.lms.ensure_tidal_dstm(player_id)
                    except Exception as exc:
                        log.debug("ensure_tidal_dstm skipped: %s", exc)
                res = self.lms.append_tidal_track_radio(
                    player_id,
                    only_if_single_track=True,
                    wait_timeout_sec=2.5,
                    dedup_timeout_sec=3.0,
                )
                if res.get("ok") and not res.get("skipped"):
                    log.info(
                        "Queued %d TIDAL radio tracks after track %s (queue=%d)",
                        int(res.get("added") or 0),
                        res.get("track_id"),
                        int(res.get("playlist_tracks") or 0),
                    )
            except Exception as exc:
                log.debug("TIDAL track radio auto-queue skipped: %s", exc)

        threading.Thread(target=_worker, name="tidal-track-radio", daemon=True).start()

    def _ensure_next_track_available(self, player_id: str) -> None:
        """If at the end of a TIDAL queue, fetch Track Radio immediately so 'next' doesn't stop."""
        if not self.auto_queue_radio or not hasattr(self.lms, "append_tidal_track_radio"):
            return
        try:
            st = self.lms.call(player_id, ["status", "-", 1, "tags:u"])
            tracks = int(st.get("playlist_tracks") or 0)
            cur_idx = int(st.get("playlist_cur_index") or 0)
            if tracks >= 1 and cur_idx + 1 >= tracks:
                self._bump_radio_gen()
                self.lms.append_tidal_track_radio(
                    player_id,
                    only_if_single_track=False,
                    wait_timeout_sec=0.0,
                    dedup_timeout_sec=3.0,
                )
        except Exception as exc:
            log.debug("Pre-next TIDAL radio check skipped: %s", exc)

    def execute(self, intent: VoiceIntent, dry_run: bool = False) -> ExecutionResult:
        t0 = time.monotonic()

        def done(ok: bool, msg: str, details: dict | None = None) -> ExecutionResult:
            return ExecutionResult(
                ok=ok,
                action=intent.action.value,
                message=msg,
                intent=intent.to_dict(),
                details=details or {},
                latency_ms=round((time.monotonic() - t0) * 1000.0, 1),
            )

        if intent.action == IntentAction.UNKNOWN:
            return done(False, "음악 명령이나 질문으로 인식하지 못했습니다.")

        if intent.action == IntentAction.ANSWER_QA:
            answer = (intent.answer or "").strip()
            if not answer:
                return done(False, "질문에 대한 답변을 생성하지 못했습니다.")
            return done(True, answer, {"answer": answer})

        if intent.action == IntentAction.WEATHER:
            try:
                answer, details = answer_weather_query(
                    intent.raw_text,
                    location_query=intent.query,
                    language=intent.language,
                    fetcher=self._weather_fetcher,
                )
                return done(True, answer, details)
            except Exception as exc:
                msg = str(exc)
                if not msg.startswith("날씨 정보를"):
                    msg = f"날씨 정보를 불러오지 못했습니다: {msg}"
                return done(False, msg)

        if intent.action == IntentAction.START_QUIZ:
            return done(
                True,
                "영어 리스닝 퀴즈를 시작합니다.",
                {"quiz_topic": intent.quiz_topic or "all", "quiz_count": intent.quiz_count or 3},
            )

        if intent.action == IntentAction.STOP_QUIZ:
            return done(True, "영어 리스닝 퀴즈를 종료합니다.")

        if intent.action == IntentAction.PLAY_YOUTUBE:
            if self.youtube_handler is not None:
                return self.youtube_handler(intent, dry_run)
            return done(
                True,
                f"유튜브 '{intent.display_query or intent.query}' 재생을 시작합니다.",
                {"query": intent.query, "display_query": intent.display_query},
            )

        if intent.action == IntentAction.STOP_YOUTUBE:
            if self.youtube_handler is not None:
                return self.youtube_handler(intent, dry_run)
            return done(True, "유튜브 재생을 종료합니다.")

        if intent.action == IntentAction.SWITCH_OUTPUT:
            if self.switch_output_handler is not None:
                return self.switch_output_handler(intent, dry_run)
            target = "내장 스피커" if intent.query == "speaker" else "USB DAC"
            return done(True, f"사운드를 {target}으로 전환했습니다.", {"target": intent.query})

        # If YouTube video is actively playing on screen, route pause/resume/next/prev to YouTube
        if self.is_youtube_active_fn() and intent.action in (
            IntentAction.PAUSE,
            IntentAction.RESUME,
            IntentAction.NEXT,
            IntentAction.PREVIOUS,
        ):
            if self.youtube_handler is not None:
                return self.youtube_handler(intent, dry_run)

        try:
            player_id = self.lms.resolve_player_id(self.player_name)
        except Exception as exc:
            return done(False, f"LMS 플레이어 연결을 찾지 못했습니다: {exc}")

        try:
            act = intent.action

            if act in (IntentAction.PLAY_SEARCH, IntentAction.PLAY_MIX, IntentAction.PLAY_FAVORITE):
                if not dry_run and self.is_youtube_active_fn() and self.youtube_handler is not None:
                    try:
                        self.youtube_handler(VoiceIntent(action=IntentAction.STOP_YOUTUBE), dry_run=False)
                    except Exception:
                        pass

            if act == IntentAction.PLAY_SEARCH:
                queries = intent.all_queries()
                if not queries:
                    return done(False, "검색할 곡명이나 가수명이 비어 있습니다.")
                res = self.lms.tidal_search_and_play(
                    player_id,
                    queries=queries,
                    search_type=intent.search_type,
                    dry_run=dry_run,
                )
                if not res.get("ok"):
                    return done(False, f"TIDAL에서 '{queries[0]}' 검색 결과를 찾지 못했습니다.", res)
                if not dry_run:
                    if res.get("category") != "classical_work":
                        self._schedule_tidal_track_radio(player_id)
                verb = "검색 확인(dry-run)" if dry_run else "재생 시작"
                return done(
                    True,
                    f"TIDAL [{res.get('category')}] '{res.get('title')}' {verb}",
                    res,
                )

            if act == IntentAction.PLAY_MIX:
                res = self.lms.play_tidal_my_mix(player_id, index=0, dry_run=dry_run)
                if not res.get("ok"):
                    return done(False, res.get("error", "TIDAL My Mix 재생 실패"), res)
                verb = "확인(dry-run)" if dry_run else "재생 시작"
                return done(True, f"TIDAL '{res.get('title')}' {verb}", res)

            if act == IntentAction.PLAY_FAVORITE:
                res = self.lms.play_favorite(player_id, query=intent.query or None, dry_run=dry_run)
                if not res.get("ok"):
                    return done(False, res.get("error", "즐겨찾기 재생 실패"), res)
                verb = "확인(dry-run)" if dry_run else "재생 시작"
                return done(True, f"즐겨찾기 '{res.get('title')}' {verb}", res)

            if act == IntentAction.PAUSE:
                if not dry_run:
                    self.lms.pause(player_id, 1)
                return done(True, "일시정지했습니다." if not dry_run else "일시정지 확인(dry-run)")

            if act == IntentAction.RESUME:
                if not dry_run:
                    self.lms.play(player_id)
                return done(True, "재생을 이어갑니다." if not dry_run else "이어재생 확인(dry-run)")

            if act == IntentAction.NEXT:
                if not dry_run:
                    self._ensure_next_track_available(player_id)
                    self.lms.next_track(player_id)
                return done(True, "다음 곡으로 넘어갑니다." if not dry_run else "다음 곡 확인(dry-run)")

            if act == IntentAction.PREVIOUS:
                if not dry_run:
                    self.lms.prev_track(player_id)
                return done(True, "이전 곡으로 돌아갑니다." if not dry_run else "이전 곡 확인(dry-run)")

            if act in (
                IntentAction.VOLUME_SET,
                IntentAction.VOLUME_UP,
                IntentAction.VOLUME_DOWN,
                IntentAction.MUTE,
                IntentAction.UNMUTE,
            ):
                if self.is_dac_mode_fn():
                    msg = (
                        "DAC 모드에서는 고음질 출력을 위해 볼륨이 100%로 고정되어 있습니다. "
                        "앰프나 스피커에서 볼륨을 조절해 주세요."
                    ) if not dry_run else "DAC 모드에서는 볼륨이 100%로 고정되어 있습니다 (dry-run)."
                    return done(
                        True,
                        msg,
                        {"fixed_volume": 100, "dac_mode": True, "volume_disabled": True, "spoken": True},
                    )

                if act == IntentAction.VOLUME_SET:
                    vol = intent.volume if intent.volume is not None else 50
                    if not dry_run:
                        self.lms.set_volume(player_id, vol)
                    return done(True, f"볼륨을 {vol}으로 설정했습니다.", {"volume": vol})

                if act in (IntentAction.VOLUME_UP, IntentAction.VOLUME_DOWN):
                    delta = intent.delta if intent.delta is not None else (10 if act == IntentAction.VOLUME_UP else -10)
                    if dry_run:
                        return done(True, f"볼륨 {delta:+d} 조정 확인(dry-run)", {"delta": delta})
                    res = self.lms.adjust_volume(player_id, delta)
                    return done(True, f"볼륨을 {res['volume']}으로 조정했습니다.", res)

                if act == IntentAction.MUTE:
                    if not dry_run:
                        self.lms.set_mute(player_id, True)
                    return done(True, "음소거했습니다.")

                if act == IntentAction.UNMUTE:
                    if not dry_run:
                        self.lms.set_mute(player_id, False)
                    return done(True, "음소거를 해제했습니다.")

            if act == IntentAction.NOW_PLAYING:
                info = self.lms.now_playing(player_id)
                msg = format_now_playing_response(info, language=intent.language)
                return done(True, msg, info)

            return done(False, f"지원하지 않는 명령입니다: {act}")
        except Exception as exc:
            return done(False, f"LMS 명령 실행 중 오류 발생: {exc}")
