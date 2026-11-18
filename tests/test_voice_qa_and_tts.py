"""Unit tests for PocoBox Simple Q&A (offline time/date + Gemini Q&A) and spoken TTS."""

from __future__ import annotations

import datetime
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "services"))
sys.path.insert(0, str(REPO_ROOT / "services" / "lms_client"))

from voice_control.cloud_stt_intent import _intent_from_llm_payload  # noqa: E402
from voice_control.daemon import VoiceController  # noqa: E402
from voice_control.executor import (  # noqa: E402
    VoiceExecutor,
    format_command_ack,
    format_command_failure,
)
from voice_control.intent_schema import ExecutionResult, IntentAction, VoiceIntent  # noqa: E402
from voice_control.rule_parser import _format_kst_date, _format_kst_time, parse_command  # noqa: E402
from voice_control.tts import clean_for_speech, split_tts_chunks  # noqa: E402


class _FakeLMS:
    def __init__(self, mode: str = "play", volume: int = 80) -> None:
        self.mode = mode
        self.volume = volume
        self.volume_history: list[int] = []

    def resolve_player_id(self, name: str | None = None) -> str:
        return "02:70:6f:63:6f:01"

    def player_status(self, player_id: str) -> dict:
        return {"mode": self.mode, "mixer volume": self.volume}

    def set_volume(self, player_id: str, value: int) -> dict:
        self.volume = int(value)
        self.volume_history.append(self.volume)
        return {"volume": self.volume}


class TestVoiceQAAndTTS(unittest.TestCase):
    def test_offline_kst_time_and_date_queries(self) -> None:
        it_time = parse_command("포코야 지금 몇 시야?")
        self.assertEqual(it_time.action, IntentAction.ANSWER_QA)
        self.assertTrue(it_time.wake_word_detected)
        self.assertIn("지금은", it_time.answer)

        it_en_time = parse_command("Hey Poco what time is it")
        self.assertEqual(it_en_time.action, IntentAction.ANSWER_QA)
        self.assertTrue(it_en_time.wake_word_detected)

        it_date = parse_command("포코야 오늘 며칠이야?")
        self.assertEqual(it_date.action, IntentAction.ANSWER_QA)
        self.assertIn("오늘은", it_date.answer)

        it_wd = parse_command("오늘 무슨 요일이야")
        self.assertEqual(it_wd.action, IntentAction.ANSWER_QA)
        self.assertIn("요일", it_wd.answer)

        kst = datetime.timezone(datetime.timedelta(hours=9))
        dt = datetime.datetime(2026, 11, 18, 14, 5, tzinfo=kst)
        self.assertEqual(_format_kst_time(dt), "지금은 오후 2시 5분입니다.")
        self.assertEqual(_format_kst_date(dt), "오늘은 2026년 11월 18일 수요일입니다.")

    def test_llm_payload_parses_answer_qa_and_rejects_music_only(self) -> None:
        qa_payload = {
            "transcript": "포코야 프랑스 수도가 어디야",
            "is_music_only": False,
            "wake_word_detected": True,
            "action": "answer_qa",
            "query": "",
            "answer": "프랑스의 수도는 파리입니다.",
            "confidence": 0.96,
        }
        intent = _intent_from_llm_payload(qa_payload, provider="gemini-audio")
        self.assertEqual(intent.action, IntentAction.ANSWER_QA)
        self.assertTrue(intent.wake_word_detected)
        self.assertEqual(intent.answer, "프랑스의 수도는 파리입니다.")

        music_payload = {
            "transcript": "Look at the stars",
            "is_music_only": True,
            "wake_word_detected": False,
            "action": "answer_qa",
            "answer": "should be ignored",
        }
        ignored = _intent_from_llm_payload(music_payload, provider="gemini-audio")
        self.assertEqual(ignored.action, IntentAction.UNKNOWN)
        self.assertEqual(ignored.answer, "")

    def test_executor_returns_qa_answer_without_lms(self) -> None:
        executor = VoiceExecutor(lms=None)  # type: ignore[arg-type]
        res = executor.execute(
            VoiceIntent(action=IntentAction.ANSWER_QA, raw_text="38 곱하기 17은?", answer="646입니다.")
        )
        self.assertTrue(res.ok)
        self.assertEqual(res.action, "answer_qa")
        self.assertEqual(res.message, "646입니다.")

    def test_tts_chunking_and_markdown_cleanup(self) -> None:
        self.assertEqual(clean_for_speech("**파리**는 `프랑스`의 수도입니다."), "파리 는 프랑스 의 수도입니다.")
        long_text = "콜드플레이는 1997년 영국 런던에서 결성된 록 밴드입니다. " * 6
        chunks = split_tts_chunks(long_text, max_chars=120)
        self.assertGreaterEqual(len(chunks), 2)
        for c in chunks:
            self.assertLessEqual(len(c), 120)

    def test_controller_ducks_and_speaks_qa_answer(self) -> None:
        with mock.patch.dict(os.environ, {"CONTINUOUS_LISTEN": "0", "VOICE_DUCK_ENABLED": "1", "VOICE_DUCK_VOLUME": "18"}):
            ctrl = VoiceController()
        fake_lms = _FakeLMS(mode="play", volume=80)
        ctrl.lms = fake_lms  # type: ignore[assignment]
        ctrl.executor.lms = fake_lms  # type: ignore[assignment]

        spoken_texts: list[str] = []
        observed_ducked_vol: list[int] = []

        def fake_speak(text: str, lang: str | None = None, **kwargs: object) -> bool:
            spoken_texts.append(text)
            observed_ducked_vol.append(fake_lms.volume)
            return True

        with mock.patch("voice_control.daemon.speak_text", side_effect=fake_speak):
            ctrl._speak_answer("프랑스의 수도는 파리입니다.")

        self.assertEqual(spoken_texts, ["프랑스의 수도는 파리입니다."])
        self.assertEqual(observed_ducked_vol, [18])
        self.assertEqual(fake_lms.volume, 80)
        self.assertFalse(ctrl._tts_speaking)

    def test_classical_query_expansion_and_command_ack_formatting(self) -> None:
        it_tchai = parse_command("포코야, 차이코프스키 피아노협주곡 1번 틀어줘")
        self.assertEqual(it_tchai.action, IntentAction.PLAY_SEARCH)
        self.assertEqual(it_tchai.query, "Tchaikovsky Piano Concerto No. 1")
        self.assertEqual(format_command_ack(it_tchai), "네, 차이코프스키 피아노협주곡 1번 틀어드릴게요.")

        it_iu = parse_command("포코야, 아이유 밤편지 틀어줘")
        self.assertEqual(it_iu.query, "IU Through the Night")
        # Spoken confirmation keeps natural Korean phrasing rather than English TIDAL query
        self.assertEqual(format_command_ack(it_iu), "네, 아이유 밤편지 틀어드릴게요.")

        self.assertEqual(
            format_command_ack(parse_command("포코야 콜드플레이 노래 틀어줘")),
            "네, 콜드플레이 노래 틀어드릴게요.",
        )
        self.assertEqual(format_command_ack(parse_command("마이믹스 틀어줘")), "네, 마이믹스 틀어드릴게요.")
        self.assertEqual(format_command_ack(parse_command("아무거나 틀어줘")), "네, 추천 음악 틀어드릴게요.")
        self.assertEqual(format_command_ack(parse_command("즐겨찾기 틀어줘")), "네, 즐겨찾기 틀어드릴게요.")
        self.assertEqual(format_command_ack(parse_command("멈춰")), "네, 음악을 멈출게요.")
        self.assertEqual(format_command_ack(parse_command("다시 틀어줘")), "네, 음악을 다시 틀어드릴게요.")
        self.assertEqual(format_command_ack(parse_command("다음 곡")), "네, 다음 곡으로 넘어갈게요.")
        self.assertEqual(format_command_ack(parse_command("이전 곡")), "네, 이전 곡으로 돌아갈게요.")
        self.assertEqual(format_command_ack(parse_command("볼륨 50으로 맞춰줘")), "네, 볼륨을 50으로 맞출게요.")
        self.assertEqual(format_command_ack(parse_command("볼륨 72로 맞춰줘")), "네, 볼륨을 72로 맞출게요.")
        self.assertEqual(format_command_ack(parse_command("볼륨 올려줘")), "네, 볼륨을 올릴게요.")
        self.assertEqual(format_command_ack(parse_command("볼륨 줄여줘")), "네, 볼륨을 줄일게요.")
        self.assertEqual(format_command_ack(parse_command("음소거 해줘")), "네, 음소거할게요.")
        self.assertEqual(format_command_ack(parse_command("음소거 해제")), "네, 음소거를 해제할게요.")

        # Non-action / already-spoken intents should not produce pre-execution acks
        self.assertIsNone(format_command_ack(parse_command("지금 몇 시야")))
        self.assertIsNone(format_command_ack(parse_command("오늘 날씨 어때?")))
        self.assertIsNone(format_command_ack(parse_command("지금 나오는 노래 뭐야?")))
        self.assertIsNone(format_command_ack(parse_command("영어 퀴즈 시작해줘")))
        self.assertIsNone(format_command_ack(parse_command("오늘 점심 뭐 먹지?")))

    def test_asr_misheard_classical_never_hijacked_by_genre_and_escalates_unresolved_korean(self) -> None:
        from voice_control.cloud_stt_intent import HybridVoiceInterpreter

        # Exact live SenseVoice mis-hearing: must resolve to Tchaikovsky Piano Concerto No. 1, NOT Piano Relax!
        it_mis = parse_command("잘코스키 피아노 협주 일번 틀어줘.")
        self.assertEqual(it_mis.action, IntentAction.PLAY_SEARCH)
        self.assertEqual(it_mis.query, "Tchaikovsky Piano Concerto No. 1")
        self.assertNotIn("Piano Relax", it_mis.all_queries())
        self.assertEqual(format_command_ack(it_mis), "네, 차이코프스키 피아노 협주 일번 틀어드릴게요.")

        # Any unknown Korean artist/work containing '피아노' must NOT be hijacked to 'Piano Relax'
        it_unknown = parse_command("라흐마니 높으 피아노 협주 이번 틀어줘")
        self.assertEqual(it_unknown.action, IntentAction.PLAY_SEARCH)
        self.assertNotIn("Piano Relax", it_unknown.all_queries())
        self.assertLess(it_unknown.confidence, 0.9)

        # Unresolved Korean PLAY_SEARCH escalates text-only to cloud LLM for ASR correction + display_query
        interp = HybridVoiceInterpreter(
            provider="gemini",
            gemini_api_key="test-key",
            local_stt_url="http://127.0.0.1:8766",
        )
        refined = VoiceIntent(
            action=IntentAction.PLAY_SEARCH,
            raw_text="라흐마니 높으 피아노 협주 이번 틀어줘",
            query="Rachmaninoff Piano Concerto No. 2",
            display_query="라흐마니노프 피아노 협주곡 2번",
            confidence=0.96,
            provider="gemini-text",
        )
        with mock.patch.object(interp, "_gemini_text_intent", return_value=refined) as mock_gem:
            out = interp.interpret_text("라흐마니 높으 피아노 협주 이번 틀어줘", use_cloud_fallback=True)
        mock_gem.assert_called_once()
        self.assertEqual(out.query, "Rachmaninoff Piano Concerto No. 2")
        self.assertEqual(format_command_ack(out), "네, 라흐마니노프 피아노 협주곡 2번 틀어드릴게요.")

    def test_command_ack_speaks_before_execution_and_handles_failure_and_dry_run(self) -> None:
        with mock.patch.dict(os.environ, {"CONTINUOUS_LISTEN": "0", "VOICE_DUCK_ENABLED": "1", "VOICE_DUCK_VOLUME": "18"}):
            ctrl = VoiceController()
        fake_lms = _FakeLMS(mode="play", volume=80)
        ctrl.lms = fake_lms  # type: ignore[assignment]
        ctrl.executor.lms = fake_lms  # type: ignore[assignment]

        timeline: list[tuple[str, object]] = []

        def fake_speak(text: str, lang: str | None = None, **kwargs: object) -> bool:
            timeline.append(("speak", text, fake_lms.volume))
            return True

        def fake_search_play(player_id: str, queries: list[str], search_type: str = "any", dry_run: bool = False) -> dict:
            timeline.append(("execute_play", queries[0], fake_lms.volume, dry_run))
            return {"ok": True, "category": "songs", "title": "Piano Concerto No. 1"}

        fake_lms.tidal_search_and_play = fake_search_play  # type: ignore[attr-defined]

        with mock.patch("voice_control.daemon.speak_text", side_effect=fake_speak):
            res = ctrl.handle_text("포코야, 차이코프스키 피아노협주곡 1번 틀어줘", dry_run=False, use_cloud=False)

        self.assertTrue(res.ok)
        self.assertTrue(res.details.get("ack_spoken"))
        self.assertEqual(res.details.get("ack_text"), "네, 차이코프스키 피아노협주곡 1번 틀어드릴게요.")
        # Strictly speaks confirmation while ducked BEFORE executing TIDAL playback, then restores volume
        self.assertEqual(
            timeline,
            [
                ("speak", "네, 차이코프스키 피아노협주곡 1번 틀어드릴게요.", 18),
                ("execute_play", "Tchaikovsky Piano Concerto No. 1", 18, False),
            ],
        )
        self.assertEqual(fake_lms.volume, 80)

        # dry_run must NOT speak or duck
        timeline.clear()
        with mock.patch("voice_control.daemon.speak_text", side_effect=fake_speak):
            dry = ctrl.handle_text("포코야, 차이코프스키 피아노협주곡 1번 틀어줘", dry_run=True, use_cloud=False)
        self.assertTrue(dry.ok)
        self.assertFalse(dry.details.get("ack_spoken"))
        self.assertEqual(dry.details.get("ack_text"), "네, 차이코프스키 피아노협주곡 1번 틀어드릴게요.")
        self.assertEqual(timeline, [("execute_play", "Tchaikovsky Piano Concerto No. 1", 80, True)])

        # If execution fails after speaking ack, speak polite apology
        timeline.clear()
        fake_lms.tidal_search_and_play = lambda *a, **kw: {"ok": False}  # type: ignore[attr-defined]
        with mock.patch("voice_control.daemon.speak_text", side_effect=fake_speak):
            fail_res = ctrl.handle_text("포코야, 없는노래xyz 틀어줘", dry_run=False, use_cloud=False)
        self.assertFalse(fail_res.ok)
        self.assertEqual(
            [entry[1] for entry in timeline],
            ["네, 없는노래xyz 틀어드릴게요.", "죄송해요, 요청하신 곡을 찾지 못했어요."],
        )
        self.assertEqual(fake_lms.volume, 80)
        self.assertEqual(
            format_command_failure(ExecutionResult(ok=False, action="play_search", message="LMS 플레이어 연결을 찾지 못했습니다")),
            "죄송해요, 플레이어에 연결하지 못했어요.",
        )

    def test_command_ack_skips_redundant_ok_chime_and_falls_back_when_disabled(self) -> None:
        import math
        import struct
        from voice_control.audio_capture import pcm_to_wav_bytes

        samples = [int(4000 * math.sin(2.0 * math.pi * 1000.0 * i / 16000)) for i in range(8000)]
        wav = pcm_to_wav_bytes(struct.pack(f"<{len(samples)}h", *samples))

        with mock.patch.dict(
            os.environ,
            {"CONTINUOUS_LISTEN": "0", "VOICE_DUCK_ENABLED": "0", "ENABLE_CHIME": "1", "ENABLE_TTS": "1", "ENABLE_COMMAND_ACK": "1"},
        ):
            ctrl = VoiceController()
        fake_lms = _FakeLMS(mode="play", volume=80)
        fake_lms.next_track = lambda pid: {"ok": True}  # type: ignore[attr-defined]
        ctrl.lms = fake_lms  # type: ignore[assignment]
        ctrl.executor.lms = fake_lms  # type: ignore[assignment]
        ctrl.interpreter.provider = "gemini"

        with mock.patch.object(
            ctrl.interpreter,
            "interpret_audio",
            return_value=VoiceIntent(action=IntentAction.NEXT, raw_text="다음 곡", wake_word_detected=True),
        ), mock.patch("voice_control.daemon.speak_text", return_value=True) as mock_speak, mock.patch.object(
            ctrl, "_play_chime"
        ) as mock_chime:
            ctrl.handle_wake_utterance(wav, "poco")

        mock_speak.assert_called_once()
        mock_chime.assert_not_called()

        # When ENABLE_COMMAND_ACK=0, falls back to playing "ok" chime without TTS ack
        ctrl.enable_command_ack = False
        with mock.patch.object(
            ctrl.interpreter,
            "interpret_audio",
            return_value=VoiceIntent(action=IntentAction.NEXT, raw_text="다음 곡", wake_word_detected=True),
        ), mock.patch("voice_control.daemon.speak_text", return_value=True) as mock_speak, mock.patch.object(
            ctrl, "_play_chime"
        ) as mock_chime:
            ctrl.handle_wake_utterance(wav, "poco")

        mock_speak.assert_not_called()
        mock_chime.assert_called_once_with("ok")


if __name__ == "__main__":
    unittest.main()
