#!/usr/bin/env python3
"""Unit tests for VoiceExecutor, HybridVoiceInterpreter (Gemini/OpenAI mocks), VAD, and chimes."""

from __future__ import annotations

import json
import math
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "services"))
sys.path.insert(0, str(REPO_ROOT / "services" / "lms_client"))

from lms_client import (  # noqa: E402
    LMSClient,
    extract_tidal_track_id,
    is_classical_movement_work,
    parse_movement_number,
)
from voice_control.audio_capture import analyze_wav_bytes, pcm_to_wav_bytes, trim_silence_wav  # noqa: E402
from voice_control.cloud_stt_intent import HybridVoiceInterpreter  # noqa: E402
from voice_control.executor import VoiceExecutor, format_command_ack  # noqa: E402
from voice_control.feedback import synthesize_tone_wav  # noqa: E402
from voice_control.intent_schema import IntentAction, VoiceIntent  # noqa: E402
from voice_control.rule_parser import parse_command  # noqa: E402


class FakeLMSClient(LMSClient):
    """In-memory mock LMS server simulating players, TIDAL search fallback, favorites, and volume."""

    def __init__(self) -> None:
        super().__init__(host="127.0.0.1", port=9000)
        self.volume = 50
        self.muted = False
        self.mode = "stop"
        self.played_items: list[str] = []
        self.commands: list[tuple[str, list]] = []

    def call(self, player_id: str, command: list) -> dict:
        self.commands.append((player_id, command))
        if command[0] == "serverstatus":
            return {
                "players_loop": [
                    {"playerid": "02:70:6f:63:6f:01", "name": "pocobox", "connected": 1, "power": 1}
                ]
            }
        if command[0] == "power":
            return {}
        if command[0] == "play":
            self.mode = "play"
            return {}
        if command[0] == "pause":
            self.mode = "pause"
            return {}
        if command[0] == "playlist" and command[1] == "index":
            return {}
        if command[0] == "mixer" and command[1] == "volume":
            if command[2] == "?":
                return {"_volume": self.volume}
            self.volume = int(command[2])
            return {}
        if command[0] == "mixer" and command[1] == "muting":
            self.muted = bool(command[2])
            return {}
        if command[0] == "status":
            return {
                "player_name": "pocobox",
                "mode": self.mode,
                "power": 1,
                "mixer volume": self.volume,
                "playlist_tracks": 1,
                "playlist_loop": [{"title": "Through the Night", "artist": "IU", "album": "Palette"}],
            }
        if command[0] == "favorites" and command[1] == "items":
            return {"loop_loop": [{"id": "0", "name": "Jazz Radio"}, {"id": "1", "name": "IU Favorites"}]}
        if command[0] == "favorites" and command[1] == "playlist":
            self.played_items.append(f"fav:{command[3]}")
            return {}
        if command[0] == "tidal" and command[1] == "items":
            item_id = next((c.split(":", 1)[1] for c in command if isinstance(c, str) and c.startswith("item_id:")), None)
            search = next((c.split(":", 1)[1] for c in command if isinstance(c, str) and c.startswith("search:")), None)
            if item_id is None and search is None:
                return {
                    "loop_loop": [
                        {"id": "2", "name": "My Mix", "type": "playlist"},
                        {"id": "7", "name": "Search", "type": "search"},
                    ]
                }
            if item_id == "2":
                return {"loop_loop": [{"id": "2.0", "name": "My Mix 1", "type": "playlist", "isaudio": 1}]}
            if item_id == "7" and search:
                return {
                    "loop_loop": [
                        {"id": f"7_{search}.2", "name": "Artists"},
                        {"id": f"7_{search}.3", "name": "Albums"},
                        {"id": f"7_{search}.4", "name": "Songs"},
                    ]
                }
            # Simulate Korean combined query returning 0 hits while English query succeeds!
            if item_id == "7_아이유 밤편지.4":
                return {"loop_loop": []}
            if item_id == "7_IU Through the Night.4":
                return {
                    "loop_loop": [
                        {"id": "7_iu.4.0", "name": "Through the Night (Karaoke)", "isaudio": 1, "type": "audio"},
                        {"id": "7_iu.4.1", "name": "Through the Night", "isaudio": 1, "type": "audio"},
                    ]
                }
            if item_id == "7_Coldplay.2":
                return {"loop_loop": [{"id": "7_cp.2.0", "name": "Coldplay", "isaudio": 0, "hasitems": 1}]}
            if item_id == "7_cp.2.0":
                return {"loop_loop": [{"id": "7_cp.2.0.top", "name": "Top Tracks", "isaudio": 1}]}
            # Simulate Beethoven Symphony No. 5 album with 4 movements
            if item_id == "7_Beethoven Symphony No. 5.3":
                return {
                    "loop_loop": [
                        {
                            "id": "7_beet5_alb",
                            "name": "Beethoven: Symphony No. 5, Op. 67",
                            "type": "playlist",
                            "isaudio": 1,
                            "hasitems": 1,
                        }
                    ]
                }
            if item_id == "7_beet5_alb":
                return {
                    "loop_loop": [
                        {"id": "7_b5.0", "name": "Symphony No. 5 in C Minor, Op. 67: I. Allegro con brio", "isaudio": 1, "type": "audio"},
                        {"id": "7_b5.1", "name": "Symphony No. 5 in C Minor, Op. 67: II. Andante con moto", "isaudio": 1, "type": "audio"},
                        {"id": "7_b5.2", "name": "Symphony No. 5 in C Minor, Op. 67: III. Allegro", "isaudio": 1, "type": "audio"},
                        {"id": "7_b5.3", "name": "Symphony No. 5 in C Minor, Op. 67: IV. Allegro", "isaudio": 1, "type": "audio"},
                    ]
                }
            # Simulate Rachmaninoff Piano Concerto No. 2 album with 3 movements
            if item_id == "7_Rachmaninoff Piano Concerto No. 2.3":
                return {
                    "loop_loop": [
                        {
                            "id": "7_rach2_alb",
                            "name": "Rachmaninoff: Piano Concerto No. 2 in C Minor, Op. 18",
                            "type": "playlist",
                            "isaudio": 1,
                            "hasitems": 1,
                        }
                    ]
                }
            if item_id == "7_rach2_alb":
                return {
                    "loop_loop": [
                        {"id": "7_r2.0", "name": "Piano Concerto No. 2 in C Minor, Op. 18: I. Moderato", "isaudio": 1, "type": "audio"},
                        {"id": "7_r2.1", "name": "Piano Concerto No. 2 in C Minor, Op. 18: II. Adagio sostenuto", "isaudio": 1, "type": "audio"},
                        {"id": "7_r2.2", "name": "Piano Concerto No. 2 in C Minor, Op. 18: III. Allegro scherzando", "isaudio": 1, "type": "audio"},
                    ]
                }
            return {"loop_loop": []}
        if command[0] == "tidal" and command[1] == "playlist" and command[2] == "play":
            self.played_items.append(command[3])
            self.mode = "play"
            return {}
        if command[0] == "tidal" and command[1] == "playlist" and command[2] == "add":
            self.played_items.append(f"add:{command[3]}")
            return {}
        return {}


class TestVoiceExecutorAndCloud(unittest.TestCase):
    def test_tidal_fallback_and_karaoke_penalty(self) -> None:
        lms = FakeLMSClient()
        executor = VoiceExecutor(lms)
        intent = parse_command("아이유 밤편지 틀어줘")
        res = executor.execute(intent)
        self.assertTrue(res.ok, res.message)
        # Should pick original track over Karaoke variant
        self.assertEqual(res.details["title"], "Through the Night")
        self.assertEqual(lms.played_items[-1], "item_id:7_iu.4.1")

    def test_artist_drilldown_to_top_tracks(self) -> None:
        lms = FakeLMSClient()
        executor = VoiceExecutor(lms)
        intent = parse_command("콜드플레이 틀어줘")
        res = executor.execute(intent)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(res.details["category"], "artists")
        self.assertEqual(lms.played_items[-1], "item_id:7_cp.2.0.top")

    def test_classical_movement_parser_and_work_detector(self) -> None:
        self.assertTrue(is_classical_movement_work("베토벤 교향곡 5번"))
        self.assertTrue(is_classical_movement_work("차이코프스키 심포니 6번"))
        self.assertTrue(is_classical_movement_work("라흐마니노프 피아노 협주곡 2번"))
        self.assertTrue(is_classical_movement_work("모차르트 피아노 소나타 16번"))
        self.assertTrue(is_classical_movement_work("바흐 첼로 모음곡 1번"))
        self.assertTrue(is_classical_movement_work("현악 4중주 1번"))
        self.assertTrue(is_classical_movement_work("Beethoven Symphony No. 5"))
        self.assertTrue(is_classical_movement_work("Chopin Piano Sonata No. 2"))
        self.assertFalse(is_classical_movement_work("아이유 밤편지"))
        self.assertFalse(is_classical_movement_work("콜드플레이 픽스 유"))
        self.assertFalse(is_classical_movement_work("쇼팽 겨울바람"))

        # Movement number parser tests
        self.assertEqual(parse_movement_number("Symphony No. 5: I. Allegro con brio"), 1)
        self.assertEqual(parse_movement_number("Symphony No. 5: II. Andante con moto"), 2)
        self.assertEqual(parse_movement_number("Piano Concerto No. 2: 2. Adagio sostenuto"), 2)
        self.assertEqual(parse_movement_number("Piano Concerto No. 2: 3. Allegro scherzando"), 3)
        self.assertEqual(parse_movement_number("Piano Sonata No. 2: III. Marche funèbre"), 3)
        self.assertEqual(parse_movement_number("Piano Sonata No. 2: IV. Finale"), 4)
        self.assertEqual(parse_movement_number("Piano Concerto No. 1: 1a. Allegro non troppo"), 1)
        self.assertEqual(parse_movement_number("Cello Suite No. 1: I. Prélude"), 1)
        self.assertEqual(parse_movement_number("베토벤 교향곡 5번 1악장"), 1)
        self.assertEqual(parse_movement_number("라흐마니노프 협주곡 제2악장"), 2)
        self.assertIsNone(parse_movement_number("Through the Night"))
        self.assertIsNone(parse_movement_number("Fix You"))

    def test_classical_multi_movement_sequential_playback(self) -> None:
        lms = FakeLMSClient()
        executor = VoiceExecutor(lms)
        intent = parse_command("베토벤 교향곡 5번 틀어줘")
        self.assertEqual(intent.search_type, "classical")

        res = executor.execute(intent)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(res.details["category"], "classical_work")
        self.assertEqual(res.details["movements_count"], 4)
        self.assertIn("전 4악장", res.details["title"])

        # Movement 1 played first (playlist play), followed by movements 2, 3, 4 sequentially (playlist add)
        expected_sequence = [
            "item_id:7_b5.0",
            "add:item_id:7_b5.1",
            "add:item_id:7_b5.2",
            "add:item_id:7_b5.3",
        ]
        self.assertEqual(lms.played_items, expected_sequence)

    def test_classical_concerto_sequential_playback(self) -> None:
        lms = FakeLMSClient()
        executor = VoiceExecutor(lms)
        intent = parse_command("라흐마니노프 피아노 협주곡 2번 틀어줘")
        self.assertEqual(intent.search_type, "classical")

        res = executor.execute(intent)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(res.details["category"], "classical_work")
        self.assertEqual(res.details["movements_count"], 3)
        self.assertIn("전 3악장", res.details["title"])

        expected_sequence = [
            "item_id:7_r2.0",
            "add:item_id:7_r2.1",
            "add:item_id:7_r2.2",
        ]
        self.assertEqual(lms.played_items, expected_sequence)

    def test_classical_explicit_movement_request(self) -> None:
        lms = FakeLMSClient()
        executor = VoiceExecutor(lms)
        # Explicit request for Movement 2
        intent = parse_command("베토벤 교향곡 5번 2악장 틀어줘")
        res = executor.execute(intent)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(res.details["category"], "classical_work")
        self.assertEqual(res.details["start_movement"], 2)
        # Should start from Movement 2 and queue subsequent movements 3 and 4
        expected_sequence = [
            "item_id:7_b5.1",
            "add:item_id:7_b5.2",
            "add:item_id:7_b5.3",
        ]
        self.assertEqual(lms.played_items, expected_sequence)

    def test_playback_volume_and_mix_controls(self) -> None:
        lms = FakeLMSClient()
        executor = VoiceExecutor(lms)

        self.assertTrue(executor.execute(parse_command("마이믹스 틀어줘")).ok)
        self.assertEqual(lms.played_items[-1], "item_id:2.0")

        self.assertTrue(executor.execute(parse_command("볼륨 70으로 맞춰줘")).ok)
        self.assertEqual(lms.volume, 70)

        self.assertTrue(executor.execute(parse_command("조금만 줄여줘")).ok)
        self.assertEqual(lms.volume, 65)

        self.assertTrue(executor.execute(parse_command("음소거 해줘")).ok)
        self.assertTrue(lms.muted)

        self.assertTrue(executor.execute(parse_command("음소거 해제")).ok)
        self.assertFalse(lms.muted)

        np = executor.execute(parse_command("지금 나오는 노래 뭐야?"))
        self.assertTrue(np.ok)
        self.assertIn("Through the Night", np.message)

        neg = executor.execute(parse_command("오늘 점심 뭐 먹지?"))
        self.assertFalse(neg.ok)
        self.assertEqual(neg.action, IntentAction.UNKNOWN.value)

    def test_dac_mode_volume_controls_disabled_and_explains(self) -> None:
        lms = FakeLMSClient()
        executor = VoiceExecutor(lms, is_dac_mode_fn=lambda: True)

        res = executor.execute(parse_command("볼륨 70으로 맞춰줘"))
        self.assertTrue(res.ok)
        self.assertTrue(res.details.get("volume_disabled"))
        self.assertEqual(res.details.get("fixed_volume"), 100)
        self.assertIn("100%로 고정", res.message)
        self.assertEqual(lms.volume, 50)  # LMS volume left untouched

        res_down = executor.execute(parse_command("볼륨 줄여줘"))
        self.assertTrue(res_down.ok)
        self.assertTrue(res_down.details.get("volume_disabled"))
        self.assertEqual(lms.volume, 50)

        res_mute = executor.execute(parse_command("음소거 해줘"))
        self.assertTrue(res_mute.ok)
        self.assertTrue(res_mute.details.get("volume_disabled"))
        self.assertFalse(lms.muted)

        intent_vol = parse_command("볼륨 올려줘")
        self.assertIsNone(format_command_ack(intent_vol, is_dac_mode=True))
        self.assertIsNotNone(format_command_ack(intent_vol, is_dac_mode=False))

    def test_gemini_audio_single_shot_mock(self) -> None:
        interp = HybridVoiceInterpreter(provider="gemini", gemini_api_key="test-key")
        fake_response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps({
                                    "transcript": "아이유 블루밍 틀어줘",
                                    "action": "play_search",
                                    "query": "IU Blueming",
                                    "fallback_queries": ["블루밍"],
                                    "search_type": "track",
                                    "volume": None,
                                    "delta": None,
                                    "confidence": 0.97,
                                })
                            }
                        ]
                    }
                }
            ]
        }
        with patch.object(interp, "_http_json", return_value=fake_response):
            wav = synthesize_tone_wav([(440.0, 0.3)])
            intent = interp.interpret_audio(wav)
        self.assertEqual(intent.action, IntentAction.PLAY_SEARCH)
        self.assertEqual(intent.query, "IU Blueming")
        self.assertEqual(intent.provider, "gemini-audio")
        self.assertIn("블루밍", intent.fallback_queries)

    def test_openai_stt_uses_fast_rule_path(self) -> None:
        interp = HybridVoiceInterpreter(provider="openai", openai_api_key="test-key")
        with patch.object(interp, "_openai_transcribe", return_value="다음 곡"), \
             patch.object(interp, "_openai_text_intent") as mock_chat:
            wav = synthesize_tone_wav([(440.0, 0.3)])
            intent = interp.interpret_audio(wav)
        self.assertEqual(intent.action, IntentAction.NEXT)
        self.assertEqual(intent.provider, "openai-stt+rule")
        mock_chat.assert_not_called()

    def test_vad_and_silence_trimming(self) -> None:
        # 0.5s silence + 0.5s loud 440Hz tone + 0.5s silence
        sr = 16000
        silence = b"\x00\x00" * (sr // 2)
        tone_samples = [int(0.4 * 32767 * math.sin(2 * math.pi * 440 * i / sr)) for i in range(sr // 2)]
        tone = struct.pack(f"<{len(tone_samples)}h", *tone_samples)

        silent_wav = pcm_to_wav_bytes(silence)
        self.assertFalse(analyze_wav_bytes(silent_wav).has_speech)

        full_wav = pcm_to_wav_bytes(silence + tone + silence)
        metrics = analyze_wav_bytes(full_wav)
        self.assertTrue(metrics.has_speech)
        self.assertGreater(metrics.rms_peak, 5000)

        trimmed = trim_silence_wav(full_wav)
        trimmed_metrics = analyze_wav_bytes(trimmed)
        self.assertTrue(trimmed_metrics.has_speech)
        self.assertLess(trimmed_metrics.duration_sec, metrics.duration_sec)

    def test_chime_wav_generation(self) -> None:
        wav = synthesize_tone_wav([(587.33, 0.08), (880.0, 0.1)])
        self.assertTrue(wav.startswith(b"RIFF"))
        self.assertGreater(len(wav), 2000)

    def test_local_sensevoice_fast_path_skips_cloud(self) -> None:
        interp = HybridVoiceInterpreter(
            provider="gemini",
            gemini_api_key="test-key",
            local_stt_url="http://127.0.0.1:8766",
        )
        self.assertTrue(interp.can_transcribe_audio())
        wav = synthesize_tone_wav([(440.0, 0.3)])
        with patch.object(
            interp,
            "transcribe_local_audio",
            return_value={"ok": True, "text": "골드 플레이 노래 틀 어 줘.", "lang": "ko", "event": "Speech", "is_music_only": False},
        ), patch.object(interp, "_gemini_audio_intent") as mock_gem_audio, patch.object(
            interp, "_gemini_text_intent"
        ) as mock_gem_text:
            intent = interp.interpret_audio(wav)
        self.assertEqual(intent.action, IntentAction.PLAY_SEARCH)
        self.assertEqual(intent.query, "Coldplay")
        self.assertEqual(intent.provider, "sensevoice+rule")
        mock_gem_audio.assert_not_called()
        mock_gem_text.assert_not_called()

    def test_local_sensevoice_classical_winter_wind_fast_path(self) -> None:
        interp = HybridVoiceInterpreter(
            provider="gemini",
            gemini_api_key="test-key",
            local_stt_url="http://127.0.0.1:8766",
        )
        wav = synthesize_tone_wav([(440.0, 0.3)])
        with patch.object(
            interp,
            "transcribe_local_audio",
            return_value={"ok": True, "text": "겨울 바람 틀어 줘.", "lang": "ko", "event": "Speech", "is_music_only": False},
        ), patch.object(interp, "_gemini_text_intent") as mock_gem_text, patch.object(
            interp, "_local_llm_text_intent"
        ) as mock_slm:
            intent = interp.interpret_audio(wav)
        self.assertEqual(intent.action, IntentAction.PLAY_SEARCH)
        self.assertEqual(intent.query, "Winter Wind")
        self.assertEqual(intent.provider, "sensevoice+rule")
        mock_gem_text.assert_not_called()
        mock_slm.assert_not_called()

    def test_llm_payload_guards_against_now_playing_hallucination_on_chitchat(self) -> None:
        from voice_control.cloud_stt_intent import _intent_from_llm_payload

        # Random conversational statement: LLM hallucinating now_playing MUST be downgraded to unknown
        fake_slm_payload = {"action": "now_playing", "query": "인생이 달라지는 거야."}
        intent = _intent_from_llm_payload(fake_slm_payload, provider="local-slm-qwen0.5b", raw_fallback="인생이 달라지는 거야.")
        self.assertEqual(intent.action, IntentAction.UNKNOWN)

        # Genuine inquiry: preserved as now_playing
        genuine_payload = {"action": "now_playing", "query": "지금 나오는 노래 뭐야"}
        genuine_intent = _intent_from_llm_payload(genuine_payload, provider="local-slm-qwen0.5b", raw_fallback="지금 나오는 노래 뭐야")
        self.assertEqual(genuine_intent.action, IntentAction.NOW_PLAYING)

    def test_llm_payload_guards_against_play_search_hallucination_on_chitchat(self) -> None:
        from voice_control.cloud_stt_intent import _intent_from_llm_payload

        # Random conversational statement: SLM hallucinating play_search MUST be downgraded to unknown
        fake_slm_payload = {"action": "play_search", "query": "저 어렵긴 해요."}
        intent = _intent_from_llm_payload(fake_slm_payload, provider="local-slm-qwen0.5b", raw_fallback="저 어렵긴 해요.")
        self.assertEqual(intent.action, IntentAction.UNKNOWN)

        # Genuine request with play verb: preserved
        genuine_payload = {"action": "play_search", "query": "아이유 밤편지 틀어줘"}
        genuine_intent = _intent_from_llm_payload(genuine_payload, provider="local-slm-qwen0.5b", raw_fallback="아이유 밤편지 틀어줘")
        self.assertEqual(genuine_intent.action, IntentAction.PLAY_SEARCH)

        # Genuine request with matched alias: preserved
        alias_payload = {"action": "play_search", "query": "아이유 밤편지"}
        alias_intent = _intent_from_llm_payload(alias_payload, provider="local-slm-qwen0.5b", raw_fallback="아이유 밤편지")
        self.assertEqual(alias_intent.action, IntentAction.PLAY_SEARCH)

    def test_local_sensevoice_music_only_skips_cloud(self) -> None:
        interp = HybridVoiceInterpreter(
            provider="gemini",
            gemini_api_key="test-key",
            local_stt_url="http://127.0.0.1:8766",
        )
        wav = synthesize_tone_wav([(440.0, 0.3)])
        with patch.object(
            interp,
            "transcribe_local_audio",
            return_value={"ok": True, "text": "", "lang": "nospeech", "event": "BGM", "is_music_only": True},
        ), patch.object(interp, "_gemini_audio_intent") as mock_gem_audio:
            intent = interp.interpret_audio(wav)
        self.assertEqual(intent.action, IntentAction.UNKNOWN)
        self.assertEqual(intent.provider, "sensevoice-local")
        mock_gem_audio.assert_not_called()

    def test_local_sensevoice_qa_escalates_text_only_to_cloud(self) -> None:
        interp = HybridVoiceInterpreter(
            provider="gemini",
            gemini_api_key="test-key",
            local_stt_url="http://127.0.0.1:8766",
        )
        wav = synthesize_tone_wav([(440.0, 0.3)])
        qa_intent = VoiceIntent(
            action=IntentAction.ANSWER_QA,
            raw_text="프랑스 수도가 어디야?",
            answer="프랑스의 수도는 파리입니다.",
            confidence=0.95,
            provider="gemini-text",
        )
        with patch.object(
            interp,
            "transcribe_local_audio",
            return_value={"ok": True, "text": "프랑스 수도가 어디야?", "lang": "ko", "event": "Speech", "is_music_only": False},
        ), patch.object(interp, "_gemini_text_intent", return_value=qa_intent) as mock_text, patch.object(
            interp, "_gemini_audio_intent"
        ) as mock_audio:
            intent = interp.interpret_audio(wav)
        self.assertEqual(intent.action, IntentAction.ANSWER_QA)
        self.assertEqual(intent.provider, "sensevoice+gemini-text")
        mock_text.assert_called_once_with("프랑스 수도가 어디야?")
        mock_audio.assert_not_called()

    def test_local_sensevoice_blind_quiz_audio(self) -> None:
        interp = HybridVoiceInterpreter(provider="local", local_stt_url="http://127.0.0.1:8766")
        self.assertTrue(interp.can_transcribe_audio())
        wav = synthesize_tone_wav([(440.0, 0.2)])
        with patch.object(
            interp,
            "transcribe_local_audio",
            return_value={"ok": True, "text": "Dolphin.", "lang": "en", "event": "Speech", "is_music_only": False},
        ):
            self.assertEqual(interp.transcribe_blind_audio(wav), "Dolphin")

    def test_post_wake_dynamic_vad_ends_on_ducked_music_floor(self) -> None:
        from voice_control.continuous_listener import ContinuousVoiceListener

        listener = ContinuousVoiceListener(on_utterance=lambda _: None)
        listener.music_playing = True
        listener.ambient_rms = 250.0
        floor = listener.initial_wake_cmd_floor()
        # Simulate ducked room floor (~75 RMS), then speech (~380 RMS), then return to ~75 RMS
        speech_seen = False
        silent_run = 0
        rms_trace = [80.0, 75.0, 74.0, 350.0, 420.0, 390.0, 310.0] + [75.0] * 10
        stopped_at = None
        for idx, rms in enumerate(rms_trace):
            th = listener.wake_cmd_threshold(floor, speech_seen)
            is_speech = rms >= th
            floor = listener.update_wake_cmd_floor(floor, rms, is_speech)
            if is_speech:
                speech_seen = True
                silent_run = 0
            elif speech_seen:
                silent_run += 1
                if silent_run >= listener.wake_silence_chunks:
                    stopped_at = idx
                    break
        self.assertTrue(speech_seen)
        self.assertIsNotNone(stopped_at)
        self.assertLess(stopped_at, len(rms_trace) - 1)

    def test_tidal_dstm_and_track_radio_auto_queue_and_dedup(self) -> None:
        self.assertEqual(extract_tidal_track_id("tidal://17603023.flc"), "17603023")
        self.assertEqual(extract_tidal_track_id("tidal://track:4242"), "4242")
        self.assertEqual(extract_tidal_track_id("https://tidal.com/browse/track/98765"), "98765")
        self.assertIsNone(extract_tidal_track_id("http://stream.wqxr.org/wqxr"))
        self.assertIsNone(extract_tidal_track_id(""))

        class RadioLMS(LMSClient):
            def __init__(self) -> None:
                super().__init__(host="127.0.0.1", port=9000)
                self.dstm_provider: str | None = None
                self.queue: list[dict] = [{"url": "tidal://17603023.flc", "title": "Seed Track"}]
                self.cur_idx = 0
                self.calls: list[list] = []

            def call(self, player_id: str, command: list) -> dict:
                self.calls.append(list(command))
                if command[0] == "serverstatus":
                    return {"players_loop": [{"playerid": "02:70:6f:63:6f:01", "name": "pocobox", "connected": 1}]}
                if command[0] == "playerpref" and command[1] == "plugin.dontstopthemusic:provider":
                    if command[2] == "?":
                        return {"_p2": self.dstm_provider}
                    self.dstm_provider = str(command[2])
                    return {}
                if command[0] == "status":
                    start = self.cur_idx if command[1] == "-" else int(command[1])
                    count = int(command[2])
                    return {
                        "playlist_tracks": len(self.queue),
                        "playlist_cur_index": str(self.cur_idx),
                        "playlist_loop": self.queue[start : start + count],
                    }
                if command[0] == "tidal_browse" and command[1:3] == ["playlist", "add"]:
                    # Simulate TIDAL returning seed track first + 2 recommendations
                    self.queue.extend([
                        {"url": "tidal://17603023.flc", "title": "Seed Track"},
                        {"url": "tidal://22222222.flc", "title": "Rec 1"},
                        {"url": "tidal://33333333.flc", "title": "Rec 2"},
                    ])
                    return {}
                if command[0] == "playlist" and command[1] == "delete":
                    del self.queue[int(command[2])]
                    return {}
                if command[0] == "playlist" and command[1] == "index" and command[2] == "+1":
                    self.cur_idx = min(len(self.queue) - 1, self.cur_idx + 1)
                    return {}
                return {}

        lms = RadioLMS()
        self.assertEqual(lms.get_dstm_provider("p1"), "")
        self.assertTrue(lms.ensure_tidal_dstm("p1"))
        self.assertEqual(lms.get_dstm_provider("p1"), "PLUGIN_TIDAL_DSTM")

        # Appending radio on a single-track queue deduplicates the seed track at index 1
        res = lms.append_tidal_track_radio("p1", only_if_single_track=True, dedup_timeout_sec=0.5)
        self.assertTrue(res["ok"])
        self.assertTrue(res["deduped_seed"])
        self.assertEqual(res["added"], 2)
        self.assertEqual([t["url"] for t in lms.queue], [
            "tidal://17603023.flc",
            "tidal://22222222.flc",
            "tidal://33333333.flc",
        ])

        # Skips if queue already has multiple tracks (e.g. album/playlist)
        skip_res = lms.append_tidal_track_radio("p1", only_if_single_track=True)
        self.assertTrue(skip_res["skipped"])

        # Executor NEXT on last track automatically fills TIDAL track radio before advancing
        lms2 = RadioLMS()
        ex = VoiceExecutor(lms2, auto_queue_radio=True, radio_settle_sec=0.0)
        next_res = ex.execute(parse_command("다음 곡"))
        self.assertTrue(next_res.ok)
        self.assertEqual(lms2.cur_idx, 1)
        self.assertEqual(lms2.queue[lms2.cur_idx]["url"], "tidal://22222222.flc")


class TestGeminiOptimizationAndTelemetry(unittest.TestCase):
    def test_gemini_quota_tracker_rpm_and_daily_limits(self) -> None:
        from voice_control.cloud_stt_intent import GeminiQuotaTracker
        tracker = GeminiQuotaTracker(daily_limit=3, max_rpm=2)
        # 1st call
        can, _ = tracker.can_call()
        self.assertTrue(can)
        tracker.record_call()

        # 2nd call (at max_rpm=2)
        can, _ = tracker.can_call()
        self.assertTrue(can)
        tracker.record_call()

        # 3rd call in same minute -> blocked by RPM
        can, reason = tracker.can_call()
        self.assertFalse(can)
        self.assertIn("rpm_limit_exceeded", reason)

        # Clear minute calls to test daily limit
        tracker._minute_calls.clear()
        can, _ = tracker.can_call()
        self.assertTrue(can)
        tracker.record_call()  # 3rd daily call

        # 4th daily call -> blocked by daily limit (3/3)
        can, reason = tracker.can_call()
        self.assertFalse(can)
        self.assertIn("daily_limit_exceeded", reason)

        stats = tracker.stats()
        self.assertEqual(stats["daily_calls"], 3)
        self.assertEqual(stats["daily_remaining"], 0)
        self.assertGreaterEqual(stats["rate_limited_count"], 2)

    def test_intent_ttl_cache_dedup_and_eviction(self) -> None:
        from voice_control.cloud_stt_intent import IntentTTLCache
        cache = IntentTTLCache(ttl_sec=1.0, max_entries=2)
        intent = VoiceIntent(action=IntentAction.ANSWER_QA, raw_text="프랑스 수도가 어디야?", answer="파리입니다.")

        cache.put("프랑스 수도가 어디야?", intent)
        # Lookup ignoring wake word
        cached = cache.get("포코야 프랑스 수도가 어디야")
        self.assertIsNotNone(cached)
        self.assertEqual(cached.answer, "파리입니다.")

        # Unknown action is not cached
        cache.put("모르는 말", VoiceIntent(action=IntentAction.UNKNOWN))
        self.assertIsNone(cache.get("모르는 말"))

    def test_is_meaningful_cloud_candidate_filters_ambient_chatter(self) -> None:
        from voice_control.cloud_stt_intent import is_meaningful_cloud_candidate
        # Ambient noise / statements -> False
        self.assertFalse(is_meaningful_cloud_candidate("버지."))
        self.assertFalse(is_meaningful_cloud_candidate("어 그날 안"))
        self.assertFalse(is_meaningful_cloud_candidate("자기전에 안구 이건는."))
        self.assertFalse(is_meaningful_cloud_candidate("저 어렵긴 해요."))

        # Actionable question / music / quiz -> True
        self.assertTrue(is_meaningful_cloud_candidate("포코야 프랑스 수도가 어디야?"))
        self.assertTrue(is_meaningful_cloud_candidate("콜드플레이가 어떤 밴드야?"))
        self.assertTrue(is_meaningful_cloud_candidate("아이유 밤편지"))
        self.assertTrue(is_meaningful_cloud_candidate("영어 퀴즈 내줘"))

    def test_interpret_audio_enforce_wake_word_skips_cloud_and_slm_on_ambient_speech(self) -> None:
        from voice_control.telemetry import VoiceTelemetryLogger
        telemetry = VoiceTelemetryLogger()
        interp = HybridVoiceInterpreter(
            provider="gemini",
            gemini_api_key="test-key",
            local_stt_url="http://127.0.0.1:8766",
            telemetry=telemetry,
        )
        wav = synthesize_tone_wav([(440.0, 0.3)])
        # Ambient speech without wake word
        with patch.object(
            interp,
            "transcribe_local_audio",
            return_value={"ok": True, "text": "자기전에 안구 이건는.", "lang": "ko", "event": "Speech", "is_music_only": False},
        ), patch.object(interp, "_gemini_text_intent") as mock_gemini, patch.object(
            interp, "_local_llm_text_intent"
        ) as mock_slm:
            intent = interp.interpret_audio(wav, enforce_wake_word=True)

        self.assertEqual(intent.action, IntentAction.UNKNOWN)
        self.assertFalse(intent.wake_word_detected)
        self.assertEqual(intent.provider, "sensevoice-local")
        # Neither SLM nor Gemini should have been called!
        mock_slm.assert_not_called()
        mock_gemini.assert_not_called()

    def test_ambient_speech_with_wake_word_is_gated_from_gemini(self) -> None:
        telemetry_mock = MagicMock()
        interp = HybridVoiceInterpreter(
            provider="gemini",
            gemini_api_key="test-key",
            local_stt_url="http://127.0.0.1:8766",
            telemetry=telemetry_mock,
        )
        wav = synthesize_tone_wav([(440.0, 0.3)])
        # Wake word present, but followed by unintelligible ambient fragment
        with patch.object(
            interp,
            "transcribe_local_audio",
            return_value={"ok": True, "text": "포코야 버지.", "lang": "ko", "event": "Speech", "is_music_only": False},
        ), patch.object(interp, "_gemini_text_intent") as mock_gemini, patch.object(
            interp, "_local_llm_text_intent", return_value=None
        ):
            intent = interp.interpret_audio(wav, enforce_wake_word=True)

        self.assertEqual(intent.action, IntentAction.UNKNOWN)
        self.assertTrue(intent.wake_word_detected)
        # Gemini should NOT have been called!
        mock_gemini.assert_not_called()
        self.assertEqual(interp.quota_tracker.stats()["skipped_ambient"], 1)
        telemetry_mock.record.assert_any_call(
            "gemini_skipped_ambient",
            transcript="포코야 버지.",
            reason="no_intent_cues",
        )

    def test_gemini_ttl_cache_hit_avoids_duplicate_api_calls(self) -> None:
        telemetry_mock = MagicMock()
        interp = HybridVoiceInterpreter(
            provider="gemini",
            gemini_api_key="test-key",
            telemetry=telemetry_mock,
        )
        fake_gemini_resp = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps({
                                    "action": "answer_qa",
                                    "answer": "프랑스의 수도는 파리입니다.",
                                    "confidence": 0.95,
                                    "wake_word_detected": True,
                                })
                            }
                        ]
                    }
                }
            ]
        }
        with patch.object(interp, "_http_json", return_value=fake_gemini_resp) as mock_http:
            # 1st call -> HTTP JSON called
            intent1 = interp._gemini_text_intent("프랑스 수도가 어디야?")
            self.assertEqual(intent1.action, IntentAction.ANSWER_QA)
            self.assertEqual(mock_http.call_count, 1)
            self.assertEqual(interp.quota_tracker.stats()["daily_calls"], 1)

            # 2nd call with same query -> Cache HIT (0 HTTP calls)
            intent2 = interp._gemini_text_intent("포코야 프랑스 수도가 어디야")
            self.assertEqual(intent2.action, IntentAction.ANSWER_QA)
            self.assertEqual(intent2.provider, "gemini-cache")
            self.assertEqual(mock_http.call_count, 1)  # Unchanged!
            self.assertEqual(interp.quota_tracker.stats()["cache_hits"], 1)

    def test_gemini_quota_exhaustion_returns_polite_offline_answer(self) -> None:
        interp = HybridVoiceInterpreter(
            provider="gemini",
            gemini_api_key="test-key",
            gemini_daily_limit=1,
        )
        fake_resp = {
            "candidates": [
                {"content": {"parts": [{"text": json.dumps({"action": "answer_qa", "answer": "1번", "confidence": 0.9})}]}}
            ]
        }
        with patch.object(interp, "_http_json", return_value=fake_resp):
            # 1st call succeeds
            res1 = interp._gemini_text_intent("질문 1")
            self.assertEqual(res1.provider, "gemini-text")

            # 2nd call exceeds daily quota -> graceful fallback
            res2 = interp._gemini_text_intent("질문 2")
            self.assertEqual(res2.provider, "gemini-quota-blocked")
            self.assertIn("한도에 도달", res2.answer)


if __name__ == "__main__":
    unittest.main(verbosity=2)
