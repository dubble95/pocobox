#!/usr/bin/env python3
"""CLI wrapper for PocoBox Voice Control (works locally via daemon HTTP API or direct execution)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _request(url: str, data: bytes | None = None, content_type: str = "application/json", timeout: float = 25.0) -> tuple[int, dict]:
    headers = {"Content-Type": content_type} if data is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except Exception:
            return exc.code, {"ok": False, "error": body}


def _download_binary(url: str, timeout: float = 30.0) -> tuple[int, bytes]:
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def main() -> int:
    default_port = os.environ.get("VOICE_HTTP_PORT", "8765")
    parser = argparse.ArgumentParser(description="PocoBox Voice Control CLI")
    parser.add_argument("--url", default=f"http://127.0.0.1:{default_port}", help="Voice daemon URL")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Show voice daemon health and configuration")

    p_say = sub.add_parser("say", help="Send a text voice command")
    p_say.add_argument("text")
    p_say.add_argument("--dry-run", action="store_true")
    p_say.add_argument("--no-cloud", action="store_true")

    p_wav = sub.add_parser("wav", help="Send a WAV audio file to the voice pipeline")
    p_wav.add_argument("path")
    p_wav.add_argument("--dry-run", action="store_true")

    p_listen = sub.add_parser("listen", help="Trigger microphone recording and execute command")
    p_listen.add_argument("--dry-run", action="store_true")

    p_quiz = sub.add_parser("quiz", help="Control the kids/beginner English listening quiz")
    q_sub = p_quiz.add_subparsers(dest="quiz_cmd", required=True)
    q_start = q_sub.add_parser("start", help="Start an English listening quiz")
    q_start.add_argument("--topic", default="all", help="Topic: all|animals|food|colors_numbers|family_school|daily|riddles")
    q_start.add_argument("--count", type=int, default=3, help="Number of questions (1-10)")
    q_start.add_argument("--voice", action="store_true", help="Run hands-free voice conversation loop")
    q_start.add_argument("--skip-if-active", action="store_true", help="Skip if quiz is already running")
    q_start.add_argument("--dry-run", action="store_true")
    q_ans = q_sub.add_parser("answer", help="Submit a text answer or quiz command (다시/힌트/패스/그만)")
    q_ans.add_argument("text")
    q_ans.add_argument("--dry-run", action="store_true")
    q_ans.add_argument("--no-cloud", action="store_true")
    q_sub.add_parser("stop", help="Stop the current quiz")
    q_sub.add_parser("status", help="Show current quiz state and score")
    q_sub.add_parser("schedule", help="Show scheduled daily morning quiz status and history")
    q_daily = q_sub.add_parser("daily", help="Trigger scheduled daily morning quiz now")
    q_daily.add_argument("--force", action="store_true", help="Force start even if quiz is already running")
    q_daily.add_argument("--dry-run", action="store_true")

    p_learn = sub.add_parser("learning", help="Manage Korean adaptive ASR corrections")
    l_sub = p_learn.add_subparsers(dest="learning_cmd", required=True)
    l_sub.add_parser("status", help="Show adaptive learning statistics")
    l_sub.add_parser("list", help="List confirmed learned corrections")
    l_forget = l_sub.add_parser("forget", help="Remove a learned correction rule")
    l_forget.add_argument("source", help="Source misrecognized phrase to remove")

    p_hist = sub.add_parser("history", help="Inspect and replay 10-minute tmpfs rolling audio history")
    h_sub = p_hist.add_subparsers(dest="history_cmd", required=True)
    h_sub.add_parser("status", help="Show rolling recorder status and segments in tmpfs")
    h_save = h_sub.add_parser("save", help="Save recent audio history as WAV file")
    h_save.add_argument("path", help="Output file path (e.g. recent.wav)")
    h_save.add_argument("--seconds", type=int, default=60, help="Seconds of history (1-600, default 60)")
    h_save.add_argument("--channel", choices=["both", "raw", "clean"], default="both", help="Channel: both (stereo), raw (mono), clean (mono AEC3)")
    h_replay = h_sub.add_parser("replay", help="Replay recent audio through Vosk and STT for debugging")
    h_replay.add_argument("--seconds", type=int, default=30, help="Seconds of history to replay (1-300, default 30)")
    h_replay.add_argument("--channel", choices=["clean", "raw"], default="clean", help="Channel to replay: clean (default) or raw")

    args = parser.parse_args()
    base = args.url.rstrip("/")

    if args.cmd == "status":
        code, res = _request(f"{base}/health")
    elif args.cmd == "say":
        payload = json.dumps({
            "text": args.text,
            "dry_run": args.dry_run,
            "use_cloud": not args.no_cloud,
        }).encode("utf-8")
        code, res = _request(f"{base}/voice/text", data=payload)
    elif args.cmd == "wav":
        wav_bytes = Path(args.path).read_bytes()
        qs = "?dry_run=1" if args.dry_run else ""
        code, res = _request(f"{base}/voice/audio{qs}", data=wav_bytes, content_type="audio/wav")
    elif args.cmd == "listen":
        qs = "?dry_run=1" if args.dry_run else ""
        code, res = _request(f"{base}/voice/listen{qs}", data=b"{}")
    elif args.cmd == "quiz":
        if args.quiz_cmd == "status":
            code, res = _request(f"{base}/quiz/state")
        elif args.quiz_cmd == "start":
            payload = json.dumps({
                "topic": args.topic,
                "count": args.count,
                "voice_loop": args.voice,
                "skip_if_active": args.skip_if_active,
                "dry_run": args.dry_run,
            }).encode("utf-8")
            code, res = _request(f"{base}/quiz/start", data=payload)
        elif args.quiz_cmd == "schedule":
            code, res = _request(f"{base}/quiz/schedule")
        elif args.quiz_cmd == "daily":
            payload = json.dumps({
                "voice_loop": True,
                "skip_if_active": not args.force,
                "dry_run": args.dry_run,
            }).encode("utf-8")
            code, res = _request(f"{base}/quiz/schedule/trigger", data=payload)
        elif args.quiz_cmd == "answer":
            payload = json.dumps({
                "text": args.text,
                "dry_run": args.dry_run,
                "use_cloud": not args.no_cloud,
            }).encode("utf-8")
            code, res = _request(f"{base}/quiz/answer", data=payload)
        elif args.quiz_cmd == "stop":
            code, res = _request(f"{base}/quiz/stop", data=b"{}")
        else:
            parser.error(f"Unknown quiz command: {args.quiz_cmd}")
    elif args.cmd == "learning":
        if args.learning_cmd in ("status", "list"):
            code, res = _request(f"{base}/learning/entries")
            if args.learning_cmd == "status" and "stats" in res:
                res = {"ok": True, "stats": res.get("stats")}
        elif args.learning_cmd == "forget":
            payload = json.dumps({"source": args.source}).encode("utf-8")
            code, res = _request(f"{base}/learning/forget", data=payload)
        else:
            parser.error(f"Unknown learning command: {args.learning_cmd}")
    elif args.cmd == "history":
        if args.history_cmd == "status":
            code, res = _request(f"{base}/debug/audio_history")
        elif args.history_cmd == "save":
            code, data = _download_binary(f"{base}/debug/audio_history/recent.wav?seconds={args.seconds}&channel={args.channel}")
            if code == 200:
                out_p = Path(args.path)
                out_p.parent.mkdir(parents=True, exist_ok=True)
                out_p.write_bytes(data)
                res = {
                    "ok": True,
                    "path": str(out_p.resolve()),
                    "bytes": len(data),
                    "seconds": args.seconds,
                    "channel": args.channel,
                }
            else:
                try:
                    res = json.loads(data.decode("utf-8"))
                except Exception:
                    res = {"ok": False, "status": code, "error": data.decode("utf-8", errors="replace")}
        elif args.history_cmd == "replay":
            payload = json.dumps({"seconds": args.seconds, "channel": args.channel}).encode("utf-8")
            code, res = _request(f"{base}/debug/audio_history/replay", data=payload)
        else:
            parser.error(f"Unknown history command: {args.history_cmd}")
    else:
        parser.error(f"Unknown command: {args.cmd}")

    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if code < 400 and res.get("ok", True) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
