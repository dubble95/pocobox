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
from voice_control.executor import VoiceExecutor  # noqa: E402
from voice_control.intent_schema import IntentAction, VoiceIntent  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
