"""Regression tests for local Korean ASR correction learning."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "services"))

from voice_control.adaptive_learning import KoreanAdaptiveLearner  # noqa: E402
from voice_control.cloud_stt_intent import HybridVoiceInterpreter, IntentAction  # noqa: E402
from voice_control.context import ConversationContext  # noqa: E402

DATASET_PATH = Path(__file__).resolve().parent / "dataset" / "korean_asr_corrections.json"


def _make_context_with_previous(display_query: str) -> ConversationContext:
    ctx = ConversationContext(expiry_sec=45.0)
    ctx.record_turn(
        role="user",
        text=f"{display_query} 틀어줘",
        action=IntentAction.PLAY_SEARCH.value,
        query=display_query,
        display_query=display_query,
    )
    ctx.record_turn(
        role="assistant",
        text=f"{display_query}을(를) 재생합니다.",
        track_title=display_query,
    )
    return ctx


class TestKoreanAdaptiveLearner(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmpdir.name) / "learning.json")
        self.learner = KoreanAdaptiveLearner(path=self.path)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_pending_before_second_confirmation(self) -> None:
        self.assertEqual(self.learner.record_correction("쇼핑 녹턴", "쇼팽 녹턴"), "pending")
        self.assertEqual(self.learner.record_correction("쇼핑 녹턴", "쇼팽 녹턴"), "active")
        self.assertEqual(self.learner.apply("쇼핑 녹턴 틀어줘"), "쇼팽 녹턴 틀어줘")

    def test_different_song_change_is_not_learned(self) -> None:
        self.assertEqual(self.learner.record_correction("아이유 좋은날", "아이유 밤편지"), "rejected")
        self.assertEqual(self.learner.apply("아이유 좋은날 틀어줘"), "아이유 좋은날 틀어줘")
        self.assertEqual(self.learner.stats()["active_rules"], 0)

    def test_empty_or_identical_phrases_are_rejected(self) -> None:
        self.assertEqual(self.learner.record_correction("", "쇼팽 녹턴"), "rejected")
        self.assertEqual(self.learner.record_correction("쇼팽 녹턴", "쇼팽 녹턴"), "rejected")
        self.assertEqual(self.learner.apply("쇼팽 녹턴"), "쇼팽 녹턴")

    def test_conflicting_target_restarts_confirmation(self) -> None:
        self.assertEqual(self.learner.record_correction("쇼핑 녹턴", "쇼팽 녹턴"), "pending")
        self.assertEqual(self.learner.record_correction("쇼핑 녹턴", "쇼팡 녹턴"), "pending")
        self.assertEqual(self.learner.record_correction("쇼핑 녹턴", "쇼팡 녹턴"), "active")
        self.assertEqual(self.learner.apply("쇼핑 녹턴 틀어줘"), "쇼팡 녹턴 틀어줘")

    def test_store_is_bounded(self) -> None:
        small = KoreanAdaptiveLearner(path=self.path, max_entries=1)
        self.assertEqual(small.record_correction("쇼핑 녹턴", "쇼팽 녹턴"), "pending")
        self.assertEqual(small.record_correction("쇼핑 녹턴", "쇼팽 녹턴"), "active")
        self.assertEqual(small.record_correction("차이콥스키", "차이코프스키"), "pending")
        self.assertEqual(small.record_correction("차이콥스키", "차이코프스키"), "active")
        self.assertEqual(small.apply("쇼핑 녹턴 틀어줘"), "쇼핑 녹턴 틀어줘")
        self.assertEqual(small.apply("차이콥스키 틀어줘"), "차이코프스키 틀어줘")

    def test_persistence_across_instances(self) -> None:
        self.assertEqual(self.learner.record_correction("쇼핑 녹턴", "쇼팽 녹턴"), "pending")
        self.assertEqual(self.learner.record_correction("쇼핑 녹턴", "쇼팽 녹턴"), "active")
        reloaded = KoreanAdaptiveLearner(path=self.path)
        self.assertEqual(reloaded.apply("쇼핑 녹턴 틀어줘"), "쇼팽 녹턴 틀어줘")
        self.assertEqual(reloaded.stats()["active_rules"], 1)

    def test_corrupted_store_is_ignored(self) -> None:
        Path(self.path).write_text("{not json", encoding="utf-8")
        ignored = KoreanAdaptiveLearner(path=self.path)
        self.assertEqual(ignored.apply("쇼핑 녹턴 틀어줘"), "쇼핑 녹턴 틀어줘")
        Path(self.path).write_text("[]", encoding="utf-8")
        non_object = KoreanAdaptiveLearner(path=self.path)
        self.assertEqual(non_object.apply("쇼핑 녹턴 틀어줘"), "쇼핑 녹턴 틀어줘")

    def test_forget_removes_rule(self) -> None:
        self.assertEqual(self.learner.record_correction("쇼핑 녹턴", "쇼팽 녹턴"), "pending")
        self.assertEqual(self.learner.record_correction("쇼핑 녹턴", "쇼팽 녹턴"), "active")
        self.assertTrue(self.learner.forget("쇼핑 녹턴"))
        self.assertEqual(self.learner.apply("쇼핑 녹턴 틀어줘"), "쇼핑 녹턴 틀어줘")
        self.assertFalse(self.learner.forget("쇼핑 녹턴"))


class TestAdaptiveLearningInVoicePipeline(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmpdir.name) / "learning.json")
        self.learner = KoreanAdaptiveLearner(path=self.path)
        self.interp = HybridVoiceInterpreter(
            local_stt_url="http://127.0.0.1:8766",
            adaptive_learner=self.learner,
        )

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_local_stt_applies_learned_correction(self) -> None:
        self.learner.record_correction("쇼핑 녹턴", "쇼팽 녹턴")
        self.learner.record_correction("쇼핑 녹턴", "쇼팽 녹턴")

        with patch.object(self.interp, "transcribe_local_audio", return_value={"text": "쇼핑 녹턴 틀어줘", "is_music_only": False}):
            intent = self.interp.interpret_audio(b"fake-wav")

        self.assertEqual(intent.raw_text, "쇼핑 녹턴 틀어줘")
        self.assertTrue(any("Chopin Nocturne" in q for q in intent.all_queries()))
        self.assertIn("adaptive", intent.provider)

    def test_context_correction_learns_after_repeated_confirmation(self) -> None:
        ctx = _make_context_with_previous("쇼핑 녹턴")
        intent = self.interp.interpret_text("그거 말고 쇼팽 녹턴", context=ctx)
        self.assertEqual(intent.provider, "context-correction-slot-inheritance")
        self.assertEqual(self.learner.stats()["pending_rules"], 1)

        ctx2 = _make_context_with_previous("쇼핑 녹턴")
        intent2 = self.interp.interpret_text("그거 말고 쇼팽 녹턴", context=ctx2)
        self.assertEqual(self.learner.stats()["active_rules"], 1)

        intent3 = self.interp.interpret_text("쇼핑 녹턴 틀어줘")
        self.assertEqual(intent3.raw_text, "쇼핑 녹턴 틀어줘")
        self.assertTrue(any("Chopin Nocturne" in q for q in intent3.all_queries()))
        self.assertIn("adaptive", intent3.provider)

    def test_dataset_driven_corrections(self) -> None:
        cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(cases), 6)
        for case in cases:
            source = case["source"]
            target = case["target"]
            self.assertEqual(self.learner.record_correction(source, target), "pending")
            self.assertEqual(self.learner.record_correction(source, target), "active")
            intent = self.interp.interpret_text(source)
            self.assertEqual(intent.raw_text, source)
            self.assertTrue(
                any(case["expected_query_contains"].lower() in q.lower() for q in intent.all_queries()),
                f"{case['id']}: {source} -> {intent.all_queries()}",
            )

    def test_health_stats_are_json_serializable(self) -> None:
        self.learner.record_correction("쇼핑 녹턴", "쇼팽 녹턴")
        stats = self.interp.learning_stats()
        json.dumps(stats)
        self.assertGreaterEqual(stats["active_rules"], 0)


class TestAdaptiveLearningDisabled(unittest.TestCase):
    def test_disabled_learner_does_not_change_transcripts(self) -> None:
        learner = KoreanAdaptiveLearner(path="/nonexistent/learning.json", enabled=False)
        interp = HybridVoiceInterpreter(adaptive_learner=learner)
        self.assertEqual(interp.interpret_text("쇼핑 녹턴 틀어줘").raw_text, "쇼핑 녹턴 틀어줘")
        self.assertEqual(learner.record_correction("쇼핑 녹턴", "쇼팽 녹턴"), "disabled")


if __name__ == "__main__":
    unittest.main(verbosity=2)
