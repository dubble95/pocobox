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
            fake_client = MagicMock()
            fake_client.list_players.return_value = [
                {"playerid": "02:70:6f:63:6f:01", "name": "pocobox", "connected": 1}
            ]
            fake_client.player_status.return_value = {"mode": "pause", "playlist_tracks": 5}

            with patch.object(dac_mod, "SQUEEZELITE_ENV", env_file), \
                 patch.object(dac_mod, "find_usb_audio_cards", return_value=[(1, "D10s")]), \
                 patch.object(dac_mod, "restart_squeezelite") as mock_restart:
                new_state = dac_mod.apply_state(fake_client, active_dac=None, is_startup=False)

            self.assertEqual(new_state, "D10s")
            mock_restart.assert_called_once()
            self.assertIn("SQUEEZELITE_OUTPUT=plughw:CARD=D10s,DEV=0", env_file.read_text())
            fake_client.play.assert_called_once_with("02:70:6f:63:6f:01")

    def test_detach_pauses_and_restores_pulse(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            env_file = Path(td) / "squeezelite.env"
            fake_client = MagicMock()
            fake_client.resolve_player_id.return_value = "02:70:6f:63:6f:01"

            with patch.object(dac_mod, "SQUEEZELITE_ENV", env_file), \
                 patch.object(dac_mod, "find_usb_audio_cards", return_value=[]), \
                 patch.object(dac_mod, "restart_squeezelite") as mock_restart:
                new_state = dac_mod.apply_state(fake_client, active_dac="D10s", is_startup=False)

            self.assertIsNone(new_state)
            fake_client.pause.assert_called_once_with("02:70:6f:63:6f:01", 1)
            mock_restart.assert_called_once()
            self.assertIn("SQUEEZELITE_OUTPUT=pulse", env_file.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
