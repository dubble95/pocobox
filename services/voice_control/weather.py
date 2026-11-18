"""Live location-aware weather service for PocoBox (KMA RSS primary + met.no / wttr.in fallback)."""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Callable

from .aliases import strip_wake_prefix

log = logging.getLogger("pocobox-voice.weather")

_KST = datetime.timezone(datetime.timedelta(hours=9))
_DEFAULT_LOCATION_NAME = "분당"
_DEFAULT_KMA_ZONE = "4113551000"  # 경기도 성남시분당구 분당동
_DEFAULT_LAT = 37.3827
_DEFAULT_LON = 127.1189
_CACHE_TTL_SEC = 300.0
_USER_AGENT = "PocoBox/1.0 (+https://github.com/pocobox)"


@dataclass(frozen=True)
class WeatherLocation:
    name: str
    en_name: str
    kma_zone: str | None = None
    lat: float | None = None
    lon: float | None = None
    wttr_query: str = ""


# Verified Korea Meteorological Administration (KMA) 10-digit dong RSS zone codes + coordinates.
# Keys are matched case-insensitively (longest match first).
KNOWN_LOCATIONS: dict[str, WeatherLocation] = {
    # 성남시 분당구 (기본 지역 및 세부 동)
    "분당": WeatherLocation("분당", "Bundang", "4113551000", 37.3827, 127.1189, "Bundang,South Korea"),
    "분당구": WeatherLocation("분당", "Bundang", "4113551000", 37.3827, 127.1189, "Bundang,South Korea"),
    "분당동": WeatherLocation("분당동", "Bundang-dong", "4113551000", 37.3728, 127.1367, "Bundang,South Korea"),
    "bundang": WeatherLocation("분당", "Bundang", "4113551000", 37.3827, 127.1189, "Bundang,South Korea"),
    "수내": WeatherLocation("수내동", "Sunae", "4113552000", 37.3782, 127.1143, "Bundang,South Korea"),
    "수내동": WeatherLocation("수내동", "Sunae", "4113552000", 37.3782, 127.1143, "Bundang,South Korea"),
    "정자": WeatherLocation("정자동", "Jeongja", "4113555000", 37.3667, 127.1082, "Bundang,South Korea"),
    "정자동": WeatherLocation("정자동", "Jeongja", "4113555000", 37.3667, 127.1082, "Bundang,South Korea"),
    "서현": WeatherLocation("서현동", "Seohyeon", "4113558000", 37.3851, 127.1235, "Bundang,South Korea"),
    "서현동": WeatherLocation("서현동", "Seohyeon", "4113558000", 37.3851, 127.1235, "Bundang,South Korea"),
    "이매": WeatherLocation("이매동", "Imae", "4113560000", 37.3961, 127.1282, "Bundang,South Korea"),
    "이매동": WeatherLocation("이매동", "Imae", "4113560000", 37.3961, 127.1282, "Bundang,South Korea"),
    "야탑": WeatherLocation("야탑동", "Yatap", "4113562000", 37.4114, 127.1287, "Bundang,South Korea"),
    "야탑동": WeatherLocation("야탑동", "Yatap", "4113562000", 37.4114, 127.1287, "Bundang,South Korea"),
    "판교": WeatherLocation("판교", "Pangyo", "4113565000", 37.3947, 127.1112, "Pangyo,South Korea"),
    "판교동": WeatherLocation("판교", "Pangyo", "4113565000", 37.3947, 127.1112, "Pangyo,South Korea"),
    "pangyo": WeatherLocation("판교", "Pangyo", "4113565000", 37.3947, 127.1112, "Pangyo,South Korea"),
    "삼평동": WeatherLocation("삼평동", "Sampyeong", "4113565500", 37.4020, 127.1086, "Pangyo,South Korea"),
    "백현동": WeatherLocation("백현동", "Baekhyeon", "4113565700", 37.3895, 127.1120, "Pangyo,South Korea"),
    "금곡동": WeatherLocation("금곡동", "Geumgok", "4113566200", 37.3504, 127.1089, "Bundang,South Korea"),
    "구미동": WeatherLocation("구미동", "Gumi", "4113567000", 37.3401, 127.1125, "Bundang,South Korea"),
    "미금": WeatherLocation("구미동", "Migeum", "4113567000", 37.3500, 127.1090, "Bundang,South Korea"),
    "운중동": WeatherLocation("운중동", "Unjung", "4113568000", 37.3921, 127.0789, "Pangyo,South Korea"),
    # 수도권 및 전국 주요 도시
    "성남": WeatherLocation("성남", "Seongnam", "4113151000", 37.4200, 127.1267, "Seongnam,South Korea"),
    "seongnam": WeatherLocation("성남", "Seongnam", "4113151000", 37.4200, 127.1267, "Seongnam,South Korea"),
    "서울": WeatherLocation("서울", "Seoul", "1111061500", 37.5665, 126.9780, "Seoul,South Korea"),
    "seoul": WeatherLocation("서울", "Seoul", "1111061500", 37.5665, 126.9780, "Seoul,South Korea"),
    "강남": WeatherLocation("강남", "Gangnam", "1168064000", 37.4979, 127.0276, "Gangnam,Seoul"),
    "서초": WeatherLocation("서초", "Seocho", "1165051000", 37.4837, 127.0324, "Seocho,Seoul"),
    "송파": WeatherLocation("송파", "Songpa", "1171051000", 37.5145, 127.1059, "Songpa,Seoul"),
    "잠실": WeatherLocation("송파", "Jamsil", "1171051000", 37.5133, 127.1001, "Songpa,Seoul"),
    "마포": WeatherLocation("마포", "Mapo", "1144055500", 37.5663, 126.9019, "Mapo,Seoul"),
    "홍대": WeatherLocation("마포", "Hongdae", "1144055500", 37.5563, 126.9220, "Mapo,Seoul"),
    "영등포": WeatherLocation("영등포", "Yeongdeungpo", "1156051500", 37.5264, 126.8962, "Seoul,South Korea"),
    "여의도": WeatherLocation("영등포", "Yeouido", "1156051500", 37.5219, 126.9245, "Seoul,South Korea"),
    "수원": WeatherLocation("수원", "Suwon", "4111565000", 37.2636, 127.0286, "Suwon,South Korea"),
    "용인": WeatherLocation("용인", "Yongin", "4146551000", 37.3220, 127.0977, "Yongin,South Korea"),
    "수지": WeatherLocation("용인 수지", "Suji", "4146551000", 37.3220, 127.0977, "Yongin,South Korea"),
    "일산": WeatherLocation("일산", "Ilsan", "4128551000", 37.6584, 126.7700, "Goyang,South Korea"),
    "고양": WeatherLocation("고양", "Goyang", "4128551000", 37.6584, 126.8320, "Goyang,South Korea"),
    "안양": WeatherLocation("안양", "Anyang", "4117151000", 37.3943, 126.9568, "Anyang,South Korea"),
    "과천": WeatherLocation("과천", "Gwacheon", "4129051000", 37.4292, 126.9876, "Gwacheon,South Korea"),
    "하남": WeatherLocation("하남", "Hanam", "4145051000", 37.5393, 127.2149, "Hanam,South Korea"),
    "평택": WeatherLocation("평택", "Pyeongtaek", "4122051000", 36.9921, 127.1129, "Pyeongtaek,South Korea"),
    "인천": WeatherLocation("인천", "Incheon", "2820051000", 37.4563, 126.7052, "Incheon,South Korea"),
    "incheon": WeatherLocation("인천", "Incheon", "2820051000", 37.4563, 126.7052, "Incheon,South Korea"),
    "부산": WeatherLocation("부산", "Busan", "2611053000", 35.1796, 129.0756, "Busan,South Korea"),
    "busan": WeatherLocation("부산", "Busan", "2611053000", 35.1796, 129.0756, "Busan,South Korea"),
    "대구": WeatherLocation("대구", "Daegu", "2711051700", 35.8714, 128.6014, "Daegu,South Korea"),
    "daegu": WeatherLocation("대구", "Daegu", "2711051700", 35.8714, 128.6014, "Daegu,South Korea"),
    "대전": WeatherLocation("대전", "Daejeon", "3011051500", 36.3504, 127.3845, "Daejeon,South Korea"),
    "daejeon": WeatherLocation("대전", "Daejeon", "3011051500", 36.3504, 127.3845, "Daejeon,South Korea"),
    "울산": WeatherLocation("울산", "Ulsan", "3111051000", 35.5384, 129.3114, "Ulsan,South Korea"),
    "세종": WeatherLocation("세종", "Sejong", "3611051000", 36.4801, 127.2890, "Sejong,South Korea"),
    "제주": WeatherLocation("제주", "Jeju", "5011054000", 33.4996, 126.5312, "Jeju,South Korea"),
    "제주도": WeatherLocation("제주", "Jeju", "5011054000", 33.4996, 126.5312, "Jeju,South Korea"),
    "jeju": WeatherLocation("제주", "Jeju", "5011054000", 33.4996, 126.5312, "Jeju,South Korea"),
    "서귀포": WeatherLocation("서귀포", "Seogwipo", "5013051000", 33.2541, 126.5601, "Seogwipo,South Korea"),
    "강릉": WeatherLocation("강릉", "Gangneung", "5115052000", 37.7519, 128.8761, "Gangneung,South Korea"),
    "춘천": WeatherLocation("춘천", "Chuncheon", "5111052000", 37.8813, 127.7298, "Chuncheon,South Korea"),
    "속초": WeatherLocation("속초", "Sokcho", "5121051000", 38.2070, 128.5918, "Sokcho,South Korea"),
    "전주": WeatherLocation("전주", "Jeonju", "5211151000", 35.8242, 127.1480, "Jeonju,South Korea"),
    "창원": WeatherLocation("창원", "Changwon", "4812151000", 35.2280, 128.6811, "Changwon,South Korea"),
    "포항": WeatherLocation("포항", "Pohang", "4711352000", 36.0190, 129.3435, "Pohang,South Korea"),
    "천안": WeatherLocation("천안", "Cheonan", "4413151000", 36.8151, 127.1139, "Cheonan,South Korea"),
    # 좌표 기반 (met.no / wttr.in) 국내 및 해외 주요 도시
    "광주": WeatherLocation("광주", "Gwangju", None, 35.1595, 126.8526, "Gwangju,South Korea"),
    "청주": WeatherLocation("청주", "Cheongju", None, 36.6424, 127.4890, "Cheongju,South Korea"),
    "여수": WeatherLocation("여수", "Yeosu", None, 34.7604, 127.6622, "Yeosu,South Korea"),
    "경주": WeatherLocation("경주", "Gyeongju", None, 35.8562, 129.2247, "Gyeongju,South Korea"),
    "화성": WeatherLocation("화성", "Hwaseong", None, 37.1995, 126.8312, "Hwaseong,South Korea"),
    "동탄": WeatherLocation("동탄", "Dongtan", None, 37.2006, 127.0748, "Hwaseong,South Korea"),
    "도쿄": WeatherLocation("도쿄", "Tokyo", None, 35.6762, 139.6503, "Tokyo"),
    "tokyo": WeatherLocation("도쿄", "Tokyo", None, 35.6762, 139.6503, "Tokyo"),
    "오사카": WeatherLocation("오사카", "Osaka", None, 34.6937, 135.5023, "Osaka"),
    "osaka": WeatherLocation("오사카", "Osaka", None, 34.6937, 135.5023, "Osaka"),
    "뉴욕": WeatherLocation("뉴욕", "New York", None, 40.7128, -74.0060, "New York"),
    "new york": WeatherLocation("뉴욕", "New York", None, 40.7128, -74.0060, "New York"),
    "런던": WeatherLocation("런던", "London", None, 51.5074, -0.1278, "London"),
    "london": WeatherLocation("런던", "London", None, 51.5074, -0.1278, "London"),
    "파리": WeatherLocation("파리", "Paris", None, 48.8566, 2.3522, "Paris"),
    "paris": WeatherLocation("파리", "Paris", None, 48.8566, 2.3522, "Paris"),
    "로스앤젤레스": WeatherLocation("로스앤젤레스", "Los Angeles", None, 34.0522, -118.2437, "Los Angeles"),
    "샌프란시스코": WeatherLocation("샌프란시스코", "San Francisco", None, 37.7749, -122.4194, "San Francisco"),
    "시드니": WeatherLocation("시드니", "Sydney", None, -33.8688, 151.2093, "Sydney"),
    "싱가포르": WeatherLocation("싱가포르", "Singapore", None, 1.3521, 103.8198, "Singapore"),
    "방콕": WeatherLocation("방콕", "Bangkok", None, 13.7563, 100.5018, "Bangkok"),
}

_SORTED_LOCATION_KEYS = sorted(KNOWN_LOCATIONS.keys(), key=len, reverse=True)

_LOCAL_PLACEHOLDERS = {
    "",
    "여기",
    "이곳",
    "우리집",
    "우리 집",
    "우리동네",
    "우리 동네",
    "동네",
    "현재 위치",
    "현위치",
    "밖",
    "밖에",
    "바깥",
    "here",
    "local",
    "outside",
    "current location",
}

_WEATHER_STOPWORDS = {
    "오늘",
    "지금",
    "현재",
    "내일",
    "명일",
    "모레",
    "내일모레",
    "글피",
    "오전",
    "오후",
    "아침",
    "점심",
    "저녁",
    "밤",
    "새벽",
    "날씨",
    "일기예보",
    "예보",
    "기온",
    "온도",
    "몇",
    "도",
    "도야",
    "어때",
    "어때요",
    "어떨까",
    "어떨까요",
    "어떠니",
    "알려줘",
    "알려주세요",
    "알려",
    "말해줘",
    "좀",
    "비",
    "비가",
    "눈",
    "눈이",
    "와",
    "오나",
    "오나요",
    "오니",
    "올까",
    "올까요",
    "우산",
    "챙겨야",
    "필요해",
    "추워",
    "더워",
    "쌀쌀해",
    "따뜻해",
    "밖에",
    "밖",
    "여기",
    "우리",
    "동네",
    "집",
    "포코",
    "포코야",
    "포코박스",
    "헤이",
    "오케이",
}


@dataclass
class WeatherForecast:
    location_name: str
    en_location_name: str
    day_offset: int
    provider: str
    condition_ko: str
    condition_en: str
    current_temp_c: float | None = None
    min_temp_c: float | None = None
    max_temp_c: float | None = None
    precip_prob_pct: int | None = None
    has_precip_now: bool = False
    has_precip_expected: bool = False
    precip_kind_ko: str = "비"
    humidity_pct: int | None = None
    wind_ms: float | None = None
    source_area: str = ""


_cache_lock = threading.Lock()
_forecast_cache: dict[str, tuple[float, Any]] = {}


def clear_weather_cache() -> None:
    with _cache_lock:
        _forecast_cache.clear()


def get_default_location() -> WeatherLocation:
    """Return the configured default PocoBox location (defaults to 분당 / 성남시 분당구)."""
    raw_name = os.environ.get("POCOBOX_LOCATION_NAME", "").strip() or _DEFAULT_LOCATION_NAME
    env_zone = os.environ.get("POCOBOX_WEATHER_ZONE", "").strip()
    env_lat = os.environ.get("POCOBOX_LOCATION_LAT", "").strip()
    env_lon = os.environ.get("POCOBOX_LOCATION_LON", "").strip()

    matched = _lookup_known_location(raw_name)
    base = matched or WeatherLocation(
        name=raw_name,
        en_name=raw_name,
        kma_zone=_DEFAULT_KMA_ZONE if raw_name == _DEFAULT_LOCATION_NAME else None,
        lat=_DEFAULT_LAT if raw_name == _DEFAULT_LOCATION_NAME else None,
        lon=_DEFAULT_LON if raw_name == _DEFAULT_LOCATION_NAME else None,
        wttr_query=raw_name,
    )

    lat = base.lat
    lon = base.lon
    try:
        if env_lat:
            lat = float(env_lat)
        if env_lon:
            lon = float(env_lon)
    except ValueError:
        pass

    return WeatherLocation(
        name=raw_name,
        en_name=base.en_name,
        kma_zone=env_zone or base.kma_zone,
        lat=lat,
        lon=lon,
        wttr_query=base.wttr_query or raw_name,
    )


def _lookup_known_location(query: str) -> WeatherLocation | None:
    q = (query or "").strip()
    if not q:
        return None
    low = q.lower()
    if low in KNOWN_LOCATIONS:
        return KNOWN_LOCATIONS[low]
    # Strip common Korean administrative / particle suffixes ("서울시", "부산은", "분당에서", "판교의")
    stripped = re.sub(r"(?:특별시|광역시|특별자치시|특별자치도|에서|쪽|근처|지역|[시구군은는이가의에])$", "", q).strip()
    if stripped and stripped.lower() in KNOWN_LOCATIONS:
        return KNOWN_LOCATIONS[stripped.lower()]
    # Substring match using longest key first (e.g. "경기도 성남시 분당구" -> "분당구", "서울 강남" -> "강남")
    for key in _SORTED_LOCATION_KEYS:
        if key.isascii():
            if re.search(rf"\b{re.escape(key)}\b", low):
                return KNOWN_LOCATIONS[key]
        else:
            if key in q:
                return KNOWN_LOCATIONS[key]
    return None


def resolve_location(location_query: str = "") -> WeatherLocation:
    cleaned = (location_query or "").strip(" .!?,~")
    if not cleaned or cleaned.lower() in _LOCAL_PLACEHOLDERS:
        return get_default_location()
    known = _lookup_known_location(cleaned)
    if known is not None:
        return known
    return WeatherLocation(name=cleaned, en_name=cleaned, kma_zone=None, lat=None, lon=None, wttr_query=cleaned)


def parse_weather_query(raw_text: str, explicit_location: str = "") -> tuple[str, int, bool]:
    """Extract (location_query, day_offset, rain_focus) from a Korean/English weather utterance."""
    stripped, _ = strip_wake_prefix((raw_text or "").strip())
    text = stripped.strip(" .!?,~")
    low = text.lower()

    day_offset = 0
    if re.search(r"(글피)", low):
        day_offset = 3
    elif re.search(r"(내일\s*모레|모레|day\s+after\s+tomorrow)", low):
        day_offset = 2
    elif re.search(r"(내일|명일|tomorrow)", low):
        day_offset = 1

    rain_focus = bool(
        re.search(r"(우산|비\s*(?:와|오나|오니|올까|소식|내려|내릴)|눈\s*(?:와|오나|오니|올까|소식|내려|내릴)|rain|raining|snow|snowing|umbrella)", low)
    )

    if explicit_location and explicit_location.strip():
        return explicit_location.strip(), day_offset, rain_focus

    # 1. Check known Korean/world city names in the utterance first (longest key first)
    for key in _SORTED_LOCATION_KEYS:
        if key.isascii():
            if re.search(rf"\b{re.escape(key)}\b", low):
                return key, day_offset, rain_focus
        else:
            # Avoid false match on single-syllable or accidental substrings; all keys are >= 2 chars
            if key in text:
                return key, day_offset, rain_focus

    # 2. English "weather in <city>" / "raining in <city>"
    m_en = re.search(r"(?:weather|forecast|temperature|rain(?:ing)?|snow(?:ing)?)\s+(?:in|at|for)\s+([a-zA-Z][a-zA-Z\s\-]{1,30})", text, re.IGNORECASE)
    if m_en:
        cand = re.sub(r"\s+(?:today|tomorrow|now|please|right\s+now)$", "", m_en.group(1), flags=re.IGNORECASE).strip()
        if cand.lower() not in _LOCAL_PLACEHOLDERS:
            return cand, day_offset, rain_focus

    # 3. Korean "<미등록 지명> (오늘|내일)? 날씨" (e.g. "삿포로 날씨 알려줘", "전라남도 순천 내일 날씨")
    m_ko = re.search(r"([가-힣]{2,8})(?:에서|의|은|는)?\s+(?:(?:지금|현재|오늘|내일|모레)\s+)?(?:날씨|일기예보|기온|온도)", text)
    if m_ko:
        cand = m_ko.group(1).strip()
        if cand not in _WEATHER_STOPWORDS and cand not in _LOCAL_PLACEHOLDERS:
            return cand, day_offset, rain_focus

    return "", day_offset, rain_focus


def _cached_fetch(cache_key: str, fn: Callable[[], Any]) -> Any:
    now = time.monotonic()
    with _cache_lock:
        hit = _forecast_cache.get(cache_key)
        if hit is not None and (now - hit[0]) < _CACHE_TTL_SEC:
            return hit[1]
    data = fn()
    with _cache_lock:
        _forecast_cache[cache_key] = (now, data)
    return data


def _http_get_text(url: str, timeout: float = 4.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _parse_kma_rss_xml(xml_text: str, loc: WeatherLocation, day_offset: int, now_kst: datetime.datetime | None = None) -> WeatherForecast:
    root = ET.fromstring(xml_text)
    source_area = (root.findtext(".//item/category") or "").strip()
    raw_items = root.findall(".//body/data")
    if not raw_items:
        raise ValueError("KMA RSS contained no forecast data entries")

    by_day: dict[int, list[dict[str, Any]]] = {}
    for node in raw_items:
        try:
            d_idx = int((node.findtext("day") or "0").strip())
            hour = int((node.findtext("hour") or "0").strip())
            temp = float((node.findtext("temp") or "0").strip())
            tmn = float((node.findtext("tmn") or "-999").strip())
            tmx = float((node.findtext("tmx") or "-999").strip())
            pop = int(float((node.findtext("pop") or "0").strip()))
            reh = int(float((node.findtext("reh") or "0").strip()))
            ws = float((node.findtext("ws") or "0").strip())
        except (TypeError, ValueError):
            continue
        by_day.setdefault(d_idx, []).append(
            {
                "hour": hour,
                "temp": temp,
                "tmn": None if tmn <= -900 else tmn,
                "tmx": None if tmx <= -900 else tmx,
                "pty": (node.findtext("pty") or "0").strip(),
                "wf_kor": (node.findtext("wfKor") or "").strip() or "맑음",
                "wf_en": (node.findtext("wfEn") or "").strip() or "Clear",
                "pop": pop,
                "reh": reh,
                "ws": ws,
            }
        )

    eff_day = day_offset if day_offset in by_day else min(by_day.keys())
    entries = by_day[eff_day]
    if not entries:
        raise ValueError(f"No KMA forecast entries for day={eff_day}")

    tmn_vals = [e["tmn"] for e in entries if e["tmn"] is not None]
    tmx_vals = [e["tmx"] for e in entries if e["tmx"] is not None]
    temps = [e["temp"] for e in entries]
    min_temp = min(tmn_vals) if tmn_vals else min(temps)
    max_temp = max(tmx_vals) if tmx_vals else max(temps)
    max_pop = max(e["pop"] for e in entries)

    precip_entries = [e for e in entries if e["pty"] != "0"]
    has_precip_expected = bool(precip_entries) or max_pop >= 50
    precip_kind = "비"
    if any(e["pty"] == "3" for e in precip_entries):
        precip_kind = "눈"
    elif any(e["pty"] == "2" for e in precip_entries):
        precip_kind = "비나 눈"
    elif any(e["pty"] == "4" for e in precip_entries):
        precip_kind = "소나기"

    if eff_day == 0:
        cur_hour = (now_kst or datetime.datetime.now(tz=_KST)).astimezone(_KST).hour
        current = min(entries, key=lambda e: abs(e["hour"] - cur_hour))
        return WeatherForecast(
            location_name=loc.name,
            en_location_name=loc.en_name,
            day_offset=0,
            provider="kma-rss",
            condition_ko=current["wf_kor"],
            condition_en=current["wf_en"],
            current_temp_c=current["temp"],
            min_temp_c=min_temp,
            max_temp_c=max_temp,
            precip_prob_pct=max_pop,
            has_precip_now=current["pty"] != "0",
            has_precip_expected=has_precip_expected,
            precip_kind_ko=precip_kind,
            humidity_pct=current["reh"],
            wind_ms=current["ws"],
            source_area=source_area,
        )

    daytime = [e for e in entries if 9 <= e["hour"] <= 18] or entries
    # Choose representative condition: prefer precipitation if expected during daytime, else most frequent sky
    day_precip = [e for e in daytime if e["pty"] != "0"]
    if day_precip:
        rep = day_precip[0]
    else:
        counts: dict[str, int] = {}
        for e in daytime:
            counts[e["wf_kor"]] = counts.get(e["wf_kor"], 0) + 1
        top_wf = max(counts.items(), key=lambda kv: kv[1])[0]
        rep = next(e for e in daytime if e["wf_kor"] == top_wf)

    return WeatherForecast(
        location_name=loc.name,
        en_location_name=loc.en_name,
        day_offset=eff_day,
        provider="kma-rss",
        condition_ko=rep["wf_kor"],
        condition_en=rep["wf_en"],
        current_temp_c=None,
        min_temp_c=min_temp,
        max_temp_c=max_temp,
        precip_prob_pct=max_pop,
        has_precip_now=False,
        has_precip_expected=has_precip_expected,
        precip_kind_ko=precip_kind,
        humidity_pct=rep["reh"],
        wind_ms=rep["ws"],
        source_area=source_area,
    )


def _map_metno_symbol(symbol_code: str) -> tuple[str, str, bool, str]:
    s = (symbol_code or "").lower()
    if "snow" in s:
        return "눈", "Snow", True, "눈"
    if "sleet" in s:
        return "비/눈", "Rain/Snow", True, "비나 눈"
    if "rainshowers" in s:
        return "소나기", "Shower", True, "소나기"
    if "rain" in s or "drizzle" in s:
        return "비", "Rain", True, "비"
    if "fog" in s:
        return "안개", "Fog", False, "비"
    if "partlycloudy" in s or "fair" in s:
        return "구름 많음", "Partly Cloudy", False, "비"
    if "cloudy" in s:
        return "흐림", "Cloudy", False, "비"
    return "맑음", "Clear", False, "비"


def _parse_metno_json(raw_json: str, loc: WeatherLocation, day_offset: int, now_kst: datetime.datetime | None = None) -> WeatherForecast:
    data = json.loads(raw_json)
    series = data.get("properties", {}).get("timeseries", [])
    if not series:
        raise ValueError("met.no response contained no timeseries")

    now = (now_kst or datetime.datetime.now(tz=_KST)).astimezone(_KST)
    target_date = (now + datetime.timedelta(days=day_offset)).date()

    day_points: list[dict[str, Any]] = []
    for item in series:
        ts_str = str(item.get("time") or "")
        if not ts_str:
            continue
        try:
            dt_utc = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        dt_kst = dt_utc.astimezone(_KST)
        if dt_kst.date() != target_date:
            continue
        details = item.get("data", {}).get("instant", {}).get("details", {})
        next1 = item.get("data", {}).get("next_1_hours") or item.get("data", {}).get("next_6_hours") or {}
        sym = (next1.get("summary") or {}).get("symbol_code", "")
        precip_mm = float((next1.get("details") or {}).get("precipitation_amount", 0.0) or 0.0)
        temp = details.get("air_temperature")
        if temp is None:
            continue
        day_points.append(
            {
                "hour": dt_kst.hour,
                "temp": float(temp),
                "sym": sym,
                "precip_mm": precip_mm,
                "reh": int(round(float(details.get("relative_humidity", 0) or 0))),
                "ws": float(details.get("wind_speed", 0.0) or 0.0),
            }
        )

    if not day_points:
        # Fallback to first available point
        first = series[0]
        details = first.get("data", {}).get("instant", {}).get("details", {})
        next1 = first.get("data", {}).get("next_1_hours") or first.get("data", {}).get("next_6_hours") or {}
        sym = (next1.get("summary") or {}).get("symbol_code", "")
        ko, en, is_p, kind = _map_metno_symbol(sym)
        t = float(details.get("air_temperature", 0.0))
        return WeatherForecast(
            location_name=loc.name,
            en_location_name=loc.en_name,
            day_offset=day_offset,
            provider="met.no",
            condition_ko=ko,
            condition_en=en,
            current_temp_c=t if day_offset == 0 else None,
            min_temp_c=t,
            max_temp_c=t,
            has_precip_now=is_p and day_offset == 0,
            has_precip_expected=is_p,
            precip_kind_ko=kind,
        )

    temps = [p["temp"] for p in day_points]
    min_temp, max_temp = min(temps), max(temps)
    has_precip_expected = any(p["precip_mm"] >= 0.2 or _map_metno_symbol(p["sym"])[2] for p in day_points)

    if day_offset == 0:
        cur = min(day_points, key=lambda p: abs(p["hour"] - now.hour))
        ko, en, is_p, kind = _map_metno_symbol(cur["sym"])
        return WeatherForecast(
            location_name=loc.name,
            en_location_name=loc.en_name,
            day_offset=0,
            provider="met.no",
            condition_ko=ko,
            condition_en=en,
            current_temp_c=cur["temp"],
            min_temp_c=min_temp,
            max_temp_c=max_temp,
            has_precip_now=is_p or cur["precip_mm"] >= 0.2,
            has_precip_expected=has_precip_expected,
            precip_kind_ko=kind,
            humidity_pct=cur["reh"],
            wind_ms=cur["ws"],
        )

    mid = min(day_points, key=lambda p: abs(p["hour"] - 14))
    ko, en, _, kind = _map_metno_symbol(mid["sym"])
    for p in day_points:
        pko, pen, pis, pkind = _map_metno_symbol(p["sym"])
        if pis:
            ko, en, kind = pko, pen, pkind
            break
    return WeatherForecast(
        location_name=loc.name,
        en_location_name=loc.en_name,
        day_offset=day_offset,
        provider="met.no",
        condition_ko=ko,
        condition_en=en,
        current_temp_c=None,
        min_temp_c=min_temp,
        max_temp_c=max_temp,
        has_precip_now=False,
        has_precip_expected=has_precip_expected,
        precip_kind_ko=kind,
        humidity_pct=mid["reh"],
        wind_ms=mid["ws"],
    )


def _map_wttr_desc(desc: str) -> tuple[str, str, bool, str]:
    low = (desc or "").lower()
    if "snow" in low or "blizzard" in low:
        return "눈", desc or "Snow", True, "눈"
    if "sleet" in low:
        return "비/눈", desc or "Sleet", True, "비나 눈"
    if "rain" in low or "drizzle" in low or "shower" in low or "thunder" in low:
        return "비", desc or "Rain", True, "비"
    if "fog" in low or "mist" in low:
        return "안개", desc or "Fog", False, "비"
    if "overcast" in low or "cloudy" in low:
        return "흐림" if "overcast" in low else "구름 많음", desc or "Cloudy", False, "비"
    return "맑음", desc or "Clear", False, "비"


def _parse_wttr_json(raw_json: str, loc: WeatherLocation, day_offset: int) -> WeatherForecast:
    data = json.loads(raw_json)
    cur_list = data.get("current_condition") or []
    days = data.get("weather") or []
    if not cur_list and not days:
        raise ValueError("wttr.in response empty")

    day_idx = min(max(0, day_offset), max(0, len(days) - 1))
    day_obj = days[day_idx] if days else {}
    hourly = day_obj.get("hourly") or []
    max_pop = max((int(float(h.get("chanceofrain", 0) or 0)) for h in hourly), default=0)
    min_temp = float(day_obj["mintempC"]) if "mintempC" in day_obj else None
    max_temp = float(day_obj["maxtempC"]) if "maxtempC" in day_obj else None

    if day_offset == 0 and cur_list:
        cur = cur_list[0]
        desc = ((cur.get("weatherDesc") or [{}])[0]).get("value", "")
        ko, en, is_p, kind = _map_wttr_desc(desc)
        temp = float(cur.get("temp_C", 0.0))
        return WeatherForecast(
            location_name=loc.name,
            en_location_name=loc.en_name,
            day_offset=0,
            provider="wttr.in",
            condition_ko=ko,
            condition_en=en,
            current_temp_c=temp,
            min_temp_c=min_temp if min_temp is not None else temp,
            max_temp_c=max_temp if max_temp is not None else temp,
            precip_prob_pct=max_pop,
            has_precip_now=is_p,
            has_precip_expected=is_p or max_pop >= 50,
            precip_kind_ko=kind,
            humidity_pct=int(float(cur.get("humidity", 0) or 0)),
        )

    mid = hourly[len(hourly) // 2] if hourly else {}
    desc = ((mid.get("weatherDesc") or [{}])[0]).get("value", "")
    ko, en, is_p, kind = _map_wttr_desc(desc)
    return WeatherForecast(
        location_name=loc.name,
        en_location_name=loc.en_name,
        day_offset=day_idx,
        provider="wttr.in",
        condition_ko=ko,
        condition_en=en,
        current_temp_c=None,
        min_temp_c=min_temp,
        max_temp_c=max_temp,
        precip_prob_pct=max_pop,
        has_precip_now=False,
        has_precip_expected=is_p or max_pop >= 50,
        precip_kind_ko=kind,
    )


def fetch_weather(
    location_query: str = "",
    day_offset: int = 0,
    *,
    timeout: float = 4.0,
    now_kst: datetime.datetime | None = None,
) -> WeatherForecast:
    """Fetch live weather for location_query (defaults to POCOBOX_LOCATION_NAME / 분당)."""
    loc = resolve_location(location_query)
    errors: list[str] = []

    # 1. Primary for Korea: KMA official 1-hour RSS (~80ms, no API key)
    if loc.kma_zone:
        try:
            url = f"https://www.weather.go.kr/w/rss/dfs/hr1-forecast.do?zone={urllib.parse.quote(loc.kma_zone)}"
            xml_text = _cached_fetch(f"kma:{loc.kma_zone}", lambda: _http_get_text(url, timeout=timeout))
            return _parse_kma_rss_xml(xml_text, loc, day_offset, now_kst=now_kst)
        except Exception as exc:
            errors.append(f"kma={exc}")
            log.warning("KMA RSS fetch failed for %s (%s): %s", loc.name, loc.kma_zone, exc)

    # 2. Coordinate fallback: Norwegian Meteorological Institute (met.no)
    if loc.lat is not None and loc.lon is not None:
        try:
            url = f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={loc.lat:.4f}&lon={loc.lon:.4f}"
            raw = _cached_fetch(f"metno:{loc.lat:.3f},{loc.lon:.3f}", lambda: _http_get_text(url, timeout=timeout))
            return _parse_metno_json(raw, loc, day_offset, now_kst=now_kst)
        except Exception as exc:
            errors.append(f"metno={exc}")
            log.warning("met.no fetch failed for %s: %s", loc.name, exc)

    # 3. Universal city-name fallback: wttr.in
    wttr_target = loc.wttr_query or (f"{loc.lat:.4f},{loc.lon:.4f}" if (loc.lat is not None and loc.lon is not None) else loc.name)
    try:
        url = f"https://wttr.in/{urllib.parse.quote(wttr_target)}?format=j1"
        raw = _cached_fetch(f"wttr:{wttr_target.lower()}", lambda: _http_get_text(url, timeout=timeout))
        return _parse_wttr_json(raw, loc, day_offset)
    except Exception as exc:
        errors.append(f"wttr={exc}")
        raise RuntimeError(f"날씨 정보를 불러오지 못했습니다 ({'; '.join(errors)})") from exc


def _fmt_temp_ko(temp_c: float) -> str:
    val = int(round(temp_c))
    if val < 0:
        return f"영하 {abs(val)}도"
    return f"{val}도"


def _ko_topic_particle(word: str) -> str:
    """Return '은' if last Hangul syllable has a final consonant (받침), else '는'."""
    if not word:
        return "은"
    ch = word[-1]
    if "가" <= ch <= "힣":
        return "은" if (ord(ch) - 0xAC00) % 28 != 0 else "는"
    return "은"


def _ko_subject_particle(word: str) -> str:
    """Return '이' if last Hangul syllable has a final consonant (받침), else '가'."""
    if not word:
        return "가"
    ch = word[-1]
    if "가" <= ch <= "힣":
        return "이" if (ord(ch) - 0xAC00) % 28 != 0 else "가"
    return "가"


def _ko_condition_now(wf: str) -> str:
    mapping = {
        "맑음": "맑고",
        "구름 많음": "구름이 많고",
        "구름 조금": "구름이 조금 있고",
        "흐림": "흐리고",
        "비": "비가 오고",
        "소나기": "소나기가 오고",
        "눈": "눈이 오고",
        "비/눈": "비나 눈이 오고",
        "안개": "안개가 끼고",
    }
    return mapping.get(wf, f"{wf} 상태이고")


def _ko_condition_future(wf: str) -> str:
    mapping = {
        "맑음": "맑겠고",
        "구름 많음": "구름이 많겠고",
        "구름 조금": "구름이 조금 있겠고",
        "흐림": "흐리겠고",
        "비": "비가 오겠고",
        "소나기": "소나기가 오겠고",
        "눈": "눈이 오겠고",
        "비/눈": "비나 눈이 오겠고",
        "안개": "안개가 끼겠고",
    }
    return mapping.get(wf, f"{wf}이 예상되고")


def format_weather_answer(
    fc: WeatherForecast,
    *,
    rain_focus: bool = False,
    language: str = "ko",
) -> str:
    """Format a WeatherForecast into 1-2 natural spoken sentences for TTS."""
    day_labels_ko = {0: "오늘", 1: "내일", 2: "모레", 3: "글피"}
    day_labels_en = {0: "today", 1: "tomorrow", 2: "the day after tomorrow", 3: "in three days"}
    day_ko = day_labels_ko.get(fc.day_offset, f"{fc.day_offset}일 뒤")
    day_en = day_labels_en.get(fc.day_offset, f"in {fc.day_offset} days")

    if language == "en":
        place = fc.en_location_name or fc.location_name
        if fc.day_offset == 0 and fc.current_temp_c is not None:
            cur_t = int(round(fc.current_temp_c))
            base = f"Currently in {place}, it is {fc.condition_en.lower()} and {cur_t} degrees."
            if fc.min_temp_c is not None and fc.max_temp_c is not None:
                lo, hi = int(round(fc.min_temp_c)), int(round(fc.max_temp_c))
                if lo != hi:
                    base += f" Today's range is {lo} to {hi} degrees."
            if fc.has_precip_now or fc.has_precip_expected:
                pop = f" ({fc.precip_prob_pct}% chance)" if fc.precip_prob_pct is not None else ""
                base += f" Bring an umbrella{pop}."
            return base
        lo = int(round(fc.min_temp_c)) if fc.min_temp_c is not None else 0
        hi = int(round(fc.max_temp_c)) if fc.max_temp_c is not None else 0
        pop_part = f" Chance of precipitation is {fc.precip_prob_pct}%." if fc.precip_prob_pct is not None else ""
        return f"In {place} {day_en}, expect {fc.condition_en.lower()} weather with a low of {lo} and a high of {hi} degrees.{pop_part}"

    place = fc.location_name
    topic = f"{place}{_ko_topic_particle(place)}"

    # Direct answer for rain / snow / umbrella questions
    if rain_focus:
        if fc.day_offset == 0 and fc.has_precip_now:
            t_part = f" 현재 기온은 {_fmt_temp_ko(fc.current_temp_c)}입니다." if fc.current_temp_c is not None else ""
            subj = f"{fc.precip_kind_ko}{_ko_subject_particle(fc.precip_kind_ko)}"
            return f"네, 현재 {place}에 {subj} 오고 있어 우산이 필요합니다.{t_part}"
        if fc.has_precip_expected:
            pop_part = f" 강수확률은 최대 {fc.precip_prob_pct}퍼센트입니다." if fc.precip_prob_pct is not None else ""
            return f"네, {day_ko} {topic} {fc.precip_kind_ko} 소식이 있어 우산을 챙기시는 게 좋겠습니다.{pop_part}"
        pop_part = f" 강수확률 {fc.precip_prob_pct}퍼센트로" if fc.precip_prob_pct is not None else ""
        if fc.day_offset == 0 and fc.current_temp_c is not None:
            return f"아니요, {day_ko} {topic} {fc.condition_ko} 상태이며{pop_part} 뚜렷한 비 소식은 없습니다. 현재 기온은 {_fmt_temp_ko(fc.current_temp_c)}입니다."
        if fc.min_temp_c is not None and fc.max_temp_c is not None:
            return f"아니요, {day_ko} {topic} {fc.condition_ko} 상태이며{pop_part} 비 소식은 없습니다. 최저 {_fmt_temp_ko(fc.min_temp_c)}, 최고 {_fmt_temp_ko(fc.max_temp_c)}입니다."
        return f"아니요, {day_ko} {topic} {fc.condition_ko} 상태로 비 소식은 없습니다."

    # General weather report for today (day_offset == 0)
    if fc.day_offset == 0 and fc.current_temp_c is not None:
        s1 = f"현재 {topic} {_ko_condition_now(fc.condition_ko)} 기온은 {_fmt_temp_ko(fc.current_temp_c)}입니다."
        extras: list[str] = []
        if fc.min_temp_c is not None and fc.max_temp_c is not None:
            lo_i, hi_i = int(round(fc.min_temp_c)), int(round(fc.max_temp_c))
            if lo_i != hi_i:
                extras.append(f"오늘 기온은 {_fmt_temp_ko(fc.min_temp_c)}에서 {_fmt_temp_ko(fc.max_temp_c)} 사이입니다")
        if fc.has_precip_now:
            extras.append("외출 시 우산을 챙기세요")
        elif fc.has_precip_expected:
            if fc.precip_prob_pct is not None:
                extras.append(f"강수확률은 최대 {fc.precip_prob_pct}퍼센트로 {fc.precip_kind_ko} 소식이 있습니다")
            else:
                extras.append(f"{fc.precip_kind_ko} 소식이 있으니 우산을 챙기세요")
        elif fc.precip_prob_pct is not None and fc.precip_prob_pct > 0:
            extras.append(f"강수확률은 {fc.precip_prob_pct}퍼센트입니다")

        if extras:
            return f"{s1} {'. '.join(extras)}."
        return s1

    # Tomorrow / future day report
    temp_part = ""
    if fc.min_temp_c is not None and fc.max_temp_c is not None:
        temp_part = f" 최저 {_fmt_temp_ko(fc.min_temp_c)}, 최고 {_fmt_temp_ko(fc.max_temp_c)}로 예상됩니다."
    s1 = f"{day_ko} {topic} {_ko_condition_future(fc.condition_ko)}{temp_part}"
    if fc.has_precip_expected and fc.precip_prob_pct is not None:
        return f"{s1} 강수확률은 최대 {fc.precip_prob_pct}퍼센트이니 우산을 챙기세요."
    if fc.precip_prob_pct is not None and fc.precip_prob_pct > 0:
        return f"{s1} 강수확률은 {fc.precip_prob_pct}퍼센트입니다."
    return s1


def answer_weather_query(
    raw_text: str,
    *,
    location_query: str = "",
    language: str = "ko",
    fetcher: Callable[..., WeatherForecast] = fetch_weather,
) -> tuple[str, dict[str, Any]]:
    """Parse a weather utterance, fetch live forecast, and return (spoken_answer, details)."""
    loc_q, day_offset, rain_focus = parse_weather_query(raw_text, explicit_location=location_query)
    fc = fetcher(loc_q, day_offset)
    answer = format_weather_answer(fc, rain_focus=rain_focus, language=language)
    details: dict[str, Any] = {
        "answer": answer,
        "location": fc.location_name,
        "day_offset": fc.day_offset,
        "provider": fc.provider,
        "condition": fc.condition_ko,
        "current_temp_c": fc.current_temp_c,
        "min_temp_c": fc.min_temp_c,
        "max_temp_c": fc.max_temp_c,
        "precip_prob_pct": fc.precip_prob_pct,
        "source_area": fc.source_area,
    }
    return answer, details
