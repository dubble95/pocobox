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
    "코코야",
    "포코향",
    "포코아",
    "뽀코야",
    "뽀코",
    "포코",
    "hey pocobox",
    "ok pocobox",
    "pocobox",
    "hey poco",
    "ok poco",
)

# Common ASR mis-hearings / spacing fixes for Korean music terms
ASR_NORMALIZATIONS: tuple[tuple[str, str], ...] = (
    ("코코야", "포코야"),
    ("포코향", "포코야"),
    ("포코아", "포코야"),
    ("뽀코야", "포코야"),
    ("뽀코", "포코"),
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
    ("타 줘", "틀어줘"),
    ("타줘", "틀어줘"),
    ("타 죠", "틀어줘"),
    ("타죠", "틀어줘"),
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
    ("플래식", "클래식"),
    ("콜래식", "클래식"),
    ("클레식", "클래식"),
    ("잘코스키", "차이코프스키"),
    ("차이코스키", "차이코프스키"),
    ("차이코프 스키", "차이코프스키"),
    ("모짜르트", "모차르트"),
    ("베토밴", "베토벤"),
    ("베토번", "베토벤"),
    ("베토펀", "베토벤"),
    ("쇼팽느", "쇼팽"),
    ("라흐마니노브", "라흐마니노프"),
    ("드뷔씨", "드뷔시"),
    ("브람느", "브람스"),
    ("리쓰트", "리스트"),
    ("슈만느", "슈만"),
    ("멘델스존느", "멘델스존"),
    ("하이든느", "하이든"),
    ("말러느", "말러"),
    ("드보르작느", "드보르작"),
    ("파가니느", "파가니니"),
    ("프로코피예느", "프로코피예프"),
    ("쇼스타코비느", "쇼스타코비치"),
    ("생쑹느", "생상스"),
    ("라벨느", "라벨"),
    ("엘가느", "엘가"),
    ("푸치느", "푸치니"),
    ("베르디느", "베르디"),
    ("바그너느", "바그너"),
    ("헨델느", "헨델"),
    # Wake word ASR mis-hearings (포코야 / 포코박스 / 헤이 포코 variants)
    ("포쿠야", "포코야"),
    ("포코약", "포코야"),
    ("포코얘", "포코야"),
    ("포코야~", "포코야"),
    ("포코박서", "포코박스"),
    ("포코박쓰", "포코박스"),
    ("포코박스~", "포코박스"),
    ("헤이 포쿠", "헤이 포코"),
    ("헤이 포코야", "헤이 포코"),
    ("오케이 포쿠", "오케이 포코"),
    ("안녕 포쿠", "안녕 포코"),
    ("포코아", "포코야"),
    ("포코얘요", "포코야"),
    ("포코야여", "포코야"),
    # Classical piece & Russian dance ASR normalizations
    ("트래팍", "트레팍"),
    ("트래 팍", "트레팍"),
    ("트레 팍", "트레팍"),
    ("호두 까기", "호두까기"),
    ("호두까기인형", "호두까기 인형"),
    ("갈잎 피리", "갈잎피리"),
    ("꽃의왈츠", "꽃의 왈츠"),
    # Common Korean command verb / particle ASR spacing & mis-hearings
    ("틀어 줄래", "틀어줄래"),
    ("틀어 줄래요", "틀어줄래요"),
    ("재생 해 줘", "재생해줘"),
    ("재생 해", "재생해"),
    ("들려 줄래", "들려줄래"),
    ("들려 줄래요", "들려줄래요"),
    ("켜 줄래", "켜줄래"),
    ("켜 줄래요", "켜줄래요"),
    ("멈춰 줄래", "멈춰줄래"),
    ("멈춰 줄래요", "멈춰줄래요"),
    ("줄여 줄래", "줄여줄래"),
    ("키워 줄래", "키워줄래"),
    ("올려 줄래", "올려줄래"),
    ("검색 해서", "검색해서"),
    ("찾아 줘", "찾아줘"),
    ("찾아서 틀어", "찾아서 틀어"),
    ("노래 틀어", "노래 틀어"),
    ("음악 틀어", "음악 틀어"),
    ("곡 틀어", "곡 틀어"),
    ("틀어 주", "틀어줘"),
    ("재생 해 주", "재생해줘"),
    ("들려 주", "들려줘"),
    # Common K-pop / Western artist ASR mis-hearings & spacing
    ("뉴 진느", "뉴진스"),
    ("블랙 핑느", "블랙핑크"),
    ("콜드 플레이느", "콜드플레이"),
    ("에드 시런느", "에드시런"),
    ("라디오 헤느", "라디오헤드"),
    ("데이 식느", "데이식스"),
    ("방탄 소년느", "방탄소년단"),
    ("테일러 스위프트", "테일러 스위프트"),
    ("테일러 스위느", "테일러 스위프트"),
    ("브루노 마스", "브루노마스"),
    ("브루노 마느", "브루노마스"),
    ("빌리 아일리시", "빌리아일리시"),
    ("빌리 아일느", "빌리아일리시"),
    ("마룬 5", "마룬파이브"),
    ("마룬 파이브", "마룬파이브"),
    ("더 위켄드", "위켄드"),
    ("위켄느", "위켄드"),
    ("핑크 플로이드", "핑크플로이드"),
    ("핑크 플로느", "핑크플로이드"),
    ("노라 존스", "노라존스"),
    ("노라 존느", "노라존스"),
    ("존 메이어", "존메이어"),
    ("존 메이느", "존메이어"),
    ("다프트 펑크", "다프트펑크"),
    ("다프트 펑느", "다프트펑크"),
    ("쳇 베이커", "쳇베이커"),
    ("쳇 베이느", "쳇베이커"),
    ("빌 에반스", "빌에반스"),
    ("빌 에반느", "빌에반스"),
    ("마일스 데이비스", "마일스데이비스"),
    ("마일스 데이느", "마일스데이비스"),
    ("류이치 사카모토", "류이치사카모토"),
    ("류이치 사카느", "류이치사카모토"),
    ("히사이시 조", "히사이시조"),
    ("히사이시 조느", "히사이시조"),
    ("조 히사이시", "히사이시조"),
    ("조 히사이느", "히사이시조"),
    # Common Korean singer ASR variants
    ("임 영웅", "임영웅"),
    ("임 영느", "임영웅"),
    ("성 시경", "성시경"),
    ("성 시경느", "성시경"),
    ("박 효신", "박효신"),
    ("박 효느", "박효신"),
    ("김 광석", "김광석"),
    ("김 광느", "김광석"),
    ("김 동률", "김동률"),
    ("김 동느", "김동률"),
    ("유 재하", "유재하"),
    ("유 재느", "유재하"),
    ("이 문세", "이문세"),
    ("이 문느", "이문세"),
    ("태 연", "태연"),
    ("태 연느", "태연"),
    ("백 예린", "백예린"),
    ("백 예느", "백예린"),
    ("혁 오", "혁오"),
    ("혁 오느", "혁오"),
    ("검정 치마", "검정치마"),
    ("검정 치마느", "검정치마"),
    ("세 븐틴", "세븐틴"),
    ("세 븐느", "세븐틴"),
    ("르 세라핌", "르세라핌"),
    ("르 세라느", "르세라핌"),
    ("아이 브", "아이브"),
    ("아이 느", "아이브"),
    ("트 와이스", "트와이스"),
    ("트 와느", "트와이스"),
    ("지 드래곤", "지드래곤"),
    ("지 드래곤느", "지드래곤"),
    ("빅 뱅", "빅뱅"),
    ("빅 뱅느", "빅뱅"),
    ("아 델", "아델"),
    ("아 델느", "아델"),
    ("오 아시스", "오아시스"),
    ("오 아시느", "오아시스"),
    ("너 바나", "너바나"),
    ("너 바느", "너바나"),
    ("노라 존느", "노라존스"),
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
    # Additional K-pop / Korean artists
    "(여자)아이들": ["(G)I-DLE", "지아이들", "아이들"],
    "지아이들": ["(G)I-DLE", "(여자)아이들"],
    "스테이씨": ["STAYC"],
    "키스오브라이프": ["KISS OF LIFE"],
    "에스파": ["aespa"],
    "엔믹스": ["NMIXX"],
    "아이들": ["(G)I-DLE", "(여자)아이들"],
    "정국": ["Jung Kook", "정국"],
    "뷔": ["V", "뷔"],
    "지민": ["Jimin", "지민"],
    "알엠": ["RM", "알엠"],
    "슈가": ["Suga", "슈가"],
    "진": ["Jin", "진"],
    "지코": ["ZICO", "지코"],
    "크러쉬": ["Crush", "크러쉬"],
    "딘": ["DEAN", "딘"],
    "이효리": ["Lee Hyori", "이효리"],
    "이선희": ["Lee Sun Hee", "이선희"],
    "패닉": ["PANIC", "패닉"],
    "듀스": ["DUES", "듀스"],
    "지오디": ["g.o.d", "god"],
    "god": ["g.o.d", "god"],
    "젝스키스": ["SechsKies", "젝스키스"],
    "신화": ["Shinhwa", "신화"],
    "클릭비": ["Click-B", "클릭비"],
    "god": ["g.o.d", "god"],
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
    # Additional Western artists
    "더 1975": ["The 1975"],
    "더 나인틴 세븐티 파이브": ["The 1975"],
    "아크틱 몽키스": ["Arctic Monkeys"],
    "아틱 몽키": ["Arctic Monkeys"],
    "플리트우드 맥": ["Fleetwood Mac"],
    "아바": ["ABBA"],
    "비 지스": ["Bee Gees"],
    "비지스": ["Bee Gees"],
    "스팅": ["Sting"],
    "폴 매카트니": ["Paul McCartney"],
    "존 레넌": ["John Lennon"],
    "조니 미첼": ["Joni Mitchell"],
    "캐롤 킹": ["Carole King"],
    "제임스 테일러": ["James Taylor"],
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
    ("심포니", "Symphony"),
    ("협주곡", "Concerto"),
    ("콘체르토", "Concerto"),
    ("협주", "Concerto"),
    ("소나타", "Sonata"),
    ("현악 4중주", "String Quartet"),
    ("현악4중주", "String Quartet"),
    ("현악 사중주", "String Quartet"),
    ("현악사중주", "String Quartet"),
    ("4중주", "Quartet"),
    ("사중주", "Quartet"),
    ("피아노 3중주", "Piano Trio"),
    ("피아노3중주", "Piano Trio"),
    ("피아노 삼중주", "Piano Trio"),
    ("피아노삼중주", "Piano Trio"),
    ("3중주", "Trio"),
    ("삼중주", "Trio"),
    ("5중주", "Quintet"),
    ("오중주", "Quintet"),
    ("세레나데", "Serenade"),
    ("디베르티멘토", "Divertimento"),
    ("녹턴", "Nocturne"),
    ("전주곡", "Prelude"),
    ("에튀드", "Etude"),
    ("연습곡", "Etude"),
    ("왈츠", "Waltz"),
    ("백조의 호수", "Swan Lake"),
    ("호두까기 인형", "The Nutcracker"),
    ("호두까기인형", "The Nutcracker"),
    ("호두까기", "The Nutcracker"),
    ("트레팍", "Trepak"),
    ("트래팍", "Trepak"),
    ("러시아 춤", "Russian Dance"),
    ("꽃의 왈츠", "Waltz of the Flowers"),
    ("사탕요정의 춤", "Dance of the Sugar Plum Fairy"),
    ("사탕 요정의 춤", "Dance of the Sugar Plum Fairy"),
    ("갈잎피리의 춤", "Dance of the Reed Flutes"),
    ("하바네라", "Habanera"),
    ("투우사의 노래", "Toreador Song"),
    ("사계", "The Four Seasons"),
    ("봄", "Spring"),
    ("여름", "Summer"),
    ("가을", "Autumn"),
    ("겨울", "Winter"),
    ("동물의 사육제", "The Carnival of the Animals"),
    ("페르 귄트", "Peer Gynt"),
    ("페르귄트", "Peer Gynt"),
    ("월광", "Moonlight Sonata"),
    ("비창", "Pathetique"),
    ("운명", "Symphony No. 5"),
    ("합창", "Symphony No. 9"),
    ("겨울바람", "Winter Wind"),
    ("겨울 바람", "Winter Wind"),
    ("혁명", "Revolutionary"),
    ("추격", "Torrent"),
    ("흑건", "Black Keys"),
)

_CLASSICAL_WORK_GUARD_RE = re.compile(
    r"(?:협주곡?|콘체르토|교향곡|심포니|소나타|녹턴|전주곡|에튀드|연습곡|모음곡|[345]중주|[삼사오]중주|트리오|세레나데|디베르티멘토|"
    r"호두까기|트레팍|트래팍|백조의\s*호수|사계|꽃의\s*왈츠|사탕요정|하바네라|카르멘|페르\s*귄트|동물의\s*사육제)"
)

_CLASSICAL_MOVEMENT_WORK_RE = re.compile(
    r"(?:협주곡?|콘체르토|교향곡|심포니|소나타|모음곡|[345]중주|[삼사오]중주|트리오|세레나데|디베르티멘토|"
    r"브란덴부르크|사계|골드베르크|호두까기|페르\s*귄트|동물의\s*사육제|"
    r"\bconcerto\b|\bsymphon(?:y|ie)\b|\bsonat[ae]\b|\bsuite\b|\bquartet[te]?\b|\btrio\b|\bquintet[te]?\b|"
    r"\bserenade\b|\bdivertimento\b|\bbrandenburg\b|\bfour\s+seasons\b|\bgoldberg\b|\bnutcracker\b)",
    re.IGNORECASE,
)


def is_classical_movement_work(text: str) -> bool:
    """Return True if text refers to a classical form composed of multiple movements."""
    return bool(_CLASSICAL_MOVEMENT_WORK_RE.search(text))


def is_classical_work(text: str) -> bool:
    """Return True if text refers to a classical form, ballet, or work title."""
    return bool(_CLASSICAL_WORK_GUARD_RE.search(text))

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
    "솔로": ["SOLO", "솔로"],
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
    "겨울바람": ["Winter Wind", "Chopin Winter Wind", "겨울바람"],
    "겨울 바람": ["Winter Wind", "Chopin Winter Wind", "겨울 바람"],
    "혁명": ["Revolutionary", "Chopin Revolutionary", "혁명"],
    "추격": ["Torrent", "Chopin Torrent", "추격"],
    "흑건": ["Black Keys", "Chopin Black Keys", "흑건"],
    # Additional popular Korean tracks
    "롤린": ["Rollin'", "롤린"],
    "브라보 마이 라이프": ["Bravo, My Life!", "브라보 마이 라이프"],
    "순정": ["Pure Love", "순정"],
    "후": ["Huuuu", "후"],
    "사랑은 언제나 목숨보다": ["Love Is Always More Precious Than Life", "사랑은 언제나 목숨보다"],
    "보고 싶다": ["I Miss You", "보고 싶다"],
    "미안해": ["I'm Sorry", "미안해"],
    "잘 지내니": ["Are You Doing Well", "잘 지내니"],
    "헤어질 때": ["When We Part", "헤어질 때"],
    "너를 위한 노래": ["A Song for You", "너를 위한 노래"],
    "첫 번째 이야기": ["First Story", "첫 번째 이야기"],
    "동경소년": ["Longing Boy", "동경소년"],
    "흰 눈": ["White Snow", "흰 눈"],
    "겨울 연가": ["Winter Sonata", "겨울 연가"],
    "첫눈": ["First Snow", "첫눈"],
    "눈꽃": ["Snow Flower", "눈꽃"],
    "벚꽃 엔딩": ["Cherry Blossom Ending", "벚꽃 엔딩"],
    "로맨틱": ["Romantic", "로맨틱"],
    "소녀": ["Girl", "소녀"],
    "안녕": ["Hello", "안녕"],
    "잘자": ["Good Night", "잘 자"],
    "잘 자": ["Good Night", "잘 자"],
    "일요일 아침": ["Sunday Morning", "일요일 아침"],
    "금요일 밤에": ["Friday Night", "금요일 밤에"],
    "토요일 밤에": ["Saturday Night", "토요일 밤에"],
    "월요일": ["Monday", "월요일"],
    "화요일": ["Tuesday", "화요일"],
    "수요일": ["Wednesday", "수요일"],
    "목요일": ["Thursday", "목요일"],
    "금요일": ["Friday", "금요일"],
    "토요일": ["Saturday", "토요일"],
    "일요일": ["Sunday", "일요일"],
    # Additional popular Western tracks
    "블라인딩 라이트": ["Blinding Lights", "블라인딩 라이트"],
    "세이브 유어 티어스": ["Save Your Tears", "세이브 유어 티어스"],
    "스타보이": ["Starboy", "스타보이"],
    "캔트 필 마이 페이스": ["Can't Feel My Face", "캔트 필 마이 페이스"],
    "쉐이프 오브 유": ["Shape of You", "쉐이프 오브 유"],
    "퍼펙트": ["Perfect", "퍼펙트"],
    "포토그래프": ["Photograph", "포토그래프"],
    "씽": ["Sing", "씽"],
    "싱크": ["Thinking Out Loud", "Thinking Out Loud"],
    "씽킹 아웃 라우드": ["Thinking Out Loud", "Thinking Out Loud"],
    "러브 유어셀프": ["Love Yourself", "러브 유어셀프"],
    "써리": ["Sorry", "써리"],
    "베이비": ["Baby", "베이비"],
    "아임 유어스": ["I'm Yours", "아임 유어스"],
    "럭키": ["Lucky", "럭키"],
    "임 유어스": ["I'm Yours", "임 유어스"],
    "롤링 인 더 딥": ["Rolling in the Deep", "롤링 인 더 딥"],
    "소메원 라이크 유": ["Someone Like You", "소메론 라이크 유"],
    "헬로": ["Hello", "헬로"],
    "세팅 파이어 투 더 레인": ["Set Fire to the Rain", "세팅 파이어 투 더 레인"],
    "원 모먼트": ["One Moment", "원 모먼트"],
    "굿 데이": ["Good Day", "굿 데이"],
    "나이트 체인지": ["Night Changes", "나이트 체인지"],
    "스토리 오브 마이 라이프": ["Story of My Life", "스토리 오브 마이 라이프"],
    "베스트 송 에버": ["Best Song Ever", "베스트 송 에버"],
    "더블 테이크": ["Double Take", "더블 테이크"],
    "플라워": ["Flower", "플라워"],
    "후아유": ["Who Are You", "후아유"],
    "러브 다이브": ["Love Dive", "러브 다이브"],
    "애프터 라이크": ["After Like", "애프터 라이크"],
    "톰보이": ["Tomboy", "톰보이"],
    "퀸카": ["Queencard", "퀸카"],
    "슈퍼 레이디": ["Super Lady", "슈퍼 레이디"],
    "화약": ["Fireworks", "화약"],
    "퀴즈": ["Quiz", "퀴즈"],
    # Classical / ballet favorites
    "트레팍": ["Trepak", "Russian Dance", "Tchaikovsky Nutcracker Trepak", "트레팍"],
    "트래팍": ["Trepak", "Russian Dance", "Tchaikovsky Nutcracker Trepak", "트래팍"],
    "호두까기 인형": ["The Nutcracker", "Nutcracker", "Tchaikovsky Nutcracker"],
    "호두까기인형": ["The Nutcracker", "Nutcracker", "Tchaikovsky Nutcracker"],
    "꽃의 왈츠": ["Waltz of the Flowers", "Tchaikovsky Waltz of the Flowers", "꽃의 왈츠"],
    "사탕요정의 춤": ["Dance of the Sugar Plum Fairy"],
    "사탕 요정의 춤": ["Dance of the Sugar Plum Fairy"],
    "갈잎피리의 춤": ["Dance of the Reed Flutes"],
    "백조의 호수": ["Swan Lake", "Tchaikovsky Swan Lake"],
    "하바네라": ["Habanera", "Bizet Carmen Habanera"],
    "투우사의 노래": ["Toreador Song", "Bizet Carmen Toreador"],
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
    "차분한": ["Calm", "Chill"],
    "슬픈": ["Sad Songs", "Heartbreak"],
    "트로트": ["Trot", "K-Trot"],
    "국악": ["Korean Traditional", "Gugak"],
    "OST": ["Drama OST", "K-Drama OST"],
    "영화음악": ["Film Score", "Movie Soundtrack"],
    "게임음악": ["Game Music", "Video Game Soundtrack"],
    "어린이": ["Kids Songs", "Children"],
    "크리스마스": ["Christmas", "Holiday"],
    "캐롤": ["Carol", "Christmas Carol"],
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
    out = re.sub(
        r"(?:제\s*)?([일이삼사오육칠팔구]|십)\s*악장",
        lambda m: f" Movement {10 if m.group(1) == '십' else _SINO_DIGITS[m.group(1)]} ",
        out,
    )
    out = re.sub(r"(?:제\s*)?(\d+)\s*악장", r" Movement \1 ", out)
    # Strip partitive particles between translated terms (e.g. "The Nutcracker 중 Trepak" -> "The Nutcracker Trepak")
    out = re.sub(r"\s+(?:중|중에|에서)\s+", " ", out)
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

    # Check classical partitive expressions (e.g. "호두까기 인형 중 트레팍", "사계 중 봄")
    m_part = re.match(r"^(.+?)\s+(?:중|중에|에서)\s+(.+)$", q)
    if m_part:
        work_raw = m_part.group(1).strip()
        part_raw = m_part.group(2).strip()
        work_trans = _translate_classical_terms(work_raw)
        part_trans = _translate_classical_terms(part_raw)
        work_cand = TRACK_ALIASES.get(work_raw, [work_trans])[0]
        part_cand = TRACK_ALIASES.get(part_raw, [part_trans])[0]
        add(f"{work_cand} {part_cand}")
        if "nutcracker" in work_cand.lower() or "호두까기" in work_raw:
            add(f"Tchaikovsky Nutcracker {part_cand}")
        elif "four seasons" in work_cand.lower() or "사계" in work_raw:
            add(f"Vivaldi Four Seasons {part_cand}")
        add(f"{work_trans} {part_trans}")
        add(part_cand)
        add(f"{work_raw} {part_raw}")

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
        # Phonetic fuzzy Jamo matching for slightly misrecognized artist or track names
        try:
            from .jamo_util import match_alias_with_jamo
            jamo_thresh = 0.80 if len(q) <= 2 else 0.68
            best_artist_key, best_artist_vals, a_score = match_alias_with_jamo(q, ARTIST_ALIASES, threshold=jamo_thresh)
            if best_artist_key is not None and best_artist_vals:
                for av in best_artist_vals:
                    add(av)
            else:
                best_track_key, best_track_vals, t_score = match_alias_with_jamo(q, TRACK_ALIASES, threshold=jamo_thresh)
                if best_track_key is not None and best_track_vals:
                    for tv in best_track_vals:
                        add(tv)

            # Check dynamic user library catalog (LMS favorites/recent playback)
            from .dynamic_catalog import get_dynamic_catalog
            d_cat = get_dynamic_catalog()
            d_name, d_type, d_meta, d_score = d_cat.match_entity(q, threshold=jamo_thresh)
            if d_name:
                if d_type == "track" and d_meta.get("artist"):
                    add(f"{d_meta['artist']} {d_name}")
                add(d_name)
        except Exception:
            pass

        translated = _translate_classical_terms(q)
        if translated != q:
            add(translated)

    # Always keep the original query as fallback
    add(q)
    return candidates
