"""Unit tests for PocoBox Voice 2.0 DSP bandpass filter, channel extraction, and Auto-Ducking."""

from __future__ import annotations

import math
import os
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "services"))
sys.path.insert(0, str(REPO_ROOT / "services" / "lms_client"))

from voice_control.audio_capture import chunk_rms, pcm_to_wav_bytes  # noqa: E402
from voice_control.audio_dsp import (  # noqa: E402
    SpeechBandpassFilter,
    decimate_48k_to_16k,
    extract_mono_channel,
    filter_wav_speech_band,
)
from voice_control.daemon import VoiceController  # noqa: E402
from voice_control.intent_schema import IntentAction, VoiceIntent  # noqa: E402
from voice_control.wakeword import VoskWakeWordSpotter  # noqa: E402


def _make_sine_pcm(freq_hz: float, dur_sec: float = 0.5, sample_rate: int = 16000, amp: int = 10000) -> bytes:
    n = int(sample_rate * dur_sec)
    samples = [int(amp * math.sin(2.0 * math.pi * freq_hz * i / sample_rate)) for i in range(n)]
    return struct.pack(f"<{n}h", *samples)


class _FakeLMS:
    def __init__(self, mode: str = "play", volume: int = 85) -> None:
        self.mode = mode
        self.volume = volume
        self.volume_history: list[int] = []

    def resolve_player_id(self, name: str | None = None) -> str:
        return "02:70:6f:63:6f:01"

    def player_status(self, player_id: str) -> dict:
        return {"mode": self.mode, "mixer volume": self.volume}

    def set_volume(self, player_id: str, value: int) -> dict:
        self.volume = int(value)
        self.volume_history.append(self.volume)
        return {"volume": self.volume}

    def get_volume(self, player_id: str) -> int:
        return self.volume

    def next_track(self, player_id: str) -> dict:
        return {"ok": True}

    def list_players(self) -> list[dict]:
        return [{"name": "pocobox", "playerid": "02:70:6f:63:6f:01", "connected": 1}]

    def now_playing(self, player_id: str) -> dict:
        return {"mode": self.mode, "volume": self.volume, "title": "Yellow", "artist": "Coldplay"}


class TestVoiceDSPAndDucking(unittest.TestCase):
    def test_speech_bandpass_rejects_bass_and_preserves_voice(self) -> None:
        bass_pcm = _make_sine_pcm(60.0, dur_sec=0.5, amp=10000)
        voice_pcm = _make_sine_pcm(1000.0, dur_sec=0.5, amp=10000)
        hiss_pcm = _make_sine_pcm(6800.0, dur_sec=0.5, amp=10000)

        bp_bass = SpeechBandpassFilter(16000, 250.0, 3500.0).process_pcm(bass_pcm)
        bp_voice = SpeechBandpassFilter(16000, 250.0, 3500.0).process_pcm(voice_pcm)
        bp_hiss = SpeechBandpassFilter(16000, 250.0, 3500.0).process_pcm(hiss_pcm)

        # Skip initial 100ms filter transient
        rms_bass = chunk_rms(bp_bass[3200:])
        rms_voice = chunk_rms(bp_voice[3200:])
        rms_hiss = chunk_rms(bp_hiss[3200:])

        self.assertGreater(rms_voice, 5500.0)
        self.assertLess(rms_bass, rms_voice * 0.15)
        self.assertLess(rms_hiss, rms_voice * 0.25)

    def test_filter_wav_speech_band_roundtrip(self) -> None:
        wav = pcm_to_wav_bytes(_make_sine_pcm(1000.0, dur_sec=0.3, amp=8000))
        filtered = filter_wav_speech_band(wav)
        self.assertTrue(filtered.startswith(b"RIFF"))
        self.assertEqual(len(filtered), len(wav))

    def test_extract_mono_channel_and_decimate(self) -> None:
        # Left = 100, Right = 3000 across 6 stereo frames
        interleaved = struct.pack("<12h", 100, 3000, 100, 3000, 100, 3000, 100, 3000, 100, 3000, 100, 3000)
        left = extract_mono_channel(interleaved, "left")
        right = extract_mono_channel(interleaved, "right")
        mono = extract_mono_channel(interleaved, "mono")
        self.assertEqual(struct.unpack("<6h", left), (100,) * 6)
        self.assertEqual(struct.unpack("<6h", right), (3000,) * 6)
        self.assertEqual(struct.unpack("<6h", mono), (1550,) * 6)

        dec = decimate_48k_to_16k(right)
        self.assertEqual(struct.unpack("<2h", dec), (3000, 3000))

    def test_wake_spotter_phrase_matching_without_model(self) -> None:
        with mock.patch.dict(os.environ, {"VOICE_WAKE_ENGINE": "off"}):
            spotter = VoskWakeWordSpotter()
        self.assertFalse(spotter.available)
        self.assertEqual(spotter._match_text("hey poco [unk]"), "hey poco")
        self.assertEqual(spotter._match_text("coco ya"), "coco ya")
        self.assertIsNone(spotter._match_text("[unk] [unk]"))
        self.assertIsNone(spotter._match_text(""))

    def test_auto_duck_saves_and_restores_volume_on_normal_command(self) -> None:
        with mock.patch.dict(os.environ, {"CONTINUOUS_LISTEN": "0", "VOICE_DUCK_ENABLED": "1", "VOICE_DUCK_VOLUME": "18", "ENABLE_CHIME": "0"}):
            ctrl = VoiceController()
        fake_lms = _FakeLMS(mode="play", volume=90)
        ctrl.lms = fake_lms  # type: ignore[assignment]
        ctrl.executor.lms = fake_lms  # type: ignore[assignment]
        ctrl.interpreter.provider = "gemini"

        ctrl.handle_wake_detected("poco")
        self.assertEqual(fake_lms.volume, 18)

        wav = pcm_to_wav_bytes(_make_sine_pcm(1000.0, dur_sec=0.5, amp=4000))
        with mock.patch.object(
            ctrl.interpreter,
            "interpret_audio",
            return_value=VoiceIntent(action=IntentAction.NEXT, raw_text="다음 곡", wake_word_detected=False),
        ):
            ctrl.handle_wake_utterance(wav, "poco")

        self.assertEqual(fake_lms.volume, 90)
        self.assertEqual(fake_lms.volume_history, [18, 90])

    def test_auto_duck_does_not_overwrite_explicit_volume_command(self) -> None:
        with mock.patch.dict(os.environ, {"CONTINUOUS_LISTEN": "0", "VOICE_DUCK_ENABLED": "1", "VOICE_DUCK_VOLUME": "18", "ENABLE_CHIME": "0"}):
            ctrl = VoiceController()
        fake_lms = _FakeLMS(mode="play", volume=90)
        ctrl.lms = fake_lms  # type: ignore[assignment]
        ctrl.executor.lms = fake_lms  # type: ignore[assignment]
        ctrl.interpreter.provider = "gemini"

        ctrl.handle_wake_detected("poco")
        self.assertEqual(fake_lms.volume, 18)

        wav = pcm_to_wav_bytes(_make_sine_pcm(1000.0, dur_sec=0.5, amp=4000))
        with mock.patch.object(
            ctrl.interpreter,
            "interpret_audio",
            return_value=VoiceIntent(action=IntentAction.VOLUME_SET, volume=55, raw_text="볼륨 55", wake_word_detected=False),
        ):
            ctrl.handle_wake_utterance(wav, "poco")

        self.assertEqual(fake_lms.volume, 55)

    def test_bare_wake_word_triggers_followup_listen_once(self) -> None:
        with mock.patch.dict(os.environ, {"CONTINUOUS_LISTEN": "0", "VOICE_DUCK_ENABLED": "1", "ENABLE_CHIME": "0"}):
            ctrl = VoiceController()
        ctrl.interpreter.provider = "gemini"
        wav = pcm_to_wav_bytes(_make_sine_pcm(1000.0, dur_sec=0.5, amp=4000))
        with mock.patch.object(
            ctrl.interpreter,
            "interpret_audio",
            return_value=VoiceIntent(action=IntentAction.UNKNOWN, raw_text="포코야", wake_word_detected=True),
        ), mock.patch.object(ctrl, "listen_once") as mock_listen:
            ctrl.handle_continuous_wav(wav)
            mock_listen.assert_called_once_with(dry_run=False)


if __name__ == "__main__":
    unittest.main()
