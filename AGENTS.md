# AGENTS.md — PocoBox Developer & Agent Guide

## Overview

**PocoBox** turns a **Xiaomi POCO F1 (`xiaomi-beryllium`)** running **postmarketOS v26.06** (Alpine Linux / `musl` libc, `aarch64`, Snapdragon 845, 5.4 GB RAM, `systemd`) into an always-on smart music box and bilingual English listening quiz assistant backed by **Lyrion Music Server (LMS · TIDAL)**, automatic **USB DAC hotplug playback**, and a **local-first hybrid voice pipeline**.

- SSH target: `ssh pocobox` (`pocobox.local` via mDNS)
- Local development host: macOS (deploys to `pocobox` via `./scripts/deploy.sh`)

---

## Repository Structure

```text
pocobox/
├── services/
│   ├── voice_control/          # Main always-on voice, TTS, quiz & display daemon
│   │   ├── daemon.py           # HTTP API (:8765), SSE subtitles, hardware button trigger
│   │   ├── adaptive_learning.py # Persistent local Korean ASR correction learner
│   │   ├── continuous_listener.py  # 24/7 mic capture, AEC3, RNNoise, Vosk wake-word, dynamic VAD
│   │   ├── cloud_stt_intent.py # Local-first SenseVoice STT + rule fast-path + Gemini/OpenAI fallback
│   │   ├── rule_parser.py      # Sub-ms offline Korean/English command & time/date parser
│   │   ├── aliases.py          # Wake prefixes, ASR normalizations, K-pop/artist/track TIDAL aliases
│   │   ├── executor.py         # Executes VoiceIntent against LMS/TIDAL or spoken Q&A
│   │   ├── english_quiz.py     # Bilingual listening quiz engine & offline question bank
│   │   ├── tts.py              # Edge Neural TTS + Google TTS fallback (built-in speaker only)
│   │   ├── audio_dsp.py        # Anti-aliased 48k->16k decimator, bandpass, RNNoise wrapper
│   │   ├── audio_aec.py        # Decoupled WebRTC AEC3 + Squeezelite VISEXPORT shm reader
│   │   ├── wakeword.py         # Local Vosk Kaldi grammar wake-word spotter
│   │   ├── display_ui.py       # Kiosk Now-Playing + live subtitle UI (/display)
│   │   └── dashboard_ui.py     # Live mic/VAD/AEC tuning & telemetry UI (/dashboard)
│   ├── lms_client/             # LMS JSON-RPC + TIDAL smart search/playback CLI & client
│   └── usb_dac_autoplay/       # USB DAC hotplug watcher + direct ALSA switching & auto-resume
├── infra/
│   ├── stt/                    # Local SenseVoice-Small INT8 Docker service (127.0.0.1:8766)
│   ├── lms/                    # Lyrion Music Server Docker Compose (:9000)
│   ├── voice/                  # pocobox-voice.service, voice.env.example, Phosh .desktop
│   ├── audio/                  # Headless PulseAudio service & WebRTC AEC3 C++ wrapper
│   ├── squeezelite/            # Native ARM64 Squeezelite build script & service
│   ├── usb-dac/                # USB-C Host DTB patch/restore scripts & DAC autoplay service
│   ├── display/                # greetd kiosk config & backlight service
│   └── system/                 # Wi-Fi no-powersave, no-suspend, nftables firewall rules
├── scripts/
│   └── deploy.sh               # One-command full stack deployment to pocobox
└── tests/
    ├── dataset/voice_commands_ko_en.json   # 84 Korean/English command regression dataset
    ├── dataset/korean_asr_corrections.json # Confirmed Korean ASR correction regression cases
    ├── fixtures/audio/*.wav    # 16kHz mono PCM16 Korean/English audio fixtures
    ├── test_*.py               # Offline unittest suite (run before every commit/deploy)
    ├── test_adaptive_learning.py # Local ASR learning, persistence, and safety tests
    ├── run_voice_benchmark.py  # Live TIDAL accuracy benchmark
    └── run_wake_regression.py  # Wake-word false-positive/true-positive regression runner
```

---

## Runtime Architecture on `pocobox`

| Component | Runtime / Address | Role |
|---|---|---|
| `pocobox-lms` | Docker (`network_mode: host`, `:9000`) | Lyrion Music Server + TIDAL plugin |
| `pocobox-stt` | Docker (`network_mode: host`, `cpuset: 4-7`, `127.0.0.1:8766`) | Local `sherpa-onnx` SenseVoice-Small INT8 STT (~150–330ms, ~385MB RAM) |
| `pocobox-voice.service` | systemd (`:8765`) | Voice daemon, continuous listener, Kiosk UI (`/display`), Dashboard (`/dashboard`) |
| `pocobox-squeezelite.service` | systemd (native ARM64) | Audio player outputting directly to USB DAC ALSA (`VISEXPORT` shm for AEC3 ref) |
| `pocobox-dac-autoplay.service` | systemd | Hotplug USB DAC switch + auto-resume / pause on detach |
| `pocobox-pulseaudio.service` | systemd (user 10000) | Headless PulseAudio strictly for mic capture & built-in speaker chimes/TTS |

### Voice Latency & Routing Flow
1. **Wake word**: Local streaming Vosk grammar (`vosk-model-small-en-us-0.15`) on AEC3 + bandpass audio → triggers instant LMS auto-ducking (`18%`) + chime.
2. **Command capture**: Dynamic post-duck VAD floor (`wake_cmd_floor`, `VOICE_WAKE_SILENCE_SEC=0.7`) ends recording ~0.7s after speech stops.
3. **Local-First STT (`LOCAL_STT_URL=http://127.0.0.1:8766`)**: SenseVoice-Small INT8 (`lang=ko`, auto-handles Korean + English + BGM/nospeech) transcribes in ~0.15–0.33s. Repeated phonetic user corrections are persisted locally and applied before intent parsing.
4. **Wake-word during music**: Vosk grammar accepts `포코야`/`poco ya` plus Korean ASR variants (`boco ya`, `poko ya`, `poco yah`, `poco ya ya`) and normalizes them before matching. During playback, AEC3-cancelled audio loosens the wake energy/SNR gate; `hey poco` stays stricter to avoid vocal false-triggers.
5. **Offline Rule Fast-Path (`rule_parser.py`)**: Playback controls, volume, TIDAL search/mix, KST time/date, and quiz start/stop execute in <1ms with **zero cloud API calls**. The adaptive learner never participates in blind quiz STT.
5. **Optional Cloud Text Fallback (`VOICE_CLOUD_FALLBACK=1`)**: Only general Q&A (`answer_qa`) or unrecognized utterances escalate **text-only** to Gemini/OpenAI (with HTTP keep-alive & 503 retry).

---

## Common Commands

```sh
# 1. Run full offline unit test suite (ALWAYS run before committing or deploying)
python3 -m unittest discover -s tests -p "test_*.py" -v

# 2. Deploy all services, configs, and STT container to pocobox
./scripts/deploy.sh pocobox

# 3. Check live service health & local STT status
curl -s http://pocobox.local:8765/health | python3 -m json.tool

# 4. Test voice pipeline live (dry-run audio or text)
curl -s -X POST "http://pocobox.local:8765/voice/audio?dry_run=1" \
  -H "Content-Type: audio/wav" --data-binary @tests/fixtures/audio/ko_next.wav
ssh pocobox 'pocobox-voice say "아이유 밤편지 틀어줘"'

# 5. Run live E2E integration tests against pocobox
POCOBOX_LIVE=1 python3 -m unittest tests/test_live_pocobox_e2e.py -v

# 6. Inspect device logs (filtering out dashboard polling noise)
ssh pocobox 'journalctl -u pocobox-voice -n 80 --no-pager | grep -v "HTTP "'
ssh pocobox 'docker logs --tail 50 pocobox-stt'
```

---

## Critical Gotchas & Constraints

1. **Alpine (`musl`) + Docker `network: host`**:
   - `pocobox` runs postmarketOS/Alpine (`musl` libc, Python 3.14). Prebuilt glibc wheels (`sherpa-onnx`, `onnxruntime`) must run in Debian-based Docker containers (`infra/stt/`).
   - Host `nftables` blocks Docker bridge DNS. **Always** set both `build.network: host` and `network_mode: host` in Docker Compose files.
2. **Snapdragon 845 big.LITTLE cores**:
   - `cpu0-3` = Kryo-3XX-Silver (1.77 GHz); `cpu4-7` = Kryo-3XX-Gold (2.65 GHz, ARMv8.2 FP16). Pin latency-sensitive inference containers to `cpuset: "4-7"`.
3. **SenseVoice Model Version & Language**:
   - Use **`sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17`** with `STT_LANG=ko` (accurately handles both Korean and English while avoiding Japanese mis-tags on short Korean words).
   - **Do NOT** use `2025-09-09` — it is a Cantonese (`yue`) fine-tune that breaks Korean recognition.
4. **Audio Path Isolation**:
   - Never route Squeezelite's USB DAC playback through PulseAudio; Squeezelite uses direct ALSA (`plughw:CARD=...,DEV=0`), while chimes and TTS must stay isolated on POCO F1's built-in speaker sink (`resolve_internal_speaker_sink`).
   - Echo cancellation is decoupled via Squeezelite's `VISEXPORT` shared memory and `libpocobox_aec3.so`.
5. **Post-Wake Dynamic VAD**:
   - Even with music ducked to 18%, living-room mic RMS stays ~60–100+. Never replace `wake_cmd_floor` with a low static threshold (`40.0`), or every wake command will stall until the 4.8s max timeout.
6. **Blind Quiz STT (Anti-Hallucination)**:
   - `transcribe_blind_audio()` must remain strictly blind to expected quiz answers so background noise or gibberish is never hallucinated into a correct answer.
7. **`/tmp` Is RAM (`tmpfs`) on `pocobox`**:
   - Clean up temporary benchmark/download artifacts under `/tmp` on the device; store persistent models under `/srv/pocobox/models/`.
8. **Secrets & Environment**:
   - Live device secrets live in `/etc/pocobox/voice.env` (mode `0600`). Never commit real API keys or print them in logs; keep defaults documented in `infra/voice/voice.env.example`.
   - `HybridVoiceInterpreter` constructor defaults should remain test-safe (do not make network calls when `LOCAL_STT_URL` is unset in unit tests).

---

## Coding & Commit Conventions

- Prefer Python standard library where practical on the host (`pocobox` Python 3.14); keep optional third-party imports (`requests`, `vosk`, `edge_tts`) gracefully guarded.
- Follow **Conventional Commits** matching git history: `feat(voice): ...`, `fix(audio): ...`, `feat(quiz): ...`, `docs: ...`.
- Add unit tests in `tests/` for any new parser rules, STT routing, VAD logic, or quiz behavior, and verify `python3 -m unittest discover -s tests -p "test_*.py" -v` passes 100% before committing or deploying.
- **Always commit, push, and deploy after completing each feature or fix unit** (once tests pass), keeping each commit focused on one logical unit of work. Run `./scripts/deploy.sh pocobox` after pushing so the device always runs the latest tested code.
