#!/usr/bin/env python3
"""Live E2E verification suite against a running PocoBox (LMS :9000 + Voice Daemon :8765).

Run with:
    POCOBOX_LIVE=1 POCOBOX_HOST=pocobox.local python3 -m unittest tests/test_live_pocobox_e2e.py -v
"""

from __future__ import annotations

import json
import os
import unittest
import urllib.request

LIVE_ENABLED = os.environ.get("POCOBOX_LIVE", "0") == "1"
HOST = os.environ.get("POCOBOX_HOST", "pocobox.local")
VOICE_URL = f"http://{HOST}:8765"


def _http_json(url: str, payload: dict | None = None, timeout: float = 15.0) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


@unittest.skipUnless(LIVE_ENABLED, "Set POCOBOX_LIVE=1 to run live E2E tests against pocobox")
class TestLivePocoBoxE2E(unittest.TestCase):
    def test_health_and_lms_connected(self) -> None:
        code, health = _http_json(f"{VOICE_URL}/health")
        self.assertEqual(code, 200)
        self.assertTrue(health["ok"])
        self.assertTrue(health["lms_connected"])
        names = [p["name"] for p in health["players"] if p.get("connected")]
        self.assertIn("pocobox", names)

    def test_live_tidal_voice_search_dry_run_suite(self) -> None:
        queries = [
            ("아이유 밤편지 틀어줘", "play_search"),
            ("콜드플레이 픽스 유 틀어줘", "play_search"),
            ("뉴진스 하입보이 재생해줘", "play_search"),
            ("잔나비 주저하는 연인들을 위해 틀어줘", "play_search"),
            ("퀸 보헤미안 랩소디 틀어줘", "play_search"),
            ("재즈 틀어줘", "play_search"),
            ("마이믹스 틀어줘", "play_mix"),
            ("즐겨찾기 틀어줘", "play_favorite"),
            ("지금 나오는 노래 뭐야?", "now_playing"),
            ("볼륨 50으로 해줘", "volume_set"),
            ("다음 곡", "next"),
            ("멈춰", "pause"),
        ]
        for text, expected_action in queries:
            with self.subTest(command=text):
                code, res = _http_json(
                    f"{VOICE_URL}/voice/text",
                    {"text": text, "dry_run": True, "use_cloud": False},
                )
                self.assertEqual(code, 200, f"Failed for '{text}': {res}")
                self.assertTrue(res["ok"], f"Not ok for '{text}': {res}")
                self.assertEqual(res["action"], expected_action)


if __name__ == "__main__":
    unittest.main(verbosity=2)
