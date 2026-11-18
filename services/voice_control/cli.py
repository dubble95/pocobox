#!/usr/bin/env python3
"""CLI wrapper for PocoBox Voice Control (works locally via daemon HTTP API or direct execution)."""

from __future__ import annotations

import argparse
import json
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


def main() -> int:
    parser = argparse.ArgumentParser(description="PocoBox Voice Control CLI")
    parser.add_argument("--url", default="http://127.0.0.1:8765", help="Voice daemon URL")
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
