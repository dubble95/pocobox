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

from lms_client import LMSClient, extract_tidal_track_id  # noqa: E402
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
            return {"loop_loop": []}
        if command[0] == "tidal" and command[1] == "playlist" and command[2] == "play":
            self.played_items.append(command[3])
            self.mode = "play"
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
