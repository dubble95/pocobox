#!/usr/bin/env python3
"""Offline wake-word regression & parameter-sweep runner for recorded PocoBox WAV clips.

Directory structure expected under --dir (auto-populated on pocobox at /srv/pocobox/logs/wake_clips):
  <dir>/tp/*.wav        -> Real user wake-word calls (MUST trigger -> True Positive)
  <dir>/fp/*.wav        -> False alarms during music/TV/noise (MUST NOT trigger -> True Negative)
  <dir>/rejected/*.wav  -> Near-misses blocked by energy/conf gates (optional negative set)

Examples:
  # Sync clips from pocobox and run regression test locally (if Vosk model exists):
  python3 tests/run_wake_regression.py --sync user@pocobox.local

  # Run directly on pocobox using its live Vosk model:
  ssh pocobox 'python3 /srv/pocobox/bin/run_wake_regression.py --dir /srv/pocobox/logs/wake_clips'

  # Grid-search optimal confirm_chunks & min_conf on collected clips:
  python3 tests/run_wake_regression.py --dir /srv/pocobox/logs/wake_clips --sweep
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(REPO_ROOT / "services"), "/srv/pocobox/bin"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from voice_control.audio_capture import CHUNK_BYTES, chunk_rms  # noqa: E402
from voice_control.wakeword import VoskWakeWordSpotter  # noqa: E402


def load_clip_pcm_and_meta(wav_path: Path) -> tuple[bytes, dict[str, Any]]:
    with wave.open(str(wav_path), "rb") as wf:
        if wf.getframerate() != 16000 or wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError(f"Unsupported WAV format in {wav_path}: {wf.getparams()}")
        pcm = wf.readframes(wf.getnframes())
    meta_path = wav_path.with_suffix(".json")
    meta: dict[str, Any] = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    return pcm, meta


def replay_clip(
    spotter: VoskWakeWordSpotter,
    pcm_bytes: bytes,
    *,
    music_playing: bool = True,
    min_rms: float = 55.0,
    snr_ratio: float = 1.25,
    ambient_rms: float | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    """Feed a 16kHz mono PCM clip chunk-by-chunk (100ms) through VoskWakeWordSpotter + energy gate."""
    spotter.reset()
    rms_window: list[float] = []
    est_amb = float(ambient_rms) if ambient_rms is not None else 20.0

    for offset in range(0, len(pcm_bytes), CHUNK_BYTES):
        chunk = pcm_bytes[offset : offset + CHUNK_BYTES]
        if len(chunk) < 320:
            continue
        r = chunk_rms(chunk)
        rms_window.append(r)
        if len(rms_window) > 6:
            rms_window.pop(0)
        is_silent = r < max(40.0, est_amb * 1.4)
        hit = spotter.accept_pcm16k(chunk, is_silent=is_silent, music_playing=music_playing)
        if hit:
            peak = max(rms_window) if rms_window else r
            if peak < min_rms:
                continue
            if music_playing and peak < est_amb * snr_ratio:
                continue
            return hit, spotter.last_event
        if ambient_rms is None:
            est_amb = est_amb * 0.90 + min(r, 2500.0) * 0.10
    return None, None


def evaluate_dataset(
    clips_dir: Path,
    spotter: VoskWakeWordSpotter,
    *,
    include_rejected_as_negative: bool = True,
    min_rms: float = 55.0,
    snr_ratio: float = 1.25,
) -> dict[str, Any]:
    tp_files = sorted((clips_dir / "tp").glob("*.wav")) if (clips_dir / "tp").is_dir() else []
    fp_files = sorted((clips_dir / "fp").glob("*.wav")) if (clips_dir / "fp").is_dir() else []
    if include_rejected_as_negative and (clips_dir / "rejected").is_dir():
        fp_files = sorted(fp_files + list((clips_dir / "rejected").glob("*.wav")))

    tp = fn = fp = tn = 0
    failures: list[dict[str, Any]] = []

    for wav_path in tp_files:
        pcm, meta = load_clip_pcm_and_meta(wav_path)
        hit, ev = replay_clip(
            spotter,
            pcm,
            music_playing=bool(meta.get("music_playing", False)),
            min_rms=min_rms,
            snr_ratio=snr_ratio,
            ambient_rms=meta.get("wake_ambient_rms"),
        )
        if hit:
            tp += 1
        else:
            fn += 1
            failures.append({"type": "FN_MISSED_WAKE", "file": wav_path.name, "meta": meta})

    for wav_path in fp_files:
        pcm, meta = load_clip_pcm_and_meta(wav_path)
        hit, ev = replay_clip(
            spotter,
            pcm,
            music_playing=bool(meta.get("music_playing", True)),
            min_rms=min_rms,
            snr_ratio=snr_ratio,
            ambient_rms=meta.get("wake_ambient_rms"),
        )
        if hit:
            fp += 1
            failures.append({"type": "FP_FALSE_ALARM", "file": wav_path.name, "triggered": hit, "event": ev, "meta": meta})
        else:
            tn += 1

    recall = round(tp / float(tp + fn), 4) if (tp + fn) > 0 else None
    precision = round(tp / float(tp + fp), 4) if (tp + fp) > 0 else None
    far = round(fp / float(fp + tn), 4) if (fp + tn) > 0 else None

    return {
        "clips_dir": str(clips_dir),
        "positive_clips": len(tp_files),
        "negative_clips": len(fp_files),
        "tp": tp,
        "fn": fn,
        "fp": fp,
        "tn": tn,
        "recall": recall,
        "precision": precision,
        "false_alarm_rate": far,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay recorded PocoBox wake clips as regression tests")
    parser.add_argument("--dir", default=str(REPO_ROOT / "tests" / "fixtures" / "wake_regression"))
    parser.add_argument("--sync", help="SSH target (e.g. user@pocobox.local) to pull /srv/pocobox/logs/wake_clips from")
    parser.add_argument("--model", default=None, help="Path to Vosk model directory")
    parser.add_argument("--min-rms", type=float, default=55.0)
    parser.add_argument("--snr-ratio", type=float, default=1.25)
    parser.add_argument("--sweep", action="store_true", help="Grid-search confirm_chunks and min_conf")
    parser.add_argument("--max-fp", type=int, default=None, help="Exit non-zero if FP exceeds this count")
    args = parser.parse_args()

    clips_dir = Path(args.dir)
    if args.sync:
        clips_dir.mkdir(parents=True, exist_ok=True)
        print(f"==> Syncing wake clips from {args.sync}:/srv/pocobox/logs/wake_clips/ -> {clips_dir}/")
        subprocess.run(
            ["scp", "-r", f"{args.sync}:/srv/pocobox/logs/wake_clips/.", str(clips_dir)],
            check=False,
        )

    spotter = VoskWakeWordSpotter(model_dir=args.model)
    if not spotter.available:
        print(f"ERROR: Vosk model not available at {spotter.model_dir}. Run on pocobox or pass --model.", file=sys.stderr)
        return 2

    if args.sweep:
        print("confirm\tmin_conf\tsnr\tTP\tFN\tFP\tTN\tRecall\tPrecision")
        for confirm in (2, 3, 4):
            for conf in (0.60, 0.70, 0.78, 0.85):
                for snr in (1.10, 1.25, 1.50):
                    spotter.music_confirm_chunks = confirm
                    spotter.music_min_confidence = conf
                    res = evaluate_dataset(clips_dir, spotter, min_rms=args.min_rms, snr_ratio=snr)
                    print(
                        f"{confirm}\t{conf:.2f}\t\t{snr:.2f}\t{res['tp']}\t{res['fn']}\t{res['fp']}\t{res['tn']}\t"
                        f"{res['recall']}\t{res['precision']}"
                    )
        return 0

    res = evaluate_dataset(clips_dir, spotter, min_rms=args.min_rms, snr_ratio=args.snr_ratio)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if args.max_fp is not None and res["fp"] > args.max_fp:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
