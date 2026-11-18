#!/usr/bin/env bash
set -euo pipefail

# Generate realistic 16kHz mono 16-bit PCM WAV voice command samples on macOS using built-in TTS.
OUT_DIR="$(cd "$(dirname "$0")" && pwd)/fixtures/audio"
mkdir -p "${OUT_DIR}"

if ! command -v say >/dev/null 2>&1 || ! command -v afconvert >/dev/null 2>&1; then
  echo "This helper requires macOS 'say' and 'afconvert'." >&2
  exit 1
fi

gen_sample() {
  local name="$1"
  local voice="$2"
  local text="$3"
  local tmp_aiff="/tmp/pocobox_tts_${name}.aiff"
  say -v "${voice}" -o "${tmp_aiff}" "${text}"
  afconvert -f WAVE -d LEI16@16000 -c 1 "${tmp_aiff}" "${OUT_DIR}/${name}.wav"
  rm -f "${tmp_aiff}"
  echo "Generated ${OUT_DIR}/${name}.wav  (\"${text}\")"
}

gen_sample "ko_iu_through_the_night" "Yuna" "포코야 아이유 밤편지 틀어줘"
gen_sample "ko_coldplay"             "Yuna" "콜드플레이 노래 틀어줘"
gen_sample "ko_jazz"                 "Yuna" "재즈 틀어줘"
gen_sample "ko_tidal_mix"            "Yuna" "마이믹스 틀어줘"
gen_sample "ko_next"                 "Yuna" "다음 곡"
gen_sample "ko_pause"                "Yuna" "잠깐 멈춰줘"
gen_sample "ko_volume_50"            "Yuna" "볼륨 50으로 맞춰줘"
gen_sample "en_radiohead"            "Samantha" "Hey Poco, play Radiohead"

echo "==> Validating all generated WAV files with PocoBox VAD..."
python3 - "${OUT_DIR}" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).resolve().parents[2] / "services"))
from voice_control.audio_capture import analyze_wav_bytes

out_dir = Path(sys.argv[1])
all_ok = True
for wav in sorted(out_dir.glob("*.wav")):
    m = analyze_wav_bytes(wav.read_bytes())
    status = "PASS" if m.has_speech else "FAIL"
    print(f"  [{status}] {wav.name:<30} dur={m.duration_sec:>4.2f}s rms={m.rms_mean:>6.1f} peak={m.rms_peak:>7.1f} speech_chunks={m.speech_chunks}/{m.total_chunks}")
    if not m.has_speech:
        all_ok = False
sys.exit(0 if all_ok else 1)
PY
