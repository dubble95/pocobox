"""Structured JSONL telemetry logger and statistical wake-word analyzer for PocoBox.

Records wake-word triggers, gate rejections, false alarms (ducked with no valid command),
true positives, clipping warnings, and AEC3 performance so thresholds, trigger phrases,
and preamp gains can be tuned statistically from real-world living-room data.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

log = logging.getLogger("pocobox-voice.telemetry")

_KST = datetime.timezone(datetime.timedelta(hours=9))
DEFAULT_TELEMETRY_PATH = "/srv/pocobox/logs/voice_telemetry.jsonl"
DEFAULT_WAKE_CLIPS_DIR = "/srv/pocobox/logs/wake_clips"
MAX_LOG_BYTES = 10 * 1024 * 1024  # 10 MB rotation threshold


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = (len(s) - 1) * (pct / 100.0)
    lo = int(idx)
    hi = min(len(s) - 1, lo + 1)
    frac = idx - lo
    return round(s[lo] + (s[hi] - s[lo]) * frac, 3)


class VoiceTelemetryLogger:
    """Thread-safe JSONL event recorder with automatic size rotation."""

    def __init__(
        self,
        path: str | Path | None = None,
        clips_dir: str | Path | None = None,
        max_bytes: int = MAX_LOG_BYTES,
    ) -> None:
        env_path = os.environ.get("VOICE_TELEMETRY_LOG", DEFAULT_TELEMETRY_PATH).strip()
        env_clips = os.environ.get("VOICE_WAKE_CLIPS_DIR", DEFAULT_WAKE_CLIPS_DIR).strip()
        self.path = Path(path) if path is not None else Path(env_path)
        self.clips_dir = Path(clips_dir) if clips_dir is not None else Path(env_clips)
        self.max_bytes = max_bytes
        self.max_clip_files = max(10, int(os.environ.get("VOICE_WAKE_CLIP_MAX_FILES", "200")))
        self.save_clips = os.environ.get("VOICE_SAVE_WAKE_CLIPS", "1").strip().lower() not in ("0", "false", "no", "off")
        self._lock = threading.Lock()
        self.enabled = os.environ.get("VOICE_TELEMETRY_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
        self._disabled_warned = False

    def _prune_clips_unlocked(self) -> None:
        """Keep total saved wake WAV clips <= max_clip_files by deleting oldest pairs."""
        try:
            wavs = sorted(self.clips_dir.rglob("*.wav"), key=lambda p: p.stat().st_mtime)
        except OSError:
            return
        excess = len(wavs) - self.max_clip_files
        for old_wav in wavs[: max(0, excess)]:
            try:
                old_wav.unlink(missing_ok=True)
                old_wav.with_suffix(".json").unlink(missing_ok=True)
            except OSError:
                pass

    def save_wake_clip(self, wav_bytes: bytes, label: str, meta: dict[str, Any]) -> str | None:
        """Save a 16kHz wake-window WAV + JSON metadata pair for offline regression testing."""
        if not self.enabled or not self.save_clips or not wav_bytes:
            return None
        safe_label = "".join(c for c in label.lower() if c.isalnum() or c in ("_", "-")) or "unknown"
        now = time.time()
        stamp = datetime.datetime.fromtimestamp(now, tz=_KST).strftime("%Y%m%d_%H%M%S")
        ms = int((now % 1.0) * 1000)
        phrase_slug = "".join(c for c in str(meta.get("phrase", "wake")).lower().replace(" ", "_") if c.isalnum() or c == "_")[:24]
        stem = f"{stamp}_{ms:03d}_{phrase_slug or 'wake'}"
        target_dir = self.clips_dir / safe_label
        wav_path = target_dir / f"{stem}.wav"
        meta_path = target_dir / f"{stem}.json"

        payload = {
            "ts": round(now, 3),
            "iso": datetime.datetime.fromtimestamp(now, tz=_KST).isoformat(timespec="seconds"),
            "label": safe_label,
            **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in meta.items() if v is not None},
        }
        with self._lock:
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                wav_path.write_bytes(wav_bytes)
                meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                self._prune_clips_unlocked()
                return str(wav_path)
            except OSError as exc:
                if not self._disabled_warned:
                    log.warning("Cannot save wake regression clip %s: %s", wav_path, exc)
                    self._disabled_warned = True
                return None

    def relabel_wake_clip(self, clip_path: str | None, new_label: str, extra_meta: dict[str, Any] | None = None) -> str | None:
        """Move a pending wake clip into its verified ground-truth folder (`tp` or `fp`)."""
        if not clip_path:
            return None
        src_wav = Path(clip_path)
        src_meta = src_wav.with_suffix(".json")
        safe_label = "".join(c for c in new_label.lower() if c.isalnum() or c in ("_", "-")) or "unknown"
        dst_dir = self.clips_dir / safe_label
        dst_wav = dst_dir / src_wav.name
        dst_meta = dst_dir / src_meta.name
        with self._lock:
            try:
                if not src_wav.exists():
                    return None
                dst_dir.mkdir(parents=True, exist_ok=True)
                src_wav.replace(dst_wav)
                meta_obj: dict[str, Any] = {}
                if src_meta.exists():
                    try:
                        meta_obj = json.loads(src_meta.read_text(encoding="utf-8"))
                    except Exception:
                        meta_obj = {}
                    src_meta.unlink(missing_ok=True)
                meta_obj["label"] = safe_label
                if extra_meta:
                    for k, v in extra_meta.items():
                        if v is not None:
                            meta_obj[k] = round(v, 4) if isinstance(v, float) else v
                dst_meta.write_text(json.dumps(meta_obj, ensure_ascii=False, indent=2), encoding="utf-8")
                return str(dst_wav)
            except OSError:
                return None

    def record(self, event: str, **fields: Any) -> dict[str, Any]:
        now = time.time()
        entry: dict[str, Any] = {
            "ts": round(now, 3),
            "iso": datetime.datetime.fromtimestamp(now, tz=_KST).isoformat(timespec="seconds"),
            "event": event,
        }
        for k, v in fields.items():
            if v is not None:
                entry[k] = round(v, 4) if isinstance(v, float) else v

        if not self.enabled:
            return entry

        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
                    rotated = self.path.with_suffix(self.path.suffix + ".1")
                    self.path.replace(rotated)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
            except OSError as exc:
                if not self._disabled_warned:
                    log.warning("Cannot write telemetry log %s: %s", self.path, exc)
                    self._disabled_warned = True
        return entry

    def read_recent(self, limit: int = 100, since_ts: float | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            with self.path.open("r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if since_ts is not None and float(obj.get("ts", 0)) < since_ts:
                        continue
                    rows.append(obj)
        except OSError:
            return []
        return rows[-limit:] if limit > 0 else rows

    def summarize(self, hours: float = 24.0) -> dict[str, Any]:
        since_ts = time.time() - hours * 3600.0 if hours > 0 else None
        events = self.read_recent(limit=0, since_ts=since_ts)
        return summarize_events(events, window_hours=hours, log_path=str(self.path))


def summarize_events(
    events: list[dict[str, Any]],
    window_hours: float = 24.0,
    log_path: str = "",
) -> dict[str, Any]:
    """Compute precision, per-phrase false-positive stats, acoustic distributions, and tuning hints."""
    counts: dict[str, int] = defaultdict(int)
    phrase_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "rejected": 0})
    mode_stats = {
        "music": {"tp": 0, "fp": 0, "rejected": 0},
        "quiet": {"tp": 0, "fp": 0, "rejected": 0},
    }

    tp_snr: list[float] = []
    fp_snr: list[float] = []
    tp_conf: list[float] = []
    fp_conf: list[float] = []
    aec_db_vals: list[float] = []
    clip_count = 0

    for ev in events:
        etype = str(ev.get("event", ""))
        counts[etype] += 1
        phrase = str(ev.get("phrase", "") or "")
        mode = "music" if ev.get("music_playing") else "quiet"
        snr = ev.get("snr_ratio")
        conf = ev.get("confidence")
        aec_db = ev.get("aec3_reduction_db")

        if isinstance(aec_db, (int, float)) and ev.get("aec3_active"):
            aec_db_vals.append(float(aec_db))

        if etype == "clip_detected":
            clip_count += 1
        elif etype == "wake_command_ok":
            mode_stats[mode]["tp"] += 1
            if phrase:
                phrase_stats[phrase]["tp"] += 1
            if isinstance(snr, (int, float)):
                tp_snr.append(float(snr))
            if isinstance(conf, (int, float)):
                tp_conf.append(float(conf))
        elif etype in ("wake_timeout_false_alarm", "wake_command_empty"):
            mode_stats[mode]["fp"] += 1
            if phrase:
                phrase_stats[phrase]["fp"] += 1
            if isinstance(snr, (int, float)):
                fp_snr.append(float(snr))
            if isinstance(conf, (int, float)):
                fp_conf.append(float(conf))
        elif etype in ("wake_rejected_energy", "wake_rejected_conf"):
            mode_stats[mode]["rejected"] += 1
            if phrase:
                phrase_stats[phrase]["rejected"] += 1

    total_tp = mode_stats["music"]["tp"] + mode_stats["quiet"]["tp"]
    total_fp = mode_stats["music"]["fp"] + mode_stats["quiet"]["fp"]
    total_rejected = mode_stats["music"]["rejected"] + mode_stats["quiet"]["rejected"]
    precision = round(total_tp / float(total_tp + total_fp), 4) if (total_tp + total_fp) > 0 else None

    per_phrase_out: dict[str, dict[str, Any]] = {}
    for p, st in sorted(phrase_stats.items(), key=lambda kv: (kv[1]["fp"], kv[1]["rejected"]), reverse=True):
        denom = st["tp"] + st["fp"]
        per_phrase_out[p] = {
            **st,
            "precision": round(st["tp"] / float(denom), 3) if denom > 0 else None,
        }

    # Generate concrete data-driven tuning recommendations
    recommendations: list[str] = []
    for p, st in per_phrase_out.items():
        if st["fp"] >= 3 and (st["precision"] is not None and st["precision"] < 0.35):
            recommendations.append(
                f"호출어 '{p}'의 오탐이 많음 (정탐 {st['tp']} / 오탐 {st['fp']}, 정밀도 {st['precision']*100:.0f}%) -> 트리거 화이트리스트에서 제외 검토"
            )
    fp_snr_p90 = _percentile(fp_snr, 90)
    tp_snr_p10 = _percentile(tp_snr, 10)
    if fp_snr_p90 is not None and tp_snr_p10 is not None and fp_snr_p90 < tp_snr_p10:
        suggested_snr = round((fp_snr_p90 + tp_snr_p10) / 2.0, 2)
        recommendations.append(
            f"오탐 SNR p90({fp_snr_p90}) < 정탐 SNR p10({tp_snr_p10}) -> VOICE_WAKE_SNR_RATIO={suggested_snr} 상향 권장"
        )
    fp_conf_p90 = _percentile(fp_conf, 90)
    tp_conf_p10 = _percentile(tp_conf, 10)
    if fp_conf_p90 is not None and tp_conf_p10 is not None and fp_conf_p90 < tp_conf_p10:
        suggested_conf = round((fp_conf_p90 + tp_conf_p10) / 2.0, 2)
        recommendations.append(
            f"오탐 신뢰도 p90({fp_conf_p90}) < 정탐 신뢰도 p10({tp_conf_p10}) -> VOICE_WAKE_MIN_CONF={suggested_conf} 조정 권장"
        )
    if clip_count >= 5:
        recommendations.append(
            f"마이크 하드웨어 클리핑 {clip_count}회 감지 -> WCD9340 DEC7 게인을 현재(94)보다 4~6dB 낮추는 것 권장"
        )
    if not recommendations:
        recommendations.append("현재 수집된 데이터 기준 특이 오탐 패턴 없음 (데이터가 더 쌓이면 자동 제안이 표시됩니다).")

    return {
        "window_hours": window_hours,
        "log_path": log_path,
        "total_events": len(events),
        "wake_true_positives": total_tp,
        "wake_false_alarms": total_fp,
        "wake_gates_rejected": total_rejected,
        "wake_precision": precision,
        "by_mode": mode_stats,
        "by_phrase": per_phrase_out,
        "event_counts": dict(counts),
        "distributions": {
            "tp_snr_p10": tp_snr_p10,
            "tp_snr_p50": _percentile(tp_snr, 50),
            "fp_snr_p50": _percentile(fp_snr, 50),
            "fp_snr_p90": fp_snr_p90,
            "tp_conf_p10": tp_conf_p10,
            "tp_conf_p50": _percentile(tp_conf, 50),
            "fp_conf_p50": _percentile(fp_conf, 50),
            "fp_conf_p90": fp_conf_p90,
            "aec3_reduction_db_p50": _percentile(aec_db_vals, 50),
            "aec3_reduction_db_p90": _percentile(aec_db_vals, 90),
            "clip_events": clip_count,
        },
        "recommendations": recommendations,
    }


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Analyze PocoBox voice wake-word telemetry JSONL")
    parser.add_argument("--log", default=os.environ.get("VOICE_TELEMETRY_LOG", DEFAULT_TELEMETRY_PATH))
    parser.add_argument("--hours", type=float, default=24.0)
    args = parser.parse_args()
    logger = VoiceTelemetryLogger(path=args.log)
    print(json.dumps(logger.summarize(hours=args.hours), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
