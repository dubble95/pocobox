"""Unit tests for PocoBox fullscreen Display UI, Dashboard JS validity, LMS now_playing fallbacks, and auto-rotation."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import struct
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
        self.assertEqual(UI_VERSION, "14")
        self.assertIn('viewport-fit=cover', DISPLAY_HTML)
        self.assertIn('apple-mobile-web-app-capable', DISPLAY_HTML)
        self.assertIn('apple-mobile-web-app-status-bar-style', DISPLAY_HTML)
        self.assertIn('/display/manifest.webmanifest', DISPLAY_HTML)
        self.assertIn('requestFullscreen', DISPLAY_HTML)
        # Cover art tap zones and swipe gestures for next/prev/toggle with visual feedback
        self.assertIn("handleCoverClick", DISPLAY_HTML)
        self.assertIn("handleTouchStart", DISPLAY_HTML)
        self.assertIn("handleTouchEnd", DISPLAY_HTML)
        self.assertIn("artTapBadge", DISPLAY_HTML)
        # Touch ripple feedback and tactile button press animations
        self.assertIn("touch-ripple", DISPLAY_HTML)
        self.assertIn("touchRippleAnim", DISPLAY_HTML)
        self.assertIn("spawnTouchRipple", DISPLAY_HTML)
        self.assertIn("bindTouchFeedback", DISPLAY_HTML)
        # Mic button has expanded right-edge touch area and instant touch response
        self.assertIn(".btn.mic::after", DISPLAY_HTML)
        self.assertIn("triggerMic", DISPLAY_HTML)
        self.assertIn("micTriggerBusy", DISPLAY_HTML)
        # No persistent topbar or footer status bars
        self.assertNotIn('class="topbar"', DISPLAY_HTML)
        self.assertNotIn('class="footer"', DISPLAY_HTML)
        # Supports portrait & landscape orientation
        self.assertIn("@media (orientation: landscape)", DISPLAY_HTML)
        self.assertIn("layout-landscape", DISPLAY_HTML)
        self.assertIn("rotateDisplay", DISPLAY_HTML)

    def test_kiosk_touch_and_udev_rules(self) -> None:
        kiosk_sh = (REPO_ROOT / "infra" / "display" / "pocobox-kiosk-start.sh").read_text(encoding="utf-8")
        self.assertIn('user_pref("dom.w3c_touch_events.enabled", 1);', kiosk_sh)
        self.assertIn('libpocobox_cage_touch.so', kiosk_sh)

        cage_touch_c = REPO_ROOT / "infra" / "display" / "pocobox_cage_touch.c"
        self.assertTrue(cage_touch_c.exists())
        self.assertIn("wlr_cursor_map_input_to_output", cage_touch_c.read_text(encoding="utf-8"))

        build_sh = REPO_ROOT / "infra" / "display" / "build-cage-touch.sh"
        self.assertTrue(build_sh.exists())

        rules_path = REPO_ROOT / "infra" / "display" / "95-pocobox-touchscreen.rules"
        self.assertTrue(rules_path.exists())
        rules = rules_path.read_text(encoding="utf-8")
        self.assertIn('ENV{WL_OUTPUT}="DSI-1"', rules)
        self.assertIn("generic ft5x06", rules)

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

        # query_current_orientation falls back gracefully when GI/Gio not on test host
        res = mod.query_current_orientation()
        self.assertTrue(res is None or isinstance(res, str))

    def test_autorotate_restores_persisted_rotation_lock(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "pocobox_autorotate",
            str(REPO_ROOT / "infra" / "display" / "pocobox-autorotate.py"),
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            persist = Path(tmp) / "display-rotation"
            lock_file = run_dir / "pocobox-rotation-lock"

            # No persisted lock -> nothing restored
            with mock.patch.dict(os.environ, {"DISPLAY_ROTATION_PERSIST": str(persist)}):
                mod.restore_persistent_rotation(lock_file)
            self.assertFalse(lock_file.exists())

            # Valid persisted lock -> restored into runtime lock
            persist.write_text("270", encoding="utf-8")
            with mock.patch.dict(os.environ, {"DISPLAY_ROTATION_PERSIST": str(persist)}):
                mod.restore_persistent_rotation(lock_file)
            self.assertEqual(lock_file.read_text(encoding="utf-8"), "270")

            # Existing runtime lock is never overwritten
            lock_file.write_text("90", encoding="utf-8")
            with mock.patch.dict(os.environ, {"DISPLAY_ROTATION_PERSIST": str(persist)}):
                mod.restore_persistent_rotation(lock_file)
            self.assertEqual(lock_file.read_text(encoding="utf-8"), "90")

            # Invalid persisted values are ignored
            lock_file.unlink()
            persist.write_text("bogus", encoding="utf-8")
            with mock.patch.dict(os.environ, {"DISPLAY_ROTATION_PERSIST": str(persist)}):
                mod.restore_persistent_rotation(lock_file)
            self.assertFalse(lock_file.exists())


def _load_backlight_module():
    spec = importlib.util.spec_from_file_location(
        "pocobox_backlight",
        str(REPO_ROOT / "infra" / "display" / "pocobox-backlight.py"),
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pocobox_backlight"] = mod
    spec.loader.exec_module(mod)
    return mod


class DisplayBacklightTests(unittest.TestCase):
    def test_power_policy_auto_turns_off_when_not_playing(self) -> None:
        mod = _load_backlight_module()
        policy = mod.DisplayPowerPolicy(power_mode="auto", idle_off_sec=10.0, touch_wake_sec=15.0)
        idle = mod.ActivitySnapshot(music_playing=False, keep_awake=False, source="voice_api")
        playing = mod.ActivitySnapshot(music_playing=True, keep_awake=True, source="voice_api")
        voice = mod.ActivitySnapshot(music_playing=False, keep_awake=True, source="voice_api")

        # On startup when idle, turn off immediately
        self.assertEqual(policy.evaluate(idle, 100.0), (False, "idle_off"))

        # Music starts -> turns ON immediately
        self.assertEqual(policy.evaluate(playing, 105.0), (True, "music_playing"))

        # Music pauses / track transition -> stays ON during idle_off_sec grace
        self.assertEqual(policy.evaluate(idle, 110.0), (True, "idle_grace"))
        self.assertEqual(policy.evaluate(idle, 114.9), (True, "idle_grace"))

        # Grace expires -> turns OFF
        self.assertEqual(policy.evaluate(idle, 115.0), (False, "idle_off"))

        # Voice / quiz interaction while idle -> turns ON, then OFF when done
        self.assertEqual(policy.evaluate(voice, 120.0), (True, "voice_or_quiz_active"))
        self.assertEqual(policy.evaluate(idle, 121.0), (False, "idle_off"))

        # Touchscreen or button press wakes display for touch_wake_sec
        policy.on_input_event(200.0)
        self.assertEqual(policy.evaluate(idle, 200.5), (True, "input_wake"))
        self.assertEqual(policy.evaluate(idle, 214.9), (True, "input_wake"))
        self.assertEqual(policy.evaluate(idle, 215.0), (False, "idle_off"))

    def test_power_policy_always_on_mode(self) -> None:
        mod = _load_backlight_module()
        policy = mod.DisplayPowerPolicy(power_mode="always_on", idle_off_sec=10.0)
        idle = mod.ActivitySnapshot(music_playing=False, keep_awake=False, source="voice_api")
        self.assertEqual(policy.evaluate(idle, 0.0), (True, "always_on"))

    def test_parse_display_state_payload(self) -> None:
        mod = _load_backlight_module()
        snap = mod.parse_display_state_payload({
            "now_playing": {"mode": "pause"},
            "display": {"music_playing": False, "keep_awake": True},
        })
        self.assertFalse(snap.music_playing)
        self.assertTrue(snap.keep_awake)
        self.assertEqual(snap.source, "voice_api")

        snap_legacy = mod.parse_display_state_payload({
            "now_playing": {"mode": "play"},
            "quiz": {"active": False},
        })
        self.assertTrue(snap_legacy.music_playing)
        self.assertTrue(snap_legacy.keep_awake)
        self.assertEqual(snap_legacy.source, "voice_api_legacy")

    def test_sysfs_backlight_on_and_off(self) -> None:
        mod = _load_backlight_module()
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "max_brightness").write_text("4095\n", encoding="utf-8")
            (base / "brightness").write_text("1800\n", encoding="utf-8")
            (base / "bl_power").write_text("0\n", encoding="utf-8")

            bl = mod.SysfsBacklight(base)
            self.assertEqual(bl.max_brightness, 4095)

            self.assertTrue(bl.set_off())
            self.assertEqual((base / "brightness").read_text(encoding="utf-8").strip(), "0")
            self.assertEqual((base / "bl_power").read_text(encoding="utf-8").strip(), "4")

            self.assertTrue(bl.set_on(1800))
            self.assertEqual((base / "bl_power").read_text(encoding="utf-8").strip(), "0")
            self.assertEqual((base / "brightness").read_text(encoding="utf-8").strip(), "1800")

            # Clamps above max_brightness
            self.assertTrue(bl.set_on(9999))
            self.assertEqual((base / "brightness").read_text(encoding="utf-8").strip(), "4095")

    def test_input_device_matching_and_event_filtering(self) -> None:
        mod = _load_backlight_module()
        self.assertTrue(mod.is_wake_input_device_name("generic ft5x06 (d0)"))
        self.assertTrue(mod.is_wake_input_device_name("pm8941_pwrkey"))
        self.assertTrue(mod.is_wake_input_device_name("gpio-keys"))
        self.assertFalse(mod.is_wake_input_device_name("Burr-Brown from TI USB Audio DAC"))
        self.assertFalse(mod.is_wake_input_device_name("Xiaomi Poco F1 Headset Jack"))
        self.assertFalse(mod.is_wake_input_device_name("spmi_haptics"))

        ev_key_press = struct.pack("qqHHi", 0, 0, 1, 116, 1)
        ev_key_release = struct.pack("qqHHi", 0, 0, 1, 116, 0)
        ev_touch_down = struct.pack("qqHHi", 0, 0, 3, 57, 42)
        ev_touch_up = struct.pack("qqHHi", 0, 0, 3, 57, -1)
        ev_syn = struct.pack("qqHHi", 0, 0, 0, 0, 0)

        self.assertTrue(mod.has_wake_trigger_in_input_bytes(ev_key_press))
        self.assertFalse(mod.has_wake_trigger_in_input_bytes(ev_key_release))
        self.assertTrue(mod.has_wake_trigger_in_input_bytes(ev_touch_down))
        self.assertFalse(mod.has_wake_trigger_in_input_bytes(ev_touch_up + ev_syn))

    def test_voice_controller_display_state_reports_keep_awake(self) -> None:
        from voice_control.daemon import VoiceController

        with mock.patch.dict(os.environ, {"CONTINUOUS_LISTEN": "0", "LOCAL_STT_URL": ""}, clear=False):
            with mock.patch("voice_control.daemon.get_internal_speaker_volume", return_value=85):
                ctrl = VoiceController()

        ctrl.lms = mock.MagicMock()
        ctrl.lms.resolve_player_id.return_value = "02:70:6f:63:6f:01"
        ctrl.lms.now_playing.return_value = {"mode": "pause", "title": "Test"}

        # Idle paused state -> keep_awake False
        st = ctrl.display_state()
        self.assertFalse(st["display"]["music_playing"])
        self.assertFalse(st["display"]["keep_awake"])

        # Playing music -> music_playing & keep_awake True
        ctrl.lms.now_playing.return_value = {"mode": "play", "title": "Test"}
        st_play = ctrl.display_state()
        self.assertTrue(st_play["display"]["music_playing"])
        self.assertTrue(st_play["display"]["keep_awake"])

        # Paused + wake word detected -> keep_awake True; timeout clears it
        ctrl.lms.now_playing.return_value = {"mode": "pause", "title": "Test"}
        with mock.patch.object(ctrl, "_play_chime"):
            ctrl.handle_wake_detected("poco")
        self.assertTrue(ctrl.display_state()["display"]["keep_awake"])
        ctrl.handle_wake_utterance(None, "poco")
        self.assertFalse(ctrl.display_state()["display"]["keep_awake"])

        # Paused + TTS speaking -> keep_awake True
        ctrl._tts_speaking = True
        self.assertTrue(ctrl.display_state()["display"]["voice_active"])
        self.assertTrue(ctrl.display_state()["display"]["keep_awake"])
        ctrl._tts_speaking = False
        self.assertFalse(ctrl.display_state()["display"]["keep_awake"])

        # Pause command caps any long wake timer to <= 3s
        ctrl._wake_display(20.0)
        ctrl._update_display_wake_for_action("pause")
        self.assertLessEqual(ctrl.display_state()["display"]["wake_remaining_sec"], 3.1)

    def test_display_control_screen_off_clears_wake_timer(self) -> None:
        from voice_control.daemon import VoiceController

        with mock.patch.dict(os.environ, {"CONTINUOUS_LISTEN": "0", "LOCAL_STT_URL": ""}, clear=False):
            with mock.patch("voice_control.daemon.get_internal_speaker_volume", return_value=85):
                ctrl = VoiceController()

        ctrl.lms = mock.MagicMock()
        ctrl.lms.resolve_player_id.return_value = "02:70:6f:63:6f:01"
        ctrl.lms.now_playing.return_value = {"mode": "pause", "title": "Test"}

        # Idle paused state starts with keep_awake False
        self.assertFalse(ctrl.display_state()["display"]["keep_awake"])

        # A recent interaction arms a long wake timer -> keep_awake True
        ctrl._wake_display(60.0)
        self.assertTrue(ctrl.display_state()["display"]["keep_awake"])
        self.assertGreater(ctrl.display_state()["display"]["wake_remaining_sec"], 0.0)

        # screen_off clears the wake timer so the backlight can power off the screen
        res = ctrl.display_control("screen_off")
        self.assertTrue(res["ok"])
        self.assertEqual(res["action"], "screen_off")
        self.assertFalse(ctrl.display_state()["display"]["keep_awake"])
        self.assertEqual(ctrl.display_state()["display"]["wake_remaining_sec"], 0.0)

        # Unknown action still errors
        res_bad = ctrl.display_control("not_a_real_action")
        self.assertFalse(res_bad["ok"])
        self.assertIn("Unknown control action", res_bad["error"])


    def test_rotate_display_persists_lock_across_reboots(self) -> None:
        """UI rotation must persist to a non-tmpfs lock so it survives reboots."""
        from voice_control.daemon import VoiceController

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "wayland-0").write_bytes(b"")  # fake Wayland socket
            persist = Path(tmp) / "display-rotation"

            def fake_wlr(*args, **kwargs):
                m = mock.MagicMock()
                m.returncode = 0
                m.stdout = json.dumps([{"name": "DSI-1", "transform": "normal"}])
                m.stderr = ""
                return m

            with mock.patch.dict(
                os.environ,
                {"XDG_RUNTIME_DIR": str(run_dir), "DISPLAY_ROTATION_PERSIST": str(persist)},
            ), mock.patch("voice_control.daemon.shutil.which", return_value="/usr/bin/wlr-randr"),                 mock.patch("voice_control.daemon.subprocess.run", side_effect=fake_wlr):
                ctrl = VoiceController()
                res = ctrl.rotate_display("270")

            self.assertTrue(res["ok"], res)
            self.assertEqual(res["transform"], "270")
            # Runtime lock written for immediate autorotate pickup...
            self.assertEqual((run_dir / "pocobox-rotation-lock").read_text(encoding="utf-8"), "270")
            # ...and persisted lock written for reboot restoration
            self.assertEqual(persist.read_text(encoding="utf-8"), "270")

            # auto mode clears both locks
            with mock.patch.dict(
                os.environ,
                {"XDG_RUNTIME_DIR": str(run_dir), "DISPLAY_ROTATION_PERSIST": str(persist)},
            ):
                res_auto = ctrl.rotate_display("auto")
            self.assertTrue(res_auto["ok"])
            self.assertFalse((run_dir / "pocobox-rotation-lock").exists())
            self.assertFalse(persist.exists())


if __name__ == "__main__":
    unittest.main()
