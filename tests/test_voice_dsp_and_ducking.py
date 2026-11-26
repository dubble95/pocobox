"""Unit tests for PocoBox Voice 2.0 DSP bandpass filter, channel extraction, and Auto-Ducking."""

from __future__ import annotations

import json
import math
import os
import struct
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "services"))
sys.path.insert(0, str(REPO_ROOT / "services" / "lms_client"))

from voice_control.audio_aec import WebRTCAEC3Processor, resample_stereo_pcm  # noqa: E402
from voice_control.audio_capture import chunk_rms, pcm_to_wav_bytes  # noqa: E402
from voice_control.audio_dsp import (  # noqa: E402
    AntiAliasDecimator48kTo16k,
    SpeechBandpassFilter,
    decimate_48k_to_16k,
    extract_mono_channel,
    filter_wav_speech_band,
    measure_pcm_peak_and_clip,
)
from voice_control.continuous_listener import ContinuousVoiceListener  # noqa: E402
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
        both = extract_mono_channel(interleaved, "both")
        diff = extract_mono_channel(interleaved, "diff")
        self.assertEqual(struct.unpack("<6h", left), (100,) * 6)
        self.assertEqual(struct.unpack("<6h", right), (3000,) * 6)
        self.assertEqual(struct.unpack("<6h", mono), (1550,) * 6)
        # (100 + 3000) * 0.70710678 = 2192
        self.assertEqual(struct.unpack("<6h", both), (2192,) * 6)
        # 100 - 3000 = -2900
        self.assertEqual(struct.unpack("<6h", diff), (-2900,) * 6)

        dec = decimate_48k_to_16k(right)
        self.assertEqual(struct.unpack("<2h", dec), (3000, 3000))

    def test_wake_spotter_phrase_matching_without_model(self) -> None:
        with mock.patch.dict(os.environ, {"VOICE_WAKE_ENGINE": "off"}):
            spotter = VoskWakeWordSpotter()
        self.assertFalse(spotter.available)
        # Default: only "poco ya" triggers; "hey poco" is a decoy (low precision)
        self.assertEqual(spotter._match_text("poco ya"), "poco ya")
        self.assertIsNone(spotter._match_text("hey poco [unk]"))
        self.assertIsNone(spotter._match_text("coco ya"))
        self.assertIsNone(spotter._match_text("boko ya"))
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
        ), mock.patch("voice_control.daemon.speak_text", return_value=True) as mock_speak:
            ctrl.handle_wake_utterance(wav, "poco")

        mock_speak.assert_called_once()
        self.assertEqual(mock_speak.call_args[0][0], "네, 다음 곡으로 넘어갈게요.")
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
        ), mock.patch("voice_control.daemon.speak_text", return_value=True) as mock_speak:
            ctrl.handle_wake_utterance(wav, "poco")

        mock_speak.assert_called_once()
        self.assertEqual(mock_speak.call_args[0][0], "네, 볼륨을 55로 맞출게요.")
        self.assertEqual(fake_lms.volume, 55)

    def test_dac_mode_auto_ducking(self) -> None:
        with mock.patch.dict(os.environ, {"CONTINUOUS_LISTEN": "0", "VOICE_DUCK_ENABLED": "1", "VOICE_DUCK_VOLUME": "18", "ENABLE_CHIME": "0"}):
            ctrl = VoiceController()
        fake_lms = _FakeLMS(mode="play", volume=100)
        ctrl.lms = fake_lms  # type: ignore[assignment]
        ctrl.executor.lms = fake_lms  # type: ignore[assignment]

        with mock.patch.object(ctrl, "is_dac_mode", return_value=True):
            ctrl.dac_duck_enabled = True
            self.assertTrue(ctrl._begin_duck())
            self.assertEqual(fake_lms.volume, 18)
            ctrl._restore_duck()
            self.assertEqual(fake_lms.volume, 100)

            # When explicitly disabled for DAC mode:
            ctrl.dac_duck_enabled = False
            self.assertFalse(ctrl._begin_duck())
            self.assertEqual(fake_lms.volume, 100)

    def test_dac_mode_volume_command_rejected_and_duck_restored_to_100(self) -> None:
        with mock.patch.dict(os.environ, {"CONTINUOUS_LISTEN": "0", "VOICE_DUCK_ENABLED": "1", "VOICE_DUCK_VOLUME": "18", "ENABLE_CHIME": "0"}):
            ctrl = VoiceController()
        fake_lms = _FakeLMS(mode="play", volume=100)
        ctrl.lms = fake_lms  # type: ignore[assignment]
        ctrl.executor.lms = fake_lms  # type: ignore[assignment]
        ctrl.interpreter.provider = "gemini"

        with mock.patch.object(ctrl, "is_dac_mode", return_value=True), \
             mock.patch.object(ctrl.executor, "is_dac_mode_fn", return_value=True):
            ctrl.handle_wake_detected("poco")
            self.assertEqual(fake_lms.volume, 18)

            wav = pcm_to_wav_bytes(_make_sine_pcm(1000.0, dur_sec=0.5, amp=4000))
            with mock.patch.object(
                ctrl.interpreter,
                "interpret_audio",
                return_value=VoiceIntent(action=IntentAction.VOLUME_SET, volume=55, raw_text="볼륨 55", wake_word_detected=False),
            ), mock.patch("voice_control.daemon.speak_text", return_value=True) as mock_speak:
                ctrl.handle_wake_utterance(wav, "poco")

            mock_speak.assert_called_once()
            self.assertIn("100%로 고정되어 있습니다", mock_speak.call_args[0][0])
            self.assertEqual(fake_lms.volume, 100)
            self.assertEqual(fake_lms.volume_history, [18, 100])

    def test_auto_duck_watchdog_restores_volume_on_timeout(self) -> None:
        """When an unhandled error or hang occurs after wake, watchdog timer forces volume restoration."""
        with mock.patch.dict(os.environ, {"CONTINUOUS_LISTEN": "0", "VOICE_DUCK_ENABLED": "1", "VOICE_DUCK_VOLUME": "18", "ENABLE_CHIME": "0"}):
            ctrl = VoiceController()
        fake_lms = _FakeLMS(mode="play", volume=90)
        ctrl.lms = fake_lms  # type: ignore[assignment]

        self.assertTrue(ctrl._begin_duck())
        self.assertEqual(fake_lms.volume, 18)
        self.assertIsNotNone(ctrl._duck_watchdog)

        # Trigger watchdog timeout directly
        ctrl._on_duck_watchdog_timeout()
        self.assertEqual(fake_lms.volume, 90)
        self.assertIsNone(ctrl._duck_saved)

    def test_dac_mode_stuck_duck_volume_safety_recovery(self) -> None:
        """If DAC volume was stuck at duck volume with no saved state, restore_duck safely recovers to 100%."""
        with mock.patch.dict(os.environ, {"CONTINUOUS_LISTEN": "0", "VOICE_DUCK_ENABLED": "1", "ENABLE_CHIME": "0"}):
            ctrl = VoiceController()
        fake_lms = _FakeLMS(mode="play", volume=18)
        ctrl.lms = fake_lms  # type: ignore[assignment]
        ctrl._duck_saved = None

        with mock.patch.object(ctrl, "is_dac_mode", return_value=True):
            ctrl._restore_duck(skip_restore=False)
            self.assertEqual(fake_lms.volume, 100)

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

    def test_antialias_decimator_rejects_12khz_alias_and_preserves_1khz(self) -> None:
        # 12kHz at 48kHz would fold straight to 4kHz with naive decimation; FIR anti-alias must crush it
        hf_48k = _make_sine_pcm(12000.0, dur_sec=0.2, sample_rate=48000, amp=12000)
        voice_48k = _make_sine_pcm(1000.0, dur_sec=0.2, sample_rate=48000, amp=12000)

        dec_hf = AntiAliasDecimator48kTo16k()
        # Process in 20ms streaming sub-chunks (1920 bytes @ 48kHz mono) to verify boundary continuity
        hf_out = b"".join(dec_hf.process_48k_to_16k(hf_48k[i : i + 1920]) for i in range(0, len(hf_48k), 1920))

        dec_v = AntiAliasDecimator48kTo16k()
        v_out = b"".join(dec_v.process_48k_to_16k(voice_48k[i : i + 1920]) for i in range(0, len(voice_48k), 1920))

        rms_hf = chunk_rms(hf_out[640:])
        rms_v = chunk_rms(v_out[640:])
        self.assertGreater(rms_v, 7500.0)
        self.assertLess(rms_hf, rms_v * 0.05)  # > 26 dB stopband rejection

    def test_measure_pcm_peak_and_clip(self) -> None:
        pcm = struct.pack("<4h", 500, -31500, 32700, -1000)
        peak, ratio = measure_pcm_peak_and_clip(pcm, clip_level=30000)
        self.assertEqual(peak, 32700)
        self.assertAlmostEqual(ratio, 0.5, places=3)

    def test_wake_spotter_music_mode_filtering_and_confirmation(self) -> None:
        import json as _json

        class _FakeRec:
            def __init__(self, responses: list[tuple[bool, dict]]) -> None:
                self._responses = list(responses)
                self.resets = 0

            def AcceptWaveform(self, data: bytes) -> bool:
                return self._responses[0][0]

            def Result(self) -> str:
                return _json.dumps(self._responses.pop(0)[1])

            def PartialResult(self) -> str:
                return _json.dumps(self._responses.pop(0)[1])

            def Reset(self) -> None:
                self.resets += 1

        with mock.patch.dict(
            os.environ,
            {"VOICE_WAKE_ENGINE": "off", "VOICE_WAKE_TRIGGER_PHRASES": "hey poco,poco ya,poco box"},
        ):
            spotter = VoskWakeWordSpotter(music_confirm_chunks=2, music_min_confidence=0.6)

        # Bare 2-syllable 'poco' ignored in both quiet and music to prevent false-triggers
        self.assertIsNone(spotter._match_text("poco", music_playing=True))
        self.assertIsNone(spotter._match_text("poco", music_playing=False))
        self.assertEqual(spotter._match_text("poco ya", music_playing=True), "poco ya")
        self.assertEqual(spotter._match_text("poco ya", music_playing=False), "poco ya")

        # Require 2 consecutive partials during music; 1 transient blip must not fire
        spotter.available = True
        spotter._rec = _FakeRec([
            (False, {"partial": "poco ya"}),
            (False, {"partial": "[unk]"}),
            (False, {"partial": "poco ya"}),
            (False, {"partial": "poco ya"}),
        ])
        chunk = b"\x00\x01" * 160
        self.assertIsNone(spotter.accept_pcm16k(chunk, music_playing=True))
        self.assertIsNone(spotter.accept_pcm16k(chunk, music_playing=True))
        self.assertIsNone(spotter.accept_pcm16k(chunk, music_playing=True))
        self.assertEqual(spotter.accept_pcm16k(chunk, music_playing=True), "poco ya")

        # Stricter confirmation for vocal-prone 'hey poco' during music playback
        spotter2 = VoskWakeWordSpotter(
            music_confirm_chunks=3,
            music_min_confidence=0.70,
            trigger_phrases=("hey poco", "poco ya", "poco box"),
        )
        spotter2.available = True
        spotter2._last_trigger_time = 0.0  # Reset anti-spam cooldown for test
        spotter2._rec = _FakeRec([
            (False, {"partial": "hey poco"}),
            (False, {"partial": "hey poco"}),
            (False, {"partial": "hey poco"}),
            (False, {"partial": "hey poco"}),
        ])
        self.assertIsNone(spotter2.accept_pcm16k(chunk, music_playing=True))
        self.assertIsNone(spotter2.accept_pcm16k(chunk, music_playing=True))
        self.assertIsNone(spotter2.accept_pcm16k(chunk, music_playing=True))
        self.assertEqual(spotter2.accept_pcm16k(chunk, music_playing=True), "hey poco")

        # Low-confidence final result rejected during music; high-confidence accepted
        spotter._last_trigger_time = 0.0
        spotter._rec = _FakeRec([
            (True, {"text": "poco ya", "result": [{"word": "poco", "conf": 0.3}, {"word": "ya", "conf": 0.4}]}),
            (True, {"text": "poco ya", "result": [{"word": "poco", "conf": 0.9}, {"word": "ya", "conf": 0.85}]}),
        ])
        self.assertIsNone(spotter.accept_pcm16k(chunk, music_playing=True))
        self.assertEqual(spotter.accept_pcm16k(chunk, music_playing=True), "poco ya")

    def test_wake_korean_variants_normalize_to_poco_ya(self) -> None:
        with mock.patch.dict(os.environ, {"VOICE_WAKE_ENGINE": "off"}):
            spotter = VoskWakeWordSpotter(
                trigger_phrases=("hey poco", "poco ya", "poco box")
            )
        ya_variants = [
            "포코야",
            "포코 야",
            "폭호야",
            "포코아",
            "포코얌",
            "포코약",
            "포코얘",
            "boco ya",
            "poko ya",
            "polco ya",
            "pork ya",
            "pocco ya",
            "poco yah",
            "poco ya ya",
            "poco ya poco ya",
        ]
        for variant in ya_variants:
            self.assertEqual(spotter._match_text(variant, music_playing=True), "poco ya", variant)
            self.assertEqual(spotter._match_text(variant, music_playing=False), "poco ya", variant)
        for variant in ("포코박스", "포코 박스", "포코박서", "포코박쓰"):
            self.assertEqual(spotter._match_text(variant, music_playing=True), "poco box", variant)
            self.assertEqual(spotter._match_text(variant, music_playing=False), "poco box", variant)
        for variant in ("헤이 포코", "헤이포코", "안녕 포코", "안녕포코"):
            self.assertEqual(spotter._match_text(variant, music_playing=False), "hey poco", variant)

    def test_wake_variant_confidence_matches_normalized_phrase(self) -> None:
        import json as _json

        class _FakeRec:
            def __init__(self, is_final: bool, payload: dict) -> None:
                self.is_final = is_final
                self.payload = payload

            def AcceptWaveform(self, data: bytes) -> bool:
                return self.is_final

            def Result(self) -> str:
                return _json.dumps(self.payload)

            def PartialResult(self) -> str:
                return _json.dumps(self.payload)

            def Reset(self) -> None:
                pass

        with mock.patch.dict(os.environ, {"VOICE_WAKE_ENGINE": "off"}):
            spotter = VoskWakeWordSpotter(music_min_confidence=0.60)
        spotter.available = True
        spotter._rec = _FakeRec(
            True,
            {"text": "boco ya", "result": [{"word": "boco", "conf": 0.9}, {"word": "ya", "conf": 0.85}]},
        )
        chunk = b"\x00\x01" * 160
        self.assertEqual(spotter.accept_pcm16k(chunk, music_playing=True), "poco ya")

        spotter.reset()
        spotter._rec = _FakeRec(
            True,
            {"text": "boco ya", "result": [{"word": "boco", "conf": 0.3}, {"word": "ya", "conf": 0.4}]},
        )
        self.assertIsNone(spotter.accept_pcm16k(chunk, music_playing=True))

    def test_wake_music_min_conf_default_is_looser_than_quiet(self) -> None:
        with mock.patch.dict(os.environ, {"VOICE_WAKE_ENGINE": "off"}, clear=False):
            os.environ.pop("VOICE_WAKE_MUSIC_MIN_CONF", None)
            os.environ.pop("VOICE_WAKE_MIN_CONF", None)
            spotter = VoskWakeWordSpotter()
        self.assertEqual(spotter.music_min_confidence, 0.65)
        self.assertEqual(spotter.quiet_min_confidence, 0.60)

        with mock.patch.dict(os.environ, {"VOICE_WAKE_ENGINE": "off", "VOICE_WAKE_MIN_CONF": "0.65"}):
            spotter = VoskWakeWordSpotter()
        self.assertEqual(spotter.music_min_confidence, 0.65)
        self.assertEqual(spotter.quiet_min_confidence, 0.60)

    def test_wake_rms_window_preserves_speech_peak_across_kaldi_is_final_delay(self) -> None:
        with mock.patch.dict(os.environ, {"VOICE_WAKE_ENGINE": "off", "VOICE_USE_AEC3": "0", "VOICE_USE_RNNOISE": "0"}):
            listener = ContinuousVoiceListener(on_utterance=lambda b: None)
        listener.music_playing = True
        listener.wake_ambient_rms = 468.1

        # Simulate user shouting '포코야' 1.0s (10 chunks) ago, followed by 9 chunks of trailing silence
        # True speech peak: 2395.7 RMS. Trailing silence: ~35.0 RMS.
        chunks_rms = [35.0] * 5 + [2395.7, 1051.5, 186.8] + [35.0] * 12
        for r in chunks_rms:
            listener._wake_rms_window.append(r)

        # 20-chunk window covers the entire 2.0s buffer so speech peak is NOT lost
        recent_peak = max(listener._wake_rms_window)
        self.assertAlmostEqual(recent_peak, 2395.7)
        self.assertTrue(listener.passes_wake_energy_gate(recent_peak))

    def test_rms_fallback_suppressed_during_music_when_wake_active(self) -> None:
        with mock.patch.dict(os.environ, {"VOICE_WAKE_ENGINE": "off", "VOICE_USE_AEC3": "0", "VOICE_USE_RNNOISE": "0"}):
            listener = ContinuousVoiceListener(on_utterance=lambda b: None)
        listener.wake_spotter.available = True
        listener.music_playing = True
        self.assertFalse(listener.should_allow_rms_fallback())
        listener.music_playing = False
        self.assertTrue(listener.should_allow_rms_fallback())
        listener.music_playing = True
        listener.rms_fallback_during_music = True
        self.assertTrue(listener.should_allow_rms_fallback())

    def test_resample_stereo_pcm_and_aec3_graceful_fallback(self) -> None:
        # 480 stereo frames @ 48kHz (10ms) with L=2000, R=-4000 -> 160 stereo frames @ 16kHz
        stereo_48k = struct.pack("<" + "hh" * 480, *([2000, -4000] * 480))
        stereo_16k = resample_stereo_pcm(stereo_48k, src_rate=48000, dst_rate=16000)
        self.assertEqual(len(stereo_16k), 160 * 4)
        s = struct.unpack("<" + "hh" * 160, stereo_16k)
        self.assertEqual(s[0], 2000)
        self.assertEqual(s[1], -4000)

        # 44.1kHz -> 16kHz preserves stereo separation and expected frame count
        stereo_44k = struct.pack("<" + "hh" * 441, *([1500, -2500] * 441))
        stereo_16k_b = resample_stereo_pcm(stereo_44k, src_rate=44100, dst_rate=16000)
        self.assertEqual(len(stereo_16k_b), 160 * 4)

        # Without native .so, WebRTCAEC3Processor passes audio through untouched
        aec = WebRTCAEC3Processor(lib_path="/nonexistent/libpocobox_aec3.so")
        self.assertFalse(aec.available)
        cap = b"\x01\x02" * 160
        self.assertEqual(aec.process_chunk(cap, stereo_16k), cap)

    def test_decoy_phrases_rejected_and_wake_energy_gate_blocks_reverb(self) -> None:
        with mock.patch.dict(os.environ, {"VOICE_WAKE_ENGINE": "off"}):
            spotter = VoskWakeWordSpotter()
        # False-positive magnets must NEVER trigger (incl. low-precision 'hey poco' / 'poco box' kept as decoys)
        for decoy in ("boko ya", "coco ya", "ok coco", "hey coco", "coco box", "poker ya", "okay", "vocal", "local", "hey poco", "poco box"):
            self.assertIsNone(spotter._match_text(decoy, music_playing=True), f"decoy should not match: {decoy}")
            self.assertIsNone(spotter._match_text(decoy, music_playing=False), f"decoy should not match: {decoy}")
        # Legitimate wake phrase still matches
        self.assertEqual(spotter._match_text("poco ya", music_playing=True), "poco ya")
        # Korean '포코야' / '포코아' transcribed as 'poco a' matches 'poco ya', while 'poco a poco' remains a decoy
        self.assertEqual(spotter._match_text("poco a", music_playing=True), "poco ya")
        self.assertIsNone(spotter._match_text("poco a poco", music_playing=True))

        # Energy/SNR gate blocks weak residual music
        with mock.patch.dict(os.environ, {"VOICE_WAKE_ENGINE": "off", "VOICE_USE_AEC3": "0", "VOICE_USE_RNNOISE": "0"}):
            listener = ContinuousVoiceListener(on_utterance=lambda b: None)
        listener.music_playing = True
        listener.wake_ambient_rms = 300.0
        self.assertFalse(listener.passes_wake_energy_gate(174.5))
        self.assertFalse(listener.passes_wake_energy_gate(315.0))  # 315 < 300 * 1.08 = 324 and diff=15 < 25
        self.assertTrue(listener.passes_wake_energy_gate(450.0))

        # AEC3-active music playback loosens the gate further because echo is already cancelled (even at 2.0 dB)
        class _FakeAec:
            available = True
            active = True
            echo_reduction_db = 2.0

        listener.aec3 = _FakeAec()  # type: ignore[assignment]
        self.assertTrue(listener.passes_wake_energy_gate(320.0))  # >= 300 * 1.01 = 303 with AEC
        self.assertFalse(listener.passes_wake_energy_gate(302.0))  # < 303 and diff=2 < 8 even with AEC

    def test_telemetry_logging_clip_relabeling_and_recommendations(self) -> None:
        import tempfile
        from voice_control.telemetry import VoiceTelemetryLogger

        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "telemetry.jsonl"
            clips_dir = Path(td) / "clips"
            tel = VoiceTelemetryLogger(path=log_path, clips_dir=clips_dir)

            # Save a pending wake clip and relabel it to fp / tp
            wav = pcm_to_wav_bytes(_make_sine_pcm(1000.0, dur_sec=0.3, amp=4000))
            pending = tel.save_wake_clip(wav, "pending", {"phrase": "coco ya", "music_playing": True, "snr_ratio": 1.3})
            self.assertIsNotNone(pending)
            moved = tel.relabel_wake_clip(pending, "fp", {"fp_reason": "timeout_no_speech"})
            self.assertIsNotNone(moved)
            self.assertTrue(Path(moved).exists())
            self.assertEqual(Path(moved).parent.name, "fp")

            # Record synthetic TP/FP distribution and verify statistical analyzer + recommendations
            for _ in range(4):
                tel.record("wake_timeout_false_alarm", phrase="coco ya", music_playing=True, snr_ratio=1.3, confidence=0.65)
            for _ in range(3):
                tel.record("wake_command_ok", phrase="poco ya", music_playing=True, snr_ratio=2.4, confidence=0.88, aec3_active=True, aec3_reduction_db=18.5)
            tel.record("wake_rejected_energy", phrase="poco ya", music_playing=True, snr_ratio=0.7)

            # Save command clips and verify listing & pruning
            cmd_wav = pcm_to_wav_bytes(_make_sine_pcm(800.0, dur_sec=0.5, amp=5000))
            saved_cmd = tel.save_command_clip(cmd_wav, "now_playing", {"transcript": "지금 노래 뭐야", "ok": True})
            self.assertIsNotNone(saved_cmd)
            self.assertTrue(Path(saved_cmd).exists())
            clips = tel.list_command_clips(limit=10)
            self.assertEqual(len(clips), 1)
            self.assertEqual(clips[0]["action"], "now_playing")
            self.assertEqual(clips[0]["transcript"], "지금 노래 뭐야")
            self.assertTrue(clips[0]["wav_exists"])

            summary = tel.summarize(hours=24.0)
            self.assertEqual(summary["wake_true_positives"], 3)
            self.assertEqual(summary["wake_false_alarms"], 4)
            self.assertEqual(summary["wake_gates_rejected"], 1)
            self.assertAlmostEqual(summary["wake_precision"], 3 / 7, places=3)
            self.assertEqual(summary["by_phrase"]["coco ya"]["fp"], 4)
            self.assertTrue(any("coco ya" in r for r in summary["recommendations"]))
            self.assertTrue(any("VOICE_WAKE_SNR_RATIO" in r for r in summary["recommendations"]))

    def test_runtime_config_reset_defaults_restores_all_values(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "CONTINUOUS_LISTEN": "1",
                "VOICE_WAKE_ENGINE": "off",
                "VOICE_USE_AEC3": "0",
                "VOICE_USE_RNNOISE": "0",
                "VOICE_VAD_THRESHOLD": "40.0",
                "VOICE_PLAYING_VAD_THRESHOLD": "180.0",
                "VOICE_PLAYING_MULTIPLIER": "2.1",
                "VOICE_DUCK_VOLUME": "18",
                "VOICE_DUCK_ENABLED": "1",
                "REQUIRE_WAKE_WORD": "1",
                "VOICE_RMS_FALLBACK_DURING_MUSIC": "0",
            },
        ):
            ctrl = VoiceController()
        ctrl.update_runtime_config({
            "quiet_threshold": 320,
            "playing_threshold": 550,
            "multiplier": 4.2,
            "duck_volume": 45,
            "duck_enabled": False,
            "require_wake_word": False,
            "rms_fallback_during_music": True,
        })
        self.assertEqual(ctrl.vad_threshold, 320.0)
        self.assertFalse(ctrl.duck_enabled)
        self.assertTrue(ctrl.continuous_listener.rms_fallback_during_music)

        res = ctrl.update_runtime_config({"reset_defaults": True})
        self.assertTrue(res["ok"])
        self.assertTrue(res["reset"])
        self.assertEqual(ctrl.vad_threshold, 40.0)
        self.assertEqual(ctrl.duck_volume, 18)
        self.assertTrue(ctrl.duck_enabled)
        self.assertTrue(ctrl.require_wake_word)
        self.assertEqual(ctrl.continuous_listener.min_threshold, 40.0)
        self.assertEqual(ctrl.continuous_listener.playing_min_threshold, 180.0)
        self.assertAlmostEqual(ctrl.continuous_listener.playing_multiplier, 2.1)
        self.assertFalse(ctrl.continuous_listener.rms_fallback_during_music)

    def test_select_internal_speaker_sink_ignores_usb_dac_and_headphones(self) -> None:
        from voice_control.feedback import select_internal_speaker_sink

        sample = (
            "0\talsa_output.usb-Burr-Brown_from_TI_USB_Audio_DAC-00.analog-stereo\tmodule-alsa-card.c\ts16le 2ch 44100Hz\tIDLE\n"
            "1\talsa_output.platform-sound.Voice_Call__Headphones__sink\tmodule-alsa-card.c\ts16le 2ch 44100Hz\tSUSPENDED\n"
            "2\talsa_output.platform-sound.Voice_Call__Speaker__sink\tmodule-alsa-card.c\ts16le 2ch 48000Hz\tIDLE\n"
        )
        self.assertEqual(
            select_internal_speaker_sink(sample),
            "alsa_output.platform-sound.Voice_Call__Speaker__sink",
        )

    def test_speaker_and_voice_prefs_update_persist_and_reset(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            prefs_file = Path(td) / "voice_prefs.json"
            with mock.patch.dict(
                os.environ,
                {"CONTINUOUS_LISTEN": "0", "VOICE_PREFS_FILE": str(prefs_file)},
            ), mock.patch("voice_control.daemon.get_internal_speaker_volume", return_value=100), mock.patch(
                "voice_control.daemon.set_internal_speaker_volume", side_effect=lambda v: max(0, min(150, int(v)))
            ):
                ctrl = VoiceController()
                res = ctrl.update_runtime_config({
                    "speaker_volume": 75,
                    "chime_volume_pct": 40,
                    "tts_voice": "ko-KR-InJoonNeural",
                    "tts_rate_pct": 10,
                    "tts_pitch_hz": -3,
                    "enable_chime": False,
                    "enable_tts": True,
                })
                self.assertTrue(res["ok"])
                self.assertEqual(ctrl.speaker_volume, 75)
                self.assertEqual(ctrl.tts_voice, "ko-KR-InJoonNeural")
                self.assertEqual(ctrl.tts_rate_pct, 10)
                self.assertEqual(ctrl.tts_pitch_hz, -3)
                self.assertFalse(ctrl.enable_chime)
                self.assertTrue(prefs_file.exists())

                # Reload into a new controller to confirm persistence
                ctrl2 = VoiceController()
                self.assertEqual(ctrl2.speaker_volume, 75)
                self.assertEqual(ctrl2.tts_voice, "ko-KR-InJoonNeural")
                self.assertEqual(ctrl2.tts_rate_pct, 10)

                # Reset speaker/voice defaults
                r2 = ctrl2.update_runtime_config({"reset_speaker_defaults": True})
                self.assertTrue(r2["ok"])
                self.assertEqual(ctrl2.speaker_volume, 100)
                self.assertEqual(ctrl2.tts_rate_pct, 0)
                self.assertEqual(ctrl2.tts_pitch_hz, 0)
                self.assertTrue(ctrl2.enable_chime)

    def test_local_wake_verifier_phrase_matching_and_queue(self) -> None:
        from voice_control.continuous_listener import LocalWakeVerifier

        # Matching wake variants
        self.assertEqual(LocalWakeVerifier.match_wake_phrase("포코야."), "poco ya")
        self.assertEqual(LocalWakeVerifier.match_wake_phrase("코코야"), "poco ya")
        self.assertEqual(LocalWakeVerifier.match_wake_phrase("포코향"), "poco ya")
        self.assertEqual(LocalWakeVerifier.match_wake_phrase("포코아"), "poco ya")
        self.assertEqual(LocalWakeVerifier.match_wake_phrase("포코양"), "poco ya")
        self.assertEqual(LocalWakeVerifier.match_wake_phrase("뽀코야"), "poco ya")
        self.assertEqual(LocalWakeVerifier.match_wake_phrase("보코야"), "poco ya")
        self.assertEqual(LocalWakeVerifier.match_wake_phrase("포고야"), "poco ya")
        self.assertEqual(LocalWakeVerifier.match_wake_phrase("고야."), "poco ya")
        self.assertEqual(LocalWakeVerifier.match_wake_phrase("헤이 포코!"), "poco ya")
        self.assertEqual(LocalWakeVerifier.match_wake_phrase("hey poco!"), "poco ya")
        self.assertEqual(LocalWakeVerifier.match_wake_phrase("오케이 포코"), "poco ya")
        self.assertEqual(LocalWakeVerifier.match_wake_phrase("안녕 포코"), "poco ya")

        # Decoys and non-wake speech
        self.assertIsNone(LocalWakeVerifier.match_wake_phrase("안녕하세요"))
        self.assertIsNone(LocalWakeVerifier.match_wake_phrase("그."))
        self.assertIsNone(LocalWakeVerifier.match_wake_phrase("알았어"))
        self.assertIsNone(LocalWakeVerifier.match_wake_phrase("피아노 협주곡 1번"))
        self.assertIsNone(LocalWakeVerifier.match_wake_phrase("갈 거야"))
        self.assertIsNone(LocalWakeVerifier.match_wake_phrase("최고야"))
        self.assertIsNone(LocalWakeVerifier.match_wake_phrase("뭐야?"))
        self.assertIsNone(LocalWakeVerifier.match_wake_phrase("덥구야"))
        self.assertIsNone(LocalWakeVerifier.match_wake_phrase("복구요"))
        self.assertIsNone(LocalWakeVerifier.match_wake_phrase("바꾸요"))
        self.assertIsNone(LocalWakeVerifier.match_wake_phrase("카고야"))
        self.assertIsNone(LocalWakeVerifier.match_wake_phrase("그렇고 말고"))
        self.assertIsNone(LocalWakeVerifier.match_wake_phrase(""))
        self.assertIsNone(LocalWakeVerifier.match_wake_phrase(None))

        # Test queue submission and worker loop with mock STT
        wake_calls: list[tuple[str, str, float, float, float]] = []

        def on_wake(phrase: str, text: str, stt_ms: float, peak: float, amb: float) -> None:
            wake_calls.append((phrase, text, stt_ms, peak, amb))

        verifier = LocalWakeVerifier(
            stt_url="http://127.0.0.1:8766",
            enabled=True,
            on_wake=on_wake,
        )

        fake_resp = json.dumps({"text": "포코야."}).encode("utf-8")
        with mock.patch("urllib.request.urlopen") as mock_url:
            mock_cm = mock.MagicMock()
            mock_cm.read.return_value = fake_resp
            mock_url.return_value.__enter__.return_value = mock_cm

            # Submit 1600 samples (0.1s PCM)
            pcm = struct.pack("<160h", *([1000] * 160))
            submitted = verifier.submit(pcm, peak_rms=500.0, ambient_rms=50.0)
            self.assertTrue(submitted)

            # Wait briefly for worker loop to invoke callback
            time.sleep(0.1)

        self.assertGreaterEqual(len(wake_calls), 1)
        self.assertEqual(wake_calls[0][0], "poco ya")
        self.assertEqual(wake_calls[0][1], "포코야.")
        self.assertEqual(wake_calls[0][3], 500.0)

        verifier.close()

    def test_stt_path_bypasses_aec3_with_raw_bandpass(self) -> None:
        with mock.patch.dict(os.environ, {
            "VOICE_STT_USE_RAW_BANDPASS": "1",
            "VOICE_USE_AEC3": "1",
            "VOICE_USE_RNNOISE": "0",
            "VOICE_LOCAL_STT_WAKE": "0",
        }):
            listener = ContinuousVoiceListener(on_utterance=lambda b: None)

        self.assertTrue(listener.stt_use_raw_bandpass)

        # Mock proc with 48k stereo audio
        # 100ms = 5 subchunks of 3840 bytes (48k stereo)
        subchunk_bytes = 3840
        stereo_48k = struct.pack("<" + "hh" * 960, *([2000, 2000] * 960))
        full_chunk = stereo_48k * 5

        mock_proc = mock.MagicMock()
        mock_proc.stdout.read.side_effect = [stereo_48k, stereo_48k, stereo_48k, stereo_48k, stereo_48k, b""]

        raw_pcm, wake_pcm, stt_pcm = listener._read_clean_16k_chunk(mock_proc)
        self.assertEqual(len(raw_pcm), 3200)  # 1600 samples @ 16kHz mono = 100ms
        self.assertEqual(len(wake_pcm), 3200)
        self.assertEqual(len(stt_pcm), 3200)

        # With stt_use_raw_bandpass=True, stt_pcm is processed through stt_bandpass directly from clean16
        # without being touched by AEC3
        self.assertIsNotNone(listener.stt_bandpass)
        listener.stop()


if __name__ == "__main__":
    unittest.main()
