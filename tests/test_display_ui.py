"""Unit tests for PocoBox fullscreen Display UI, Dashboard JS validity, LMS now_playing fallbacks, and auto-rotation."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "services"))
sys.path.insert(0, str(REPO_ROOT / "services" / "lms_client"))

from lms_client import LMSClient  # noqa: E402
from voice_control.dashboard_ui import DASHBOARD_HTML  # noqa: E402
from voice_control.display_ui import DISPLAY_HTML, DISPLAY_MANIFEST, UI_VERSION  # noqa: E402

_JSC_PATH = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"


def _extract_script(html: str) -> str:
    m = re.search(r"<script>(.*)</script>", html, re.S)
    assert m is not None, "Missing <script> block"
    return m.group(1)


def _check_js_syntax(js_code: str) -> None:
    """Validate JS syntax using jsc or node if available on host."""
    # Always guard against raw newline inside a regex literal (the Python non-raw-string bug!)
    if re.search(r"/[^\n/]*\n[^\n/]*/[gimsuy]*", js_code):
        # Verify specifically for .replace(/\n pattern corruption
        assert ".replace(/\n" not in js_code, "Corrupted newline inside JS regex literal"

    if os.path.exists(_JSC_PATH):
        with tempfile.NamedTemporaryFile(suffix=".js", mode="w", encoding="utf-8", delete=False) as tf:
            tf.write(js_code)
            src_path = tf.name
        try:
            checker = f"new Function(readFile({json.dumps(src_path)})); print('JS_OK');"
            res = subprocess.run([_JSC_PATH, "-e", checker], capture_output=True, text=True, check=False)
            assert res.returncode == 0 and "JS_OK" in res.stdout, (
                f"JavaScriptCore syntax error:\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}"
            )
        finally:
            try:
                os.unlink(src_path)
            except OSError:
                pass


class DisplayUITests(unittest.TestCase):
    def test_display_js_syntax_is_valid(self) -> None:
        js = _extract_script(DISPLAY_HTML)
        self.assertNotIn(".replace(/\n", js)
        _check_js_syntax(js)

    def test_dashboard_js_syntax_is_valid(self) -> None:
        js = _extract_script(DASHBOARD_HTML)
        self.assertNotIn(".replace(/\n", js)
        self.assertIn(r".replace(/\n+/g", js)
        _check_js_syntax(js)

    def test_display_is_fullscreen_without_persistent_bars(self) -> None:
        self.assertEqual(UI_VERSION, "5")
        self.assertIn('viewport-fit=cover', DISPLAY_HTML)
        self.assertIn('apple-mobile-web-app-capable', DISPLAY_HTML)
        self.assertIn('apple-mobile-web-app-status-bar-style', DISPLAY_HTML)
        self.assertIn('/display/manifest.webmanifest', DISPLAY_HTML)
        self.assertIn('requestFullscreen', DISPLAY_HTML)
        # No persistent topbar or footer status bars
        self.assertNotIn('class="topbar"', DISPLAY_HTML)
        self.assertNotIn('class="footer"', DISPLAY_HTML)
        # Supports portrait & landscape orientation
        self.assertIn("@media (orientation: landscape)", DISPLAY_HTML)
        self.assertIn("layout-landscape", DISPLAY_HTML)
        self.assertIn("rotateDisplay", DISPLAY_HTML)

    def test_display_manifest_is_valid_fullscreen_pwa(self) -> None:
        manifest = json.loads(DISPLAY_MANIFEST)
        self.assertEqual(manifest["display"], "fullscreen")
        self.assertEqual(manifest["orientation"], "any")
        self.assertEqual(manifest["start_url"], "/display")

    def test_lms_now_playing_falls_back_to_remote_meta_and_current_title(self) -> None:
        client = LMSClient()
        fake_status = {
            "player_name": "pocobox",
            "mode": "play",
            "power": 1,
            "mixer volume": 82,
            "playlist_tracks": 1,
            "time": 42.5,
            "current_title": "Live Radio Fallback Title",
            "playlist_loop": [],
            "remoteMeta": {
                "title": "Remote Track Title",
                "artist": "Remote Artist",
                "album": "Remote Album",
                "coverid": "-999",
                "artwork_url": "/imageproxy/https%3A%2F%2Fexample.com%2Fart.jpg/image.jpg",
                "duration": "245",
            },
        }
        with mock.patch.object(client, "player_status", return_value=fake_status):
            np = client.now_playing("02:70:6f:63:6f:01")
        self.assertEqual(np["title"], "Remote Track Title")
        self.assertEqual(np["artist"], "Remote Artist")
        self.assertEqual(np["album"], "Remote Album")
        self.assertEqual(np["coverid"], "-999")
        self.assertEqual(np["duration"], 245.0)
        self.assertIn("imageproxy", np["artwork_url"])

        # Fallback to current_title when both playlist_loop and remoteMeta lack title
        fake_status_minimal = {
            "player_name": "pocobox",
            "mode": "play",
            "power": 1,
            "mixer volume": 50,
            "current_title": "Stream Name",
            "playlist_loop": [],
        }
        with mock.patch.object(client, "player_status", return_value=fake_status_minimal):
            np2 = client.now_playing("02:70:6f:63:6f:01")
        self.assertEqual(np2["title"], "Stream Name")

    def test_autorotate_orientation_parsing_and_mapping(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "pocobox_autorotate",
            str(REPO_ROOT / "infra" / "display" / "pocobox-autorotate.py"),
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        self.assertEqual(
            mod.parse_orientation_line("=== Has accelerometer (orientation: normal, tilt: vertical)"),
            "normal",
        )
        self.assertEqual(
            mod.parse_orientation_line("    Accelerometer orientation changed: left-up"),
            "left-up",
        )
        self.assertEqual(
            mod.parse_orientation_line("    Accelerometer orientation changed: right-up"),
            "right-up",
        )
        self.assertIsNone(mod.parse_orientation_line("    Tilt changed: face-down"))

        self.assertEqual(mod.map_orientation("normal"), "normal")
        self.assertEqual(mod.map_orientation("left-up"), "90")
        self.assertEqual(mod.map_orientation("bottom-up"), "180")
        self.assertEqual(mod.map_orientation("right-up"), "270")
        self.assertIsNone(mod.map_orientation("undefined"))


if __name__ == "__main__":
    unittest.main()
