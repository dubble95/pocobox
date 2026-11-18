#!/usr/bin/env python3
"""Minimal zero-dependency Lyrion Music Server (LMS) JSON-RPC client for PocoBox."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any


class LMSClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 9000, timeout: float = 10.0) -> None:
        self.url = f"http://{host}:{port}/jsonrpc.js"
        self.timeout = timeout

    def call(self, player_id: str, command: list[Any]) -> dict[str, Any]:
        payload = json.dumps({
            "id": 1,
            "method": "slim.request",
            "params": [player_id, command],
        }).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(f"LMS JSON-RPC error ({self.url}): {exc}") from exc
        return body.get("result", {})

    def server_status(self) -> dict[str, Any]:
        return self.call("", ["serverstatus", 0, 100])

    def list_players(self) -> list[dict[str, Any]]:
        status = self.server_status()
        return status.get("players_loop", [])

    def resolve_player_id(self, preferred_name: str = "pocobox") -> str:
        players = self.list_players()
        if not players:
            raise RuntimeError("No LMS players connected.")
        connected = [p for p in players if p.get("connected")] or players
        for player in connected:
            if player.get("name", "").lower() == preferred_name.lower():
                return player["playerid"]
        return connected[0]["playerid"]

    def player_status(self, player_id: str) -> dict[str, Any]:
        return self.call(player_id, ["status", "-", 1, "tags:aAlKd"])

    def play(self, player_id: str) -> dict[str, Any]:
        return self.call(player_id, ["play"])

    def pause(self, player_id: str, state: int = 1) -> dict[str, Any]:
        return self.call(player_id, ["pause", state])

    def toggle_pause(self, player_id: str) -> dict[str, Any]:
        return self.call(player_id, ["pause"])

    def next_track(self, player_id: str) -> dict[str, Any]:
        return self.call(player_id, ["playlist", "index", "+1"])

    def prev_track(self, player_id: str) -> dict[str, Any]:
        return self.call(player_id, ["playlist", "index", "-1"])

    def set_volume(self, player_id: str, value: str) -> dict[str, Any]:
        return self.call(player_id, ["mixer", "volume", str(value)])

    def list_apps(self, player_id: str) -> list[dict[str, Any]]:
        res = self.call(player_id, ["apps", 0, 100])
        return res.get("appss_loop", [])

    def tidal_items(self, player_id: str, query: str | None = None, item_id: str | None = None, limit: int = 20) -> dict[str, Any]:
        cmd: list[Any] = ["tidal", "items", 0, limit]
        if item_id:
            cmd.append(f"item_id:{item_id}")
        if query:
            cmd.append(f"search:{query}")
        return self.call(player_id, cmd)

    def tidal_play_item(self, player_id: str, item_id: str) -> dict[str, Any]:
        return self.call(player_id, ["tidal", "playlist", "play", f"item_id:{item_id}"])

    def play_url(self, player_id: str, url: str) -> dict[str, Any]:
        return self.call(player_id, ["playlist", "play", url])


def main() -> int:
    parser = argparse.ArgumentParser(description="PocoBox LMS JSON-RPC CLI")
    parser.add_argument("--host", default="127.0.0.1", help="LMS host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9000, help="LMS HTTP port (default: 9000)")
    parser.add_argument("--player", default="pocobox", help="Preferred player name (default: pocobox)")

    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("players", help="List connected players")
    sub.add_parser("status", help="Show player status")
    sub.add_parser("apps", help="List installed LMS apps/plugins")
    sub.add_parser("play", help="Start/resume playback")
    sub.add_parser("pause", help="Pause playback")
    sub.add_parser("toggle", help="Toggle play/pause")
    sub.add_parser("next", help="Next track")
    sub.add_parser("prev", help="Previous track")

    p_vol = sub.add_parser("volume", help="Set or adjust volume (e.g. 50, +10, -10)")
    p_vol.add_argument("value")

    p_ts = sub.add_parser("tidal-search", help="Search TIDAL via LMS plugin")
    p_ts.add_argument("query")
    p_ts.add_argument("--item-id", default=None)

    p_tp = sub.add_parser("tidal-play", help="Play a TIDAL item_id via LMS plugin")
    p_tp.add_argument("item_id")

    p_url = sub.add_parser("play-url", help="Play a direct stream or file URL")
    p_url.add_argument("url")

    args = parser.parse_args()
    client = LMSClient(host=args.host, port=args.port)

    if args.cmd == "players":
        print(json.dumps(client.list_players(), ensure_ascii=False, indent=2))
        return 0

    player_id = client.resolve_player_id(args.player)

    if args.cmd == "status":
        out = client.player_status(player_id)
    elif args.cmd == "apps":
        out = client.list_apps(player_id)
    elif args.cmd == "play":
        out = client.play(player_id)
    elif args.cmd == "pause":
        out = client.pause(player_id, 1)
    elif args.cmd == "toggle":
        out = client.toggle_pause(player_id)
    elif args.cmd == "next":
        out = client.next_track(player_id)
    elif args.cmd == "prev":
        out = client.prev_track(player_id)
    elif args.cmd == "volume":
        out = client.set_volume(player_id, args.value)
    elif args.cmd == "tidal-search":
        out = client.tidal_items(player_id, query=args.query, item_id=args.item_id)
    elif args.cmd == "tidal-play":
        out = client.tidal_play_item(player_id, args.item_id)
    elif args.cmd == "play-url":
        out = client.play_url(player_id, args.url)
    else:
        parser.error(f"Unknown command: {args.cmd}")

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
