"""Executes structured VoiceIntent actions against Lyrion Music Server (LMS) + TIDAL."""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Support importing lms_client both from repo layout and deployed /srv/pocobox/bin
_THIS_DIR = Path(__file__).resolve().parent
for _p in (str(_THIS_DIR.parent / "lms_client"), "/srv/pocobox/bin"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lms_client import LMSClient  # noqa: E402
from .intent_schema import ExecutionResult, IntentAction, VoiceIntent  # noqa: E402


class VoiceExecutor:
    def __init__(self, lms: LMSClient, player_name: str = "pocobox") -> None:
        self.lms = lms
        self.player_name = player_name

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
            return done(False, "음악 관련 음성 명령으로 인식하지 못했습니다.")

        try:
            player_id = self.lms.resolve_player_id(self.player_name)
        except Exception as exc:
            return done(False, f"LMS 플레이어 연결을 찾지 못했습니다: {exc}")

        try:
            act = intent.action

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
                    self.lms.next_track(player_id)
                return done(True, "다음 곡으로 넘어갑니다." if not dry_run else "다음 곡 확인(dry-run)")

            if act == IntentAction.PREVIOUS:
                if not dry_run:
                    self.lms.prev_track(player_id)
                return done(True, "이전 곡으로 돌아갑니다." if not dry_run else "이전 곡 확인(dry-run)")

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
                if info.get("title"):
                    msg = f"현재 곡: {info['artist']} - {info['title']} ({info['mode']})"
                else:
                    msg = f"현재 재생 중인 곡이 없습니다 (상태: {info.get('mode')})"
                return done(True, msg, info)

            return done(False, f"지원하지 않는 명령입니다: {act}")
        except Exception as exc:
            return done(False, f"LMS 명령 실행 중 오류 발생: {exc}")
