#!/usr/bin/env python3
"""Data-driven accuracy & regression test suite for PocoBox Korean/English voice commands."""

from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "services"))

from voice_control.rule_parser import parse_command  # noqa: E402

DATASET_PATH = Path(__file__).resolve().parent / "dataset" / "voice_commands_ko_en.json"


class TestVoiceIntentDataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))

    def test_dataset_not_empty(self) -> None:
        self.assertGreaterEqual(len(self.cases), 60)

    def test_all_voice_command_cases(self) -> None:
        failures: list[str] = []
        t0 = time.monotonic()

        for case in self.cases:
            cid = case["id"]
            text = case["text"]
            intent = parse_command(text)

            if intent.action.value != case["expected_action"]:
                failures.append(
                    f"[{cid}] '{text}': expected action={case['expected_action']} got={intent.action.value} (query='{intent.query}')"
                )
                continue

            if "expected_query_contains" in case:
                needle = case["expected_query_contains"].lower()
                if needle not in intent.query.lower():
                    failures.append(
                        f"[{cid}] '{text}': expected '{needle}' in query='{intent.query}' (all={intent.all_queries()})"
                    )

            if "expected_fallback_contains" in case:
                needle = case["expected_fallback_contains"].lower()
                all_q = [q.lower() for q in intent.all_queries()]
                if not any(needle in q for q in all_q):
                    failures.append(
                        f"[{cid}] '{text}': expected '{needle}' in all_queries={intent.all_queries()}"
                    )

            if "expected_search_type" in case:
                if intent.search_type != case["expected_search_type"]:
                    failures.append(
                        f"[{cid}] '{text}': expected search_type={case['expected_search_type']} got={intent.search_type}"
                    )

            if "expected_volume" in case:
                if intent.volume != case["expected_volume"]:
                    failures.append(
                        f"[{cid}] '{text}': expected volume={case['expected_volume']} got={intent.volume}"
                    )

            if "expected_delta" in case:
                if intent.delta != case["expected_delta"]:
                    failures.append(
                        f"[{cid}] '{text}': expected delta={case['expected_delta']} got={intent.delta}"
                    )

            if "expected_wake" in case:
                if intent.wake_word_detected != case["expected_wake"]:
                    failures.append(
                        f"[{cid}] '{text}': expected wake_word_detected={case['expected_wake']} got={intent.wake_word_detected}"
                    )

        elapsed_ms = (time.monotonic() - t0) * 1000.0
        avg_ms = elapsed_ms / len(self.cases)
        # Ensure rule parser stays ultra-fast (< 5ms/command even on slow CPUs)
        self.assertLess(avg_ms, 5.0, f"Rule parser too slow: {avg_ms:.2f}ms/case")
        self.assertEqual(
            failures,
            [],
            f"{len(failures)}/{len(self.cases)} dataset cases failed:\n" + "\n".join(failures),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
