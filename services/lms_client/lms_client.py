#!/usr/bin/env python3
"""Zero-dependency Lyrion Music Server (LMS) JSON-RPC client with smart TIDAL search for PocoBox."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from typing import Any


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[0-9a-zA-Z가-힣]+", text.lower()) if len(t) >= 1}


def _score_candidate(item: dict[str, Any], query: str) -> float:
    name = str(item.get("name", "")).strip()
    if not name:
        return -1.0
    name_l = name.lower()
    query_l = query.strip().lower()
    score = 0.0
    if name_l == query_l:
        score += 100.0
    elif query_l in name_l:
        score += 50.0
    q_tokens = _tokenize(query_l)
    n_tokens = _tokenize(name_l)
    if q_tokens:
        overlap = len(q_tokens & n_tokens) / len(q_tokens)
        score += overlap * 40.0
    if item.get("isaudio"):
        score += 5.0
    # Penalize karaoke / instrumental / tribute / cover versions unless explicitly requested
    for noise in ("karaoke", "instrumental", "tribute", "lullaby", "ringtone", "originally perf", "made famous", "in the style of"):
        if noise in name_l and noise not in query_l:
            score -= 35.0
    return score


class LMSClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 9000, timeout: float = 10.0) -> None:
        self.host = host
        self.port = port
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
        return self.call(player_id, ["status", "-", 1, "tags:aAlKdc"])

    def now_playing(self, player_id: str) -> dict[str, Any]:
        st = self.player_status(player_id)
        loop = st.get("playlist_loop") or []
        track = loop[0] if loop else {}
        return {
            "player_id": player_id,
            "player_name": st.get("player_name", ""),
            "mode": st.get("mode", "stop"),
            "power": st.get("power", 0),
            "volume": st.get("mixer volume", 0),
            "playlist_tracks": int(st.get("playlist_tracks", 0) or 0),
            "time": float(st.get("time", 0) or 0),
            "duration": float(st.get("duration", track.get("duration", 0)) or 0),
            "title": track.get("title", ""),
            "artist": track.get("artist", ""),
            "album": track.get("album", ""),
            "coverid": str(track.get("coverid", "") or ""),
        }

    def power(self, player_id: str, state: int = 1) -> dict[str, Any]:
        return self.call(player_id, ["power", state])

    def play(self, player_id: str) -> dict[str, Any]:
        self.power(player_id, 1)
        return self.call(player_id, ["play"])

    def pause(self, player_id: str, state: int = 1) -> dict[str, Any]:
        return self.call(player_id, ["pause", state])

    def toggle_pause(self, player_id: str) -> dict[str, Any]:
        return self.call(player_id, ["pause"])

    def next_track(self, player_id: str) -> dict[str, Any]:
        return self.call(player_id, ["playlist", "index", "+1"])

    def prev_track(self, player_id: str) -> dict[str, Any]:
        return self.call(player_id, ["playlist", "index", "-1"])

    def get_volume(self, player_id: str) -> int:
        res = self.call(player_id, ["mixer", "volume", "?"])
        try:
            return int(float(res.get("_volume", 0)))
        except (TypeError, ValueError):
            return 0

    def set_volume(self, player_id: str, value: str | int) -> dict[str, Any]:
        if isinstance(value, int):
            value = max(0, min(100, value))
        elif isinstance(value, str) and value.isdigit():
            value = max(0, min(100, int(value)))
        return self.call(player_id, ["mixer", "volume", str(value)])

    def adjust_volume(self, player_id: str, delta: int) -> dict[str, Any]:
        current = self.get_volume(player_id)
        target = max(0, min(100, current + delta))
        self.set_volume(player_id, target)
        return {"previous_volume": current, "volume": target}

    def set_mute(self, player_id: str, muted: bool) -> dict[str, Any]:
        return self.call(player_id, ["mixer", "muting", 1 if muted else 0])

    def list_apps(self, player_id: str) -> list[dict[str, Any]]:
        res = self.call(player_id, ["apps", 0, 100])
        return res.get("appss_loop", [])

    def list_favorites(self, player_id: str, limit: int = 50) -> list[dict[str, Any]]:
        res = self.call(player_id, ["favorites", "items", 0, limit])
        return res.get("loop_loop", [])

    def play_favorite(self, player_id: str, query: str | None = None, dry_run: bool = False) -> dict[str, Any]:
        items = self.list_favorites(player_id)
        if not items:
            return {"ok": False, "error": "No favorites found in LMS."}
        chosen = items[0]
        if query:
            scored = sorted(items, key=lambda it: _score_candidate(it, query), reverse=True)
            if _score_candidate(scored[0], query) > 0:
                chosen = scored[0]
        item_id = chosen.get("id")
        if not dry_run and item_id is not None:
            self.power(player_id, 1)
            self.call(player_id, ["favorites", "playlist", "play", f"item_id:{item_id}"])
        return {
            "ok": True,
            "played": not dry_run,
            "item_id": item_id,
            "title": chosen.get("name", ""),
        }

    def tidal_items(
        self,
        player_id: str,
        query: str | None = None,
        item_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        cmd: list[Any] = ["tidal", "items", 0, limit]
        if item_id is not None:
            cmd.append(f"item_id:{item_id}")
        if query:
            cmd.append(f"search:{query}")
        return self.call(player_id, cmd)

    def tidal_play_item(self, player_id: str, item_id: str) -> dict[str, Any]:
        self.power(player_id, 1)
        return self.call(player_id, ["tidal", "playlist", "play", f"item_id:{item_id}"])

    def _find_tidal_root_id(self, player_id: str, name_lower: str, item_type: str | None = None) -> str | None:
        root = self.tidal_items(player_id, limit=25)
        for item in root.get("loop_loop", []):
            if item_type and item.get("type") == item_type:
                return str(item.get("id"))
            if str(item.get("name", "")).strip().lower() == name_lower:
                return str(item.get("id"))
        return None

    def play_tidal_my_mix(self, player_id: str, index: int = 0, dry_run: bool = False) -> dict[str, Any]:
        mix_root_id = self._find_tidal_root_id(player_id, "my mix")
        if mix_root_id is None:
            return {"ok": False, "error": "TIDAL My Mix menu not found."}
        mixes = self.tidal_items(player_id, item_id=mix_root_id, limit=20).get("loop_loop", [])
        if not mixes:
            return {"ok": False, "error": "No TIDAL My Mix items available."}
        chosen = mixes[min(max(0, index), len(mixes) - 1)]
        if not dry_run:
            self.tidal_play_item(player_id, str(chosen["id"]))
        return {
            "ok": True,
            "played": not dry_run,
            "item_id": chosen.get("id"),
            "title": chosen.get("name", "My Mix"),
        }

    def tidal_search_and_play(
        self,
        player_id: str,
        queries: list[str],
        search_type: str = "any",
        dry_run: bool = False,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search TIDAL via LMS plugin using ordered query candidates and play the best match."""
        search_root_id = self._find_tidal_root_id(player_id, "search", item_type="search")
        if search_root_id is None:
            return {"ok": False, "error": "TIDAL Search menu not found (is TIDAL plugin logged in?)."}

        category_order_map = {
            "track": ["songs", "everything", "albums", "playlists", "artists"],
            "song": ["songs", "everything", "albums", "playlists", "artists"],
            "artist": ["artists", "songs", "everything", "albums", "playlists"],
            "album": ["albums", "everything", "songs", "playlists", "artists"],
            "playlist": ["playlists", "everything", "songs", "albums", "artists"],
            "any": ["songs", "everything", "artists", "albums", "playlists"],
        }
        desired_cats = category_order_map.get(search_type.lower(), category_order_map["any"])

        tried: list[str] = []
        for raw_q in queries:
            q = raw_q.strip()
            if not q or q in tried:
                continue
            tried.append(q)

            cats_res = self.tidal_items(player_id, query=q, item_id=search_root_id, limit=10)
            cat_by_name = {
                str(c.get("name", "")).strip().lower(): str(c.get("id"))
                for c in cats_res.get("loop_loop", [])
                if c.get("id") is not None
            }

            for cat_name in desired_cats:
                cat_id = cat_by_name.get(cat_name)
                if not cat_id:
                    continue
                items_res = self.tidal_items(player_id, item_id=cat_id, limit=limit)
                items = items_res.get("loop_loop", [])
                if not items:
                    continue

                # Prefer playable audio/playlist items when scoring
                ranked = sorted(items, key=lambda it: _score_candidate(it, q), reverse=True)
                best = ranked[0]
                best_id = str(best.get("id"))
                best_title = str(best.get("name", ""))

                # If an artist link is chosen and isn't directly playable, drill down to Top Tracks
                play_id = best_id
                if cat_name == "artists" and not best.get("isaudio") and best.get("hasitems"):
                    sub_items = self.tidal_items(player_id, item_id=best_id, limit=15).get("loop_loop", [])
                    for sub in sub_items:
                        sub_name = str(sub.get("name", "")).lower()
                        if "top track" in sub_name or "track" in sub_name or sub.get("isaudio"):
                            play_id = str(sub.get("id"))
                            break

                if not dry_run:
                    self.tidal_play_item(player_id, play_id)

                return {
                    "ok": True,
                    "played": not dry_run,
                    "matched_query": q,
                    "category": cat_name,
                    "item_id": play_id,
                    "title": best_title,
                    "candidates": [
                        {"id": it.get("id"), "name": it.get("name"), "type": it.get("type")}
                        for it in ranked[:5]
                    ],
                }

        return {
            "ok": False,
            "error": f"No TIDAL results found for queries: {tried}",
            "tried_queries": tried,
        }

    def play_url(self, player_id: str, url: str) -> dict[str, Any]:
        self.power(player_id, 1)
        return self.call(player_id, ["playlist", "play", url])


def main() -> int:
    parser = argparse.ArgumentParser(description="PocoBox LMS JSON-RPC CLI")
    parser.add_argument("--host", default="127.0.0.1", help="LMS host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9000, help="LMS HTTP port (default: 9000)")
    parser.add_argument("--player", default="pocobox", help="Preferred player name (default: pocobox)")

    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("players", help="List connected players")
    sub.add_parser("status", help="Show raw player status")
    sub.add_parser("now-playing", help="Show concise now-playing info")
    sub.add_parser("apps", help="List installed LMS apps/plugins")
    sub.add_parser("favorites", help="List LMS favorites")
    sub.add_parser("play", help="Start/resume playback")
    sub.add_parser("pause", help="Pause playback")
    sub.add_parser("toggle", help="Toggle play/pause")
    sub.add_parser("next", help="Next track")
    sub.add_parser("prev", help="Previous track")

    p_vol = sub.add_parser("volume", help="Set or adjust volume (e.g. 50, +10, -10)")
    p_vol.add_argument("value")

    p_fav = sub.add_parser("play-favorite", help="Play a favorite by optional name")
    p_fav.add_argument("query", nargs="?", default=None)
    p_fav.add_argument("--dry-run", action="store_true")

    p_mix = sub.add_parser("tidal-mix", help="Play TIDAL My Mix")
    p_mix.add_argument("--index", type=int, default=0)
    p_mix.add_argument("--dry-run", action="store_true")

    p_ts = sub.add_parser("tidal-search", help="Low-level TIDAL items browse/search")
    p_ts.add_argument("query")
    p_ts.add_argument("--item-id", default=None)

    p_tp = sub.add_parser("tidal-play", help="Play a TIDAL item_id via LMS plugin")
    p_tp.add_argument("item_id")

    p_tpq = sub.add_parser("tidal-play-query", help="Smart search & play on TIDAL")
    p_tpq.add_argument("queries", nargs="+", help="Ordered search query candidates")
    p_tpq.add_argument("--type", dest="search_type", default="any", choices=["any", "track", "artist", "album", "playlist"])
    p_tpq.add_argument("--dry-run", action="store_true")

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
    elif args.cmd == "now-playing":
        out = client.now_playing(player_id)
    elif args.cmd == "apps":
        out = client.list_apps(player_id)
    elif args.cmd == "favorites":
        out = client.list_favorites(player_id)
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
        if args.value.startswith(("+", "-")):
            out = client.adjust_volume(player_id, int(args.value))
        else:
            out = client.set_volume(player_id, args.value)
    elif args.cmd == "play-favorite":
        out = client.play_favorite(player_id, query=args.query, dry_run=args.dry_run)
    elif args.cmd == "tidal-mix":
        out = client.play_tidal_my_mix(player_id, index=args.index, dry_run=args.dry_run)
    elif args.cmd == "tidal-search":
        out = client.tidal_items(player_id, query=args.query, item_id=args.item_id)
    elif args.cmd == "tidal-play":
        out = client.tidal_play_item(player_id, args.item_id)
    elif args.cmd == "tidal-play-query":
        out = client.tidal_search_and_play(player_id, args.queries, search_type=args.search_type, dry_run=args.dry_run)
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
