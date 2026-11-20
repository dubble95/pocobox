"""PocoBox YouTube & Continuous K-Pop Music Video Streaming Service.

Provides a rich, curated catalog of top official K-pop Music Videos,
dynamic YouTube search integration via LMS, playlist management,
and seamless continuous video playback state tracking.
"""

from __future__ import annotations

import logging
import random
import re
from typing import Any
import urllib.parse

log = logging.getLogger("pocobox-voice.youtube")

# Curated catalog of verified, embed-friendly official K-Pop Music Videos.
# Each entry includes: id (YouTube video ID), title, artist, category, year.
KPOP_MV_CATALOG: list[dict[str, str]] = [
    # --- Top Girl Groups ---
    {
        "id": "11cta61wi0g",
        "title": "Hype Boy (Official MV)",
        "artist": "NewJeans (뉴진스)",
        "category": "girlgroup",
        "year": "2022",
    },
    {
        "id": "ArmDp-zijuc",
        "title": "Super Shy (Official MV)",
        "artist": "NewJeans (뉴진스)",
        "category": "girlgroup",
        "year": "2023",
    },
    {
        "id": "vrM2mrI83uk",
        "title": "How Sweet (Official MV)",
        "artist": "NewJeans (뉴진스)",
        "category": "girlgroup",
        "year": "2024",
    },
    {
        "id": "XCkpalH6Ppk",
        "title": "ETA (Official MV)",
        "artist": "NewJeans (뉴진스)",
        "category": "girlgroup",
        "year": "2023",
    },
    {
        "id": "pSUydWEqKwE",
        "title": "Ditto (Official MV side A)",
        "artist": "NewJeans (뉴진스)",
        "category": "girlgroup",
        "year": "2022",
    },
    {
        "id": "phuiAIQaU40",
        "title": "Supernova (Official MV)",
        "artist": "aespa (에스파)",
        "category": "girlgroup",
        "year": "2024",
    },
    {
        "id": "nFYwcndNuOY",
        "title": "Armageddon (Official MV)",
        "artist": "aespa (에스파)",
        "category": "girlgroup",
        "year": "2024",
    },
    {
        "id": "jWQx2f-CErU",
        "title": "Whiplash (Official MV)",
        "artist": "aespa (에스파)",
        "category": "girlgroup",
        "year": "2024",
    },
    {
        "id": "4TWR90KJl84",
        "title": "Next Level (Official MV)",
        "artist": "aespa (에스파)",
        "category": "girlgroup",
        "year": "2021",
    },
    {
        "id": "Os_heh8vPfs",
        "title": "Spicy (Official MV)",
        "artist": "aespa (에스파)",
        "category": "girlgroup",
        "year": "2023",
    },
    {
        "id": "6ZUIwj3FgUY",
        "title": "I AM (Official MV)",
        "artist": "IVE (아이브)",
        "category": "girlgroup",
        "year": "2023",
    },
    {
        "id": "Y8JFxS1HlDo",
        "title": "LOVE DIVE (Official MV)",
        "artist": "IVE (아이브)",
        "category": "girlgroup",
        "year": "2022",
    },
    {
        "id": "F0B7HDiY-10",
        "title": "After LIKE (Official MV)",
        "artist": "IVE (아이브)",
        "category": "girlgroup",
        "year": "2022",
    },
    {
        "id": "hLvWy2b857I",
        "title": "Perfect Night (Official MV)",
        "artist": "LE SSERAFIM (르세라핌)",
        "category": "girlgroup",
        "year": "2023",
    },
    {
        "id": "n6B5gQXlB-0",
        "title": "CRAZY (Official MV)",
        "artist": "LE SSERAFIM (르세라핌)",
        "category": "girlgroup",
        "year": "2024",
    },
    {
        "id": "bNKXxwOQ48E",
        "title": "EASY (Official MV)",
        "artist": "LE SSERAFIM (르세라핌)",
        "category": "girlgroup",
        "year": "2024",
    },
    {
        "id": "pyf8cbqyfPs",
        "title": "ANTIFRAGILE (Official MV)",
        "artist": "LE SSERAFIM (르세라핌)",
        "category": "girlgroup",
        "year": "2022",
    },
    {
        "id": "Vk5-c_v4gMU",
        "title": "Magnetic (Official MV)",
        "artist": "ILLIT (아일릿)",
        "category": "girlgroup",
        "year": "2024",
    },
    {
        "id": "g_WHB3yoQdQ",
        "title": "Cherish (My Love) (Official MV)",
        "artist": "ILLIT (아일릿)",
        "category": "girlgroup",
        "year": "2024",
    },
    {
        "id": "v_gO_P8gug8",
        "title": "퀸카 (Queencard) (Official MV)",
        "artist": "(여자)아이들 ((G)I-DLE)",
        "category": "girlgroup",
        "year": "2023",
    },
    {
        "id": "vBJVWOOsB4A",
        "title": "나는 아픈 건 딱 질색이니까 (Fate)",
        "artist": "(여자)아이들 ((G)I-DLE)",
        "category": "girlgroup",
        "year": "2024",
    },
    {
        "id": "-Uw97CvOgKU",
        "title": "SHEESH (Official MV)",
        "artist": "BABYMONSTER (베이비몬스터)",
        "category": "girlgroup",
        "year": "2024",
    },
    {
        "id": "zbbX4a_4v7s",
        "title": "DRIP (Official MV)",
        "artist": "BABYMONSTER (베이비몬스터)",
        "category": "girlgroup",
        "year": "2024",
    },
    {
        "id": "qCDPprTDkJE",
        "title": "Pink Venom (Official MV)",
        "artist": "BLACKPINK (블랙핑크)",
        "category": "girlgroup",
        "year": "2022",
    },
    {
        "id": "950BdJKBhGo",
        "title": "Shut Down (Official MV)",
        "artist": "BLACKPINK (블랙핑크)",
        "category": "girlgroup",
        "year": "2022",
    },
    {
        "id": "ioNng23DkIM",
        "title": "How You Like That (Official MV)",
        "artist": "BLACKPINK (블랙핑크)",
        "category": "girlgroup",
        "year": "2020",
    },
    {
        "id": "WA97qDH6w9I",
        "title": "Sticky (Official MV)",
        "artist": "KISS OF LIFE (키스오브라이프)",
        "category": "girlgroup",
        "year": "2024",
    },
    {
        "id": "9yfrVqCcrS0",
        "title": "내 이름 맑음 (My Name is Malguem) MV",
        "artist": "QWER (큐더블유이알)",
        "category": "girlgroup",
        "year": "2024",
    },
    {
        "id": "ImuWa3SJulY",
        "title": "고민중독 (T.B.H) Official MV",
        "artist": "QWER (큐더블유이알)",
        "category": "girlgroup",
        "year": "2024",
    },
    # --- Top Boy Groups ---
    {
        "id": "gdZLi9oWNZg",
        "title": "Dynamite (Official MV)",
        "artist": "BTS (방탄소년단)",
        "category": "boygroup",
        "year": "2020",
    },
    {
        "id": "WMweEpGlu_U",
        "title": "Butter (Official MV)",
        "artist": "BTS (방탄소년단)",
        "category": "boygroup",
        "year": "2021",
    },
    {
        "id": "XsX3ATc3FbA",
        "title": "작은 것들을 위한 시 (Boy With Luv) MV",
        "artist": "BTS (방탄소년단)",
        "category": "boygroup",
        "year": "2019",
    },
    {
        "id": "0P0aQrm2LSQ",
        "title": "Chk Chk Boom (Official MV)",
        "artist": "Stray Kids (스트레이 키즈)",
        "category": "boygroup",
        "year": "2024",
    },
    {
        "id": "NqLB6ub1BOw",
        "title": "MANIAC (Official MV)",
        "artist": "Stray Kids (스트레이 키즈)",
        "category": "boygroup",
        "year": "2022",
    },
    {
        "id": "JsOOis4bBFg",
        "title": "특 (S-Class) (Official MV)",
        "artist": "Stray Kids (스트레이 키즈)",
        "category": "boygroup",
        "year": "2023",
    },
    {
        "id": "_ZAgIHmHLdc",
        "title": "MAESTRO (Official MV)",
        "artist": "SEVENTEEN (세븐틴)",
        "category": "boygroup",
        "year": "2024",
    },
    {
        "id": "-GQg25oP0S4",
        "title": "손오공 (Super) (Official MV)",
        "artist": "SEVENTEEN (세븐틴)",
        "category": "boygroup",
        "year": "2023",
    },
    {
        "id": "cCkAcVOS3ig",
        "title": "HOT (Official MV)",
        "artist": "SEVENTEEN (세븐틴)",
        "category": "boygroup",
        "year": "2022",
    },
    {
        "id": "97Fk8pZ0GfE",
        "title": "Boom Boom Bass (Official MV)",
        "artist": "RIIZE (라이즈)",
        "category": "boygroup",
        "year": "2024",
    },
    {
        "id": "vJ3Zpkm8Lg0",
        "title": "Get A Guitar (Official MV)",
        "artist": "RIIZE (라이즈)",
        "category": "boygroup",
        "year": "2023",
    },
    {
        "id": "hVAc1Vf2ITU",
        "title": "첫 만남은 계획대로 되지 않아 (plot twist) MV",
        "artist": "TWS (투어스)",
        "category": "boygroup",
        "year": "2024",
    },
    # --- Top Soloists & Collaborations ---
    {
        "id": "ekr2nIex040",
        "title": "APT. (Official MV)",
        "artist": "ROSE & Bruno Mars",
        "category": "solo",
        "year": "2024",
    },
    {
        "id": "bfe4txvGmGQ",
        "title": "Mantra (Official MV)",
        "artist": "JENNIE (제니)",
        "category": "solo",
        "year": "2024",
    },
    {
        "id": "U1_0Vc-9mNw",
        "title": "UP (KARINA Solo) (Special Video)",
        "artist": "aespa KARINA (카리나)",
        "category": "solo",
        "year": "2024",
    },
    {
        "id": "cJ-KtgbuId0",
        "title": "꽃 (FLOWER) (Official MV)",
        "artist": "JISOO (지수)",
        "category": "solo",
        "year": "2023",
    },
    {
        "id": "JleoAppaxi0",
        "title": "Love wins all (Official MV)",
        "artist": "IU (아이유)",
        "category": "solo",
        "year": "2024",
    },
    {
        "id": "2iPZ5C_u478",
        "title": "Shopper (Official MV)",
        "artist": "IU (아이유)",
        "category": "solo",
        "year": "2024",
    },
    {
        "id": "0-q1K8530JF",
        "title": "Celebrity (Official MV)",
        "artist": "IU (아이유)",
        "category": "solo",
        "year": "2021",
    },
    {
        "id": "QU9c0053UAU",
        "title": "Seven (feat. Latto) (Official MV)",
        "artist": "정국 (Jung Kook)",
        "category": "solo",
        "year": "2023",
    },
    {
        "id": "DffX44_N0nU",
        "title": "SPOT! (feat. JENNIE) (Official MV)",
        "artist": "지코 (ZICO)",
        "category": "solo",
        "year": "2024",
    },
    {
        "id": "tKxUf50y4mQ",
        "title": "녹아내려요 (Melt Down) MV",
        "artist": "DAY6 (데이식스)",
        "category": "band",
        "year": "2024",
    },
    {
        "id": "vnS_6zGdeeg",
        "title": "한 페이지가 될 수 있게 (Time of Our Life) MV",
        "artist": "DAY6 (데이식스)",
        "category": "band",
        "year": "2019",
    },
]


def _format_item(item: dict[str, str]) -> dict[str, Any]:
    vid = item["id"]
    return {
        "id": vid,
        "title": item.get("title", ""),
        "artist": item.get("artist", ""),
        "category": item.get("category", "kpop"),
        "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "url": f"https://www.youtube.com/watch?v={vid}",
    }


def get_kpop_playlist(
    category: str = "all",
    *,
    shuffle: bool = True,
    seed: int | None = None,
) -> list[dict[str, Any]]:
    """Return a curated K-Pop MV playlist, optionally filtered and shuffled."""
    cat = (category or "all").strip().lower()
    if cat in ("all", "kpop", "popular", "mv"):
        items = list(KPOP_MV_CATALOG)
    elif cat in ("girl", "girlgroup", "걸그룹"):
        items = [x for x in KPOP_MV_CATALOG if x.get("category") == "girlgroup"]
    elif cat in ("boy", "boygroup", "보이그룹"):
        items = [x for x in KPOP_MV_CATALOG if x.get("category") == "boygroup"]
    elif cat in ("solo", "솔로"):
        items = [x for x in KPOP_MV_CATALOG if x.get("category") in ("solo", "band")]
    else:
        # Match query substring against artist, title, or category
        q_norm = cat.replace(" ", "").lower()
        items = [
            x
            for x in KPOP_MV_CATALOG
            if q_norm in x.get("artist", "").replace(" ", "").lower()
            or q_norm in x.get("title", "").replace(" ", "").lower()
            or q_norm in x.get("category", "").lower()
        ]
        if not items:
            items = list(KPOP_MV_CATALOG)

    if shuffle:
        rng = random.Random(seed) if seed is not None else random
        items = list(items)
        rng.shuffle(items)

    return [_format_item(x) for x in items]


def extract_video_id_from_url_or_thumb(url: str) -> str | None:
    """Extract YouTube 11-char video ID from various URL patterns or imageproxy URLs."""
    if not url:
        return None
    # Check imageproxy or direct thumbnail URL (e.g. /vi/<id>/hqdefault.jpg or /vi%2F<id>%2F)
    m = re.search(r"(?:vi[/]|vi%2F)([a-zA-Z0-9_-]{11})(?:[/]|%2F)", url)
    if m:
        return m.group(1)
    # Check standard youtube URLs (watch?v=..., youtu.be/..., /v/...)
    m = re.search(r"(?:v=|youtu\.be/|/v/|/embed/)([a-zA-Z0-9_-]{11})", url)
    if m:
        return m.group(1)
    # If the string itself is 11 chars
    clean = url.strip()
    if len(clean) == 11 and re.fullmatch(r"[a-zA-Z0-9_-]{11}", clean):
        return clean
    return None


def search_youtube_videos(
    query: str,
    lms_client: Any | None = None,
    player_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search YouTube for music videos matching query.

    Checks curated catalog first for known artists/songs; then queries LMS YouTube
    Video Search (if available) to retrieve dynamic results.
    """
    q_clean = (query or "").strip()
    if not q_clean or q_clean.lower() in ("kpop", "케이팝", "뮤비", "뮤직비디오", "유튜브", "인기곡"):
        return get_kpop_playlist("all", shuffle=True)

    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    # 1. Check curated catalog matching artist or title
    q_norm = q_clean.replace(" ", "").lower()
    for item in KPOP_MV_CATALOG:
        art_norm = item.get("artist", "").replace(" ", "").lower()
        tit_norm = item.get("title", "").replace(" ", "").lower()
        if q_norm in art_norm or q_norm in tit_norm or art_norm in q_norm:
            formatted = _format_item(item)
            vid = formatted["id"]
            if vid not in seen_ids:
                seen_ids.add(vid)
                results.append(formatted)

    # 2. Query LMS YouTube app if available
    if lms_client is not None:
        try:
            pid = player_id or lms_client.resolve_player_id("pocobox")
            # Query Video Search (item_id:1)
            search_query = f"{q_clean} MV" if not re.search(r"(?:mv|뮤비|뮤직비디오|music video)", q_clean, re.IGNORECASE) else q_clean
            resp = lms_client.call(pid, ["youtube", "items", 0, limit, "item_id:1", f"search:{search_query}"])
            raw_items = resp.get("loop_loop") or []
            for it in raw_items:
                name = it.get("name", "")
                img = it.get("image", "")
                vid = extract_video_id_from_url_or_thumb(img)
                if not vid and "url" in it:
                    vid = extract_video_id_from_url_or_thumb(it["url"])
                if vid and vid not in seen_ids:
                    seen_ids.add(vid)
                    results.append({
                        "id": vid,
                        "title": name,
                        "artist": q_clean,
                        "category": "search",
                        "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                        "url": f"https://www.youtube.com/watch?v={vid}",
                    })
                    if len(results) >= limit:
                        break
        except Exception as exc:
            log.debug("LMS YouTube video search failed: %s", exc)

    if not results:
        # Fallback to general K-pop catalog
        return get_kpop_playlist("all", shuffle=True)

    return results


class YouTubePlaybackManager:
    """Manages active YouTube video queue, index, and playback state for continuous playback."""

    def __init__(self) -> None:
        self.active: bool = False
        self.playing: bool = False
        self.query: str = "kpop"
        self.queue: list[dict[str, Any]] = []
        self.index: int = 0
        self.volume: int = 100
        self.muted: bool = False

    def load_queue(
        self,
        videos: list[dict[str, Any]],
        query: str = "kpop",
        start_index: int = 0,
    ) -> dict[str, Any]:
        """Load a list of videos and prepare for continuous playback."""
        self.queue = list(videos)
        self.query = query
        self.index = max(0, min(len(self.queue) - 1, start_index)) if self.queue else 0
        self.active = bool(self.queue)
        self.playing = bool(self.queue)
        cur = self.current_video()
        return {
            "ok": True,
            "active": self.active,
            "playing": self.playing,
            "index": self.index,
            "total": len(self.queue),
            "video": cur,
        }

    def current_video(self) -> dict[str, Any] | None:
        if self.queue and 0 <= self.index < len(self.queue):
            return self.queue[self.index]
        return None

    def next_video(self) -> dict[str, Any] | None:
        """Advance to next video (loops back to 0 if at end for true continuous playback)."""
        if not self.queue:
            return None
        self.index = (self.index + 1) % len(self.queue)
        self.playing = True
        return self.current_video()

    def prev_video(self) -> dict[str, Any] | None:
        """Go back to previous video in queue."""
        if not self.queue:
            return None
        self.index = (self.index - 1) % len(self.queue)
        self.playing = True
        return self.current_video()

    def set_playing(self, playing: bool) -> None:
        self.playing = bool(playing)

    def stop(self) -> None:
        self.active = False
        self.playing = False

    def to_dict(self) -> dict[str, Any]:
        cur = self.current_video()
        return {
            "active": self.active,
            "playing": self.playing,
            "query": self.query,
            "index": self.index,
            "total": len(self.queue),
            "volume": self.volume,
            "current": cur,
        }
