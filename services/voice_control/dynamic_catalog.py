"""Dynamic music catalog and user library phonetic indexer for PocoBox."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .jamo_util import find_best_jamo_match, jamo_phonetic_similarity

log = logging.getLogger("pocobox-voice.dynamic_catalog")


class DynamicMusicCatalog:
    """In-memory dynamic music catalog indexed from LMS favorites, recent playback, and playlists.

    Provides sub-millisecond Jamo phonetic matching against user's personal listening history.
    """

    def __init__(self, update_interval_sec: float = 300.0) -> None:
        self.update_interval_sec = update_interval_sec
        self._lock = threading.Lock()
        self._last_update_ts: float = 0.0

        # Cached entities: entity_name -> metadata
        self._artists: dict[str, dict[str, Any]] = {}
        self._tracks: dict[str, dict[str, Any]] = {}
        self._playlists: dict[str, dict[str, Any]] = {}

    @property
    def total_entities(self) -> int:
        with self._lock:
            return len(self._artists) + len(self._tracks) + len(self._playlists)

    def add_artist(self, name: str, source: str = "manual") -> None:
        clean = (name or "").strip()
        if not clean or len(clean) < 2:
            return
        with self._lock:
            self._artists[clean] = {"source": source, "added_at": time.time()}

    def add_track(self, title: str, artist: str = "", source: str = "manual") -> None:
        clean_title = (title or "").strip()
        clean_artist = (artist or "").strip()
        if not clean_title or len(clean_title) < 2:
            return
        with self._lock:
            self._tracks[clean_title] = {
                "artist": clean_artist,
                "source": source,
                "added_at": time.time(),
            }
            if clean_artist and len(clean_artist) >= 2:
                self._artists[clean_artist] = {"source": source, "added_at": time.time()}

    def add_playlist(self, name: str, source: str = "manual") -> None:
        clean = (name or "").strip()
        if not clean or len(clean) < 2:
            return
        with self._lock:
            self._playlists[clean] = {"source": source, "added_at": time.time()}

    def update_from_lms(self, lms_client: Any, player_id: str) -> int:
        """Fetch favorites, playlist items, and recent status from LMS and populate index."""
        if lms_client is None or not player_id:
            return 0

        added_count = 0
        t0 = time.perf_counter()

        # 1. Fetch LMS favorites
        try:
            favs = lms_client.list_favorites(player_id, limit=60)
            for item in favs:
                name = str(item.get("name") or "").strip()
                if name:
                    # Often favorite names are "Artist - Track" or "Playlist Name"
                    if " - " in name:
                        art, trk = name.split(" - ", 1)
                        self.add_track(trk, art, source="lms_favorite")
                        added_count += 1
                    else:
                        self.add_playlist(name, source="lms_favorite")
                        added_count += 1
        except Exception as exc:
            log.debug("DynamicMusicCatalog: failed to fetch LMS favorites: %s", exc)

        # 2. Fetch current/recent playlist queue
        try:
            status = lms_client.player_status(player_id)
            loop = status.get("playlist_loop") or []
            for trk in loop:
                title = str(trk.get("title") or "").strip()
                artist = str(trk.get("artist") or "").strip()
                if title:
                    self.add_track(title, artist, source="lms_queue")
                    added_count += 1
        except Exception as exc:
            log.debug("DynamicMusicCatalog: failed to fetch LMS player status: %s", exc)

        # 3. Fetch LMS named playlists
        try:
            res = lms_client.call(player_id, ["playlists", 0, 50])
            playlists = (res.get("result") or {}).get("playlists_loop") or []
            for pl in playlists:
                pl_name = str(pl.get("playlist") or "").strip()
                if pl_name:
                    self.add_playlist(pl_name, source="lms_playlist")
                    added_count += 1
        except Exception as exc:
            log.debug("DynamicMusicCatalog: failed to fetch LMS playlists: %s", exc)

        with self._lock:
            self._last_update_ts = time.time()

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        log.info(
            "DynamicMusicCatalog updated: %d entities (artists=%d, tracks=%d, playlists=%d) in %.1fms",
            added_count,
            len(self._artists),
            len(self._tracks),
            len(self._playlists),
            elapsed_ms,
        )
        return added_count

    def match_entity(
        self,
        query: str,
        *,
        threshold: float = 0.68,
    ) -> tuple[str | None, str | None, dict[str, Any], float]:
        """Fuzzy match query against indexed user library using Jamo phonetic distance.

        Returns (canonical_name, entity_type, metadata, similarity_score).
        entity_type is 'artist', 'track', 'playlist', or None.
        Runs in < 0.5ms.
        """
        clean_q = "".join(query.split()).strip()
        if not clean_q or len(clean_q) < 2:
            return None, None, {}, 0.0

        with self._lock:
            artists_copy = dict(self._artists)
            tracks_copy = dict(self._tracks)
            playlists_copy = dict(self._playlists)

        # 1. Exact match check
        for name, meta in artists_copy.items():
            if "".join(name.split()).lower() == clean_q.lower():
                return name, "artist", meta, 1.0

        for name, meta in tracks_copy.items():
            if "".join(name.split()).lower() == clean_q.lower():
                return name, "track", meta, 1.0

        for name, meta in playlists_copy.items():
            if "".join(name.split()).lower() == clean_q.lower():
                return name, "playlist", meta, 1.0

        # 2. Phonetic Jamo Fuzzy Matching
        best_name: str | None = None
        best_type: str | None = None
        best_meta: dict[str, Any] = {}
        best_score = 0.0

        # Search artists
        a_cand, a_score = find_best_jamo_match(clean_q, artists_copy.keys(), threshold=threshold)
        if a_cand and a_score > best_score:
            best_score = a_score
            best_name = a_cand
            best_type = "artist"
            best_meta = artists_copy[a_cand]

        # Search tracks
        t_cand, t_score = find_best_jamo_match(clean_q, tracks_copy.keys(), threshold=threshold)
        if t_cand and t_score > best_score:
            best_score = t_score
            best_name = t_cand
            best_type = "track"
            best_meta = tracks_copy[t_cand]

        # Search playlists
        p_cand, p_score = find_best_jamo_match(clean_q, playlists_copy.keys(), threshold=threshold)
        if p_cand and p_score > best_score:
            best_score = p_score
            best_name = p_cand
            best_type = "playlist"
            best_meta = playlists_copy[p_cand]

        if best_name and best_score >= threshold:
            return best_name, best_type, best_meta, round(best_score, 4)

        return None, None, {}, round(best_score, 4)


# Global singleton instance for voice control pipeline
_GLOBAL_CATALOG: DynamicMusicCatalog | None = None


def get_dynamic_catalog() -> DynamicMusicCatalog:
    global _GLOBAL_CATALOG
    if _GLOBAL_CATALOG is None:
        _GLOBAL_CATALOG = DynamicMusicCatalog()
    return _GLOBAL_CATALOG
