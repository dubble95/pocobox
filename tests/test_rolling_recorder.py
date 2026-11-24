"""Unit tests for the rolling in-memory/tmpfs audio history recorder."""

from __future__ import annotations

import math
import os
import struct
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "services"))
sys.path.insert(0, str(REPO_ROOT / "services" / "lms_client"))

from voice_control.daemon import VoiceController  # noqa: E402
from voice_control.rolling_recorder import (  # noqa: E402
    RollingAudioRecorder,
    build_wav_bytes,
    extract_stereo_channel,
    interleave_stereo,
)


def _make_sine_pcm(freq_hz: float, dur_sec: float = 0.5, sample_rate: int = 16000, amp: int = 10000) -> bytes:
    n = int(sample_rate * dur_sec)
    samples = [int(amp * math.sin(2.0 * math.pi * freq_hz * i / sample_rate)) for i in range(n)]
    return struct.pack(f"<{n}h", *samples)


class TestRollingAudioRecorder(unittest.TestCase):
    def test_interleave_and_extract_stereo_channels(self) -> None:
        ch0 = struct.pack("<4h", 100, 200, 300, 400)
        ch1 = struct.pack("<4h", 1000, 2000, 3000, 4000)

        stereo = interleave_stereo(ch0, ch1)
        self.assertEqual(len(stereo), 16)  # 4 frames * 4 bytes

        extracted_ch0 = extract_stereo_channel(stereo, 0)
        extracted_ch1 = extract_stereo_channel(stereo, 1)

        self.assertEqual(extracted_ch0, ch0)
        self.assertEqual(extracted_ch1, ch1)

    def test_build_wav_bytes_header_validation(self) -> None:
        pcm = _make_sine_pcm(440.0, dur_sec=0.25)
        wav = build_wav_bytes(pcm, sample_rate=16000, channels=1)

        self.assertTrue(wav.startswith(b"RIFF"))
        self.assertIn(b"WAVEfmt ", wav)
        self.assertEqual(len(wav), len(pcm) + 44)

    def test_feed_creates_segments_and_prunes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            buf_dir = Path(td)
            # Use small 1.0s segments and 3.0s retention for rapid test
            recorder = RollingAudioRecorder(
                buffer_dir=buf_dir,
                segment_sec=1.0,
                retention_sec=3.0,
                sample_rate=16000,
                enabled=True,
            )

            chunk_ch0 = _make_sine_pcm(440.0, dur_sec=0.5, amp=5000)
            chunk_ch1 = _make_sine_pcm(880.0, dur_sec=0.5, amp=2000)

            # Feed 2 chunks = 1.0s -> triggers 1st segment flush
            recorder.feed(chunk_ch0, chunk_ch1, is_music_playing=True, aec3_db=12.5)
            recorder.feed(chunk_ch0, chunk_ch1, is_music_playing=True, aec3_db=13.0)

            # Wait for writer queue to write segment file
            time.sleep(0.15)
            segments = recorder.list_segments()
            self.assertGreaterEqual(len(segments), 1)

            first_wav = buf_dir / segments[0]["file"]
            self.assertTrue(first_wav.exists())

            with wave.open(str(first_wav), "rb") as wf:
                self.assertEqual(wf.getnchannels(), 2)
                self.assertEqual(wf.getframerate(), 16000)
                self.assertEqual(wf.getsampwidth(), 2)

            # Check metadata json
            meta_json = first_wav.with_suffix(".json")
            self.assertTrue(meta_json.exists())

            # Feed more to test pruning
            for _ in range(8):
                recorder.feed(chunk_ch0, chunk_ch1, is_music_playing=False)
            time.sleep(0.2)

            # Prune with simulated future time
            removed = recorder.prune(now=time.time() + 10.0)
            self.assertGreater(removed, 0)

            recorder.close()

    def test_get_recent_wav_combines_stored_and_memory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            buf_dir = Path(td)
            recorder = RollingAudioRecorder(
                buffer_dir=buf_dir,
                segment_sec=1.0,
                retention_sec=10.0,
                sample_rate=16000,
                enabled=True,
            )

            # Feed 1.5 seconds of audio (1.0s written to disk, 0.5s in memory)
            for _ in range(3):
                pcm_raw = _make_sine_pcm(300.0, dur_sec=0.5, amp=4000)
                pcm_clean = _make_sine_pcm(600.0, dur_sec=0.5, amp=2000)
                recorder.feed(pcm_raw, pcm_clean)

            time.sleep(0.15)

            # Retrieve recent audio
            wav_both = recorder.get_recent_wav(seconds=1.0, channel="both")
            wav_raw = recorder.get_recent_wav(seconds=1.0, channel="raw")
            wav_clean = recorder.get_recent_wav(seconds=1.0, channel="clean")

            self.assertTrue(wav_both.startswith(b"RIFF"))
            self.assertTrue(wav_raw.startswith(b"RIFF"))
            self.assertTrue(wav_clean.startswith(b"RIFF"))

            # Stereo should have roughly twice the data bytes of mono
            self.assertAlmostEqual(len(wav_both) - 44, (len(wav_raw) - 44) * 2, delta=8)

            recorder.close()

    def test_controller_audio_history_endpoints_and_replay(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(
                os.environ,
                {
                    "CONTINUOUS_LISTEN": "1",
                    "VOICE_DUCK_ENABLED": "1",
                    "ENABLE_CHIME": "0",
                    "VOICE_ROLLING_DIR": td,
                    "VOICE_ROLLING_RETENTION_SEC": "600",
                    "VOICE_ROLLING_SEGMENT_SEC": "30",
                },
            ):
                ctrl = VoiceController()

            self.assertIsNotNone(ctrl.rolling_recorder)
            self.assertTrue(ctrl.rolling_recorder.enabled)

            # Feed test audio into continuous listener's rolling recorder
            pcm_raw = _make_sine_pcm(500.0, dur_sec=1.0, amp=3000)
            pcm_clean = _make_sine_pcm(500.0, dur_sec=1.0, amp=2000)
            ctrl.rolling_recorder.feed(pcm_raw, pcm_clean, is_music_playing=False)

            status = ctrl.rolling_recorder.status_dict()
            self.assertTrue(status["ok"])
            self.assertEqual(status["retention_sec"], 600.0)

            # Replay recent audio
            replay_res = ctrl.replay_audio_history(seconds=1.0, channel="clean")
            self.assertTrue(replay_res["ok"])
            self.assertEqual(replay_res["channel"], "clean")

            ctrl.rolling_recorder.close()

    def test_cli_history_subcommands(self) -> None:
        from voice_control import cli

        with mock.patch.object(cli, "_request", return_value=(200, {"ok": True, "count": 2})) as mock_req:
            with mock.patch.object(sys, "argv", ["pocobox-voice", "history", "status"]):
                code = cli.main()
                self.assertEqual(code, 0)
                mock_req.assert_called_with("http://127.0.0.1:8765/debug/audio_history")

        with tempfile.TemporaryDirectory() as td:
            save_path = Path(td) / "test_out.wav"
            fake_wav = b"RIFF....WAVE"
            with mock.patch.object(cli, "_download_binary", return_value=(200, fake_wav)) as mock_dl:
                with mock.patch.object(sys, "argv", ["pocobox-voice", "history", "save", str(save_path), "--seconds", "45", "--channel", "clean"]):
                    code = cli.main()
                    self.assertEqual(code, 0)
                    self.assertTrue(save_path.exists())
                    self.assertEqual(save_path.read_bytes(), fake_wav)
                    mock_dl.assert_called_with("http://127.0.0.1:8765/debug/audio_history/recent.wav?seconds=45&channel=clean")

        with mock.patch.object(cli, "_request", return_value=(200, {"ok": True, "replayed": True})) as mock_req:
            with mock.patch.object(sys, "argv", ["pocobox-voice", "history", "replay", "--seconds", "20", "--channel", "raw"]):
                code = cli.main()
                self.assertEqual(code, 0)
                mock_req.assert_called_with(
                    "http://127.0.0.1:8765/debug/audio_history/replay",
                    data=b'{"seconds": 20, "channel": "raw"}',
                )
