"""Comprehensive unit and integration tests for PocoBox Kids/Beginner English Listening Quiz."""

from __future__ import annotations

import http.client
import json
import os
import random
import sys
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "services"))
sys.path.insert(0, str(REPO_ROOT / "services" / "lms_client"))

from voice_control.audio_capture import VadMetrics  # noqa: E402
from voice_control.cloud_stt_intent import HybridVoiceInterpreter, _intent_from_llm_payload  # noqa: E402
from voice_control.daemon import VoiceController, make_handler  # noqa: E402
from voice_control.english_quiz import (  # noqa: E402
    BUILTIN_QUIZ_BANK,
    DEFAULT_QUIZ_COUNT,
    TOPIC_LABELS,
    EnglishQuizSession,
    QuizQuestion,
    classify_quiz_command,
    is_local_answer_match,
    normalize_answer,
    parse_quiz_options,
    question_from_dict,
    split_bilingual_segments,
)
from voice_control.feedback import CHIMES, synthesize_tone_wav  # noqa: E402
from voice_control.intent_schema import IntentAction  # noqa: E402
from voice_control.rule_parser import parse_command  # noqa: E402
from voice_control.tts import speak_segments  # noqa: E402


class _FakeLMS:
    def __init__(self, mode: str = "play", volume: int = 75) -> None:
        self.mode = mode
        self.volume = volume
        self.paused_calls: list[int] = []
        self.play_calls: int = 0

    def resolve_player_id(self, name: str | None = None) -> str:
        return "02:70:6f:63:6f:01"

    def player_status(self, player_id: str) -> dict:
        return {"mode": self.mode, "mixer volume": self.volume}

    def now_playing(self, player_id: str) -> dict:
        return {"title": "Test Song", "artist": "IU", "mode": self.mode, "volume": self.volume}

    def list_players(self) -> list[dict]:
        return [{"name": "pocobox", "playerid": "02:70:6f:63:6f:01", "connected": 1}]

    def pause(self, player_id: str, state: int = 1) -> dict:
        self.paused_calls.append(state)
        self.mode = "pause" if state == 1 else "play"
        return {"mode": self.mode}

    def play(self, player_id: str) -> dict:
        self.play_calls += 1
        self.mode = "play"
        return {"mode": self.mode}

    def set_volume(self, player_id: str, value: int) -> dict:
        self.volume = int(value)
        return {"volume": self.volume}


class TestEnglishQuizEngine(unittest.TestCase):
    def test_builtin_quiz_bank_integrity(self) -> None:
        self.assertGreaterEqual(len(BUILTIN_QUIZ_BANK), 40)
        ids = [q.id for q in BUILTIN_QUIZ_BANK]
        self.assertEqual(len(ids), len(set(ids)), "Duplicate quiz question IDs")
        cats = {q.category for q in BUILTIN_QUIZ_BANK}
        for expected_cat in ("animals", "food", "colors_numbers", "family_school", "daily", "riddles"):
            self.assertIn(expected_cat, cats)
        for q in BUILTIN_QUIZ_BANK:
            self.assertTrue(q.english_text.strip())
            self.assertTrue(q.english_text.strip().endswith("?"), f"Expected question mark in {q.id}: {q.english_text}")
            # Every built-in listening question has 2-3 English sentences
            sentence_marks = sum(q.english_text.count(ch) for ch in (".", "?", "!"))
            self.assertGreaterEqual(sentence_marks, 2, f"Expected >=2 sentences in {q.id}: {q.english_text}")
            self.assertTrue(q.korean_meaning.strip())
            self.assertTrue(q.accepted_answers)
            self.assertTrue(q.hint_ko.strip())
            self.assertTrue(q.explanation_ko.strip())

    def test_rule_parser_start_and_stop_quiz(self) -> None:
        it1 = parse_command("포코야 영어 퀴즈 시작해줘")
        self.assertEqual(it1.action, IntentAction.START_QUIZ)
        self.assertTrue(it1.wake_word_detected)
        self.assertEqual(it1.quiz_topic, "all")
        self.assertEqual(it1.quiz_count, DEFAULT_QUIZ_COUNT)
        self.assertEqual(DEFAULT_QUIZ_COUNT, 3)

        it2 = parse_command("포코야 동물 영어 퀴즈 3문제 내줘")
        self.assertEqual(it2.action, IntentAction.START_QUIZ)
        self.assertEqual(it2.quiz_topic, "animals")
        self.assertEqual(it2.quiz_count, 3)

        it3 = parse_command("생활 영어 문장 퀴즈 두 문제 해줘")
        self.assertEqual(it3.action, IntentAction.START_QUIZ)
        self.assertEqual(it3.quiz_topic, "daily")
        self.assertEqual(it3.quiz_count, 2)

        it4 = parse_command("Hey Poco play English quiz")
        self.assertEqual(it4.action, IntentAction.START_QUIZ)
        self.assertTrue(it4.wake_word_detected)

        it5 = parse_command("포코야 영어 퀴즈 그만")
        self.assertEqual(it5.action, IntentAction.STOP_QUIZ)

        it6 = parse_command("stop quiz")
        self.assertEqual(it6.action, IntentAction.STOP_QUIZ)

    def test_parse_quiz_options_and_commands(self) -> None:
        self.assertEqual(parse_quiz_options("음식 영어 퀴즈 7문제"), ("food", 7))
        self.assertEqual(parse_quiz_options("색깔 퀴즈 세 문제"), ("colors_numbers", 3))
        self.assertEqual(parse_quiz_options("영어 퀴즈 99문제"), ("all", 10))  # clamped

        self.assertEqual(classify_quiz_command("다시 들려줘"), "repeat")
        self.assertEqual(classify_quiz_command("천천히 다시 말해줘"), "repeat_slow")
        self.assertEqual(classify_quiz_command("힌트 줘"), "hint")
        self.assertEqual(classify_quiz_command("모르겠어요 패스"), "skip")
        self.assertEqual(classify_quiz_command("지금 몇 점이야?"), "score")
        self.assertEqual(classify_quiz_command("그만할래요"), "stop")
        self.assertIsNone(classify_quiz_command("코끼리예요"))

    def test_answer_normalization_and_forgiving_matching(self) -> None:
        q_word = QuizQuestion(
            id="t1",
            category="animals",
            kind="word_meaning",
            english_text="Elephant",
            korean_meaning="코끼리",
            accepted_answers=("코끼리",),
            hint_ko="긴 코!",
            explanation_ko="코끼리예요.",
        )
        self.assertEqual(normalize_answer("정답은 코끼리예요!"), "코끼리")
        self.assertTrue(is_local_answer_match("코끼리", q_word))
        self.assertTrue(is_local_answer_match("코끼리예요!", q_word))
        self.assertTrue(is_local_answer_match("정답은 코끼리인 것 같아요", q_word))
        self.assertFalse(is_local_answer_match("사자", q_word))

        q_riddle = QuizQuestion(
            id="t2",
            category="riddles",
            kind="listen_and_reply",
            english_text="What color is a banana?",
            korean_meaning="Yellow (노란색)",
            accepted_answers=("yellow", "옐로우", "노란색", "노랑"),
            hint_ko="바나나 색!",
            explanation_ko="노란색이에요.",
        )
        self.assertTrue(is_local_answer_match("Yellow!", q_riddle))
        self.assertTrue(is_local_answer_match("옐로우예요", q_riddle))
        self.assertTrue(is_local_answer_match("노란색이요", q_riddle))
        self.assertFalse(is_local_answer_match("파란색", q_riddle))

        q_short = QuizQuestion(
            id="t3",
            category="food",
            kind="word_meaning",
            english_text="Water",
            korean_meaning="물",
            accepted_answers=("물",),
            hint_ko="마시는 것!",
            explanation_ko="물이에요.",
        )
        self.assertTrue(is_local_answer_match("물이에요", q_short))
        self.assertFalse(is_local_answer_match("동물", q_short))

    def test_full_quiz_session_lifecycle(self) -> None:
        q1 = QuizQuestion("q1", "animals", "word_meaning", "Elephant", "코끼리", ("코끼리",), "긴 코!", "코끼리예요.")
        q2 = QuizQuestion("q2", "daily", "sentence_meaning", "I am hungry.", "나 배고파요", ("배고파", "배고파요"), "밥 먹을 때!", "배고파요.")
        q3 = QuizQuestion("q3", "riddles", "listen_and_reply", "What color is a banana?", "Yellow (노란색)", ("yellow", "노란색"), "바나나 색!", "노란색이에요.")

        sess = EnglishQuizSession(rng=random.Random(42))
        start = sess.start(topic="all", count=3, custom_questions=[q1, q2, q3])
        self.assertTrue(start.ok)
        self.assertTrue(start.active)
        self.assertTrue(start.expect_response)
        self.assertEqual(start.event, "started")
        # Questions are asked in English without "Listen carefully"
        en_segs = [txt for role, txt in start.segments if role == "en"]
        self.assertTrue(any("Question 1" in s for s in en_segs))
        self.assertFalse(any("listen carefully" in txt.lower() for _, txt in start.segments))
        self.assertEqual(en_segs.count("Elephant"), 2)
        self.assertTrue(any("What does it mean" in s for s in en_segs))
        self.assertFalse(any(role == "ko" for role, _ in start.segments))

        # Repeat slow
        rep = sess.handle_input("천천히 다시 들려줘")
        self.assertEqual(rep.event, "repeat")
        self.assertTrue(rep.expect_response)
        self.assertTrue(any(role == "en_slow" and txt == "Elephant" for role, txt in rep.segments))
        self.assertTrue(any(role == "en" and "slowly" in txt.lower() for role, txt in rep.segments))

        # Hint
        hint = sess.handle_input("힌트 줘")
        self.assertEqual(hint.event, "hint")
        self.assertTrue(hint.expect_response)
        self.assertIn("긴 코!", hint.display_text)

        # 1st wrong answer -> auto hint & retry (stays on Q1!)
        wrong1 = sess.handle_input("기린")
        self.assertEqual(wrong1.event, "retry_hint")
        self.assertTrue(wrong1.active)
        self.assertTrue(wrong1.expect_response)
        self.assertEqual(sess.current_index, 0)

        # Correct on 2nd try -> advances to Q2
        ok1 = sess.handle_input("코끼리예요")
        self.assertEqual(ok1.event, "correct")
        self.assertTrue(ok1.expect_response)
        self.assertEqual(ok1.chime, "quiz_correct")
        self.assertEqual(sess.score, 1)
        self.assertEqual(sess.current_index, 1)

        # Skip Q2 -> reveals answer and advances to Q3
        skip2 = sess.handle_input("모르겠어요")
        self.assertEqual(skip2.event, "reveal_next")
        self.assertTrue(skip2.expect_response)
        self.assertIn("배고파요", skip2.display_text)
        self.assertEqual(sess.score, 1)
        self.assertEqual(sess.current_index, 2)

        # Score check mid-quiz
        sc = sess.handle_input("지금 몇 점이야")
        self.assertEqual(sc.event, "score")
        self.assertTrue(sc.expect_response)
        self.assertIn("1", sc.spoken_summary)

        # Answer Q3 correctly -> finishes quiz!
        fin = sess.handle_input("Yellow")
        self.assertEqual(fin.event, "finished")
        self.assertFalse(fin.active)
        self.assertFalse(fin.expect_response)
        self.assertEqual(fin.chime, "quiz_finish")
        self.assertEqual(sess.score, 2)
        self.assertIn("3문제 중에서 2문제", fin.spoken_summary)

    def test_llm_payload_start_quiz_and_question_from_dict(self) -> None:
        intent = _intent_from_llm_payload(
            {
                "transcript": "포코야 동물 영어 퀴즈 4문제 시작해줘",
                "is_music_only": False,
                "wake_word_detected": True,
                "action": "start_quiz",
                "confidence": 0.95,
            },
            provider="gemini-audio",
        )
        self.assertEqual(intent.action, IntentAction.START_QUIZ)
        self.assertTrue(intent.wake_word_detected)
        self.assertEqual(intent.quiz_topic, "animals")
        self.assertEqual(intent.quiz_count, 4)

        q = question_from_dict({
            "kind": "word_meaning",
            "category": "animals",
            "english_text": "Tiger",
            "korean_meaning": "호랑이",
            "accepted_answers": ["호랑이"],
            "hint_ko": "줄무늬가 멋진 동물이에요!",
            "explanation_ko": "Tiger는 호랑이예요!",
        })
        self.assertIsNotNone(q)
        assert q is not None
        self.assertEqual(q.english_text, "Tiger")

    def test_split_bilingual_segments_removes_konglish_in_explanations(self) -> None:
        import re

        segs = split_bilingual_segments(
            "정답은 Elephant, 코끼리예요. 'long nose'는 긴 코, 'pick up food'는 음식을 집어 올린다는 뜻이에요."
        )
        en_parts = [txt for role, txt in segs if role == "en"]
        ko_parts = [txt for role, txt in segs if role == "ko"]
        self.assertEqual(en_parts, ["Elephant", "long nose", "pick up food"])
        for ko in ko_parts:
            self.assertIsNone(re.search(r"[A-Za-z]", ko), f"English leaked into Korean TTS chunk: {ko!r}")

        # Verify ALL 48 built-in explanations/hints have zero English letters left in 'ko' chunks
        for q in BUILTIN_QUIZ_BANK:
            for text in (q.explanation_ko, q.hint_ko):
                for role, chunk in split_bilingual_segments(text):
                    if role == "ko":
                        self.assertIsNone(re.search(r"[A-Za-z]", chunk), f"English in ko chunk for {q.id}: {chunk!r}")

    def test_blind_audio_stt_never_leaks_answer_and_rejects_random_speech(self) -> None:
        interp = HybridVoiceInterpreter(provider="gemini", gemini_api_key="test-key")
        captured_payloads: list[dict] = []

        def fake_http_json(url: str, body: dict, **kwargs: object) -> dict:
            captured_payloads.append(body)
            if len(captured_payloads) == 1:
                # Step 1: blind audio STT returns literal nonsense spoken by user
                return {"candidates": [{"content": {"parts": [{"text": '{"transcript": "아무말 대잔치"}'}]}}]}
            # Step 2: strict semantic grader rejects nonsense
            return {"candidates": [{"content": {"parts": [{"text": '{"is_correct": false}'}]}}]}

        with mock.patch.object(interp, "_http_json", side_effect=fake_http_json):
            out = interp.transcribe_and_grade_quiz_audio(
                b"RIFF\x00\x00\x00\x00",
                english_text="I am a very big gray animal with large ears. What animal am I?",
                korean_meaning="Elephant (코끼리)",
                accepted_answers=("elephant", "코끼리"),
                kind="story_quiz",
            )
        self.assertEqual(out["transcript"], "아무말 대잔치")
        self.assertFalse(out["is_correct"])
        # Confirm Step 1 audio request NEVER contained the expected answer!
        first_req_str = json.dumps(captured_payloads[0], ensure_ascii=False)
        self.assertNotIn("Elephant", first_req_str)
        self.assertNotIn("코끼리", first_req_str)

    def test_chimes_and_bilingual_speak_segments(self) -> None:
        for name in ("quiz_prompt", "quiz_correct", "quiz_wrong", "quiz_finish"):
            self.assertIn(name, CHIMES)
            wav = synthesize_tone_wav(CHIMES[name], volume=0.25)
            self.assertTrue(wav.startswith(b"RIFF"))

        synth_calls: list[tuple[str, str, str, int]] = []
        played_order: list[bytes] = []

        def fake_synth(text: str, lang: str, voice: str, rate_pct: int, pitch_hz: int) -> bytes:
            synth_calls.append((text, lang, voice, rate_pct))
            return f"PCM:{lang}:{voice}:{rate_pct}:{text}".encode("utf-8")

        def fake_play(pcm: bytes, sink: str | None = None, env: dict | None = None) -> tuple[bool, str | None]:
            played_order.append(pcm)
            return True, sink

        with mock.patch("voice_control.tts.synthesize_pcm", side_effect=fake_synth), \
             mock.patch("voice_control.tts.play_pcm", side_effect=fake_play), \
             mock.patch("voice_control.tts.resolve_internal_speaker_sink", return_value="fake-sink"), \
             mock.patch("voice_control.tts.time.sleep"):
            ok = speak_segments(
                [("ko", "정답은"), ("en", "Elephant"), ("pause", "0.2"), ("en_slow", "Elephant")],
                ko_voice="ko-KR-SunHiNeural",
                en_voice="en-US-JennyNeural",
                ko_rate_pct=0,
                en_rate_pct=-8,
                slow_en_rate_pct=-18,
                enabled=True,
            )
        self.assertTrue(ok)
        self.assertEqual(
            played_order,
            [
                "PCM:ko:ko-KR-SunHiNeural:0:정답은".encode("utf-8"),
                "PCM:en:en-US-JennyNeural:-8:Elephant".encode("utf-8"),
                "PCM:en:en-US-JennyNeural:-18:Elephant".encode("utf-8"),
            ],
        )


class TestEnglishQuizDaemonIntegration(unittest.TestCase):
    def _make_controller(self) -> tuple[VoiceController, _FakeLMS]:
        with mock.patch.dict(os.environ, {"CONTINUOUS_LISTEN": "0", "VOICE_PROVIDER": "local"}):
            ctrl = VoiceController()
        fake_lms = _FakeLMS(mode="play", volume=75)
        ctrl.lms = fake_lms  # type: ignore[assignment]
        ctrl.executor.lms = fake_lms  # type: ignore[assignment]
        return ctrl, fake_lms

    def test_text_quiz_pauses_and_resumes_lms(self) -> None:
        ctrl, fake_lms = self._make_controller()
        spoken_batches: list[list[tuple[str, str]]] = []

        def fake_speak_segments(segs: list[tuple[str, str]], **kwargs: object) -> bool:
            spoken_batches.append(segs)
            return True

        with mock.patch("voice_control.daemon.speak_segments", side_effect=fake_speak_segments), \
             mock.patch("voice_control.daemon.play_chime"):
            res_start = ctrl.handle_text("포코야 동물 영어 퀴즈 2문제 시작해줘", dry_run=False, use_cloud=False)
            self.assertTrue(res_start.ok)
            self.assertEqual(res_start.action, "start_quiz")
            self.assertTrue(ctrl.quiz.active)
            # LMS paused cleanly when quiz started
            self.assertEqual(fake_lms.mode, "pause")

            # Answer Q1 and Q2 correctly
            q1 = ctrl.quiz.current_question()
            assert q1 is not None
            res1 = ctrl.handle_text(q1.korean_meaning, dry_run=False, use_cloud=False)
            self.assertTrue(res1.ok)
            self.assertEqual(res1.details["quiz_event"], "correct")

            q2 = ctrl.quiz.current_question()
            assert q2 is not None
            res2 = ctrl.handle_text(q2.korean_meaning, dry_run=False, use_cloud=False)
            self.assertTrue(res2.ok)
            self.assertEqual(res2.details["quiz_event"], "finished")
            self.assertFalse(ctrl.quiz.active)

            # Wait briefly for background TTS + LMS resume thread
            deadline = time.monotonic() + 2.0
            while fake_lms.mode != "play" and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(fake_lms.mode, "play")
            self.assertGreaterEqual(len(spoken_batches), 3)

    def test_hands_free_voice_loop_completes_conversation(self) -> None:
        ctrl, fake_lms = self._make_controller()
        ctrl.interpreter = HybridVoiceInterpreter(provider="gemini", gemini_api_key="test-key")
        q1 = QuizQuestion("v1", "food", "word_meaning", "Apple", "사과", ("사과",), "빨간 과일!", "사과예요.")
        q2 = QuizQuestion("v2", "animals", "word_meaning", "Lion", "사자", ("사자",), "동물의 왕!", "사자예요.")

        # Mock 2 spoken answers: "사과예요" then "사자"
        fake_metrics = VadMetrics(duration_sec=1.0, rms_mean=300.0, rms_peak=900.0, speech_chunks=6, total_chunks=10, has_speech=True)
        fake_wav = b"RIFF" + b"\x00" * 64
        stt_responses = iter([
            {"transcript": "사과예요", "command": "answer", "is_correct": True},
            {"transcript": "사자", "command": "answer", "is_correct": True},
        ])

        events_order: list[str] = []

        def fake_speak_segments(*args: object, **kwargs: object) -> bool:
            events_order.append("speak")
            return True

        def fake_chime(kind: str, **kwargs: object) -> None:
            events_order.append(f"chime:{kind}")

        with mock.patch("voice_control.daemon.speak_segments", side_effect=fake_speak_segments), \
             mock.patch("voice_control.daemon.play_chime", side_effect=fake_chime), \
             mock.patch("voice_control.daemon.time.sleep"), \
             mock.patch("voice_control.daemon.record_utterance_wav", return_value=(fake_wav, fake_metrics)), \
             mock.patch("voice_control.daemon.filter_wav_speech_band", side_effect=lambda b: b), \
             mock.patch("voice_control.daemon.trim_silence_wav", side_effect=lambda b, **kw: b), \
             mock.patch("voice_control.daemon.normalize_wav_gain", side_effect=lambda b: b), \
             mock.patch.object(ctrl.interpreter, "transcribe_and_grade_quiz_audio", side_effect=lambda *a, **kw: next(stt_responses)):
            ctrl._pause_lms_for_quiz()
            turn = ctrl.quiz.start(topic="all", count=2, custom_questions=[q1, q2])
            ctrl._run_quiz_voice_loop(turn)

        self.assertFalse(ctrl.quiz.active)
        self.assertEqual(ctrl.quiz.score, 2)
        self.assertFalse(ctrl._quiz_loop_active)
        self.assertEqual(fake_lms.mode, "play")
        # Verify exact audio cue ordering:
        # Q1 speak -> quiz_prompt cue -> Q1 correct chime -> Q2 speak -> quiz_prompt cue -> finish chime -> final speak (no trailing prompt!)
        self.assertEqual(
            events_order,
            [
                "speak",
                "chime:quiz_prompt",
                "chime:quiz_correct",
                "speak",
                "chime:quiz_prompt",
                "chime:quiz_finish",
                "speak",
            ],
        )

    def test_hands_free_voice_loop_exits_gracefully_after_two_silences(self) -> None:
        ctrl, fake_lms = self._make_controller()
        ctrl.interpreter = HybridVoiceInterpreter(provider="gemini", gemini_api_key="test-key")
        silent_metrics = VadMetrics(duration_sec=2.0, rms_mean=5.0, rms_peak=10.0, speech_chunks=0, total_chunks=20, has_speech=False)

        with mock.patch("voice_control.daemon.speak_segments", return_value=True) as mock_speak, \
             mock.patch("voice_control.daemon.play_chime"), \
             mock.patch("voice_control.daemon.record_utterance_wav", return_value=(b"", silent_metrics)):
            ctrl._pause_lms_for_quiz()
            turn = ctrl.quiz.start(topic="animals", count=3)
            ctrl._run_quiz_voice_loop(turn)

        self.assertFalse(ctrl.quiz.active)
        self.assertFalse(ctrl._quiz_loop_active)
        self.assertEqual(fake_lms.mode, "play")
        # Initial prompt + 1st silence gentle replay + 2nd silence timeout exit = 3 TTS calls
        self.assertEqual(mock_speak.call_count, 3)

    def test_http_quiz_endpoints(self) -> None:
        ctrl, _ = self._make_controller()
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ctrl))
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            def req(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                raw = json.dumps(body).encode("utf-8") if body is not None else b""
                headers = {"Content-Type": "application/json"} if body is not None else {}
                conn.request(method, path, body=raw, headers=headers)
                resp = conn.getresponse()
                data = json.loads(resp.read().decode("utf-8"))
                conn.close()
                return resp.status, data

            code, st = req("GET", "/quiz/state")
            self.assertEqual(code, 200)
            self.assertFalse(st["quiz"]["active"])
            self.assertIn("animals", st["topics"])

            code, started = req("POST", "/quiz/start", {"topic": "animals", "count": 2, "dry_run": True})
            self.assertEqual(code, 200)
            self.assertTrue(started["ok"])
            self.assertTrue(started["details"]["quiz"]["active"])

            code, skipped = req("POST", "/quiz/start", {"topic": "all", "count": 3, "skip_if_active": True, "dry_run": True})
            self.assertEqual(code, 200)
            self.assertTrue(skipped["ok"])
            self.assertTrue(skipped.get("skipped"))

            code, ans = req("POST", "/quiz/answer", {"text": "힌트", "dry_run": True, "use_cloud": False})
            self.assertEqual(code, 200)
            self.assertEqual(ans["details"]["quiz_event"], "hint")

            code, stopped = req("POST", "/quiz/stop", {"dry_run": True})
            self.assertEqual(code, 200)
            self.assertFalse(stopped["details"]["quiz"]["active"])

            code, sched = req("GET", "/quiz/schedule")
            self.assertEqual(code, 200)
            self.assertTrue(sched["ok"])
            self.assertIn("schedule_label", sched)

            code, trig = req("POST", "/quiz/schedule/trigger", {"dry_run": True})
            self.assertEqual(code, 200)
            self.assertTrue(trig["ok"])
            self.assertEqual(trig["action"], "start_quiz")
            self.assertTrue(trig["details"]["quiz"]["active"])
        finally:
            server.shutdown()
            server.server_close()

    def test_daily_quiz_systemd_timer_config(self) -> None:
        timer_txt = (REPO_ROOT / "infra" / "voice" / "pocobox-daily-quiz.timer").read_text(encoding="utf-8")
        svc_txt = (REPO_ROOT / "infra" / "voice" / "pocobox-daily-quiz.service").read_text(encoding="utf-8")
        self.assertIn("Mon..Fri *-*-* 07:45:00 Asia/Seoul", timer_txt)
        self.assertIn("Sat,Sun *-*-* 09:00:00 Asia/Seoul", timer_txt)
        self.assertIn("Persistent=false", timer_txt)
        self.assertIn("pocobox-voice quiz daily", svc_txt)

    def test_schedule_timestamp_parser_to_kst(self) -> None:
        from voice_control.daemon import _parse_systemd_timestamp_to_kst
        kst, rel, epoch = _parse_systemd_timestamp_to_kst("Wed 2026-11-18 22:45:04 GMT")
        self.assertEqual(kst, "2026-11-19 07:45:04 KST")
        self.assertIsNotNone(epoch)
        self.assertIn("전", rel)

        kst_na, rel_na, ep_na = _parse_systemd_timestamp_to_kst("n/a")
        self.assertEqual(kst_na, "-")
        self.assertEqual(rel_na, "-")
        self.assertIsNone(ep_na)


if __name__ == "__main__":
    unittest.main()
