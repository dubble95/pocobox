#!/usr/bin/env python3
"""Benchmark & report generator for PocoBox voice command test dataset (offline & live LMS/TIDAL)."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "services"))

from voice_control.rule_parser import parse_command  # noqa: E402

DATASET_PATH = Path(__file__).resolve().parent / "dataset" / "voice_commands_ko_en.json"


def check_case(case: dict) -> tuple[bool, str, dict]:
    intent = parse_command(case["text"])
    errs: list[str] = []

    if intent.action.value != case["expected_action"]:
        errs.append(f"action={intent.action.value} != {case['expected_action']}")
    if "expected_query_contains" in case and case["expected_query_contains"].lower() not in intent.query.lower():
        errs.append(f"query='{intent.query}' missing '{case['expected_query_contains']}'")
    if "expected_fallback_contains" in case:
        needle = case["expected_fallback_contains"].lower()
        if not any(needle in q.lower() for q in intent.all_queries()):
            errs.append(f"all_queries={intent.all_queries()} missing '{needle}'")
    if "expected_search_type" in case and intent.search_type != case["expected_search_type"]:
        errs.append(f"search_type={intent.search_type} != {case['expected_search_type']}")
    if "expected_volume" in case and intent.volume != case["expected_volume"]:
        errs.append(f"volume={intent.volume} != {case['expected_volume']}")
    if "expected_delta" in case and intent.delta != case["expected_delta"]:
        errs.append(f"delta={intent.delta} != {case['expected_delta']}")
    if "expected_wake" in case and intent.wake_word_detected != case["expected_wake"]:
        errs.append(f"wake={intent.wake_word_detected} != {case['expected_wake']}")

    return (len(errs) == 0), "; ".join(errs), intent.to_dict()


def run_live_check(base_url: str, cases: list[dict]) -> tuple[int, int, list[str]]:
    passed = 0
    total = 0
    failures: list[str] = []
    for case in cases:
        if case["expected_action"] == "unknown":
            continue
        total += 1
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/voice/text",
            data=json.dumps({"text": case["text"], "dry_run": True, "use_cloud": False}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                res = json.loads(resp.read().decode("utf-8"))
            if res.get("ok") and res.get("action") == case["expected_action"]:
                passed += 1
            else:
                failures.append(f"[{case['id']}] '{case['text']}' -> {res.get('message')}")
        except Exception as exc:
            failures.append(f"[{case['id']}] '{case['text']}' -> HTTP error: {exc}")
    return passed, total, failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PocoBox voice command benchmark report")
    parser.add_argument("--live", default="", help="Optional live daemon URL, e.g. http://pocobox.local:8765")
    parser.add_argument("--live-limit", type=int, default=20, help="Max live TIDAL dry-run queries (default: 20)")
    args = parser.parse_args()

    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    by_cat: dict[str, list[bool]] = defaultdict(list)
    failures: list[str] = []

    t0 = time.monotonic()
    for c in cases:
        ok, err, _ = check_case(c)
        by_cat[c["category"]].append(ok)
        if not ok:
            failures.append(f"[{c['id']}] '{c['text']}': {err}")
    elapsed_ms = (time.monotonic() - t0) * 1000.0

    total = len(cases)
    passed = sum(sum(v) for v in by_cat.values())

    print("=== PocoBox Voice Intent Benchmark (Offline Rule + Alias Engine) ===")
    print(f"Total: {passed}/{total} ({passed / total * 100:.1f}%) in {elapsed_ms:.2f}ms ({elapsed_ms / total:.3f}ms/cmd)\n")
    print(f"{'Category':<28} {'Passed':>10} {'Rate':>8}")
    print("-" * 48)
    for cat, results in by_cat.items():
        p = sum(results)
        n = len(results)
        print(f"{cat:<28} {p:>4}/{n:<5} {p / n * 100:>7.1f}%")

    if failures:
        print("\nFailures:")
        for f in failures:
            print("  -", f)

    if args.live:
        sample = [c for c in cases if c["expected_action"] != "unknown"][: args.live_limit]
        print(f"\n=== Live PocoBox Dry-Run Verification ({args.live}, {len(sample)} cmds) ===")
        lp, lt, lf = run_live_check(args.live, sample)
        print(f"Live Passed: {lp}/{lt} ({lp / max(1, lt) * 100:.1f}%)")
        for f in lf:
            print("  -", f)
        if lf:
            return 1

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
