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
    format_now_playing_response,
)
from voice_control.intent_schema import ExecutionResult, IntentAction, VoiceIntent  # noqa: E402
from voice_control.rule_parser import _format_kst_date, _format_kst_time, parse_command  # noqa: E402
from voice_control.tts import (  # noqa: E402
    _trim_pcm_silence,
    clean_for_speech,
    resolve_bilingual_voices,
    speak_text,
    split_bilingual_segments,
    split_tts_chunks,
    synthesize_bilingual_pcm,
)


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

    def test_bilingual_tts_segmentation_for_now_playing_and_general_speech(self) -> None:
        # Pure Korean & pure English fast paths
        self.assertEqual(
            split_bilingual_segments("프랑스의 수도는 파리입니다."),
            [("ko", "프랑스의 수도는 파리입니다.")],
        )
        self.assertEqual(
            split_bilingual_segments("Now playing Rachmaninoff: Piano Concerto No. 2, by Valentina Lisitsa."),
            [("en", "Now playing Rachmaninoff: Piano Concerto No. 2, by Valentina Lisitsa.")],
        )

        # Classical music with opus numbers, movements, colons, commas, and performer name
        self.assertEqual(
            split_bilingual_segments(
                "지금 나오는 곡은 Rachmaninoff: Piano Concerto No. 2 in C Minor, Op. 18: 1. Moderato, 아티스트는 Valentina Lisitsa입니다."
            ),
            [
                ("ko", "지금 나오는 곡은"),
                ("en", "Rachmaninoff: Piano Concerto No. 2 in C Minor, Op. 18: 1. Moderato"),
                ("ko", "아티스트는"),
                ("en", "Valentina Lisitsa"),
                ("ko", "입니다."),
            ],
        )

        # Musical sharps (#) and accented European composer/artist names stay intact in English
        self.assertEqual(
            clean_for_speech("### 제목: Moonlight Sonata in C# Minor"),
            "제목: Moonlight Sonata in C# Minor",
        )
        self.assertEqual(
            split_bilingual_segments(
                "지금 나오는 곡은 Moonlight Sonata in C# Minor, Op. 27, No. 2, 아티스트는 Frédéric Chopin입니다."
            ),
            [
                ("ko", "지금 나오는 곡은"),
                ("en", "Moonlight Sonata in C# Minor, Op. 27, No. 2"),
                ("ko", "아티스트는"),
                ("en", "Frédéric Chopin"),
                ("ko", "입니다."),
            ],
        )
        self.assertEqual(
            split_bilingual_segments("지금 나오는 곡은 APT., 아티스트는 ROSÉ & Bruno Mars입니다."),
            [
                ("ko", "지금 나오는 곡은"),
                ("en", "APT."),
                ("ko", "아티스트는"),
                ("en", "ROSÉ & Bruno Mars"),
                ("ko", "입니다."),
            ],
        )

        # Leading/trailing bare numbers in titles vs Korean counters (년/번/악장)
        self.assertEqual(
            split_bilingual_segments("지금 나오는 곡은 7 Rings, 아티스트는 Ariana Grande입니다."),
            [
                ("ko", "지금 나오는 곡은"),
                ("en", "7 Rings"),
                ("ko", "아티스트는"),
                ("en", "Ariana Grande"),
                ("ko", "입니다."),
            ],
        )
        self.assertEqual(
            split_bilingual_segments("지금 나오는 곡은 24K Magic, 아티스트는 Bruno Mars입니다."),
            [
                ("ko", "지금 나오는 곡은"),
                ("en", "24K Magic"),
                ("ko", "아티스트는"),
                ("en", "Bruno Mars"),
                ("ko", "입니다."),
            ],
        )
        self.assertEqual(
            split_bilingual_segments("네, Piano Concerto No. 1 틀어드릴게요."),
            [
                ("ko", "네,"),
                ("en", "Piano Concerto No. 1"),
                ("ko", "틀어드릴게요."),
            ],
        )
        self.assertEqual(
            split_bilingual_segments("Coldplay 2024년 콘서트와 Beethoven 9번 교향곡입니다."),
            [
                ("en", "Coldplay"),
                ("ko", "2024년 콘서트와"),
                ("en", "Beethoven"),
                ("ko", "9번 교향곡입니다."),
            ],
        )

        # Parenthesized English subtitle & Remastered year
        self.assertEqual(
            split_bilingual_segments("지금 나오는 곡은 밤편지 (Through the Night), 아티스트는 IU입니다."),
            [
                ("ko", "지금 나오는 곡은 밤편지"),
                ("en", "Through the Night"),
                ("ko", "아티스트는"),
                ("en", "IU"),
                ("ko", "입니다."),
            ],
        )
        self.assertEqual(
            split_bilingual_segments("지금 나오는 곡은 Bohemian Rhapsody (Remastered 2011), 아티스트는 Queen입니다."),
            [
                ("ko", "지금 나오는 곡은"),
                ("en", "Bohemian Rhapsody (Remastered 2011)"),
                ("ko", "아티스트는"),
                ("en", "Queen"),
                ("ko", "입니다."),
            ],
        )

        # General Q&A mixed English terms (including quoted terms & classical subtitles)
        self.assertEqual(
            split_bilingual_segments("OpenAI가 만든 ChatGPT는 인공지능입니다."),
            [
                ("en", "OpenAI"),
                ("ko", "가 만든"),
                ("en", "ChatGPT"),
                ("ko", "는 인공지능입니다."),
            ],
        )
        self.assertEqual(
            split_bilingual_segments("비틀즈의 “Hey Jude”는 명곡입니다."),
            [
                ("ko", "비틀즈의"),
                ("en", "Hey Jude"),
                ("ko", "는 명곡입니다."),
            ],
        )
        self.assertEqual(
            split_bilingual_segments(
                "지금 나오는 곡은 Symphony No. 9 in E Minor, Op. 95 'From the New World', 아티스트는 Antonín Dvořák입니다."
            ),
            [
                ("ko", "지금 나오는 곡은"),
                ("en", "Symphony No. 9 in E Minor, Op. 95 'From the New World'"),
                ("ko", "아티스트는"),
                ("en", "Antonín Dvořák"),
                ("ko", "입니다."),
            ],
        )

        # Korean measurement units and single isolated letters must NOT trigger jarring English switches
        self.assertEqual(
            split_bilingual_segments("서울의 현재 기온은 15°C이고 풍속은 3m/s, 거리는 10km, 샘플레이트는 48kHz입니다."),
            [("ko", "서울의 현재 기온은 15°C이고 풍속은 3m/s, 거리는 10km, 샘플레이트는 48kHz입니다.")],
        )
        self.assertEqual(
            split_bilingual_segments("A팀과 B형 학생의 K팝 공연입니다."),
            [("ko", "A팀과 B형 학생의 K팝 공연입니다.")],
        )

    def test_bilingual_voice_resolution_and_pcm_silence_trimming(self) -> None:
        with mock.patch("voice_control.tts.edge_tts_available", return_value=True), mock.patch.dict(
            os.environ, {"TTS_EN_VOICE": "", "QUIZ_EN_VOICE": ""}, clear=False
        ):
            self.assertEqual(resolve_bilingual_voices("ko-KR-SunHiNeural"), ("ko-KR-SunHiNeural", "en-US-JennyNeural"))
            self.assertEqual(resolve_bilingual_voices("ko-KR-InJoonNeural"), ("ko-KR-InJoonNeural", "en-US-GuyNeural"))
            self.assertEqual(
                resolve_bilingual_voices("ko-KR-HyunsuMultilingualNeural"),
                ("ko-KR-HyunsuMultilingualNeural", "ko-KR-HyunsuMultilingualNeural"),
            )
            self.assertEqual(resolve_bilingual_voices("en-US-JennyNeural"), ("ko-KR-SunHiNeural", "en-US-JennyNeural"))
            self.assertEqual(resolve_bilingual_voices("en-US-GuyNeural"), ("ko-KR-InJoonNeural", "en-US-GuyNeural"))
            self.assertEqual(resolve_bilingual_voices("google"), ("google", "google"))

        with mock.patch("voice_control.tts.edge_tts_available", return_value=True), mock.patch.dict(
            os.environ, {"TTS_EN_VOICE": "en-US-GuyNeural"}, clear=False
        ):
            self.assertEqual(resolve_bilingual_voices("ko-KR-SunHiNeural"), ("ko-KR-SunHiNeural", "en-US-GuyNeural"))

        with mock.patch("voice_control.tts.edge_tts_available", return_value=True), mock.patch.dict(
            os.environ, {"TTS_EN_VOICE": "kokoro-en"}, clear=False
        ):
            self.assertEqual(resolve_bilingual_voices("ko-KR-SunHiNeural"), ("ko-KR-SunHiNeural", "kokoro-en"))
            self.assertEqual(resolve_bilingual_voices("kokoro-en"), ("ko-KR-SunHiNeural", "kokoro-en"))

        # Trim 200ms leading silence + 300ms trailing silence around 100ms tone at 24kHz s16le
        import struct

        sr = 24000
        lead = b"\x00\x00" * (sr * 200 // 1000)
        tone = struct.pack("<h", 4000) * (sr * 100 // 1000)
        trail = b"\x00\x00" * (sr * 300 // 1000)
        trimmed = _trim_pcm_silence(lead + tone + trail, sample_rate=sr)
        self.assertGreaterEqual(len(trimmed), len(tone))
        self.assertLess(len(trimmed), len(lead + tone + trail) // 2)

        # Pure silence returns untouched (never wipes quiet audio)
        only_silence = b"\x00\x00" * (sr * 100 // 1000)
        self.assertEqual(_trim_pcm_silence(only_silence, sample_rate=sr), only_silence)

    def test_speak_text_routes_mixed_and_pure_english_natively(self) -> None:
        calls: list[tuple[str, str, str, int, int]] = []
        played: list[bytes] = []

        def fake_synth(text: str, lang: str, voice: str, rate_pct: int, pitch_hz: int) -> bytes:
            calls.append((text, lang, voice, rate_pct, pitch_hz))
            # Distinct non-silent 2-byte aligned marker bytes
            return b"\x00\x20" * 300

        def fake_play(pcm: bytes, sink: str | None = None, env: dict | None = None) -> tuple[bool, str | None]:
            played.append(pcm)
            return True, sink

        with mock.patch("voice_control.tts.edge_tts_available", return_value=True), mock.patch(
            "voice_control.tts.synthesize_pcm", side_effect=fake_synth
        ), mock.patch("voice_control.tts.play_pcm", side_effect=fake_play), mock.patch(
            "voice_control.tts.resolve_internal_speaker_sink", return_value="fake-sink"
        ), mock.patch.dict(os.environ, {"TTS_LANG": "", "TTS_EN_VOICE": "", "QUIZ_EN_VOICE": ""}, clear=False):
            # Mixed Korean + English speaks via parallel bilingual synthesis and ONE single play_pcm call
            ok = speak_text(
                "지금 나오는 곡은 Yellow, 아티스트는 Coldplay입니다.",
                voice="ko-KR-SunHiNeural",
                rate_pct=0,
                pitch_hz=0,
                enabled=True,
            )
            self.assertTrue(ok)
            self.assertEqual(len(played), 1)
            self.assertEqual(
                {(t, l, v) for t, l, v, _, _ in calls},
                {
                    ("지금 나오는 곡은", "ko", "ko-KR-SunHiNeural"),
                    ("Yellow", "en", "en-US-JennyNeural"),
                    ("아티스트는", "ko", "ko-KR-SunHiNeural"),
                    ("Coldplay", "en", "en-US-JennyNeural"),
                    ("입니다.", "ko", "ko-KR-SunHiNeural"),
                },
            )

            # Pure English text automatically uses native English voice even when TTS voice is SunHi
            calls.clear()
            played.clear()
            ok_en = speak_text("Now playing Fix You, by Coldplay.", voice="ko-KR-SunHiNeural", enabled=True)
            self.assertTrue(ok_en)
            self.assertEqual(calls, [("Now playing Fix You, by Coldplay.", "en", "en-US-JennyNeural", 0, 0)])

            # HyunsuMultilingualNeural uses single native multilingual call without splitting
            calls.clear()
            pcm_multi = synthesize_bilingual_pcm(
                "지금 나오는 곡은 Yellow, 아티스트는 Coldplay입니다.",
                voice="ko-KR-HyunsuMultilingualNeural",
            )
            self.assertTrue(pcm_multi)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][2], "ko-KR-HyunsuMultilingualNeural")

    def test_kokoro_tts_synthesis_and_fallback(self) -> None:
        import wave, io, struct
        from voice_control.tts import synthesize_pcm, fetch_kokoro_tts_wav

        # Create a valid 24kHz mono PCM16 WAV in memory
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(struct.pack("<h", 3000) * 240)
        valid_wav = buf.getvalue()

        # Success case: returns decoded raw PCM bytes
        with mock.patch("voice_control.tts.fetch_kokoro_tts_wav", return_value=valid_wav) as mock_fetch:
            pcm = synthesize_pcm("Hello world", lang="en", voice="kokoro-en", rate_pct=0, pitch_hz=0)
            mock_fetch.assert_called_once_with("Hello world")
            self.assertEqual(len(pcm), 240 * 2)

        # Fallback case: when Kokoro fails, falls back to edge/google synthesis
        with mock.patch("voice_control.tts.fetch_kokoro_tts_wav", side_effect=RuntimeError("Connection refused")), \
             mock.patch("voice_control.tts._synthesize_chunk_mp3", return_value=b"fake-mp3"), \
             mock.patch("subprocess.run") as mock_subp:
            mock_subp.return_value.stdout = b"\x00\x10" * 100
            pcm_fb = synthesize_pcm("Hello fallback", lang="en", voice="kokoro-en", rate_pct=0, pitch_hz=0)
            self.assertEqual(pcm_fb, b"\x00\x10" * 100)

    def test_format_now_playing_response_and_controller_bilingual_pronunciation(self) -> None:
        self.assertEqual(
            format_now_playing_response(
                {
                    "title": "Rachmaninoff: Piano Concerto No. 2 in C Minor, Op. 18: 1. Moderato",
                    "artist": "Valentina Lisitsa",
                    "mode": "play",
                }
            ),
            "지금 나오는 곡은 Rachmaninoff: Piano Concerto No. 2 in C Minor, Op. 18: 1. Moderato, 아티스트는 Valentina Lisitsa입니다.",
        )
        self.assertEqual(
            format_now_playing_response({"title": "밤편지", "artist": "아이유", "mode": "play"}),
            "지금 나오는 곡은 아이유의 밤편지입니다.",
        )
        self.assertEqual(
            format_now_playing_response({"title": "Love wins all", "artist": "아이유", "mode": "play"}),
            "지금 나오는 곡은 아이유의 Love wins all입니다.",
        )
        self.assertEqual(
            format_now_playing_response({"title": "서른 즈음에", "artist": "김광석", "mode": "pause"}),
            "일시정지 중인 곡은 김광석의 서른 즈음에입니다.",
        )
        self.assertEqual(
            format_now_playing_response({"title": "WQXR Classical Radio", "artist": "", "mode": "play"}),
            "지금 나오는 곡은 WQXR Classical Radio입니다.",
        )
        self.assertEqual(
            format_now_playing_response({"title": "", "artist": "", "mode": "stop"}),
            "현재 재생 중인 곡이 없습니다.",
        )
        self.assertEqual(
            format_now_playing_response({"title": "Fix You", "artist": "Coldplay", "mode": "play"}, language="en"),
            "Now playing Fix You, by Coldplay.",
        )
        self.assertEqual(
            format_now_playing_response({"title": "", "artist": "", "mode": "stop"}, language="en"),
            "Nothing is currently playing.",
        )

    def test_now_playing_phrasing_coverage_and_instant_offline_parsing(self) -> None:
        from voice_control.rule_parser import parse_command

        test_phrases = [
            "지금 음악 뭐야?",
            "지금 음악 뭐야",
            "지금 노래 뭐야",
            "지금 곡 뭐야",
            "지금 트랙 뭐야",
            "지금 틀고 있는 음악 뭐야",
            "지금 나오는 노래 뭐야",
            "지금 나오는 거 뭐야",
            "지금 듣고 있는 음악 뭐야",
            "이 음악 뭐야",
            "이 노래 뭐야",
            "이 곡 뭐야",
            "그 노래 뭐야",
            "무슨 음악이야",
            "무슨 노래야",
            "무슨 곡이야",
            "무슨 노래 나와",
            "노래 제목 뭐야",
            "음악 제목 알려줘",
            "곡 제목이 뭐야",
            "가수가 누구야",
            "누구 노래야",
            "현재 재생 중인 곡",
            "현재 곡",
            "what's playing",
            "what song is this",
            "current song",
            "now playing",
        ]
        for phrase in test_phrases:
            intent = parse_command(phrase)
            self.assertEqual(
                intent.action,
                IntentAction.NOW_PLAYING,
                f"Failed to parse NOW_PLAYING for phrase: {phrase}",
            )
            self.assertGreaterEqual(
                intent.confidence,
                0.9,
                f"Low confidence for NOW_PLAYING phrase: {phrase}",
            )

    def test_empty_or_punctuation_stt_never_hits_cloud_fallback(self) -> None:
        from voice_control.cloud_stt_intent import HybridVoiceInterpreter

        interp = HybridVoiceInterpreter(
            provider="gemini",
            gemini_api_key="test-key",
            local_stt_url="http://127.0.0.1:8766",
        )
        with mock.patch.object(interp, "_gemini_text_intent") as mock_gem:
            # Punctuation only, whitespace, or single filler must NEVER hit cloud fallback
            for noise in [".", "...", "!", "  ", "어", "야", "응", "포코야 ."]:
                res = interp.interpret_text(noise, use_cloud_fallback=True)
                self.assertEqual(res.action, IntentAction.UNKNOWN)
            mock_gem.assert_not_called()

        # In interpret_audio with local STT returning "."
        with mock.patch.object(interp, "transcribe_local_audio", return_value={"text": "."}), mock.patch.object(
            interp, "_gemini_text_intent"
        ) as mock_gem:
            res = interp.interpret_audio(b"fake_wav")
            self.assertEqual(res.action, IntentAction.UNKNOWN)
            mock_gem.assert_not_called()

    def test_semantic_intent_matcher_coverage(self) -> None:
        from voice_control.semantic_intent import parse_semantic_command

        test_cases = [
            ("소리가 너무 커서 안 들려 줄여줘", IntentAction.VOLUME_DOWN),
            ("이거 노래 제목이 뭔지 알려줄래", IntentAction.NOW_PLAYING),
            ("마이믹스 재생해줘", IntentAction.PLAY_MIX),
            ("아무거나 신나는 거 틀어줘", IntentAction.PLAY_MIX),
            ("음악 잠깐 멈춰", IntentAction.PAUSE),
            ("소리 조금만 낮춰줘", IntentAction.VOLUME_DOWN),
            ("더 크게 해줘", IntentAction.VOLUME_UP),
            ("다음 곡 틀어줘", IntentAction.NEXT),
            ("what song is this", IntentAction.NOW_PLAYING),
            ("make it louder", IntentAction.VOLUME_UP),
        ]
        for phrase, expected_action in test_cases:
            intent = parse_semantic_command(phrase)
            self.assertIsNotNone(intent, f"Semantic matcher returned None for {phrase}")
            self.assertEqual(
                intent.action,
                expected_action,
                f"Semantic mismatch for {phrase}: expected {expected_action}, got {intent.action}",
            )
            self.assertGreaterEqual(intent.confidence, 0.85)

    def test_local_slm_intent_parsing_and_fallback_to_cloud(self) -> None:
        from voice_control.cloud_stt_intent import HybridVoiceInterpreter

        interp = HybridVoiceInterpreter(
            provider="gemini",
            gemini_api_key="test-key",
            local_stt_url="http://127.0.0.1:8766",
            local_llm_url="http://127.0.0.1:8767",
            enable_local_llm=True,
        )

        mock_slm_intent = VoiceIntent(
            action=IntentAction.PLAY_SEARCH,
            raw_text="비 올 때 듣는 재즈 골라줘",
            query="비 올 때 듣는 재즈",
            confidence=0.95,
            provider="local-slm-qwen0.5b",
        )

        # 1. When local SLM succeeds, cloud Gemini is NEVER called!
        with mock.patch.object(interp, "_local_llm_text_intent", return_value=mock_slm_intent) as mock_slm, mock.patch.object(
            interp, "_gemini_text_intent"
        ) as mock_gem:
            res = interp.interpret_text("비 올 때 듣는 재즈 골라줘", use_cloud_fallback=True)
            self.assertEqual(res.action, IntentAction.PLAY_SEARCH)
            self.assertEqual(res.provider, "local-slm-qwen0.5b")
            mock_slm.assert_called_once()
            mock_gem.assert_not_called()

        # 2. When local SLM fails or returns None, it falls back to cloud Gemini
        mock_gem_intent = VoiceIntent(
            action=IntentAction.ANSWER_QA,
            raw_text="양자 컴퓨터 원리가 뭐야?",
            answer="양자 컴퓨터는 큐비트를 사용합니다.",
            confidence=0.98,
            provider="gemini-text",
        )
        with mock.patch.object(interp, "_local_llm_text_intent", return_value=None) as mock_slm, mock.patch.object(
            interp, "_gemini_text_intent", return_value=mock_gem_intent
        ) as mock_gem:
            res = interp.interpret_text("양자 컴퓨터 원리가 뭐야?", use_cloud_fallback=True)
            self.assertEqual(res.action, IntentAction.ANSWER_QA)
            self.assertEqual(res.provider, "gemini-text")
            mock_slm.assert_called_once()
            mock_gem.assert_called_once()


if __name__ == "__main__":
    unittest.main()
