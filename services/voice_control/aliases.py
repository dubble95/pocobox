"""Korean/English artist, track, genre aliases, wake words, and number normalization for TIDAL."""

from __future__ import annotations

import re

# Wake word prefixes stripped from the start of spoken utterances
WAKE_PREFIXES: tuple[str, ...] = (
    "헤이 포코박스",
    "오케이 포코박스",
    "포코박스야",
    "포코박스",
    "헤이 포코",
    "오케이 포코",
    "안녕 포코",
    "포코야",
    "포코",
    "hey pocobox",
    "ok pocobox",
    "pocobox",
    "hey poco",
    "ok poco",
)

# Common ASR mis-hearings / spacing fixes for Korean music terms
ASR_NORMALIZATIONS: tuple[tuple[str, str], ...] = (
    ("아 이유", "아이유"),
    ("아이 유", "아이유"),
    ("밤 편지", "밤편지"),
    ("반 편지", "밤편지"),
    ("반편지", "밤편지"),
    ("볼룸", "볼륨"),
    ("볼늄", "볼륨"),
    ("타이날", "타이달"),
    ("마이 믹스", "마이믹스"),
    ("플레이 리스트", "플레이리스트"),
    ("플리", "플레이리스트"),
    ("일시 정지", "일시정지"),
    ("다음곡", "다음 곡"),
    ("이전곡", "이전 곡"),
    ("앞곡", "앞 곡"),
    ("뉴 진스", "뉴진스"),
    ("블랙 핑크", "블랙핑크"),
    ("콜드 플레이", "콜드플레이"),
    ("골드 플레이", "콜드플레이"),
    ("골드플레이", "콜드플레이"),
    ("에드 시런", "에드시런"),
    ("라디오 헤드", "라디오헤드"),
    ("raio head", "Radiohead"),
    ("radio head", "Radiohead"),
    ("데이 식스", "데이식스"),
    ("방탄 소년단", "방탄소년단"),
    # SenseVoice-Small Korean spacing / phonetic suffix quirks
    ("틀 어 줘", "틀어줘"),
    ("틀어 줘", "틀어줘"),
    ("틀 어", "틀어"),
    ("틀러줘", "틀어줘"),
    ("틀러죠", "틀어줘"),
    ("멈춰 줘", "멈춰줘"),
    ("멈춰 죠", "멈춰줘"),
    ("맞춰어 줘", "맞춰줘"),
    ("맞춰어", "맞춰"),
    ("맞춰 줘", "맞춰줘"),
    ("시작 해 줘", "시작해줘"),
    ("시작 해", "시작해"),
    ("들려 줘", "들려줘"),
    ("재생 해 줘", "재생해줘"),
    # Common SenseVoice classical composer / work ASR mis-hearings
    ("잘코스키", "차이코프스키"),
    ("차이코스키", "차이코프스키"),
    ("차이코프 스키", "차이코프스키"),
    ("모짜르트", "모차르트"),
    ("베토밴", "베토벤"),
)

# Korean artist name -> primary TIDAL search name(s)
ARTIST_ALIASES: dict[str, list[str]] = {
    "아이유": ["IU"],
    "뉴진스": ["NewJeans"],
    "잔나비": ["JANNABI", "잔나비"],
    "방탄소년단": ["BTS"],
    "비티에스": ["BTS"],
    "블랙핑크": ["BLACKPINK"],
    "에스파": ["aespa"],
    "데이식스": ["DAY6"],
    "임영웅": ["Lim Young Woong", "임영웅"],
    "성시경": ["Sung Si Kyung", "성시경"],
    "박효신": ["Park Hyo Shin", "박효신"],
    "김광석": ["Kim Kwang Seok", "김광석"],
    "김동률": ["Kim Dong Ryul", "김동률"],
    "유재하": ["Yoo Jae Ha", "유재하"],
    "이문세": ["Lee Moon Sae", "이문세"],
    "태연": ["TAEYEON"],
    "악뮤": ["AKMU"],
    "악동뮤지션": ["AKMU"],
    "혁오": ["HYUKOH"],
    "검정치마": ["The Black Skirts", "검정치마"],
    "백예린": ["Yerin Baek", "백예린"],
    "세븐틴": ["SEVENTEEN"],
    "르세라핌": ["LE SSERAFIM"],
    "아이브": ["IVE"],
    "트와이스": ["TWICE"],
    "지드래곤": ["G-DRAGON"],
    "빅뱅": ["BIGBANG"],
    "콜드플레이": ["Coldplay"],
    "라디오헤드": ["Radiohead"],
    "비틀즈": ["The Beatles"],
    "퀸": ["Queen"],
    "에드시런": ["Ed Sheeran"],
    "테일러스위프트": ["Taylor Swift"],
    "테일러 스위프트": ["Taylor Swift"],
    "브루노마스": ["Bruno Mars"],
    "브루노 마스": ["Bruno Mars"],
    "아델": ["Adele"],
    "빌리아일리시": ["Billie Eilish"],
    "빌리 아일리시": ["Billie Eilish"],
    "마룬파이브": ["Maroon 5"],
    "마룬5": ["Maroon 5"],
    "위켄드": ["The Weeknd"],
    "더 위켄드": ["The Weeknd"],
    "오아시스": ["Oasis"],
    "너바나": ["Nirvana"],
    "핑크플로이드": ["Pink Floyd"],
    "핑크 플로이드": ["Pink Floyd"],
    "쳇베이커": ["Chet Baker"],
    "쳇 베이커": ["Chet Baker"],
    "빌에반스": ["Bill Evans"],
    "빌 에반스": ["Bill Evans"],
    "마일스데이비스": ["Miles Davis"],
    "마일스 데이비스": ["Miles Davis"],
    "류이치사카모토": ["Ryuichi Sakamoto"],
    "류이치 사카모토": ["Ryuichi Sakamoto"],
    "히사이시조": ["Joe Hisaishi"],
    "히사이시 조": ["Joe Hisaishi"],
    "노라존스": ["Norah Jones"],
    "노라 존스": ["Norah Jones"],
    "존메이어": ["John Mayer"],
    "존 메이어": ["John Mayer"],
    "다프트펑크": ["Daft Punk"],
    "다프트 펑크": ["Daft Punk"],
    # Classical composers
    "차이코프스키": ["Tchaikovsky"],
    "차이콥스키": ["Tchaikovsky"],
    "베토벤": ["Beethoven"],
    "모차르트": ["Mozart"],
    "쇼팽": ["Chopin"],
    "바흐": ["Bach"],
    "라흐마니노프": ["Rachmaninoff"],
    "비발디": ["Vivaldi"],
    "드뷔시": ["Debussy"],
    "브람스": ["Brahms"],
    "슈베르트": ["Schubert"],
    "리스트": ["Liszt"],
    "슈만": ["Schumann"],
    "멘델스존": ["Mendelssohn"],
    "하이든": ["Haydn"],
    "말러": ["Mahler"],
    "드보르작": ["Dvorak"],
    "파가니니": ["Paganini"],
    "프로코피예프": ["Prokofiev"],
    "쇼스타코비치": ["Shostakovich"],
    "생상스": ["Saint-Saens"],
    "라벨": ["Ravel"],
    "엘가": ["Elgar"],
    "푸치니": ["Puccini"],
    "베르디": ["Verdi"],
    "바그너": ["Wagner"],
    "헨델": ["Handel"],
}

# Classical music form / title fragments -> TIDAL English equivalents
_CLASSICAL_WORK_TERMS: tuple[tuple[str, str], ...] = (
    ("피아노 협주곡", "Piano Concerto"),
    ("피아노협주곡", "Piano Concerto"),
    ("피아노 협주", "Piano Concerto"),
    ("피아노협주", "Piano Concerto"),
    ("바이올린 협주곡", "Violin Concerto"),
    ("바이올린협주곡", "Violin Concerto"),
    ("바이올린 협주", "Violin Concerto"),
    ("바이올린협주", "Violin Concerto"),
    ("첼로 협주곡", "Cello Concerto"),
    ("첼로협주곡", "Cello Concerto"),
    ("첼로 협주", "Cello Concerto"),
    ("첼로협주", "Cello Concerto"),
    ("피아노 소나타", "Piano Sonata"),
    ("피아노소나타", "Piano Sonata"),
    ("바이올린 소나타", "Violin Sonata"),
    ("바이올린소나타", "Violin Sonata"),
    ("첼로 모음곡", "Cello Suite"),
    ("첼로모음곡", "Cello Suite"),
    ("교향곡", "Symphony"),
    ("협주곡", "Concerto"),
    ("협주", "Concerto"),
    ("소나타", "Sonata"),
    ("녹턴", "Nocturne"),
    ("전주곡", "Prelude"),
    ("에튀드", "Etude"),
    ("연습곡", "Etude"),
    ("왈츠", "Waltz"),
    ("백조의 호수", "Swan Lake"),
    ("호두까기 인형", "Nutcracker"),
    ("사계", "The Four Seasons"),
    ("월광", "Moonlight Sonata"),
    ("비창", "Pathetique"),
    ("운명", "Symphony No. 5"),
    ("합창", "Symphony No. 9"),
)

_CLASSICAL_WORK_GUARD_RE = re.compile(
    r"(?:협주곡?|교향곡|소나타|녹턴|전주곡|에튀드|연습곡|모음곡|4중주|사중주|트리오)"
)

_SINO_WORK_NUM_RE = re.compile(r"(?:제\s*)?([일이삼사오육칠팔구]|십)\s*번")

_GENERIC_MOOD_TOKENS: frozenset[str] = frozenset({
    "잔잔한", "신나는", "조용한", "편안한", "부드러운", "감성", "감성적인",
    "카페", "수면", "공부", "운동", "드라이브", "아침", "저녁", "밤",
    "겨울", "여름", "가을", "봄", "인기", "최신", "명곡", "모음",
    "재즈", "클래식", "피아노", "로파이", "팝송", "팝", "발라드",
    "힙합", "록", "락", "어쿠스틱", "보사노바",
})

# Popular Korean song titles -> TIDAL English/canonical titles
TRACK_ALIASES: dict[str, list[str]] = {
    "밤편지": ["Through the Night", "밤편지"],
    "좋은날": ["Good Day", "좋은 날"],
    "좋은 날": ["Good Day", "좋은 날"],
    "블루밍": ["Blueming"],
    "라일락": ["LILAC"],
    "에잇": ["eight"],
    "주저하는 연인들을 위해": ["for lovers who hesitate", "주저하는 연인들을 위해"],
    "다이너마이트": ["Dynamite"],
    "버터": ["Butter"],
    "봄날": ["Spring Day"],
    "하입보이": ["Hype Boy"],
    "디토": ["Ditto"],
    "슈퍼샤이": ["Super Shy"],
    "예뻤어": ["You Were Beautiful", "예뻤어"],
    "한 페이지가 될 수 있게": ["Time of Our Life", "한 페이지가 될 수 있게"],
    "야생화": ["Wild Flower", "야생화"],
    "셰이프 오브 유": ["Shape of You"],
    "픽스 유": ["Fix You"],
    "옐로우": ["Yellow"],
    "비바 라 비다": ["Viva La Vida"],
    "보헤미안 랩소디": ["Bohemian Rhapsody"],
    "크립": ["Creep"],
    "예스터데이": ["Yesterday"],
    "렛 잇 비": ["Let It Be"],
    "아파트": ["APT."],
    "거리에서": ["On The Street", "거리에서"],
    "서른 즈음에": ["Around Thirty", "서른 즈음에"],
}

# Korean mood/genre -> TIDAL friendly search keywords
GENRE_MOOD_ALIASES: dict[str, list[str]] = {
    "재즈": ["Jazz", "Chill Jazz"],
    "클래식": ["Classical", "Classical Relax"],
    "피아노": ["Piano Relax", "Solo Piano"],
    "잔잔한 피아노": ["Peaceful Piano", "Calm Piano"],
    "로파이": ["Lo-Fi Beats", "Lofi"],
    "팝송": ["Pop Hits", "Top Pop"],
    "팝": ["Pop Hits"],
    "발라드": ["Korean Ballad", "K-Ballad"],
    "힙합": ["Hip-Hop"],
    "록": ["Rock Classics", "Rock"],
    "락": ["Rock Classics", "Rock"],
    "어쿠스틱": ["Acoustic Chill", "Acoustic"],
    "보사노바": ["Bossa Nova"],
    "카페": ["Coffee Shop Acoustic", "Cafe Jazz"],
    "수면": ["Sleep", "Deep Sleep"],
    "공부": ["Study Beats", "Focus"],
    "운동": ["Workout", "Cardio"],
    "드라이브": ["Driving Hits", "Road Trip"],
    "신나는": ["Feel Good Hits", "Upbeat Pop"],
    "잔잔한": ["Chill Vibes", "Relaxing"],
}

# Sino-Korean number parsing for volume commands (e.g. "오십", "삼십오", "백")
_SINO_DIGITS: dict[str, int] = {
    "영": 0, "공": 0, "일": 1, "이": 2, "삼": 3, "사": 4,
    "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9,
}


def parse_korean_number(text: str) -> int | None:
    """Extract an integer 0..100 from Arabic digits or Sino-Korean words."""
    m = re.search(r"(?<!\d)(\d{1,3})(?!\d)", text)
    if m:
        return int(m.group(1))

    # Match patterns like 백, 오십, 삼십오, 십오, 팔십, 영
    if re.search(r"(?:^|\s|볼륨\s*)백(?:\s|으로|퍼센트|%|$)", text):
        return 100

    m_sino = re.search(r"([일이삼사오육칠팔구])?십([일이삼사오육칠팔구])?", text)
    if m_sino:
        tens = _SINO_DIGITS.get(m_sino.group(1), 1) if m_sino.group(1) else 1
        ones = _SINO_DIGITS.get(m_sino.group(2), 0) if m_sino.group(2) else 0
        return tens * 10 + ones

    if re.search(r"(?:^|\s)(영|공)(?:\s|으로|$)", text):
        return 0
    return None


def normalize_asr_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text.strip())
    for src, dst in ASR_NORMALIZATIONS:
        cleaned = re.sub(re.escape(src), dst, cleaned, flags=re.IGNORECASE)
    return cleaned


def strip_wake_prefix(text: str) -> tuple[str, bool]:
    lowered = text.strip().lower()
    for prefix in sorted(WAKE_PREFIXES, key=len, reverse=True):
        if lowered.startswith(prefix):
            rest = text.strip()[len(prefix):].lstrip(" ,.!?:;~")
            return rest, True
    return text.strip(), False


def _translate_classical_terms(text: str) -> str:
    """Translate Korean classical music work terms and numbering (e.g. '피아노협주곡 1번' / '피아노 협주 일번' -> 'Piano Concerto No. 1')."""
    out = text
    for ko, en in _CLASSICAL_WORK_TERMS:
        if ko in out:
            out = out.replace(ko, f" {en} ")
    out = _SINO_WORK_NUM_RE.sub(
        lambda m: f" No. {10 if m.group(1) == '십' else _SINO_DIGITS[m.group(1)]} ",
        out,
    )
    out = re.sub(r"제\s*(\d+)\s*번", r" No. \1 ", out)
    out = re.sub(r"(\d+)\s*번", r" No. \1 ", out)
    return re.sub(r"\s+", " ", out).strip()


def expand_tidal_queries(query: str) -> list[str]:
    """Generate ordered TIDAL-friendly search candidates (English/canonical first for K-pop & classical)."""
    q = query.strip()
    if not q:
        return []

    candidates: list[str] = []

    def add(val: str) -> None:
        v = re.sub(r"\s+", " ", val.strip())
        if v and v.lower() not in {c.lower() for c in candidates}:
            candidates.append(v)

    # Direct exact alias matches (artist / track / genre)
    for table in (TRACK_ALIASES, ARTIST_ALIASES, GENRE_MOOD_ALIASES):
        if q in table:
            for alias in table[q]:
                add(alias)

    # Check artist + track / combined phrase substitution
    matched_artists: list[tuple[str, list[str]]] = []
    matched_tracks: list[tuple[str, list[str]]] = []
    matched_moods: list[tuple[str, list[str]]] = []

    for k in sorted(ARTIST_ALIASES, key=len, reverse=True):
        if k in q:
            matched_artists.append((k, ARTIST_ALIASES[k]))
            break

    for k in sorted(TRACK_ALIASES, key=len, reverse=True):
        if k in q:
            matched_tracks.append((k, TRACK_ALIASES[k]))
            break

    is_classical_work = bool(_CLASSICAL_WORK_GUARD_RE.search(q))
    q_tokens = q.split()
    if not is_classical_work and (
        q in GENRE_MOOD_ALIASES
        or (len(q_tokens) <= 2 and all(tok in _GENERIC_MOOD_TOKENS for tok in q_tokens))
    ):
        for k in sorted(GENRE_MOOD_ALIASES, key=len, reverse=True):
            if k == q or re.search(rf"(?:^|\s){re.escape(k)}(?:\s|$)", q):
                matched_moods.append((k, GENRE_MOOD_ALIASES[k]))
                break

    # If both artist and track matched (e.g., "아이유 밤편지" -> "IU Through the Night")
    if matched_artists and matched_tracks:
        a_key, a_vals = matched_artists[0]
        t_key, t_vals = matched_tracks[0]
        add(f"{a_vals[0]} {t_vals[0]}")
        add(t_vals[0])
        add(f"{a_vals[0]} {t_key}")
        add(a_vals[0])
    elif matched_artists:
        a_key, a_vals = matched_artists[0]
        rem = q.replace(a_key, "").strip()
        if rem:
            translated_rem = _translate_classical_terms(rem)
            if translated_rem != rem:
                add(f"{a_vals[0]} {translated_rem}")
            add(f"{a_vals[0]} {rem}")
            add(translated_rem if translated_rem != rem else rem)
        for av in a_vals:
            add(av)
    elif matched_tracks:
        t_key, t_vals = matched_tracks[0]
        rem = q.replace(t_key, "").strip()
        if rem:
            add(f"{rem} {t_vals[0]}")
        for tv in t_vals:
            add(tv)
    elif matched_moods:
        m_key, m_vals = matched_moods[0]
        for mv in m_vals:
            add(mv)
    else:
        translated = _translate_classical_terms(q)
        if translated != q:
            add(translated)

    # Always keep the original query as fallback
    add(q)
    return candidates
