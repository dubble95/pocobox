#!/usr/bin/env python3
"""Zero-dependency Lyrion Music Server (LMS) JSON-RPC client with smart TIDAL search for PocoBox."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any

_TIDAL_TRACK_ID_RE = re.compile(
    r"(?:tidal|wimp)://(?:track:)?(\d+)(?:\.[a-z0-9]+)?$|tidal\.com/(?:browse/)?track/(\d+)",
    re.IGNORECASE,
)
_YT_TRACK_ID_RE = re.compile(
    r"(?:v=|youtu\.be/|/v/|/embed/|youtube://)([a-zA-Z0-9_-]{11})|(?:vi[/]|vi%2F)([a-zA-Z0-9_-]{11})",
    re.IGNORECASE,
)


def extract_tidal_track_id(url: str | None) -> str | None:
    """Extract numeric TIDAL track ID from an LMS track URL (e.g. 'tidal://17603023.flc')."""
    if not url:
        return None
    m = _TIDAL_TRACK_ID_RE.search(str(url).strip())
    if not m:
        return None
    return m.group(1) or m.group(2)


def extract_youtube_video_id(url: str | None) -> str | None:
    """Extract 11-character YouTube video ID from a URL or stream path."""
    if not url:
        return None
    cleaned = str(url).strip()
    m = _YT_TRACK_ID_RE.search(cleaned)
    if m:
        return m.group(1) or m.group(2)
    if len(cleaned) == 11 and re.fullmatch(r"[a-zA-Z0-9_-]{11}", cleaned):
        return cleaned
    return None


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
    for noise in (
        "karaoke", "instrumental", "tribute", "lullaby", "ringtone",
        "originally perf", "made famous", "in the style of",
        "orchestral", "music box", "piano version", "8-bit", "(cover)",
        "transcribed", "transcription", "arranged", "arrangement",
    ):
        if noise in name_l and noise not in query_l:
            score -= 35.0
    return score


_ROMAN_NUMERALS: dict[str, int] = {
    "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5,
    "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10,
    "XI": 11, "XII": 12,
}

_SINO_MVT_MAP: dict[str, int] = {
    "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9, "십": 10,
}

_CLASSICAL_MOVEMENT_WORK_RE = re.compile(
    r"(?:협주곡?|콘체르토|교향곡|심포니|소나타|모음곡|[345]중주|[삼사오]중주|트리오|세레나데|디베르티멘토|"
    r"브란덴부르크|사계|골드베르크|"
    r"\bconcerto\b|\bsymphon(?:y|ie)\b|\bsonat[ae]\b|\bsuite\b|\bquartet[te]?\b|\btrio\b|\bquintet[te]?\b|"
    r"\bserenade\b|\bdivertimento\b|\bbrandenburg\b|\bfour\s+seasons\b|\bgoldberg\b)",
    re.IGNORECASE,
)


def is_classical_movement_work(text: str) -> bool:
    """Return True if text refers to a classical form composed of multiple movements."""
    return bool(_CLASSICAL_MOVEMENT_WORK_RE.search(text))


def parse_movement_number(text: str) -> int | None:
    """Extract 1-based movement number from a track title or query."""
    if not text:
        return None

    # Check Korean movement phrase: "1악장", "제1악장", "일악장"
    m_ko = re.search(r"(?:제\s*)?([일이삼사오육칠팔구]|\d{1,2})\s*악장", text)
    if m_ko:
        val = m_ko.group(1)
        if val.isdigit():
            return int(val)
        if val in _SINO_MVT_MAP:
            return _SINO_MVT_MAP[val]

    # Check English movement phrase: "Movement 2", "Mov. 2", "Mov 2", "Part 2", "Act 2"
    m_en = re.search(r"\b(?:mov(?:ement)?|part|act)\.?\s*(\d{1,2}|[IVXLCDM]+)\b", text, re.IGNORECASE)
    if m_en:
        val = m_en.group(1)
        if val.isdigit():
            return int(val)
        if val.upper() in _ROMAN_NUMERALS:
            return _ROMAN_NUMERALS[val.upper()]

    # Split on colon, dash, or pipe where movement descriptions usually follow
    parts = re.split(r"[:\-–—|]", text)
    for p in reversed(parts):
        p_clean = p.strip()
        if not p_clean:
            continue
        # Check Roman numerals: "I. Allegro", "II - Adagio", "IV: Presto", "Mov. I", "Part I", "Act I"
        m_ro = re.match(
            r"^(?:(?:mov(?:ement)?|part|act)\.?\s*)?([IVXLCDM]+)(?:[.\s:\-–—a-z0-9]|$)",
            p_clean,
            re.IGNORECASE,
        )
        if m_ro:
            ro_key = m_ro.group(1).upper()
            if ro_key in _ROMAN_NUMERALS:
                return _ROMAN_NUMERALS[ro_key]

        # Check Arabic numerals: "1. Allegro", "1a. Allegro", "2. Andante", "Movement 1", "Mov 1"
        m_num = re.match(
            r"^(?:(?:mov(?:ement)?|part|act)\.?\s*)?(\d{1,2})(?:[a-z]?)(?:[.\s:\-–—]|$)",
            p_clean,
            re.IGNORECASE,
        )
        if m_num:
            return int(m_num.group(1))

    # Also check "1st Movement", "First Movement", "Prelude", "Overture"
    if re.search(r"\b(?:1st|first)\s+mov(?:ement)?\b", text, re.IGNORECASE):
        return 1
    if re.search(r"\b(?:2nd|second)\s+mov(?:ement)?\b", text, re.IGNORECASE):
        return 2
    if re.search(r"\b(?:3rd|third)\s+mov(?:ement)?\b", text, re.IGNORECASE):
        return 3
    if re.search(r"\b(?:4th|fourth)\s+mov(?:ement)?\b", text, re.IGNORECASE):
        return 4
    if re.search(r"\b(?:miniature\s+overture|pr[eé]lude)\b", text, re.IGNORECASE):
        return 1

    return None


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

    def resolve_player_id(self, preferred_name: str = "pocobox", timeout: float = 2.5) -> str:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            players = self.list_players()
            if players:
                connected = [p for p in players if p.get("connected")] or players
                for player in connected:
                    if player.get("name", "").lower() == preferred_name.lower():
                        return player["playerid"]
                return connected[0]["playerid"]
            if time.monotonic() >= deadline:
                break
            time.sleep(0.25)
        raise RuntimeError(f"No LMS players connected (waited up to {timeout:.1f}s).")

    def player_status(self, player_id: str) -> dict[str, Any]:
        return self.call(player_id, ["status", "-", 1, "tags:aAlKdcu"])

    def now_playing(self, player_id: str) -> dict[str, Any]:
        st = self.player_status(player_id)
        loop = st.get("playlist_loop") or []
        track = loop[0] if loop else {}
        remote_meta = st.get("remoteMeta") if isinstance(st.get("remoteMeta"), dict) else {}

        raw_title = str(track.get("title") or "").strip()
        url = str(track.get("url") or "").strip()
        is_url_title = raw_title.startswith(("http://", "https://", "youtube://")) or "youtube.com" in raw_title

        title = ""
        if not is_url_title and raw_title:
            title = raw_title
        elif remote_meta.get("title"):
            title = str(remote_meta.get("title")).strip()
        elif st.get("remote_title"):
            title = str(st.get("remote_title")).strip()
        elif st.get("current_title"):
            title = str(st.get("current_title")).strip()
        else:
            title = raw_title

        artist = str(track.get("artist") or remote_meta.get("artist") or st.get("remote_artist") or "").strip()
        album = str(track.get("album") or remote_meta.get("album") or "").strip()
        coverid = str(track.get("coverid") or remote_meta.get("coverid") or "")
        artwork_url = str(track.get("artwork_url") or remote_meta.get("artwork_url") or "")

        # If artist is missing but title has "Artist - Title" (common for YouTube / stream titles):
        if not artist and " - " in title:
            parts = title.split(" - ", 1)
            candidate_artist = parts[0].strip()
            candidate_title = parts[1].strip()
            if candidate_artist and candidate_title:
                artist = candidate_artist
                title = candidate_title

        # If playing YouTube via LMS and album/artwork_url are missing, derive metadata from video ID:
        is_youtube = url.startswith("youtube://") or "youtube.com" in url or "youtu.be" in url or raw_title.startswith("youtube://")
        if is_youtube:
            if not album:
                album = "YouTube"
            if not artwork_url:
                vid = extract_youtube_video_id(url) or extract_youtube_video_id(raw_title)
                if vid:
                    artwork_url = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
                    if not coverid:
                        coverid = vid

        duration = float(
            st.get("duration")
            or track.get("duration")
            or remote_meta.get("duration")
            or 0
        )
        return {
            "player_id": player_id,
            "player_name": st.get("player_name", ""),
            "mode": st.get("mode", "stop"),
            "power": st.get("power", 0),
            "volume": st.get("mixer volume", 0),
            "digital_volume_control": int(st.get("digital_volume_control", 1) or 0),
            "use_volume_control": int(st.get("use_volume_control", 1) or 0),
            "playlist_tracks": int(st.get("playlist_tracks", 0) or 0),
            "time": float(st.get("time", 0) or 0),
            "duration": duration,
            "title": str(title),
            "artist": str(artist),
            "album": str(album),
            "coverid": coverid,
            "artwork_url": artwork_url,
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

    def _extract_classical_suite_from_album(
        self,
        album_tracks: list[dict[str, Any]],
        query: str,
        start_movement: int = 1,
    ) -> list[dict[str, Any]]:
        """Extract a contiguous list of movement tracks for the requested work from album tracks.

        Starts from start_movement (default 1) and gathers consecutive movements (1, 2, 3, ...).
        """
        if not album_tracks:
            return []

        q_tokens = _tokenize(query)
        work_tokens = {
            t for t in q_tokens
            if t not in {
                "the", "in", "of", "and", "no", "op", "major", "minor",
                "flat", "sharp", "dur", "moll", "movement", "mov", "act", "part",
            } and not t.isdigit()
        }

        parsed: list[tuple[int, int | None, dict[str, Any], float, str]] = []
        for i, t in enumerate(album_tracks):
            name = str(t.get("name", "")).strip()
            t_tokens = _tokenize(name)
            overlap = len(work_tokens & t_tokens) / max(1, len(work_tokens))
            mvt = parse_movement_number(name)
            parsed.append((i, mvt, t, overlap, name))

        best_seq: list[dict[str, Any]] = []

        # Find starting movement matching the requested work
        for idx, (i, mvt, t, overlap, name) in enumerate(parsed):
            # Accept if movement matches start_movement and has good token overlap or album has few tracks
            if mvt == start_movement and (overlap >= 0.25 or len(parsed) <= 6):
                seq = [t]
                expected = start_movement + 1
                for next_i, next_mvt, next_t, next_overlap, next_name in parsed[idx + 1:]:
                    if next_mvt == expected:
                        seq.append(next_t)
                        expected += 1
                    elif next_mvt == 1 and expected > 1:
                        # New work starting on same album
                        break
                    elif expected > start_movement + 1 and next_overlap < 0.15:
                        break
                if len(seq) > len(best_seq):
                    best_seq = seq

        # Fallback: if entire album is 2-6 tracks and all tracks have sequential movement numbers
        if not best_seq and len(album_tracks) in (2, 3, 4, 5, 6):
            mvts = [parse_movement_number(str(t.get("name", ""))) for t in album_tracks]
            if mvts[0] == start_movement and all(m is not None for m in mvts):
                if mvts == list(range(start_movement, start_movement + len(mvts))):
                    best_seq = list(album_tracks)

        return best_seq

    def resolve_classical_work(
        self,
        player_id: str,
        query: str,
        search_root_id: str,
        start_movement: int = 1,
        limit: int = 10,
    ) -> dict[str, Any] | None:
        """Find and return all movements of a classical work starting from start_movement."""
        cats_res = self.tidal_items(player_id, query=query, item_id=search_root_id, limit=10)
        cat_by_name = {
            str(c.get("name", "")).strip().lower(): str(c.get("id"))
            for c in cats_res.get("loop_loop", [])
            if c.get("id") is not None
        }
        album_cat_id = cat_by_name.get("albums")
        if not album_cat_id:
            return None

        albums_res = self.tidal_items(player_id, item_id=album_cat_id, limit=limit)
        albums = albums_res.get("loop_loop", [])
        if not albums:
            return None

        scored = sorted(albums, key=lambda alb: _score_candidate(alb, query), reverse=True)

        for alb in scored[:5]:
            if _score_candidate(alb, query) <= 0:
                continue
            aid = str(alb.get("id"))
            tracks_res = self.tidal_items(player_id, item_id=aid, limit=60)
            tracks = tracks_res.get("loop_loop", [])
            suite = self._extract_classical_suite_from_album(tracks, query, start_movement=start_movement)
            if len(suite) >= 2 or (len(suite) >= 1 and start_movement > 1):
                return {
                    "album": alb,
                    "tracks": suite,
                    "matched_query": query,
                }
        return None

    def tidal_search_and_play(
        self,
        player_id: str,
        queries: list[str],
        search_type: str = "any",
        dry_run: bool = False,
        limit: int = 10,
        play_action: str = "play",
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
            "classical": ["albums", "songs", "everything", "playlists", "artists"],
            "any": ["songs", "everything", "artists", "albums", "playlists"],
        }
        desired_cats = category_order_map.get(search_type.lower(), category_order_map["any"])

        is_classical_req = (search_type.lower() == "classical") or any(
            is_classical_movement_work(raw_q) for raw_q in queries
        )

        if is_classical_req:
            for raw_q in queries:
                q = raw_q.strip()
                if not q:
                    continue
                start_mvt = parse_movement_number(q) or 1
                # Clean query for album search by stripping movement tokens so album matches
                clean_work_q = re.sub(
                    r"(?:\b(?:mov(?:ement)?|part|act)\.?\s*\d+\b|\b(?:1st|2nd|3rd|4th)\s+mov(?:ement)?\b|(?:제\s*)?(?:[일이삼사오육칠팔구]|\d+)\s*악장)",
                    "",
                    q,
                    flags=re.IGNORECASE,
                )
                clean_work_q = re.sub(r"\s+", " ", clean_work_q).strip(" :-–—")
                target_q = clean_work_q if len(clean_work_q) >= 3 else q

                work_res = self.resolve_classical_work(
                    player_id,
                    target_q,
                    search_root_id=search_root_id,
                    start_movement=start_mvt,
                    limit=limit,
                )
                if work_res and work_res.get("tracks"):
                    suite = work_res["tracks"]
                    track_1 = suite[0]
                    work_album = work_res.get("album") or {}
                    alb_title = str(work_album.get("name") or "").strip()
                    t1_title = str(track_1.get("name") or "").strip()
                    display_title = alb_title or t1_title
                    if len(suite) > 1:
                        end_mvt = start_mvt + len(suite) - 1
                        mvt_suffix = f" ({start_mvt}-{end_mvt}악장)" if start_mvt > 1 else f" (전 {len(suite)}악장)"
                        display_title = f"{display_title}{mvt_suffix}"

                    if not dry_run:
                        st = self.player_status(player_id)
                        is_playing = st.get("mode") == "play"
                        tracks_count = int(st.get("playlist_tracks") or 0)
                        if play_action in ("insert", "queue_next") and (is_playing or tracks_count > 0):
                            for extra_t in reversed(suite):
                                self.call(player_id, ["tidal", "playlist", "insert", f"item_id:{extra_t['id']}"])
                        else:
                            self.tidal_play_item(player_id, str(track_1["id"]))
                            for extra_t in suite[1:]:
                                self.call(player_id, ["tidal", "playlist", "add", f"item_id:{extra_t['id']}"])

                    return {
                        "ok": True,
                        "played": not dry_run and play_action not in ("insert", "queue_next"),
                        "queued": not dry_run and play_action in ("insert", "queue_next"),
                        "play_action": play_action,
                        "matched_query": q,
                        "category": "classical_work",
                        "item_id": str(track_1.get("id")),
                        "title": display_title,
                        "album_name": alb_title,
                        "movements_count": len(suite),
                        "start_movement": start_mvt,
                        "candidates": [
                            {"id": it.get("id"), "name": it.get("name"), "type": it.get("type")}
                            for it in suite
                        ],
                    }

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
                if is_classical_req and cat_name == "songs":
                    target_mvt = parse_movement_number(q) or 1
                    mvt_matches = [
                        it for it in ranked
                        if parse_movement_number(str(it.get("name", ""))) == target_mvt
                    ]
                    if mvt_matches:
                        best = mvt_matches[0]
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
                    st = self.player_status(player_id)
                    is_playing = st.get("mode") == "play"
                    tracks_count = int(st.get("playlist_tracks") or 0)
                    if play_action in ("insert", "queue_next") and (is_playing or tracks_count > 0):
                        self.call(player_id, ["tidal", "playlist", "insert", f"item_id:{play_id}"])
                    else:
                        self.tidal_play_item(player_id, play_id)

                return {
                    "ok": True,
                    "played": not dry_run and play_action not in ("insert", "queue_next"),
                    "queued": not dry_run and play_action in ("insert", "queue_next"),
                    "play_action": play_action,
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

    def youtube_search(
        self,
        player_id: str,
        query: str,
        search_type: str = "video",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search YouTube via LMS YouTube plugin (item_id:1 video, item_id:2 music, item_id:4 playlist)."""
        item_id = "1" if search_type == "video" else ("4" if search_type == "playlist" else "2")
        resp = self.call(player_id, ["youtube", "items", 0, limit, f"item_id:{item_id}", f"search:{query}"])
        return resp.get("loop_loop") or []

    def youtube_play(self, player_id: str, video_id: str) -> dict[str, Any]:
        """Play a YouTube video URL on the LMS player."""
        vid = video_id.strip()
        url = vid if vid.startswith("youtube://") else f"youtube://www.youtube.com/v/{vid}"
        return self.play_url(player_id, url)

    def get_dstm_provider(self, player_id: str) -> str:
        """Return currently configured LMS Don't Stop The Music provider ('' if disabled)."""
        res = self.call(player_id, ["playerpref", "plugin.dontstopthemusic:provider", "?"])
        val = res.get("_p2")
        if not val or str(val) in ("0", "None"):
            return ""
        return str(val)

    def ensure_tidal_dstm(
        self,
        player_id: str,
        provider: str = "PLUGIN_TIDAL_DSTM",
        *,
        force: bool = False,
    ) -> bool:
        """Ensure LMS Don't Stop The Music (TIDAL Radio autoplay) is enabled for the player."""
        current = self.get_dstm_provider(player_id)
        if current and not force:
            return current == provider
        self.call(player_id, ["playerpref", "plugin.dontstopthemusic:provider", provider])
        return True

    def append_tidal_track_radio(
        self,
        player_id: str,
        track_id: str | None = None,
        *,
        only_if_single_track: bool = True,
        wait_timeout_sec: float = 0.0,
        dedup_timeout_sec: float = 3.0,
    ) -> dict[str, Any]:
        """Append TIDAL Track Radio recommendations after the currently playing TIDAL track.

        Deduplicates the seed track if TIDAL returns it as the first radio item so the
        currently playing song is never played twice in a row.
        """
        deadline = time.monotonic() + max(0.0, float(wait_timeout_sec))
        before_tracks = 0
        cur_index = 0
        resolved_id = track_id
        while True:
            st = self.call(player_id, ["status", "-", 2, "tags:u"])
            before_tracks = int(st.get("playlist_tracks") or 0)
            cur_index = int(st.get("playlist_cur_index") or 0)
            if only_if_single_track and before_tracks > 1:
                return {
                    "ok": True,
                    "skipped": True,
                    "reason": "queue_already_populated",
                    "playlist_tracks": before_tracks,
                }
            loop = st.get("playlist_loop") or []
            cur_url = str(loop[0].get("url") or "") if loop else ""
            if not resolved_id:
                resolved_id = extract_tidal_track_id(cur_url)
            if resolved_id:
                break
            if time.monotonic() >= deadline:
                return {
                    "ok": False,
                    "reason": "no_tidal_track_id",
                    "playlist_tracks": before_tracks,
                }
            time.sleep(0.3)

        self.call(player_id, ["tidal_browse", "playlist", "add", "type:track_mix", f"id:{resolved_id}"])

        # Wait briefly for LMS's async TIDAL track_mix callback to populate the queue, then remove duplicate seed
        dedup_deadline = time.monotonic() + max(0.0, float(dedup_timeout_sec))
        new_tracks = before_tracks
        deduped = False
        while True:
            st2 = self.call(player_id, ["status", cur_index, 2, "tags:u"])
            new_tracks = int(st2.get("playlist_tracks") or before_tracks)
            if new_tracks > before_tracks:
                loop2 = st2.get("playlist_loop") or []
                if len(loop2) >= 2:
                    next_url = str(loop2[1].get("url") or "")
                    if extract_tidal_track_id(next_url) == resolved_id:
                        self.call(player_id, ["playlist", "delete", cur_index + 1])
                        new_tracks = max(before_tracks, new_tracks - 1)
                        deduped = True
                break
            if time.monotonic() >= dedup_deadline:
                break
            time.sleep(0.25)

        return {
            "ok": True,
            "track_id": resolved_id,
            "added": max(0, new_tracks - before_tracks),
            "deduped_seed": deduped,
            "playlist_tracks": new_tracks,
        }


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
    p_tpq.add_argument("--next", action="store_true", help="Queue as next track instead of playing immediately")

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
        out = client.tidal_search_and_play(
            player_id,
            args.queries,
            search_type=args.search_type,
            dry_run=args.dry_run,
            play_action="insert" if getattr(args, "next", False) else "play",
        )
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
