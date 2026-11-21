"""Unit tests for Korean Jamo phonetic matching and conversation context slot inheritance."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "services"))

from voice_control.aliases import expand_tidal_queries
from voice_control.context import ConversationContext
from voice_control.intent_schema import IntentAction
from voice_control.jamo_util import (
    decompose_hangul,
    find_best_jamo_match,
    jamo_phonetic_similarity,
    match_alias_with_jamo,
)
from voice_control.rule_parser import parse_command


class TestJamoPhoneticMatching(unittest.TestCase):
    """Test Korean Jamo decomposition and phonetic distance matching."""

    def test_decompose_hangul(self) -> None:
        self.assertEqual(decompose_hangul("아이유"), "ㅇㅏㅇㅣㅇㅠ")
        self.assertEqual(decompose_hangul("비발디"), "ㅂㅣㅂㅏㄹㄷㅣ")
        self.assertEqual(decompose_hangul("차이코프스키"), "ㅊㅏㅇㅣㅋㅗㅍㅡㅅㅡㅋㅣ")
        self.assertEqual(decompose_hangul("IU 123"), "iu 123")
        self.assertEqual(decompose_hangul(""), "")

    def test_jamo_phonetic_similarity_handles_asr_distortions(self) -> None:
        # Classical composers with typical speech recognition distortions
        self.assertGreater(jamo_phonetic_similarity("차이코프스키", "잘코스키"), 0.65)
        self.assertGreater(jamo_phonetic_similarity("비발디", "이발디"), 0.80)
        self.assertGreater(jamo_phonetic_similarity("라흐마니노프", "라흐만이 너프"), 0.85)
        self.assertGreater(jamo_phonetic_similarity("쇼팽 녹턴", "쇼핑 녹턴"), 0.88)
        self.assertGreater(jamo_phonetic_similarity("뉴진스", "유진스"), 0.90)

    def test_find_best_jamo_match(self) -> None:
        candidates = ["아이유", "차이코프스키", "비발디", "뉴진스", "쇼팽"]
        match, score = find_best_jamo_match("잘코스키", candidates, threshold=0.65)
        self.assertEqual(match, "차이코프스키")
        self.assertGreater(score, 0.65)

        match2, score2 = find_best_jamo_match("이발디", candidates, threshold=0.70)
        self.assertEqual(match2, "비발디")

    def test_match_alias_with_jamo(self) -> None:
        alias_dict = {
            "차이코프스키": ["Tchaikovsky"],
            "비발디": ["Vivaldi"],
            "아이유": ["IU"],
        }
        # Exact match
        k, v, s = match_alias_with_jamo("아이유", alias_dict)
        self.assertEqual(k, "아이유")
        self.assertEqual(v, ["IU"])
        self.assertEqual(s, 1.0)

        # Fuzzy phonetic match
        k_f, v_f, s_f = match_alias_with_jamo("잘코스키", alias_dict, threshold=0.65)
        self.assertEqual(k_f, "차이코프스키")
        self.assertEqual(v_f, ["Tchaikovsky"])
        self.assertGreater(s_f, 0.65)

    def test_expand_tidal_queries_fuzzy_jamo_fallback(self) -> None:
        # "잘코스키" should fuzzy-match "차이코프스키" and emit "Tchaikovsky"
        expanded = expand_tidal_queries("잘코스키")
        self.assertTrue(any("Tchaikovsky" in c for c in expanded))


class TestConversationContextSlotInheritance(unittest.TestCase):
    """Test multi-turn dialogue state tracking and slot inheritance."""

    def setUp(self) -> None:
        self.ctx = ConversationContext(expiry_sec=45.0)

    def test_music_correction_slot_inheritance(self) -> None:
        # Turn 1: User requested IU music
        self.ctx.record_turn(
            role="assistant",
            text="아이유 음악을 재생합니다.",
            action=IntentAction.PLAY_SEARCH.value,
            query="IU",
            display_query="아이유",
            artist="아이유",
        )

        # Turn 2: User corrects "그거 말고 밤편지"
        intent = parse_command("그거 말고 밤편지", context=self.ctx)
        self.assertEqual(intent.action, IntentAction.PLAY_SEARCH)
        self.assertIn("Through the Night", intent.query)
        self.assertEqual(intent.display_query, "아이유 밤편지")
        self.assertEqual(intent.provider, "context-correction-slot-inheritance")

    def test_another_song_inherits_artist(self) -> None:
        self.ctx.record_turn(
            role="assistant",
            text="아이유의 좋은날을 재생합니다.",
            action=IntentAction.PLAY_SEARCH.value,
            query="IU Good Day",
            artist="아이유",
        )

        intent = parse_command("다른 노래 틀어줘", context=self.ctx)
        self.assertEqual(intent.action, IntentAction.PLAY_SEARCH)
        self.assertEqual(intent.query, "IU")
        self.assertEqual(intent.search_type, "artist")
        self.assertEqual(intent.provider, "context-artist-another-song")

    def test_classical_movement_continuation(self) -> None:
        self.ctx.record_turn(
            role="assistant",
            text="차이코프스키 피아노 협주곡 1번을 재생합니다.",
            action=IntentAction.PLAY_SEARCH.value,
            query="Tchaikovsky Piano Concerto No. 1",
            track_title="차이코프스키 피아노 협주곡 1번",
        )

        intent = parse_command("1악장만 틀어줘", context=self.ctx)
        self.assertEqual(intent.action, IntentAction.PLAY_SEARCH)
        self.assertIn("Movement 1", intent.query)
        self.assertEqual(intent.search_type, "classical")
        self.assertEqual(intent.provider, "context-classical-movement-inheritance")

    def test_weather_followup_inherits_location(self) -> None:
        self.ctx.record_turn(
            role="assistant",
            text="현재 판교 기온은 12도입니다.",
            action=IntentAction.WEATHER.value,
            query="판교",
            location="판교",
        )

        intent = parse_command("내일은?", context=self.ctx)
        self.assertEqual(intent.action, IntentAction.WEATHER)
        self.assertEqual(intent.query, "판교")
        self.assertEqual(intent.provider, "context-weather-inheritance")

        intent2 = parse_command("비 와?", context=self.ctx)
        self.assertEqual(intent2.action, IntentAction.WEATHER)
        self.assertEqual(intent2.query, "판교")

    def test_volume_continuation_inherits_direction_and_delta(self) -> None:
        self.ctx.record_turn(
            role="assistant",
            text="볼륨을 10% 올렸습니다.",
            action=IntentAction.VOLUME_UP.value,
            volume_delta=10,
        )

        intent = parse_command("조금 더", context=self.ctx)
        self.assertEqual(intent.action, IntentAction.VOLUME_UP)
        self.assertEqual(intent.delta, 5)
        self.assertEqual(intent.provider, "context-volume-inheritance")

        intent_big = parse_command("더 크게", context=self.ctx)
        self.assertEqual(intent_big.action, IntentAction.VOLUME_UP)
        self.assertEqual(intent_big.delta, 10)

    def test_switch_music_with_bakwojwo(self) -> None:
        intent_iu = parse_command("아이유로 바꿔줘")
        self.assertEqual(intent_iu.action, IntentAction.PLAY_SEARCH)
        self.assertEqual(intent_iu.query, "IU")

        intent_classic = parse_command("클래식으로 바꿔줘")
        self.assertEqual(intent_classic.action, IntentAction.PLAY_SEARCH)
        self.assertIn("Classical", intent_classic.query)

        intent_night = parse_command("밤편지로 바꿔줘")
        self.assertEqual(intent_night.action, IntentAction.PLAY_SEARCH)
        self.assertIn("Through the Night", intent_night.query)

        # Ensure trailing "로" in title like "솔로" is preserved and expanded
        intent_solo = parse_command("솔로로 바꿔줘")
        self.assertEqual(intent_solo.action, IntentAction.PLAY_SEARCH)
        self.assertEqual(intent_solo.query, "SOLO")

    def test_another_song_with_bakwojwo_and_genre_continuation(self) -> None:
        # Artist continuation
        self.ctx.record_turn(
            role="assistant",
            text="아이유의 좋은날을 재생합니다.",
            action=IntentAction.PLAY_SEARCH.value,
            query="IU Good Day",
            artist="아이유",
        )
        intent_art = parse_command("다른 노래로 바꿔줘", context=self.ctx)
        self.assertEqual(intent_art.action, IntentAction.PLAY_SEARCH)
        self.assertEqual(intent_art.query, "IU")
        self.assertEqual(intent_art.provider, "context-artist-another-song")

        # Genre continuation without artist
        self.ctx.clear_slots()
        self.ctx.record_turn(
            role="assistant",
            text="재즈 음악을 재생합니다.",
            action=IntentAction.PLAY_SEARCH.value,
            query="Jazz",
            display_query="재즈",
            search_type="playlist",
        )
        intent_genre = parse_command("다른 거 틀어줘", context=self.ctx)
        self.assertEqual(intent_genre.action, IntentAction.PLAY_SEARCH)
        self.assertEqual(intent_genre.query, "Jazz")
        self.assertEqual(intent_genre.provider, "context-genre-another-song")

    def test_track_switch_inherits_artist_slot(self) -> None:
        self.ctx.record_turn(
            role="assistant",
            text="아이유 음악을 재생합니다.",
            action=IntentAction.PLAY_SEARCH.value,
            query="IU",
            display_query="아이유",
            artist="아이유",
        )

        # Track under same artist inherits artist prefix
        intent_track = parse_command("좋은 날로 바꿔줘", context=self.ctx)
        self.assertEqual(intent_track.action, IntentAction.PLAY_SEARCH)
        self.assertIn("Good Day", intent_track.query)
        self.assertEqual(intent_track.display_query, "아이유 좋은 날")
        self.assertEqual(intent_track.provider, "rule-context-artist-track")

        # New artist is NOT inherited
        intent_new_art = parse_command("뉴진스로 바꿔줘", context=self.ctx)
        self.assertEqual(intent_new_art.action, IntentAction.PLAY_SEARCH)
        self.assertEqual(intent_new_art.query, "NewJeans")

        # Audio output switch is NOT captured as song
        intent_out = parse_command("소리 dac으로 바꿔줘", context=self.ctx)
        self.assertEqual(intent_out.action, IntentAction.SWITCH_OUTPUT)
        self.assertEqual(intent_out.query, "dac")

    def test_dashboard_ui_contains_learning_elements(self) -> None:
        from voice_control.dashboard_ui import DASHBOARD_HTML
        self.assertIn("statLrnActive", DASHBOARD_HTML)
        self.assertIn("statLrnPending", DASHBOARD_HTML)
        self.assertIn("statLrnRej", DASHBOARD_HTML)
        self.assertIn("statLrnApplied", DASHBOARD_HTML)
        self.assertIn("pollLearningEntries", DASHBOARD_HTML)
        self.assertIn("forgetLearningEntry", DASHBOARD_HTML)

    def test_local_slm_payload_includes_gbnf_grammar(self) -> None:
        from unittest.mock import MagicMock
        from voice_control.cloud_stt_intent import HybridVoiceInterpreter, _LOCAL_SLM_GBNF

        interp = HybridVoiceInterpreter(
            local_llm_url="http://127.0.0.1:8767",
            enable_local_llm=True,
        )
        interp._http_json = MagicMock(return_value={
            "choices": [{"message": {"content": '{"action": "play_search", "query": "아이유"}'}}]
        })

        intent = interp._local_llm_text_intent("아이유 노래 틀어줘")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.action, IntentAction.PLAY_SEARCH)

        # Verify GBNF grammar was passed in payload
        call_args = interp._http_json.call_args
        payload = call_args[0][1]
        self.assertEqual(payload.get("grammar"), _LOCAL_SLM_GBNF)
        self.assertEqual(payload.get("response_format"), {"type": "json_object"})


if __name__ == "__main__":
    unittest.main()
