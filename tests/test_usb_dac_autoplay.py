#!/usr/bin/env python3
"""Unit tests for PocoBox USB DAC hotplug detection and LMS auto-play transitions."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "services" / "lms_client"))
sys.path.insert(0, str(REPO_ROOT / "services" / "usb_dac_autoplay"))

import pocobox_dac_autoplay as dac_mod  # noqa: E402


class TestUsbDacAutoplay(unittest.TestCase):
    def test_find_usb_audio_cards_filters_internal_and_capture_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sound_dir = root / "sound"
            sound_dir.mkdir()

            # card0: internal platform sound (NOT USB)
            internal_dev = root / "platform-sound"
            internal_dev.mkdir()
            c0 = sound_dir / "card0"
            c0.mkdir()
            (c0 / "device").symlink_to(internal_dev)
            (c0 / "id").write_text("F1\n")
            (c0 / "pcmC0D0p").mkdir()

            # card1: USB microphone (capture-only, no *p playback device)
            usb_mic = root / "usb1" / "1-1"
            usb_mic.mkdir(parents=True)
            c1 = sound_dir / "card1"
            c1.mkdir()
            (c1 / "device").symlink_to(usb_mic)
            (c1 / "id").write_text("USBMic\n")
            (c1 / "pcmC1D0c").mkdir()

            # card2: USB DAC (USB + playback PCM)
            usb_dac = root / "usb1" / "1-2"
            usb_dac.mkdir(parents=True)
            c2 = sound_dir / "card2"
            c2.mkdir()
            (c2 / "device").symlink_to(usb_dac)
            (c2 / "id").write_text("DAC\n")
            (c2 / "pcmC2D0p").mkdir()

            with patch.object(dac_mod, "SOUND_SYSFS", sound_dir):
                cards = dac_mod.find_usb_audio_cards()

        self.assertEqual(cards, [(2, "DAC")])

    def test_attach_switches_to_dac_and_resumes_queue(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            env_file = Path(td) / "squeezelite.env"
            state_file = Path(td) / "audio-state.json"
            fake_client = MagicMock()
            fake_client.list_players.return_value = [
                {"playerid": "02:70:6f:63:6f:01", "name": "pocobox", "connected": 1}
            ]
            fake_client.player_status.return_value = {"mode": "pause", "playlist_tracks": 5}

            with patch.dict(os.environ, {"ENABLE_AEC": "0"}), \
                 patch.object(dac_mod, "SQUEEZELITE_ENV", env_file), \
                 patch.object(dac_mod, "AUDIO_STATE_FILE", state_file), \
                 patch.object(dac_mod, "find_usb_audio_cards", return_value=[(1, "D10s")]), \
                 patch.object(dac_mod, "set_usb_dac_hw_full_volume", return_value=["PCM,0"]) as mock_hw_vol, \
                 patch.object(dac_mod, "release_usb_cards_from_pulseaudio", return_value=[]) as mock_release, \
                 patch.object(dac_mod, "restart_squeezelite") as mock_restart:
                new_state = dac_mod.apply_state(fake_client, active_dac=None, is_startup=False)

            self.assertEqual(new_state, "D10s")
            mock_hw_vol.assert_called_once_with(1)
            mock_release.assert_called_once()
            mock_restart.assert_called_once()
            env_text = env_file.read_text()
            self.assertIn("SQUEEZELITE_OUTPUT=hw:CARD=D10s,DEV=0", env_text)
            self.assertIn("-R -u vLE", env_text)
            fake_client.set_volume.assert_called_once_with("02:70:6f:63:6f:01", 100)
            fake_client.play.assert_called_once_with("02:70:6f:63:6f:01")

    def test_attach_with_webrtc_aec_routes_through_aec_sink(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            env_file = Path(td) / "squeezelite.env"
            state_file = Path(td) / "audio-state.json"
            fake_client = MagicMock()
            fake_client.list_players.return_value = [
                {"playerid": "02:70:6f:63:6f:01", "name": "pocobox", "connected": 1}
            ]
            fake_client.player_status.return_value = {"mode": "pause", "playlist_tracks": 3}

            with patch.dict(os.environ, {"ENABLE_AEC": "1"}), \
                 patch.object(dac_mod, "SQUEEZELITE_ENV", env_file), \
                 patch.object(dac_mod, "AUDIO_STATE_FILE", state_file), \
                 patch.object(dac_mod, "find_usb_audio_cards", return_value=[(1, "DAC")]), \
                 patch.object(dac_mod, "stop_squeezelite") as mock_stop, \
                 patch.object(dac_mod, "setup_pulse_aec", return_value=True) as mock_aec, \
                 patch.object(dac_mod, "restart_squeezelite") as mock_restart, \
                 patch.object(dac_mod, "restart_voice_if_active") as mock_voice:
                new_state = dac_mod.apply_state(fake_client, active_dac=None, is_startup=False)

            self.assertEqual(new_state, "DAC")
            mock_stop.assert_called_once()
            mock_aec.assert_called_once_with("DAC")
            mock_restart.assert_called_once()
            mock_voice.assert_called_once()
            env_text = env_file.read_text()
            self.assertIn("SQUEEZELITE_OUTPUT=pulse", env_text)
            self.assertIn("PULSE_SINK=pocobox_aec_sink", env_text)
            self.assertIn('"aec": true', state_file.read_text())
            fake_client.play.assert_called_once_with("02:70:6f:63:6f:01")

    def test_detach_pauses_and_restores_pulse(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            env_file = Path(td) / "squeezelite.env"
            state_file = Path(td) / "audio-state.json"
            fake_client = MagicMock()
            fake_client.resolve_player_id.return_value = "02:70:6f:63:6f:01"

            with patch.dict(os.environ, {"ENABLE_AEC": "0"}), \
                 patch.object(dac_mod, "SQUEEZELITE_ENV", env_file), \
                 patch.object(dac_mod, "AUDIO_STATE_FILE", state_file), \
                 patch.object(dac_mod, "find_usb_audio_cards", return_value=[]), \
                 patch.object(dac_mod, "restart_squeezelite") as mock_restart:
                new_state = dac_mod.apply_state(fake_client, active_dac="D10s", is_startup=False)

            self.assertIsNone(new_state)
            fake_client.pause.assert_called_once_with("02:70:6f:63:6f:01", 1)
            mock_restart.assert_called_once()
            self.assertIn("SQUEEZELITE_OUTPUT=pulse", env_file.read_text())

    def test_ensure_visexport_arg_appends_only_when_supported(self) -> None:
        with patch.object(dac_mod, "squeezelite_supports_visexport", return_value=True):
            self.assertEqual(
                dac_mod.ensure_visexport_arg("-m 02:70:6f:63:6f:01 -W"),
                "-m 02:70:6f:63:6f:01 -W -v",
            )
            self.assertEqual(
                dac_mod.ensure_visexport_arg("-m 02:70:6f:63:6f:01 -W -v"),
                "-m 02:70:6f:63:6f:01 -W -v",
            )
        with patch.object(dac_mod, "squeezelite_supports_visexport", return_value=False):
            self.assertEqual(
                dac_mod.ensure_visexport_arg("-m 02:70:6f:63:6f:01 -W"),
                "-m 02:70:6f:63:6f:01 -W",
            )

    def test_set_usb_dac_hw_full_volume_locks_playback_controls_only(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, capture_output=True, text=True, check=False):
            calls.append(list(cmd))
            res = MagicMock()
            res.returncode = 0
            if "scontrols" in cmd:
                res.stdout = "Simple mixer control 'PCM',0\nSimple mixer control 'Mic',0\n"
            elif "sget" in cmd and "PCM,0" in cmd:
                res.stdout = "Simple mixer control 'PCM',0\n  Capabilities: pvolume pswitch pswitch-joined\n"
            elif "sget" in cmd and "Mic,0" in cmd:
                res.stdout = "Simple mixer control 'Mic',0\n  Capabilities: cvolume cswitch\n"
            else:
                res.stdout = ""
            return res

        with patch.dict(os.environ, {"DAC_FORCE_HW_FULL_VOLUME": "1"}), \
             patch.object(dac_mod.subprocess, "run", side_effect=fake_run):
            adjusted = dac_mod.set_usb_dac_hw_full_volume(1)

        self.assertEqual(adjusted, ["PCM,0"])
        self.assertIn(["amixer", "-c", "1", "sset", "PCM,0", "100%", "unmute"], calls)
        self.assertFalse(any("sset" in c and "Mic,0" in c for c in calls))

    def test_release_usb_cards_from_pulseaudio(self) -> None:
        fake_cards = [
            "0\talsa_card.usb-Burr-Brown_from_TI_USB_Audio_DAC-00\tmodule-alsa-card.c",
            "1\talsa_card.platform-sound\tmodule-alsa-card.c",
        ]
        ok = MagicMock(returncode=0)
        with patch.object(dac_mod, "_list_pa_short", return_value=fake_cards), \
             patch.object(dac_mod, "run_pactl", return_value=ok) as mock_pactl:
            released = dac_mod.release_usb_cards_from_pulseaudio()

        self.assertEqual(released, ["alsa_card.usb-Burr-Brown_from_TI_USB_Audio_DAC-00"])
        mock_pactl.assert_called_once_with(
            ["set-card-profile", "alsa_card.usb-Burr-Brown_from_TI_USB_Audio_DAC-00", "off"]
        )

    def test_configure_dac_lms_volume_fixed_mode_disables_digital_volume(self) -> None:
        fake_client = MagicMock()
        with patch.dict(os.environ, {"DAC_LMS_FIXED_VOLUME": "1"}):
            dac_mod.configure_dac_lms_volume(fake_client, "02:70:6f:63:6f:01")
        fake_client.call.assert_called_once_with(
            "02:70:6f:63:6f:01", ["playerpref", "digitalVolumeControl", 0]
        )
        fake_client.set_volume.assert_called_once_with("02:70:6f:63:6f:01", 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
