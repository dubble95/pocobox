"""Voice-conversational English Listening Quiz engine for PocoBox.

Reads 2-3 sentence English listening stories, descriptions, or riddles aloud using
native English TTS, plays a crisp cue chime when waiting for the user's answer,
and accepts spoken answers in either English or Korean.
"""

from __future__ import annotations

import collections
import difflib
import os
import random
import re
import threading
from dataclasses import dataclass
from typing import Any

from .aliases import parse_korean_number, strip_wake_prefix


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class QuizQuestion:
    id: str
    category: str  # animals | food | colors_numbers | family_school | daily | riddles
    kind: str  # story_quiz | listen_and_reply | word_meaning | sentence_meaning
    english_text: str
    korean_meaning: str
    accepted_answers: tuple[str, ...]
    hint_ko: str
    explanation_ko: str

    def to_public_dict(self, reveal: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "category": self.category,
            "kind": self.kind,
        }
        if reveal:
            data["english_text"] = self.english_text
            data["korean_meaning"] = self.korean_meaning
            data["explanation_ko"] = self.explanation_ko
        return data


@dataclass
class QuizTurnResponse:
    ok: bool
    event: str  # started | correct | retry_hint | reveal_next | repeat | hint | score | finished | stopped | error
    active: bool
    chime: str | None
    segments: list[tuple[str, str]]  # [("ko", "..."), ("en", "..."), ("en_slow", "..."), ("pause", "0.35")]
    display_header: str
    display_text: str
    display_status: str
    spoken_summary: str
    state: dict[str, Any]
    expect_response: bool = False


DEFAULT_QUIZ_COUNT = 3

TOPIC_LABELS: dict[str, str] = {
    "all": "종합 듣기 퀴즈",
    "animals": "동물",
    "food": "음식과 과일",
    "colors_numbers": "자연·계절·숫자",
    "family_school": "장소·학교·직업",
    "daily": "일상 스토리",
    "riddles": "사물 수수께끼",
}

BUILTIN_QUIZ_BANK: tuple[QuizQuestion, ...] = (
    # --- Animals (동물 · 2~3문장 듣기 추리) ---
    QuizQuestion(
        id="ani_01",
        category="animals",
        kind="story_quiz",
        english_text=(
            "I am a very big gray animal with large ears. "
            "I use my long nose to drink water and pick up food from the ground. "
            "What animal am I?"
        ),
        korean_meaning="Elephant (코끼리)",
        accepted_answers=("elephant", "엘리펀트", "코끼리"),
        hint_ko="큰 귀와 긴 코를 가진 아주 커다란 회색 동물이에요! 초성 힌트는 ㅋㄲㄹ!",
        explanation_ko="정답은 Elephant, 코끼리예요. 'long nose'는 긴 코, 'pick up food'는 음식을 집어 올린다는 뜻이에요.",
    ),
    QuizQuestion(
        id="ani_02",
        category="animals",
        kind="story_quiz",
        english_text=(
            "I am a black and white bird that lives in a very cold, icy place. "
            "I cannot fly in the sky, but I am a wonderful swimmer in the freezing ocean. "
            "What animal am I?"
        ),
        korean_meaning="Penguin (펭귄)",
        accepted_answers=("penguin", "펭귄", "팽귄"),
        hint_ko="하늘은 날지 못하지만 차가운 바다에서 수영을 아주 잘하는 검은색과 흰색 새예요!",
        explanation_ko="정답은 Penguin, 펭귄이에요. 'freezing ocean'은 꽁꽁 얼 듯 차가운 바다를 뜻해요.",
    ),
    QuizQuestion(
        id="ani_03",
        category="animals",
        kind="story_quiz",
        english_text=(
            "I am the tallest animal living on land, and I have brown spots all over my body. "
            "I use my very long neck to reach fresh green leaves at the top of tall trees. "
            "What animal am I?"
        ),
        korean_meaning="Giraffe (기린)",
        accepted_answers=("giraffe", "지라프", "기린"),
        hint_ko="육지에서 가장 키가 크고, 아주 긴 목으로 높은 나무의 잎을 먹는 동물이에요!",
        explanation_ko="정답은 Giraffe, 기린이에요. 'tallest animal'은 가장 키가 큰 동물, 'brown spots'는 갈색 무늬예요.",
    ),
    QuizQuestion(
        id="ani_04",
        category="animals",
        kind="story_quiz",
        english_text=(
            "I am a clever sea animal that loves to leap high out of the waves. "
            "I swim in groups with my family and make clicking sounds to talk to my friends. "
            "What animal am I?"
        ),
        korean_meaning="Dolphin (돌고래)",
        accepted_answers=("dolphin", "돌핀", "돌고래", "고래"),
        hint_ko="바다 파도 위로 높이 점프하고, 소리를 내며 친구들과 대화하는 똑똑한 바다 동물이에요!",
        explanation_ko="정답은 Dolphin, 돌고래예요. 'leap out of the waves'는 파도 밖으로 뛰어오른다는 표현이에요.",
    ),
    QuizQuestion(
        id="ani_05",
        category="animals",
        kind="story_quiz",
        english_text=(
            "I carry a hard round shell on my back everywhere I go. "
            "I walk very slowly on land, and I pull my head inside my shell when I feel scared. "
            "What animal am I?"
        ),
        korean_meaning="Turtle (거북이)",
        accepted_answers=("turtle", "tortoise", "터틀", "거북이", "거북"),
        hint_ko="등에 단단한 껍질을 메고 천천히 걸으며, 무서울 때 머리를 쏙 숨기는 동물이에요!",
        explanation_ko="정답은 Turtle, 거북이예요. 'hard shell'은 단단한 등껍질, 'feel scared'는 무섭다는 뜻이에요.",
    ),
    QuizQuestion(
        id="ani_06",
        category="animals",
        kind="story_quiz",
        english_text=(
            "During the daytime, I sleep upside down inside dark, quiet caves. "
            "When night falls, I spread my wings and use echoes to find flying insects to eat. "
            "What animal am I?"
        ),
        korean_meaning="Bat (박쥐)",
        accepted_answers=("bat", "배트", "뱃", "박쥐"),
        hint_ko="낮에는 어두운 동굴에서 거꾸로 매달려 자고, 밤에 날아다니며 벌레를 잡는 동물이에요!",
        explanation_ko="정답은 Bat, 박쥐예요. 'upside down'은 거꾸로, 'use echoes'는 메아리를 이용한다는 뜻이에요.",
    ),
    QuizQuestion(
        id="ani_07",
        category="animals",
        kind="story_quiz",
        english_text=(
            "I have bright orange fur with bold black stripes, and I live deep in the forest. "
            "I am a powerful hunter from the big cat family, and unlike most cats, I enjoy swimming. "
            "What animal am I?"
        ),
        korean_meaning="Tiger (호랑이)",
        accepted_answers=("tiger", "타이거", "호랑이", "범"),
        hint_ko="주황색 털에 검은 줄무늬가 있고, 숲속에 사는 힘센 고양이과 사냥꾼이에요!",
        explanation_ko="정답은 Tiger, 호랑이예요. 'black stripes'는 검은 줄무늬, 'powerful hunter'는 힘센 사냥꾼이에요.",
    ),
    QuizQuestion(
        id="ani_08",
        category="animals",
        kind="story_quiz",
        english_text=(
            "I start my life as a tiny caterpillar that loves munching on green leaves. "
            "Later, I rest inside a cozy shell and come out with bright, colorful wings to visit flowers. "
            "What am I?"
        ),
        korean_meaning="Butterfly (나비)",
        accepted_answers=("butterfly", "버터플라이", "나비", "호랑나비"),
        hint_ko="처음엔 애벌레였다가 번데기를 거쳐 예쁜 날개로 꽃 사이를 날아다니는 곤충이에요!",
        explanation_ko="정답은 Butterfly, 나비예요. 'caterpillar'는 애벌레, 'colorful wings'는 알록달록한 날개예요.",
    ),
    # --- Food (음식과 과일 · 2~3문장 듣기 추리) ---
    QuizQuestion(
        id="food_01",
        category="food",
        kind="story_quiz",
        english_text=(
            "I am a huge round fruit with thick green stripes on the outside. "
            "When you slice me open, I am bright red, juicy, and cool, which makes me a favorite summer treat. "
            "What fruit am I?"
        ),
        korean_meaning="Watermelon (수박)",
        accepted_answers=("watermelon", "워터멜론", "수박"),
        hint_ko="겉은 초록색 줄무늬, 속은 빨갛고 시원한 여름 대표 과일이에요!",
        explanation_ko="정답은 Watermelon, 수박이에요. 'slice open'은 잘라 열다, 'juicy'는 과즙이 풍부하다는 뜻이에요.",
    ),
    QuizQuestion(
        id="food_02",
        category="food",
        kind="story_quiz",
        english_text=(
            "I am a long, curved fruit wrapped in a bright yellow peel. "
            "You peel off my soft skin before taking a bite, and monkeys are famous for loving me. "
            "What fruit am I?"
        ),
        korean_meaning="Banana (바나나)",
        accepted_answers=("banana", "바나나"),
        hint_ko="노란 껍질을 벗겨 먹는 길쭉하고 살짝 구부러진 과일이에요! 원숭이가 아주 좋아하죠.",
        explanation_ko="정답은 Banana, 바나나예요. 'yellow peel'은 노란 껍질, 'take a bite'는 한 입 베어 물다는 뜻이에요.",
    ),
    QuizQuestion(
        id="food_03",
        category="food",
        kind="story_quiz",
        english_text=(
            "I am a freezing, sweet dessert that comes in flavors like vanilla, chocolate, and mint. "
            "People eat me in a cone or a cup, and if you leave me in the sun, I quickly melt into a puddle. "
            "What food am I?"
        ),
        korean_meaning="Ice cream (아이스크림)",
        accepted_answers=("ice cream", "icecream", "아이스크림"),
        hint_ko="콘이나 컵에 담아 먹는 차갑고 달콤한 디저트예요! 햇볕에 두면 금방 녹아 버려요.",
        explanation_ko="정답은 Ice cream, 아이스크림이에요. 'melt into a puddle'은 물웅덩이처럼 녹아내린다는 표현이에요.",
    ),
    QuizQuestion(
        id="food_04",
        category="food",
        kind="story_quiz",
        english_text=(
            "I am a crunchy orange vegetable that grows underground in the soil. "
            "Rabbits love to nibble on me, and parents often say eating me helps keep your eyes healthy. "
            "What vegetable am I?"
        ),
        korean_meaning="Carrot (당근)",
        accepted_answers=("carrot", "캐럿", "당근"),
        hint_ko="땅속에서 자라는 아삭아삭한 주황색 채소예요! 토끼가 좋아하고 눈 건강에도 좋아요.",
        explanation_ko="정답은 Carrot, 당근이에요. 'grow underground'는 땅속에서 자란다는 뜻이에요.",
    ),
    QuizQuestion(
        id="food_05",
        category="food",
        kind="story_quiz",
        english_text=(
            "I am a round, flat bread baked in a very hot oven until my crust is crispy. "
            "I am covered with red tomato sauce, melted cheese, and tasty toppings, then cut into triangles. "
            "What food am I?"
        ),
        korean_meaning="Pizza (피자)",
        accepted_answers=("pizza", "피자"),
        hint_ko="토마토 소스와 녹은 치즈를 얹어 오븐에 굽고, 삼각형 조각으로 잘라 먹는 음식이에요!",
        explanation_ko="정답은 Pizza, 피자예요. 'melted cheese'는 녹은 치즈, 'cut into triangles'는 삼각형으로 자른다는 뜻이에요.",
    ),
    QuizQuestion(
        id="food_06",
        category="food",
        kind="story_quiz",
        english_text=(
            "I am a small bright red fruit shaped a little like a heart. "
            "I have tiny seeds dotted on my outside and a cute little green leafy hat on top. "
            "What fruit am I?"
        ),
        korean_meaning="Strawberry (딸기)",
        accepted_answers=("strawberry", "스트로베리", "딸기"),
        hint_ko="하트 모양처럼 생긴 빨간 과일이고, 겉에 작은 씨가 콕콕 박혀 있어요!",
        explanation_ko="정답은 Strawberry, 딸기예요. 'shaped like a heart'는 하트 모양이라는 뜻이에요.",
    ),
    QuizQuestion(
        id="food_07",
        category="food",
        kind="story_quiz",
        english_text=(
            "Busy bees fly from flower to flower collecting sweet nectar to make me. "
            "I am golden, thick, and sticky, and people drizzle me over warm pancakes or tea. "
            "What food am I?"
        ),
        korean_meaning="Honey (꿀)",
        accepted_answers=("honey", "허니", "꿀", "벌꿀"),
        hint_ko="꿀벌이 꽃에서 모은 즙으로 만드는 달콤하고 끈적한 황금빛 음식이에요!",
        explanation_ko="정답은 Honey, 꿀이에요. 'sticky'는 끈적끈적한, 'drizzle over'는 위에 살짝 뿌린다는 뜻이에요.",
    ),
    QuizQuestion(
        id="food_08",
        category="food",
        kind="story_quiz",
        english_text=(
            "You can boil me, fry me, or scramble me for a healthy morning breakfast. "
            "I have a smooth, hard shell on the outside, and when you crack me open, you find a bright yellow center. "
            "What food am I?"
        ),
        korean_meaning="Egg (달걀 / 계란)",
        accepted_answers=("egg", "eggs", "에그", "달걀", "계란"),
        hint_ko="삶거나 프라이로 아침에 자주 먹어요. 단단한 껍질을 깨면 노란 노른자가 나와요!",
        explanation_ko="정답은 Egg, 달걀이에요. 'crack open'은 톡 깨서 연다는 표현이에요.",
    ),
    # --- Family, School, Places & Jobs (장소·학교·직업 · 2~3문장 듣기 추리) ---
    QuizQuestion(
        id="sch_01",
        category="family_school",
        kind="story_quiz",
        english_text=(
            "This is a peaceful, quiet building filled with thousands of books on tall wooden shelves. "
            "People visit this place to study, read exciting stories, and borrow books to take home for free. "
            "What place is this?"
        ),
        korean_meaning="Library (도서관)",
        accepted_answers=("library", "라이브러리", "도서관", "도서실"),
        hint_ko="수천 권의 책이 꽂혀 있고, 조용히 책을 읽거나 무료로 빌려 갈 수 있는 곳이에요!",
        explanation_ko="정답은 Library, 도서관이에요. 'borrow books'는 책을 빌린다는 뜻이에요.",
    ),
    QuizQuestion(
        id="sch_02",
        category="family_school",
        kind="story_quiz",
        english_text=(
            "I spend my day in a bright classroom with a whiteboard, desks, and many curious students. "
            "I explain new lessons, answer questions, and help children learn and grow every day. "
            "What is my job?"
        ),
        korean_meaning="Teacher (선생님)",
        accepted_answers=("teacher", "티처", "선생님", "교사", "선생"),
        hint_ko="교실에서 학생들에게 새로운 내용을 가르치고 질문에 답해 주시는 직업이에요!",
        explanation_ko="정답은 Teacher, 선생님이에요. 'curious students'는 호기심 많은 학생들을 뜻해요.",
    ),
    QuizQuestion(
        id="sch_03",
        category="family_school",
        kind="story_quiz",
        english_text=(
            "You come to this huge, busy place when you want to travel far away by air. "
            "You check in your suitcases, show your passport, and walk to a gate to board a big plane. "
            "What place is this?"
        ),
        korean_meaning="Airport (공항)",
        accepted_answers=("airport", "에어포트", "공항"),
        hint_ko="여권을 보여주고 여행 가방을 부친 뒤, 비행기를 타러 가는 아주 큰 곳이에요!",
        explanation_ko="정답은 Airport, 공항이에요. 'show your passport'는 여권을 보여주다, 'board a plane'은 비행기에 탑승하다예요.",
    ),
    QuizQuestion(
        id="sch_04",
        category="family_school",
        kind="story_quiz",
        english_text=(
            "I usually wear a clean white coat and carry a stethoscope around my neck at the hospital. "
            "When someone has a fever or a sore throat, I examine them and give medicine to help them feel better. "
            "What is my job?"
        ),
        korean_meaning="Doctor (의사)",
        accepted_answers=("doctor", "닥터", "의사", "의사 선생님"),
        hint_ko="병원에서 하얀 가운과 청진기를 하고, 아픈 사람을 진찰해서 낫게 도와주는 직업이에요!",
        explanation_ko="정답은 Doctor, 의사예요. 'have a fever'는 열이 나다, 'sore throat'은 목이 아프다는 뜻이에요.",
    ),
    QuizQuestion(
        id="sch_05",
        category="family_school",
        kind="story_quiz",
        english_text=(
            "This is a lively place where skilled chefs cook delicious meals in a busy kitchen. "
            "Guests sit comfortably at tables, look through a menu, and order their favorite dishes. "
            "What place is this?"
        ),
        korean_meaning="Restaurant (식당 / 레스토랑)",
        accepted_answers=("restaurant", "레스토랑", "식당", "음식점"),
        hint_ko="요리사가 맛있는 음식을 만들고, 손님들이 메뉴판을 보고 주문해서 먹는 곳이에요!",
        explanation_ko="정답은 Restaurant, 식당이에요. 'look through a menu'는 메뉴판을 훑어보다라는 표현이에요.",
    ),
    QuizQuestion(
        id="sch_06",
        category="family_school",
        kind="story_quiz",
        english_text=(
            "Whenever the emergency bell rings, I quickly put on my heavy protective coat and helmet. "
            "I rush out on a bright red truck and spray water from a big hose to put out dangerous flames. "
            "What is my job?"
        ),
        korean_meaning="Firefighter (소방관)",
        accepted_answers=("firefighter", "fireman", "파이어파이터", "소방관", "소방수"),
        hint_ko="빨간 차를 타고 출동해서 큰 호스로 물을 뿌려 위험한 불을 끄는 멋진 직업이에요!",
        explanation_ko="정답은 Firefighter, 소방관이에요. 'put out flames'는 불길을 끄다라는 뜻이에요.",
    ),
    QuizQuestion(
        id="sch_07",
        category="family_school",
        kind="story_quiz",
        english_text=(
            "Families love visiting this outdoor park on sunny weekends to see wild creatures up close. "
            "You can walk along paths to watch playful monkeys, sleeping lions, and tall giraffes. "
            "What place is this?"
        ),
        korean_meaning="Zoo (동물원)",
        accepted_answers=("zoo", "주", "동물원"),
        hint_ko="주말에 가족과 함께 원숭이, 사자, 기린 같은 야생 동물을 가까이서 구경하는 곳이에요!",
        explanation_ko="정답은 Zoo, 동물원이에요. 'up close'는 아주 가까이서라는 뜻이에요.",
    ),
    QuizQuestion(
        id="sch_08",
        category="family_school",
        kind="story_quiz",
        english_text=(
            "Students use me every single day in class to write answers and sketch pictures in notebooks. "
            "I am made of thin wood with dark lead inside, and I often have a pink eraser on my top end. "
            "What am I?"
        ),
        korean_meaning="Pencil (연필)",
        accepted_answers=("pencil", "펜슬", "연필"),
        hint_ko="공책에 글씨를 쓰거나 그림을 그릴 때 쓰는 나무로 된 학용품이고, 끝에 지우개가 달려 있기도 해요!",
        explanation_ko="정답은 Pencil, 연필이에요. 'sketch pictures'는 그림을 스케치하다라는 뜻이에요.",
    ),
    # --- Daily Stories (일상 스토리 듣기 이해 · 2~3문장 + 질문) ---
    QuizQuestion(
        id="day_01",
        category="daily",
        kind="story_quiz",
        english_text=(
            "Tom looked out the bedroom window and noticed dark gray clouds covering the sky. "
            "Before stepping outside, he put on his yellow raincoat and grabbed something to hold over his head to stay dry. "
            "What did Tom grab?"
        ),
        korean_meaning="Umbrella (우산)",
        accepted_answers=("umbrella", "엄브렐라", "엄브렐러", "우산"),
        hint_ko="먹구름이 가득한 날, 비에 젖지 않으려고 머리 위에 쓰려고 챙긴 물건이에요!",
        explanation_ko="정답은 Umbrella, 우산이에요. 'stay dry'는 젖지 않고 마른 상태를 유지한다는 표현이에요.",
    ),
    QuizQuestion(
        id="day_02",
        category="daily",
        kind="story_quiz",
        english_text=(
            "Mia bought four fresh apples at the neighborhood market this morning. "
            "On her way home, she gave two apples to her best friend and ate one crunchy apple herself. "
            "How many apples does Mia have left?"
        ),
        korean_meaning="One (1개)",
        accepted_answers=("one", "1", "원", "하나", "한개", "한 개", "1개", "일"),
        hint_ko="사과 4개를 샀는데, 친구에게 2개를 주고 자기가 1개를 먹었어요. 몇 개 남았을까요?",
        explanation_ko="정답은 One, 1개예요! 4 빼기 2 빼기 1은 1이죠. 'have left'는 남았다는 뜻이에요.",
    ),
    QuizQuestion(
        id="day_03",
        category="daily",
        kind="story_quiz",
        english_text=(
            "Today is Jake's favorite day of the whole year, and all of his friends gathered in his living room. "
            "They lit ten bright candles on a chocolate cake and sang a cheerful song right before he made a wish. "
            "What special day are they celebrating?"
        ),
        korean_meaning="Birthday (생일)",
        accepted_answers=("birthday", "벌스데이", "버스데이", "생일", "생일 파티", "생일파티"),
        hint_ko="초콜릿 케이크에 촛불 10개를 켜고 축하 노래를 부르며 소원을 비는 특별한 날이에요!",
        explanation_ko="정답은 Birthday, 생일이에요. 'make a wish'는 소원을 빌다는 멋진 표현이에요.",
    ),
    QuizQuestion(
        id="day_04",
        category="daily",
        kind="story_quiz",
        english_text=(
            "Sarah finished her science homework at nine o'clock and brushed her teeth carefully. "
            "She changed into her soft pajamas, turned off the lamp, and pulled a cozy blanket up to her chin. "
            "What is Sarah going to do next?"
        ),
        korean_meaning="Sleep (잠자기 / 자다)",
        accepted_answers=("sleep", "go to sleep", "sleeping", "go to bed", "슬립", "잠", "잠자기", "자다", "자요", "잘 거예요", "자려고"),
        hint_ko="양치하고 잠옷으로 갈아입고 불을 끈 뒤, 포근한 이불을 덮었어요. 이제 무엇을 할까요?",
        explanation_ko="정답은 Sleep, 잠자기예요. 'cozy blanket'은 포근한 이불, 'turn off the lamp'는 불을 끄다예요.",
    ),
    QuizQuestion(
        id="day_05",
        category="daily",
        kind="story_quiz",
        english_text=(
            "Ben and his family packed big towels, sunglasses, and swimsuits into the back of their car. "
            "When they finally arrived, they felt warm sand under their bare feet and heard salty ocean waves crashing. "
            "Where did Ben's family go?"
        ),
        korean_meaning="Beach (해변 / 바닷가 / 바다)",
        accepted_answers=("beach", "the beach", "ocean", "sea", "비치", "해변", "바닷가", "바다", "해수욕장"),
        hint_ko="수영복과 선글라스를 챙겨 갔더니 따뜻한 모래와 시원한 파도 소리가 들렸어요. 어디일까요?",
        explanation_ko="정답은 Beach, 바닷가(해변)예요. 'bare feet'은 맨발, 'ocean waves crashing'은 파도가 부서지는 소리예요.",
    ),
    QuizQuestion(
        id="day_06",
        category="daily",
        kind="story_quiz",
        english_text=(
            "Kevin put on his thick winter coat, warm wool gloves, and a long knitted scarf. "
            "In the white backyard, he rolled three big balls of snow, stacked them up, and added a carrot for a nose. "
            "What did Kevin build?"
        ),
        korean_meaning="Snowman (눈사람)",
        accepted_answers=("snowman", "a snowman", "스노우맨", "스노맨", "눈사람"),
        hint_ko="눈을 동그랗게 세 덩이 굴려 쌓아 올리고, 당근으로 코를 만들어 주었어요!",
        explanation_ko="정답은 Snowman, 눈사람이에요. 'stack them up'은 차곡차곡 쌓아 올린다는 뜻이에요.",
    ),
    QuizQuestion(
        id="day_07",
        category="daily",
        kind="story_quiz",
        english_text=(
            "Emma opened the refrigerator door because her stomach was making a loud rumbling sound. "
            "She had been playing soccer all afternoon and had not eaten anything since early morning. "
            "How does Emma feel right now?"
        ),
        korean_meaning="Hungry (배고픔 / 배고파요)",
        accepted_answers=("hungry", "헝그리", "배고파", "배고파요", "배고픔", "배고프다", "배가 고파", "배가 고파요"),
        hint_ko="오후 내내 축구를 하고 아침 이후로 아무것도 못 먹어서 배에서 꼬르륵 소리가 났대요!",
        explanation_ko="정답은 Hungry, 배고파요예요. 'rumbling sound'는 배에서 나는 꼬르륵 소리예요.",
    ),
    QuizQuestion(
        id="day_08",
        category="daily",
        kind="story_quiz",
        english_text=(
            "Lucy rushed to the corner bus stop at eight in the morning with her heavy backpack on. "
            "Suddenly, she gasped because she remembered leaving her math workbook and lunchbox on the kitchen table. "
            "Where was Lucy going?"
        ),
        korean_meaning="School (학교)",
        accepted_answers=("school", "스쿨", "학교"),
        hint_ko="아침 8시에 책가방을 메고 수학 문제집과 도시락을 챙겨야 했던 곳은 어디일까요?",
        explanation_ko="정답은 School, 학교예요. 'math workbook'은 수학 문제집, 'lunchbox'는 도시락통이에요.",
    ),
    # --- Nature, Seasons, Time & Numbers (자연·계절·숫자 · 2~3문장 듣기 추리) ---
    QuizQuestion(
        id="col_01",
        category="colors_numbers",
        kind="story_quiz",
        english_text=(
            "Right after a warm afternoon rain shower, the bright sun peeks out from behind the clouds. "
            "Suddenly, a huge colorful arch stretches across the sky with red, yellow, green, blue, and purple bands. "
            "What appeared in the sky?"
        ),
        korean_meaning="Rainbow (무지개)",
        accepted_answers=("rainbow", "레인보우", "레인보", "무지개"),
        hint_ko="비가 그치고 해가 나오면 하늘에 빨주노초파남보 알록달록하게 뜨는 아치예요!",
        explanation_ko="정답은 Rainbow, 무지개예요. 'peeks out'은 구름 뒤에서 빼꼼 얼굴을 내민다는 뜻이에요.",
    ),
    QuizQuestion(
        id="col_02",
        category="colors_numbers",
        kind="story_quiz",
        english_text=(
            "Out of the four seasons in a year, this one is the coldest and quietest of all. "
            "Most trees have lost their leaves, ponds freeze into solid ice, and white snow drifts down from the sky. "
            "Which season is this?"
        ),
        korean_meaning="Winter (겨울)",
        accepted_answers=("winter", "윈터", "겨울", "겨울철"),
        hint_ko="일 년 네 계절 중 가장 춥고, 연못이 꽁꽁 얼고 하얀 눈이 내리는 계절이에요!",
        explanation_ko="정답은 Winter, 겨울이에요. 'freeze into solid ice'는 단단한 얼음으로 언다는 뜻이에요.",
    ),
    QuizQuestion(
        id="col_03",
        category="colors_numbers",
        kind="story_quiz",
        english_text=(
            "I rise in the east every morning and fill the whole Earth with bright golden light and warmth. "
            "Green plants need my energy to grow tall, and I slowly sink below the horizon every evening. "
            "What am I?"
        ),
        korean_meaning="Sun (해 / 태양)",
        accepted_answers=("sun", "the sun", "썬", "선", "해", "태양", "햇님", "해님"),
        hint_ko="매일 아침 동쪽에서 떠올라 지구를 따뜻하게 비추고 저녁에 지는 커다란 별이에요!",
        explanation_ko="정답은 Sun, 태양(해)이에요. 'rise in the east'는 동쪽에서 뜬다는 뜻이에요.",
    ),
    QuizQuestion(
        id="col_04",
        category="colors_numbers",
        kind="story_quiz",
        english_text=(
            "I hang on the living room wall or sit quietly on your bedside table with numbers from one to twelve. "
            "My thin hands keep moving around in a circle all day and night so you always know if you are on time. "
            "What am I?"
        ),
        korean_meaning="Clock (시계)",
        accepted_answers=("clock", "watch", "클락", "클록", "시계", "벽시계", "탁상시계"),
        hint_ko="1부터 12까지 숫자가 적혀 있고, 바늘이 빙글빙글 돌며 시간을 알려주는 물건이에요!",
        explanation_ko="정답은 Clock, 시계예요. 'on time'은 제시간에, 늦지 않게라는 표현이에요.",
    ),
    QuizQuestion(
        id="col_05",
        category="colors_numbers",
        kind="story_quiz",
        english_text=(
            "In this lovely season, the freezing winter winds finally fade away and gentle warm breezes begin to blow. "
            "Pink cherry blossoms and colorful tulips bloom in the parks, and baby birds start singing in the trees. "
            "Which season is this?"
        ),
        korean_meaning="Spring (봄)",
        accepted_answers=("spring", "스프링", "봄", "봄철"),
        hint_ko="추운 겨울이 지나고 따뜻한 바람이 불며 벚꽃과 튤립이 활짝 피는 계절이에요!",
        explanation_ko="정답은 Spring, 봄이에요. 'cherry blossoms'는 벚꽃, 'bloom'은 꽃이 핀다는 뜻이에요.",
    ),
    QuizQuestion(
        id="col_06",
        category="colors_numbers",
        kind="story_quiz",
        english_text=(
            "Every week has seven days, beginning with Monday and ending on Sunday. "
            "Suppose today is Wednesday and your class has a fun field trip exactly two days from now. "
            "What day of the week is the field trip?"
        ),
        korean_meaning="Friday (금요일)",
        accepted_answers=("friday", "프라이데이", "금요일", "금"),
        hint_ko="오늘이 수요일(Wednesday)이라면, 정확히 이틀 뒤는 무슨 요일일까요?",
        explanation_ko="정답은 Friday, 금요일이에요! Wednesday(수) → Thursday(목) → Friday(금) 순서예요.",
    ),
    QuizQuestion(
        id="col_07",
        category="colors_numbers",
        kind="story_quiz",
        english_text=(
            "I glow softly in the dark night sky surrounded by thousands of twinkling stars. "
            "On some nights I look like a big round silver coin, and on other nights I curve like a thin banana. "
            "What am I?"
        ),
        korean_meaning="Moon (달)",
        accepted_answers=("moon", "the moon", "문", "달", "달님", "보름달", "초승달"),
        hint_ko="반짝이는 별들 사이로 밤하늘에 뜨고, 둥근 보름달이었다가 초승달로 모양이 바뀌어요!",
        explanation_ko="정답은 Moon, 달이에요. 'twinkling stars'는 반짝이는 별들이라는 표현이에요.",
    ),
    QuizQuestion(
        id="col_08",
        category="colors_numbers",
        kind="story_quiz",
        english_text=(
            "In nature, a spider walks on eight thin legs, while a playful puppy runs on four legs. "
            "If you spot one spider on a web and one puppy on the grass, how many legs do they have altogether?"
        ),
        korean_meaning="Twelve (12개)",
        accepted_answers=("twelve", "12", "트웰브", "열둘", "열두", "열두개", "열두 개", "12개", "십이"),
        hint_ko="거미 다리 8개와 강아지 다리 4개를 모두 합치면 몇 개일까요?",
        explanation_ko="정답은 Twelve, 12개예요! 8 더하기 4는 12죠. 'altogether'는 모두 합쳐서라는 뜻이에요.",
    ),
    # --- Everyday Object Riddles (사물 수수께끼 · 2~3문장 듣기 추리) ---
    QuizQuestion(
        id="rid_01",
        category="riddles",
        kind="story_quiz",
        english_text=(
            "I stand in your room with four sturdy legs and a wide flat top where you place books or dinner plates. "
            "Even though I have four legs just like a dog, I can never walk or run across the floor. "
            "What am I?"
        ),
        korean_meaning="Table / Desk (책상 / 식탁)",
        accepted_answers=("table", "desk", "테이블", "데스크", "책상", "식탁", "탁자"),
        hint_ko="강아지처럼 다리가 4개나 있지만 절대 걸어 다닐 수 없고, 위에 책이나 밥그릇을 올려놓는 가구예요!",
        explanation_ko="정답은 Table 또는 Desk, 책상·식탁이에요. 'flat top'은 평평한 윗면이라는 뜻이에요.",
    ),
    QuizQuestion(
        id="rid_02",
        category="riddles",
        kind="story_quiz",
        english_text=(
            "You hold me in your hand every morning and night right beside the bathroom sink. "
            "I have a slim handle and tiny soft bristles that team up with minty paste to keep your smile sparkling clean. "
            "What am I?"
        ),
        korean_meaning="Toothbrush (칫솔)",
        accepted_answers=("toothbrush", "투스브러시", "투스브러쉬", "칫솔"),
        hint_ko="매일 아침저녁 세면대 앞에서 치약을 묻혀 이를 반짝반짝 닦을 때 쓰는 물건이에요!",
        explanation_ko="정답은 Toothbrush, 칫솔이에요. 'bristles'는 칫솔모, 'minty paste'는 민트향 치약이에요.",
    ),
    QuizQuestion(
        id="rid_03",
        category="riddles",
        kind="story_quiz",
        english_text=(
            "I have two round wheels, pedals for your feet to push, and handlebars to steer left and right. "
            "I never need a single drop of gasoline, and you should always wear a helmet when riding me in the park. "
            "What am I?"
        ),
        korean_meaning="Bicycle (자전거)",
        accepted_answers=("bicycle", "bike", "바이시클", "바이크", "자전거"),
        hint_ko="바퀴가 두 개고 발로 페달을 밟아 앞으로 가요! 공원에서 탈 때 헬멧을 꼭 써야 해요.",
        explanation_ko="정답은 Bicycle(Bike), 자전거예요. 'steer'는 방향을 조종한다는 뜻이에요.",
    ),
    QuizQuestion(
        id="rid_04",
        category="riddles",
        kind="story_quiz",
        english_text=(
            "You stand in front of me every morning to comb your hair or check if your outfit looks neat. "
            "Whenever you smile, wave, or make a funny face at me, I instantly copy the exact same face back to you. "
            "What am I?"
        ),
        korean_meaning="Mirror (거울)",
        accepted_answers=("mirror", "미러", "거울"),
        hint_ko="아침에 머리를 빗을 때 보고, 내가 웃거나 윙크하면 똑같이 따라 하는 물건이에요!",
        explanation_ko="정답은 Mirror, 거울이에요. 'comb your hair'는 머리를 빗다, 'copy'는 똑같이 따라 하다예요.",
    ),
    QuizQuestion(
        id="rid_05",
        category="riddles",
        kind="story_quiz",
        english_text=(
            "I fold shut like a notebook, and when you open me, I have a bright screen and a keyboard full of letters. "
            "I run on a rechargeable battery so people can carry me in a bag to type homework, code, or watch videos anywhere. "
            "What am I?"
        ),
        korean_meaning="Laptop (노트북 / 컴퓨터)",
        accepted_answers=("laptop", "computer", "notebook", "랩톱", "랩탑", "노트북", "컴퓨터"),
        hint_ko="공책처럼 접어서 가방에 넣고 다닐 수 있고, 화면과 키보드가 붙어 있는 전자기기예요!",
        explanation_ko="정답은 Laptop, 노트북이에요. 'fold shut'은 접어서 닫다, 'rechargeable battery'는 충전식 배터리예요.",
    ),
    QuizQuestion(
        id="rid_06",
        category="riddles",
        kind="story_quiz",
        english_text=(
            "I zoom rapidly across the countryside on two long steel tracks from one city to another. "
            "I have many connected cars carrying hundreds of passengers, and I stop briefly at big stations along the way. "
            "What am I?"
        ),
        korean_meaning="Train (기차 / 열차)",
        accepted_answers=("train", "subway", "트레인", "기차", "열차", "전철", "지하철"),
        hint_ko="긴 철길 위를 빠르게 달리며, 여러 칸이 연결되어 많은 승객을 싣고 역마다 서는 탈것이에요!",
        explanation_ko="정답은 Train, 기차예요. 'steel tracks'는 철길, 'passengers'는 승객들을 뜻해요.",
    ),
    QuizQuestion(
        id="rid_07",
        category="riddles",
        kind="story_quiz",
        english_text=(
            "We always come as a matching pair, one for your left foot and one for your right foot. "
            "Some of us have laces to tie into a bow, and we protect your feet whenever you walk, jump, or race outside. "
            "What are we?"
        ),
        korean_meaning="Shoes (신발 / 운동화)",
        accepted_answers=("shoes", "shoe", "sneakers", "슈즈", "신발", "운동화", "구두"),
        hint_ko="왼발과 오른발에 한 짝씩 신으며, 끈을 묶기도 하고 밖에서 발을 보호해 주는 물건이에요!",
        explanation_ko="정답은 Shoes, 신발이에요. 'matching pair'는 한 켤레, 'laces'는 신발 끈이에요.",
    ),
    QuizQuestion(
        id="rid_08",
        category="riddles",
        kind="story_quiz",
        english_text=(
            "I have a front cover, a back cover, and hundreds of paper pages filled with adventures and facts. "
            "You turn my pages one by one as you read, and you slip a bookmark inside me when you take a break. "
            "What am I?"
        ),
        korean_meaning="Book (책)",
        accepted_answers=("book", "books", "북", "책", "동화책", "소설책"),
        hint_ko="앞표지와 뒷표지 사이에 종이 페이지가 가득하고, 읽다가 쉴 때는 책갈피를 꽂아 두어요!",
        explanation_ko="정답은 Book, 책이에요. 'turn pages'는 페이지를 넘기다, 'bookmark'는 책갈피예요.",
    ),
)


_KO_TRAILING_ENDINGS = (
    "인 것 같아요",
    "인것같아요",
    "인 것 같아",
    "인것같아",
    "라는 뜻이에요",
    "라는 뜻이야",
    "라는 뜻",
    "뜻이에요",
    "뜻이야",
    "입니다",
    "이에요",
    "예요",
    "에요",
    "인가요",
    "이야",
    "인가",
    "같아요",
    "같아",
    "해요",
    "어요",
    "아요",
    "요",
    "야",
    "다",
)

_KO_LEADING_FILLERS = (
    "정답은 ",
    "정답 ",
    "답은 ",
    "그건 ",
    "그거 ",
    "이건 ",
    "이거 ",
    "아마 ",
    "음 ",
    "어 ",
    "저 ",
    "나는 ",
    "저는 ",
    "it is ",
    "it's ",
    "that is ",
    "that's ",
    "they are ",
    "a ",
    "an ",
    "the ",
)


def normalize_answer(text: str) -> str:
    """Normalize a spoken Korean/English quiz answer for forgiving local comparison."""
    if not text:
        return ""
    s, _ = strip_wake_prefix(text.strip())
    s = s.lower()
    s = re.sub(r"[.!?,~'\"`“”‘’()\[\]]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    changed = True
    while changed and s:
        changed = False
        for pref in _KO_LEADING_FILLERS:
            if s.startswith(pref) and len(s) > len(pref):
                s = s[len(pref) :].strip()
                changed = True
                break

    for end in _KO_TRAILING_ENDINGS:
        if s.endswith(end) and len(s) > len(end):
            stem = s[: -len(end)].strip()
            if stem:
                s = stem
                break

    return s


def compact_answer(text: str) -> str:
    return re.sub(r"\s+", "", normalize_answer(text))


def is_local_answer_match(user_text: str, question: QuizQuestion) -> bool:
    """Return True if user_text locally matches any accepted answer for question."""
    raw_norm = normalize_answer(user_text)
    if not raw_norm:
        return False
    raw_compact = compact_answer(user_text)

    basic = re.sub(r"[.!?,~'\"`“”‘’()\[\]\s]+", "", strip_wake_prefix(user_text.strip())[0].lower())

    candidates = list(question.accepted_answers) + [question.korean_meaning]
    if question.kind in ("story_quiz", "listen_and_reply"):
        for part in re.split(r"[(),/]+", question.korean_meaning):
            if part.strip():
                candidates.append(part.strip())

    for ans in candidates:
        ans_norm = normalize_answer(ans)
        ans_compact = compact_answer(ans)
        ans_basic = re.sub(r"[.!?,~'\"`“”‘’()\[\]\s]+", "", ans.lower())
        if not ans_compact:
            continue

        if raw_norm == ans_norm or raw_compact == ans_compact or (ans_basic and basic == ans_basic):
            return True

        # Substring containment (e.g., "그거 코끼리 아니야" contains "코끼리")
        if len(ans_compact) >= 2 and ans_compact in raw_compact:
            return True
        if len(ans_basic) >= 2 and ans_basic in basic:
            return True

        # Single-char nouns like "꿀", "달", "해", "책", "봄", "물" match exact token
        if len(ans_compact) == 1 and ans_compact in raw_norm.split():
            return True

        # Fuzzy match for minor STT slips (only for >= 3-char targets)
        if len(ans_compact) >= 3 and len(raw_compact) >= 2:
            ratio = difflib.SequenceMatcher(None, raw_compact, ans_compact).ratio()
            if ratio >= 0.80:
                return True

    return False


def classify_quiz_command(text: str) -> str | None:
    """Detect in-quiz voice control commands: stop | repeat_slow | repeat | hint | skip | score."""
    if not text or not text.strip():
        return None
    cleaned, _ = strip_wake_prefix(text.strip())
    low = cleaned.strip(" .!?,~").lower()
    if not low:
        return None

    # Stop / Quit
    if re.search(
        r"(^(?:영어\s*)?퀴즈\s*(?:그만|종료|끝|중단|나가기|꺼|멈춰)|^(?:그만|그만할래|그만해|그만할게|종료|끝|끝내|끝내줘|나가기|멈춰|스톱|스탑)(?:\s*해\s*줘|\s*할래|\s*할게요|\s*줘|\s*요)?$|^stop(?:\s+quiz)?$|^quit(?:\s+quiz)?$|^exit(?:\s+quiz)?$)",
        low,
    ):
        return "stop"

    # Slow repeat
    if re.search(r"(천천히|느리게|좀\s*더\s*천천히|slow(?:er|ly)?)", low):
        return "repeat_slow"

    # Repeat
    if re.search(
        r"(다시\s*(?:들려|말해|읽어|해|한\s*번)?|한\s*번\s*더|한번\s*더|못\s*들었|뭐라고|잘\s*안\s*들렸|^다시$|^repeat$|^again$|say\s+(?:it\s+)?again|one\s+more\s+time)",
        low,
    ):
        return "repeat"

    # Hint
    if re.search(r"(힌트|힌트\s*줘|힌트\s*알려|초성|^hint$|give\s+me\s+a\s+hint)", low):
        return "hint"

    # Skip / Pass / Don't know
    if re.search(
        r"(패스|모르겠|몰라|모르겠어|모르겠어요|잘\s*몰라|넘어가|다음\s*문제|정답\s*(?:알려|뭐야|보여)|포기|^skip$|^pass$|i\s+don'?t\s+know|next\s+question)",
        low,
    ):
        return "skip"

    # Score check
    if re.search(r"(점수|몇\s*점|몇\s*개\s*맞|몇\s*문제\s*맞|지금\s*점수|^score$|my\s+score)", low):
        return "score"

    return None


def parse_quiz_options(text: str) -> tuple[str, int]:
    """Extract (topic, question_count) from a start-quiz command."""
    low = (text or "").lower()
    topic = "all"
    if re.search(r"(동물|animal)", low):
        topic = "animals"
    elif re.search(r"(음식|과일|먹을|food|fruit)", low):
        topic = "food"
    elif re.search(r"(색깔|색상|숫자|자연|계절|시간|color|colour|number|nature|season)", low):
        topic = "colors_numbers"
    elif re.search(r"(가족|학교|학용품|장소|직업|family|school|place|job)", low):
        topic = "family_school"
    elif re.search(r"(문장|회화|생활|일상|스토리|이야기|인사|표현|daily|story|sentence|phrase)", low):
        topic = "daily"
    elif re.search(r"(질문|수수께끼|사물|대화|riddle|object|question)", low):
        topic = "riddles"

    count = DEFAULT_QUIZ_COUNT
    m = re.search(r"(\d+)\s*(?:문제|개|문항|questions?)", low)
    if m:
        count = int(m.group(1))
    else:
        ko_counts = {
            "한": 1, "하나": 1, "일": 1,
            "두": 2, "둘": 2, "이": 2,
            "세": 3, "셋": 3, "삼": 3,
            "네": 4, "넷": 4, "사": 4,
            "다섯": 5, "오": 5,
            "여섯": 6, "육": 6,
            "일곱": 7, "칠": 7,
            "여덟": 8, "팔": 8,
            "아홉": 9, "구": 9,
            "열": 10, "십": 10,
        }
        m_ko = re.search(r"(한|하나|두|둘|세|셋|네|넷|다섯|여섯|일곱|여덟|아홉|열|일|이|삼|사|오|육|칠|팔|구|십)\s*(?:문제|개)", low)
        if m_ko:
            count = ko_counts[m_ko.group(1)]
        else:
            num = parse_korean_number(low)
            if num is not None and 1 <= num <= 10:
                count = num

    count = max(1, min(10, count))
    return topic, count


def question_from_dict(raw: dict[str, Any], idx: int = 0) -> QuizQuestion | None:
    """Validate and convert an LLM-generated question dict into a QuizQuestion."""
    try:
        en = str(raw.get("english_text") or "").strip()
        ko = str(raw.get("korean_meaning") or "").strip()
        hint = str(raw.get("hint_ko") or "").strip()
        expl = str(raw.get("explanation_ko") or "").strip()
        kind = str(raw.get("kind") or "story_quiz").strip().lower()
        cat = str(raw.get("category") or "daily").strip().lower()
        accepted_raw = raw.get("accepted_answers") or []
        accepted = tuple(str(x).strip() for x in accepted_raw if str(x).strip())
        if not en or not ko:
            return None
        if not accepted:
            accepted = (ko,)
        if kind not in ("story_quiz", "listen_and_reply", "word_meaning", "sentence_meaning"):
            kind = "story_quiz"
        if not hint:
            hint = "영어 문장 속 핵심 단서를 천천히 다시 들어보세요!"
        if not expl:
            expl = f"정답은 '{ko}'이에요!"
        return QuizQuestion(
            id=str(raw.get("id") or f"gen_{idx}_{random.randint(100, 999)}"),
            category=cat,
            kind=kind,
            english_text=en,
            korean_meaning=ko,
            accepted_answers=accepted,
            hint_ko=hint,
            explanation_ko=expl,
        )
    except Exception:
        return None


_EN_SPAN_RE = re.compile(
    r"['\"“”‘’]?([A-Za-z][A-Za-z0-9'\-]*(?:\s+[A-Za-z][A-Za-z0-9'\-]*)*)['\"“”‘’]?"
)


def split_bilingual_segments(text: str) -> list[tuple[str, str]]:
    """Split mixed Korean/English explanation text into ('ko', ...) and ('en', ...) segments
    so English words inside Korean explanations are spoken with native English pronunciation."""
    s = (text or "").strip()
    if not s:
        return []
    if not re.search(r"[A-Za-z]", s):
        return [("ko", s)]

    segments: list[tuple[str, str]] = []
    pos = 0
    for m in _EN_SPAN_RE.finditer(s):
        before = s[pos : m.start()].strip(" \t\r\n()[]\"'“”‘’")
        before = before.lstrip(",·→ ")
        before = before.rstrip("(")
        if before and re.search(r"[가-힣0-9]", before):
            segments.append(("ko", before))
        en_phrase = m.group(1).strip(" \t'\"“”‘’")
        if en_phrase:
            segments.append(("en", en_phrase))
        pos = m.end()

    tail = s[pos:].strip(" \t\r\n()[]\"'“”‘’")
    tail = tail.lstrip(",·→ ")
    if tail and re.search(r"[가-힣0-9]", tail):
        segments.append(("ko", tail))

    return segments or [("ko", s)]


def _prompt_segments_for_question(q: QuizQuestion, number: int, *, slow: bool = False) -> list[tuple[str, str]]:
    en_role = "en_slow" if slow else "en"
    if q.kind == "word_meaning":
        return [
            ("en", f"Question {number}."),
            ("pause", "0.25"),
            (en_role, q.english_text),
            ("pause", "0.45"),
            (en_role, q.english_text),
            ("pause", "0.25"),
            ("en", "What does it mean in Korean?"),
        ]
    if q.kind == "sentence_meaning":
        return [
            ("en", f"Question {number}."),
            ("pause", "0.25"),
            (en_role, q.english_text),
            ("pause", "0.3"),
            ("en", "What does it mean in Korean?"),
        ]
    # story_quiz & listen_and_reply: read 2-3 vivid English sentences ending in a question!
    return [
        ("en", f"Question {number}."),
        ("pause", "0.25"),
        (en_role, q.english_text),
    ]


def _display_prompt_text(q: QuizQuestion) -> str:
    if q.kind == "word_meaning":
        return "🎧 What does it mean in Korean?"
    if q.kind == "sentence_meaning":
        return "🎧 Listen to this sentence! What does it mean in Korean?"
    return "🎧 영어 이야기를 듣고 정답을 말해 보세요!"


def _praise_line(streak: int) -> str:
    if streak >= 4:
        return f"와우! {streak}문제 연속 정답이에요! 영어 듣기 실력이 정말 대단해요!"
    if streak == 3:
        return "우와! 3연속 정답이에요! 정말 멋져요!"
    if streak == 2:
        return "딩동댕! 2연속 정답이에요! 아주 잘 듣고 있어요!"
    return random.choice(
        (
            "딩동댕! 정답이에요! 정말 잘 들었어요!",
            "맞았어요! 영어를 아주 정확하게 이해했네요!",
            "정답이에요! 최고예요!",
        )
    )


def _final_encouragement(score: int, total: int) -> str:
    if total <= 0:
        return "다음에 또 같이 영어 퀴즈 해요!"
    ratio = score / total
    if ratio >= 1.0:
        return "모든 문제를 다 맞혔어요! 완벽한 영어 리스닝 챔피언이네요!"
    if ratio >= 0.8:
        return "정말 대단해요! 영어 문장 듣기 실력이 아주 훌륭해요!"
    if ratio >= 0.5:
        return "참 잘했어요! 조금만 더 연습하면 금방 만점 받을 거예요!"
    return "끝까지 열심히 들어주고 도전해서 정말 멋져요! 다음에 또 같이 연습해요!"



# ---------------------------------------------------------------------------
# Infinite Procedural English Listening Quiz Question Generator
# Generates endless 2~3 sentence listening stories, descriptions, math/clocks,
# and riddles without relying on a static question bank.
# ---------------------------------------------------------------------------

_ANIMALS_BLUEPRINTS: list[dict[str, Any]] = [
    {
        "key": "elephant",
        "en": "Elephant",
        "ko": "코끼리",
        "hint": "큰 귀와 긴 코를 가진 커다란 회색 동물이에요! 초성 힌트는 ㅋㄲㄹ!",
        "expl": "정답은 Elephant, 코끼리예요. 'long trunk'는 코끼리의 긴 코를 뜻해요.",
        "accepted": ("elephant", "엘리펀트", "코끼리"),
        "stories": [
            "I am a very large gray mammal with big ears and white tusks. I use my long trunk to spray water and pick up fresh grass to eat. What animal am I?",
            "I live in a family herd in the warm grasslands. I have a long, flexible trunk and giant ears that flap like fans to cool me down. What animal am I?",
            "I am the largest animal walking on land. People love watching me splash water over my back with my long nose. What animal am I?",
        ],
    },
    {
        "key": "lion",
        "en": "Lion",
        "ko": "사자",
        "hint": "밀림의 왕으로 불리며, 멋진 갈기를 가진 힘센 고양이과 동물이에요! 초성 힌트는 ㅅㅈ!",
        "expl": "정답은 Lion, 사자예요. 'thick mane'은 수사자의 풍성한 갈기예요.",
        "accepted": ("lion", "라이언", "사자"),
        "stories": [
            "I am known as the king of the jungle, and the male has a thick brown mane around his neck. I let out a mighty roar that echoes across the savanna. What animal am I?",
            "I am a powerful golden cat that lives in a family pride. I nap under the shade of acacia trees during the hot day and hunt under the stars. What animal am I?",
        ],
    },
    {
        "key": "tiger",
        "en": "Tiger",
        "ko": "호랑이",
        "hint": "주황색 털에 검은 줄무늬가 있고, 용감하고 수영을 즐기는 동물이에요! 초성 힌트는 ㅎㄹㅇ!",
        "expl": "정답은 Tiger, 호랑이예요. 'black stripes'는 검은 줄무늬예요.",
        "accepted": ("tiger", "타이거", "호랑이", "범"),
        "stories": [
            "I have fiery orange fur covered with bold black stripes. I am a solitary big cat that loves hunting in dense forests and swimming in rivers. What animal am I?",
            "My bold stripes help me hide in the tall grass. Unlike most house cats, I love playing and swimming in cool rivers. What animal am I?",
        ],
    },
    {
        "key": "giraffe",
        "en": "Giraffe",
        "ko": "기린",
        "hint": "육지에서 가장 키가 크고 아주 긴 목으로 나뭇잎을 먹는 동물이에요! 초성 힌트는 ㄱㄹ!",
        "expl": "정답은 Giraffe, 기린이에요. 'tallest land animal'은 가장 키가 큰 육지 동물이에요.",
        "accepted": ("giraffe", "지라프", "기린"),
        "stories": [
            "I am the tallest land animal on Earth with long legs and a patchy coat. My extremely long neck helps me reach sweet leaves at the treetops. What animal am I?",
            "I have brown spots all over my yellow body and a long purple tongue. I can see enemies from far away because I am so tall. What animal am I?",
        ],
    },
    {
        "key": "monkey",
        "en": "Monkey",
        "ko": "원숭이",
        "hint": "긴 팔과 꼬리로 나뭇가지를 타고 바나나를 좋아하는 동물이에요! 초성 힌트는 ㅇㅅㅇ!",
        "expl": "정답은 Monkey, 원숭이예요. 'swing from branch to branch'는 가지를 건너다닌다는 뜻이에요.",
        "accepted": ("monkey", "몽키", "원숭이"),
        "stories": [
            "I am a clever and playful creature with long arms and a flexible tail. I love to swing from branch to branch in the jungle and eat sweet bananas. What animal am I?",
            "I live high in the green forest trees with my noisy family. I groom my friends' fur and make silly faces while chattering. What animal am I?",
        ],
    },
    {
        "key": "penguin",
        "en": "Penguin",
        "ko": "펭귄",
        "hint": "하늘은 못 날지만 차가운 바다에서 수영을 잘하고 뒤뚱뒤뚱 걷는 새예요! 초성 힌트는 ㅍㄱ!",
        "expl": "정답은 Penguin, 펭귄이에요. 'waddle'은 뒤뚱뒤뚱 걷다는 뜻이에요.",
        "accepted": ("penguin", "펭귄", "팽귄"),
        "stories": [
            "I am a flightless bird wearing a natural black and white tuxedo. I waddle across the Antarctic ice and dive into freezing water to catch fish. What animal am I?",
            "I have wings shaped like flippers that make me an incredible swimmer. I slide on my belly across the snow and stay warm in giant huddles. What animal am I?",
        ],
    },
    {
        "key": "dolphin",
        "en": "Dolphin",
        "ko": "돌고래",
        "hint": "바다에서 뛰어오르고 소리로 대화하는 똑똑한 바다 동물이에요! 초성 힌트는 ㄷㄱㄹ!",
        "expl": "정답은 Dolphin, 돌고래예요. 'blowhole'은 머리 위의 숨구멍이에요.",
        "accepted": ("dolphin", "돌핀", "돌고래", "고래"),
        "stories": [
            "I am a friendly and intelligent marine mammal that breathes air through a blowhole. I jump gracefully through ocean waves and communicate with clicks and whistles. What animal am I?",
            "I swim swiftly beside big boats and love playing with waves. I live with my family pod and help guide my friends through the deep blue sea. What animal am I?",
        ],
    },
    {
        "key": "whale",
        "en": "Whale",
        "ko": "고래",
        "hint": "바다에서 가장 거대하고 등에서 물을 뿜어 올리는 동물이에요! 초성 힌트는 ㄱㄹ!",
        "expl": "정답은 Whale, 고래예요. 'vast blue ocean'은 광활한 푸른 바다예요.",
        "accepted": ("whale", "웨일", "고래"),
        "stories": [
            "I am the biggest creature living in the vast blue ocean. I sing mysterious songs across thousands of miles and shoot water high into the sky. What animal am I?",
            "Even though I am enormous, I am very gentle. I take deep breaths at the ocean surface and dive into the deep dark waters. What animal am I?",
        ],
    },
    {
        "key": "kangaroo",
        "en": "Kangaroo",
        "ko": "캥거루",
        "hint": "배에 아기 주머니가 있고 튼튼한 다리로 깡충깡충 뛰는 호주 동물이에요! 초성 힌트는 ㅋㄱㄹ!",
        "expl": "정답은 Kangaroo, 캥거루예요. 'pouch'는 아기 주머니예요.",
        "accepted": ("kangaroo", "캥거루"),
        "stories": [
            "I have powerful hind legs and a muscular tail to help me balance. Mother carries her tiny joey inside a warm pouch on her belly. What animal am I?",
            "I hop across the Australian plains faster than a person can run. My big feet push me high into the air with every giant leap. What animal am I?",
        ],
    },
    {
        "key": "panda",
        "en": "Panda",
        "ko": "판다",
        "hint": "검은색과 흰색 털을 가졌고 대나무를 맛있게 먹는 동물이에요! 초성 힌트는 ㅍㄷ!",
        "expl": "정답은 Panda, 판다예요. 'bamboo'는 대나무를 뜻해요.",
        "accepted": ("panda", "팬더", "판다"),
        "stories": [
            "I am a chubby bear with distinct black and white fur patches. I live in misty mountain bamboo forests and spend hours munching on green bamboo stalks. What animal am I?",
            "I have black circles around my eyes that make me look like I am wearing glasses. I love climbing trees and rolling around in the green grass. What animal am I?",
        ],
    },
    {
        "key": "rabbit",
        "en": "Rabbit",
        "ko": "토끼",
        "hint": "긴 귀와 몽실한 꼬리를 가졌고 깡충깡충 뛰며 당근을 좋아하는 동물이에요! 초성 힌트는 ㅌㄲ!",
        "expl": "정답은 Rabbit, 토끼예요. 'twitching ears'는 쫑긋거리는 귀예요.",
        "accepted": ("rabbit", "bunny", "래빗", "토끼", "버니"),
        "stories": [
            "I have long twitching ears, a soft fluffy tail, and strong back feet. I love hopping across grassy meadows and nibbling on fresh carrots. What animal am I?",
            "I dig cozy burrows underground to sleep safely. When I get excited, I jump straight into the air and twist my body. What animal am I?",
        ],
    },
    {
        "key": "koala",
        "en": "Koala",
        "ko": "코알라",
        "hint": "호주 나무에 매달려 하루 종일 잠을 자고 유칼립투스 잎을 먹는 동물이에요! 초성 힌트는 ㅋㅇㄹ!",
        "expl": "정답은 Koala, 코알라예요. 'eucalyptus'는 유칼립투스 나뭇잎이에요.",
        "accepted": ("koala", "코알라"),
        "stories": [
            "I am a cute marsupial that lives high in the trees of Australia. I spend nearly twenty hours a day sleeping and wake up to munch on eucalyptus leaves. What animal am I?",
            "I hug tree trunks tightly with my sharp curved claws. I have large round fuzzy ears and a dark leathery nose. What animal am I?",
        ],
    },
    {
        "key": "zebra",
        "en": "Zebra",
        "ko": "얼룩말",
        "hint": "온몸에 흑백 줄무늬가 있는 아프리카 초원의 동물이에요! 초성 힌트는 ㅇㄹㅁ!",
        "expl": "정답은 Zebra, 얼룩말이에요. 'black and white stripes'는 흑백 줄무늬예요.",
        "accepted": ("zebra", "지브라", "얼룩말"),
        "stories": [
            "I look like a wild horse, but my entire body is painted with black and white stripes. I run in large herds across the African grasslands to stay safe from predators. What animal am I?",
            "No two animals in my family have the exact same striped pattern. I gallop together across the open plains under the sun. What animal am I?",
        ],
    },
    {
        "key": "bear",
        "en": "Bear",
        "ko": "곰",
        "hint": "두꺼운 털과 날카로운 발톱을 가졌고 겨울잠을 자는 큰 동물이에요! 초성 힌트는 ㄱ!",
        "expl": "정답은 Bear, 곰이에요. 'sharp claws'는 날카로운 발톱이에요.",
        "accepted": ("bear", "베어", "곰"),
        "stories": [
            "I am a large animal with thick warm fur and sharp claws. In the autumn I eat berries and catch salmon in rivers before sleeping all through the cold winter. What animal am I?",
            "I stand tall on my hind legs to look over tall bushes. I love sweet forest honey and swimming in cold mountain streams. What animal am I?",
        ],
    },
    {
        "key": "owl",
        "en": "Owl",
        "ko": "부엉이 / 올빼미",
        "hint": "낮에는 자고 밤에 사냥하며, 고개를 홱 돌릴 수 있는 밤의 새예요! 초성 힌트는 ㅂㅇㅇ!",
        "expl": "정답은 Owl, 부엉이(올빼미)예요. 'turn my head'는 고개를 돌린다는 뜻이에요.",
        "accepted": ("owl", "아울", "부엉이", "올빼미"),
        "stories": [
            "I sleep silently during the day and hunt for mice in the dark night. I can turn my head almost all the way around and make a soft hooting sound. What animal am I?",
            "I have huge round eyes that see clearly in total darkness. My soft feathers let me fly completely silently through the night air. What bird am I?",
        ],
    },
    {
        "key": "turtle",
        "en": "Turtle",
        "ko": "거북이",
        "hint": "단단한 등껍질을 메고 천천히 걸으며 위험할 때 숨는 동물이에요! 초성 힌트는 ㄱㅂㅇ!",
        "expl": "정답은 Turtle, 거북이예요. 'protective shell'은 단단한 등껍질이에요.",
        "accepted": ("turtle", "tortoise", "터틀", "거북이", "거북"),
        "stories": [
            "I carry a hard protective shell on my back and move slowly across the ground. When danger approaches, I pull my head and legs safely inside my shell. What animal am I?",
            "Some of my cousins swim thousands of miles in the ocean with flippers, while I walk peacefully on dry land. I can live for more than a hundred years. What animal am I?",
        ],
    },
    {
        "key": "frog",
        "en": "Frog",
        "ko": "개구리",
        "hint": "올챙이에서 자라나 긴 다리로 점프하고 연못에 사는 동물이에요! 초성 힌트는 ㄱㄱㄹ!",
        "expl": "정답은 Frog, 개구리예요. 'tadpole'은 올챙이를 뜻해요.",
        "accepted": ("frog", "toad", "프로그", "개구리"),
        "stories": [
            "I begin my life as a tiny swimming tadpole in a freshwater pond. When I grow up, I leap around with long legs and catch flies with my sticky tongue. What animal am I?",
            "I sit peacefully on a green lily pad in the summer pond. When the rain pours down, I puff up my throat and sing ribbit ribbit. What animal am I?",
        ],
    },
    {
        "key": "dog",
        "en": "Dog",
        "ko": "강아지 / 개",
        "hint": "사람의 가장 친한 친구로, 꼬리를 흔들고 멍멍 짖는 동물이에요! 초성 힌트는 ㄱㅇㅈ!",
        "expl": "정답은 Dog, 강아지(개)예요. 'wag tail'은 꼬리를 흔든다는 뜻이에요.",
        "accepted": ("dog", "puppy", "도그", "퍼피", "개", "강아지", "댕댕이"),
        "stories": [
            "People call me human's best friend because I am loyal, playful, and loving. I wag my tail happily when you come home and bark when someone knocks on the door. What animal am I?",
            "I love playing fetch in the park and chasing tennis balls across the grass. When I want a treat, I sit nicely and shake hands with my paw. What animal am I?",
        ],
    },
    {
        "key": "cat",
        "en": "Cat",
        "ko": "고양이",
        "hint": "부드러운 발바닥과 수염을 가졌고 기분 좋을 때 골골송을 부르는 동물이에요! 초성 힌트는 ㄱㅇㅇ!",
        "expl": "정답은 Cat, 고양이예요. 'purr'는 기분 좋을 때 나는 골골 소리예요.",
        "accepted": ("cat", "kitten", "kitty", "캣", "고양이", "야옹이"),
        "stories": [
            "I have soft paws with retractable claws and long whiskers on my cheeks. I love curling up in warm sunshine and purr softly when you pet my fur. What animal am I?",
            "I can leap silently from the floor to the top of tall bookshelves. When I am hungry, I rub gently against your leg and meow softly. What animal am I?",
        ],
    },
    {
        "key": "horse",
        "en": "Horse",
        "ko": "말",
        "hint": "멋진 갈기와 단단한 말굽으로 초원을 빠르게 달리는 동물이에요! 초성 힌트는 ㅁ!",
        "expl": "정답은 Horse, 말이에요. 'gallop'은 말이 질주한다는 뜻이에요.",
        "accepted": ("horse", "호스", "말"),
        "stories": [
            "I have a flowing mane, a long tail, and hard hooves on my feet. People ride on my back as I gallop quickly across open green pastures. What animal am I?",
            "I sleep standing up on my four strong legs and eat crisp apples and hay. I make a loud neighing sound to call my herd. What animal am I?",
        ],
    },
    {
        "key": "cow",
        "en": "Cow",
        "ko": "소 / 젖소",
        "hint": "목장에서 풀을 뜯으며 음메 소리를 내고 신선한 우유를 주는 동물이에요! 초성 힌트는 ㅅ!",
        "expl": "정답은 Cow, 젖소(소)예요. 'graze'는 풀을 뜯는다는 뜻이에요.",
        "accepted": ("cow", "카우", "소", "젖소", "황소"),
        "stories": [
            "I graze peacefully on green clover in a farm pasture. I chew my cud, make a gentle moo sound, and give fresh white milk for people to drink. What animal am I?",
            "I have a large gentle body, two ears with yellow tags, and a swishing tail. Farmers take care of me in the red barn every morning. What animal am I?",
        ],
    },
    {
        "key": "bee",
        "en": "Bee",
        "ko": "꿀벌 / 벌",
        "hint": "꽃 사이를 날아다니며 꿀을 모으고 붕붕 소리를 내는 곤충이에요! 초성 힌트는 ㅂ!",
        "expl": "정답은 Bee, 꿀벌(벌)이에요. 'sweet nectar'는 달콤한 꽃꿀이에요.",
        "accepted": ("bee", "honeybee", "비", "벌", "꿀벌"),
        "stories": [
            "I buzz from blossom to blossom with tiny wings to collect pollen and sweet nectar. I live in a hive with my queen and make golden honey. What insect am I?",
            "I do a special waggle dance to tell my sisters where the best flowers are blooming. My body has yellow and black stripes and I can sting if frightened. What insect am I?",
        ],
    },
    {
        "key": "butterfly",
        "en": "Butterfly",
        "ko": "나비",
        "hint": "애벌레에서 번데기를 거쳐 알록달록한 날개로 꽃밭을 날아다니는 곤충이에요! 초성 힌트는 ㄴㅂ!",
        "expl": "정답은 Butterfly, 나비예요. 'flutter'는 팔랑팔랑 난다는 뜻이에요.",
        "accepted": ("butterfly", "버터플라이", "나비", "호랑나비"),
        "stories": [
            "I start my life as a crawling caterpillar that eats green leaves. Later I emerge from a cozy chrysalis with dazzling colorful wings to dance in the garden. What insect am I?",
            "I flutter gently over garden flowers on warm sunny afternoons. I taste sweet nectar using my feet and drink with a curly straw-like tongue. What insect am I?",
        ],
    },
    {
        "key": "shark",
        "en": "Shark",
        "ko": "상어",
        "hint": "뾰족한 등지느러미와 날카로운 이빨로 바다를 헤엄치는 사냥꾼이에요! 초성 힌트는 ㅅㅇ!",
        "expl": "정답은 Shark, 상어예요. 'dorsal fin'은 뾰족한 등지느러미예요.",
        "accepted": ("shark", "샤크", "상어"),
        "stories": [
            "I have rows of triangular sharp teeth and a pointed dorsal fin that cuts through the ocean surface. I am a top predator swimming swiftly through deep water. What animal am I?",
            "My skeleton is made entirely of flexible cartilage instead of heavy bone. I have an amazing sense of smell that detects things in the ocean from miles away. What animal am I?",
        ],
    },
    {
        "key": "octopus",
        "en": "Octopus",
        "ko": "문어",
        "hint": "다리가 8개 있고 먹물을 뿜으며 몸 색깔을 바꿀 수 있는 바다 생물이에요! 초성 힌트는 ㅁㅇ!",
        "expl": "정답은 Octopus, 문어예요. 'eight arms'는 8개의 다리(팔)예요.",
        "accepted": ("octopus", "옥토퍼스", "문어"),
        "stories": [
            "I am a clever sea creature with three hearts, blue blood, and eight flexible arms covered in suckers. I squirt dark ink to escape when an enemy comes near. What sea creature am I?",
            "I can change my skin color and texture in less than a second to match coral rocks. I have no bones at all, so I can squeeze through tiny openings. What sea creature am I?",
        ],
    },
]

_FOOD_BLUEPRINTS: list[dict[str, Any]] = [
    {
        "key": "watermelon",
        "en": "Watermelon",
        "ko": "수박",
        "hint": "겉은 초록색 줄무늬, 속은 빨갛고 시원한 여름 대표 과일이에요! 초성 힌트는 ㅅㅂ!",
        "expl": "정답은 Watermelon, 수박이에요. 'thick green rind'는 두꺼운 초록 껍질이에요.",
        "accepted": ("watermelon", "워터멜론", "수박"),
        "stories": [
            "I am a heavy round fruit with dark green stripes on my thick rind. When you slice me open on a hot summer afternoon, I am red, sweet, and full of refreshing juice. What fruit am I?",
            "People love bringing me to beach picnics and summer barbecues. I have lots of tiny black seeds inside my crisp red fruit. What fruit am I?",
        ],
    },
    {
        "key": "banana",
        "en": "Banana",
        "ko": "바나나",
        "hint": "노란 껍질을 벗겨 먹는 길쭉한 과일이고 원숭이가 좋아해요! 초성 힌트는 ㅂㄴㄴ!",
        "expl": "정답은 Banana, 바나나예요. 'yellow peel'은 노란 껍질이에요.",
        "accepted": ("banana", "바나나"),
        "stories": [
            "I grow in large bunches in tropical sunshine and start out bright green. When I am ripe and sweet, my skin turns yellow and you peel it off to eat me. What fruit am I?",
            "I am a curved yellow fruit that is soft and delicious to bite. Monkeys love eating me high in the trees, and children pack me in lunchboxes. What fruit am I?",
        ],
    },
    {
        "key": "apple",
        "en": "Apple",
        "ko": "사과",
        "hint": "빨갛고 아삭아삭한 과일로, 백설공주 이야기에도 나와요! 초성 힌트는 ㅅㄱ!",
        "expl": "정답은 Apple, 사과예요. 'crunchy'는 아삭아삭하다는 뜻이에요.",
        "accepted": ("apple", "애플", "사과"),
        "stories": [
            "I grow on orchard trees and turn bright red, green, or yellow in the autumn. I make a satisfying crunch when you take a bite, and doctors say eating one a day keeps them away. What fruit am I?",
            "People bake me into warm pies with cinnamon or press me into sweet cider. I have a brown stem on top and tiny seeds in my core. What fruit am I?",
        ],
    },
    {
        "key": "strawberry",
        "en": "Strawberry",
        "ko": "딸기",
        "hint": "하트 모양의 빨간 과일로, 겉에 작은 씨가 콕콕 박혀 있어요! 초성 힌트는 ㄸㄱ!",
        "expl": "정답은 Strawberry, 딸기예요. 'heart-shaped'는 하트 모양이에요.",
        "accepted": ("strawberry", "스트로베리", "딸기"),
        "stories": [
            "I am a heart-shaped bright red berry wearing a tiny green leafy cap. My outside is dotted with hundreds of tiny seeds, and I taste sweet on birthday cakes. What fruit am I?",
            "Children love picking me in warm greenhouses during the spring. I am juicy, sweet, and make delicious pink ice cream and smoothies. What fruit am I?",
        ],
    },
    {
        "key": "carrot",
        "en": "Carrot",
        "ko": "당근",
        "hint": "땅속에서 자라는 주황색 채소로 토끼가 아주 좋아해요! 초성 힌트는 ㄷㄱ!",
        "expl": "정답은 Carrot, 당근이에요. 'grow underground'는 땅속에서 자란다는 뜻이에요.",
        "accepted": ("carrot", "캐럿", "당근"),
        "stories": [
            "I am a crunchy orange root vegetable that grows hidden underground in the garden soil. Rabbits are famous for nibbling on me, and I help keep your eyesight sharp. What vegetable am I?",
            "You can chop me into vegetable soup or use me to make a nose for a snowy winter snowman. I have bushy green leaves on top and a bright orange body. What vegetable am I?",
        ],
    },
    {
        "key": "pizza",
        "en": "Pizza",
        "ko": "피자",
        "hint": "토마토 소스와 치즈를 올려 구운 삼각형 조각 음식이에요! 초성 힌트는 ㅍㅈ!",
        "expl": "정답은 Pizza, 피자예요. 'melted cheese'는 녹아내린 치즈예요.",
        "accepted": ("pizza", "피자"),
        "stories": [
            "I am baked in a blistering hot stone oven until my crust is crispy. I am topped with savory tomato sauce, melted stretchy cheese, and cut into triangular slices. What food am I?",
            "Families love ordering me on Friday movie nights in a big square cardboard box. When you pull a warm slice away, the mozzarella cheese stretches out. What food am I?",
        ],
    },
    {
        "key": "ice_cream",
        "en": "Ice cream",
        "ko": "아이스크림",
        "hint": "콘이나 컵에 담아 먹는 차갑고 달콤한 디저트로 햇볕에 녹아요! 초성 힌트는 ㅇㅇㅅㅋㄹ!",
        "expl": "정답은 Ice cream, 아이스크림이에요. 'melt'는 녹는다는 뜻이에요.",
        "accepted": ("ice cream", "icecream", "아이스크림"),
        "stories": [
            "I am a freezing, creamy dessert that comes in delicious flavors like chocolate, vanilla, and strawberry. If you don't eat me quickly on a sunny day, I melt into a sweet puddle. What food am I?",
            "People enjoy licking me off a crispy waffle cone at the park. I am sweet, chilly, and make everyone smile on hot summer afternoons. What treat am I?",
        ],
    },
    {
        "key": "egg",
        "en": "Egg",
        "ko": "달걀 / 계란",
        "hint": "단단한 껍질을 톡 깨면 노란 노른자가 나오는 아침 식사 단골 음식이에요! 초성 힌트는 ㄷㄱ!",
        "expl": "정답은 Egg, 달걀(계란)이에요. 'crack open'은 톡 깨서 연다는 뜻이에요.",
        "accepted": ("egg", "eggs", "에그", "달걀", "계란"),
        "stories": [
            "I have an oval shell that protects me until you crack me into a frying pan. Inside, I have clear egg whites and a bright golden yellow yolk. What food am I?",
            "People boil, fry, or scramble me for a nutritious breakfast every morning. Hens lay me in soft nests inside the farm coop. What food am I?",
        ],
    },
    {
        "key": "honey",
        "en": "Honey",
        "ko": "꿀",
        "hint": "꿀벌이 꽃에서 모은 달콤하고 끈적한 황금빛 음식이에요! 초성 힌트는 ㄲ!",
        "expl": "정답은 Honey, 꿀이에요. 'sticky'는 끈적끈적하다는 뜻이에요.",
        "accepted": ("honey", "허니", "꿀", "벌꿀"),
        "stories": [
            "Busy bees collect flower nectar all day long to craft me inside their hive. I am thick, golden, and sticky, and people drizzle me over warm toast or pancakes. What food am I?",
            "I never spoil even after sitting in a glass jar for years. Bears love finding me in hollow trees, and people stir me into warm tea when they have a cold. What food am I?",
        ],
    },
    {
        "key": "milk",
        "en": "Milk",
        "ko": "우유",
        "hint": "하얗고 고소한 음료로, 젖소에게서 얻고 시리얼에 부어 먹어요! 초성 힌트는 ㅇㅇ!",
        "expl": "정답은 Milk, 우유예요. 'calcium'은 뼈를 튼튼하게 하는 칼슘이에요.",
        "accepted": ("milk", "밀크", "우유"),
        "stories": [
            "I am a nutritious white liquid full of calcium that helps children build strong bones and teeth. People pour me over crunchy breakfast cereal every morning. What drink am I?",
            "You keep me cold in the refrigerator door in a carton or glass bottle. Friendly dairy cows produce me on green pastures. What drink am I?",
        ],
    },
    {
        "key": "chocolate",
        "en": "Chocolate",
        "ko": "초콜릿",
        "hint": "카카오로 만든 달콤한 갈색 간식으로 입안에서 사르르 녹아요! 초성 힌트는 ㅊㅋㄹ!",
        "expl": "정답은 Chocolate, 초콜릿이에요. 'cocoa'는 카카오를 뜻해요.",
        "accepted": ("chocolate", "초콜릿", "초콜렛", "초코"),
        "stories": [
            "I am made from roasted cacao beans harvested in tropical lands. I am a rich brown treat that melts smoothly on your tongue, and people gift me on Valentine's Day. What treat am I?",
            "You can bake me into cookies, break me into sweet squares, or drink me hot with marshmallows on a winter evening. What sweet food am I?",
        ],
    },
    {
        "key": "bread",
        "en": "Bread",
        "ko": "빵",
        "hint": "밀가루 반죽을 오븐에 구워 만들고 샌드위치를 만들 때 쓰는 음식이에요! 초성 힌트는 ㅃ!",
        "expl": "정답은 Bread, 빵이에요. 'bakery'는 빵집이에요.",
        "accepted": ("bread", "브레드", "빵"),
        "stories": [
            "Bakers mix flour, water, and yeast together and let the dough rise before sliding it into the oven. The whole bakery smells wonderful when fresh loaves come out with a golden crust. What food am I?",
            "You slice two pieces of me to make a tasty ham and cheese sandwich for lunch. You can also pop me in a toaster until I am warm and crunchy. What food am I?",
        ],
    },
]

_FAMILY_SCHOOL_BLUEPRINTS: list[dict[str, Any]] = [
    {
        "key": "teacher",
        "en": "Teacher",
        "ko": "선생님 / 교사",
        "hint": "교실에서 학생들에게 새로운 지식을 가르쳐 주시는 분이에요! 초성 힌트는 ㅅㅅㄴ!",
        "expl": "정답은 Teacher, 선생님이에요. 'classroom'은 교실이에요.",
        "accepted": ("teacher", "티처", "선생님", "선생", "교사"),
        "stories": [
            "I write lessons on the classroom whiteboard and hand out worksheets to curious students. I answer tricky questions and help children read, write, and learn every day. What is my job?",
            "I greet my students with a smile at the classroom door every morning. I read storybooks aloud, organize fun science experiments, and grade homework. What is my job?",
        ],
    },
    {
        "key": "doctor",
        "en": "Doctor",
        "ko": "의사",
        "hint": "병원에서 흰 가운을 입고 청진기로 환자를 진찰해 주는 분이에요! 초성 힌트는 ㅇㅅ!",
        "expl": "정답은 Doctor, 의사예요. 'stethoscope'는 청진기예요.",
        "accepted": ("doctor", "닥터", "의사", "의사선생님", "의사 선생님"),
        "stories": [
            "I wear a clean white lab coat and carry a stethoscope around my neck at the hospital. When you feel sick or have a fever, I examine your throat and prescribe medicine to help you heal. What is my job?",
            "I listen to your heartbeat thump thump thump through my metal stethoscope. I help people stay healthy, recover from colds, and heal broken bones. What is my job?",
        ],
    },
    {
        "key": "firefighter",
        "en": "Firefighter",
        "ko": "소방관",
        "hint": "빨간 소방차를 타고 출동해 불을 끄고 사람들을 구하는 용감한 분이에요! 초성 힌트는 ㅅㅂㄱ!",
        "expl": "정답은 Firefighter, 소방관이에요. 'put out flames'는 불길을 끈다는 뜻이에요.",
        "accepted": ("firefighter", "fireman", "파이어파이터", "소방관", "소방수"),
        "stories": [
            "When the loud emergency bell sounds at the station, I slide down the pole and put on my heavy yellow helmet. I ride a bright red truck and spray powerful water hoses to put out dangerous fires. What is my job?",
            "I am brave and strong, always ready to rescue people and pets from burning buildings. I drive through the city with sirens blaring to help anyone in danger. What is my job?",
        ],
    },
    {
        "key": "police_officer",
        "en": "Police officer",
        "ko": "경찰관 / 경찰",
        "hint": "순찰차를 타고 동네를 지키며 사람들을 안전하게 보호해 주는 분이에요! 초성 힌트는 ㄱㅊㄱ!",
        "expl": "정답은 Police officer, 경찰관이에요. 'keep the neighborhood safe'는 동네를 안전하게 지킨다는 뜻이에요.",
        "accepted": ("police officer", "police", "cop", "폴리스", "경찰", "경찰관"),
        "stories": [
            "I wear a neat blue uniform, a shiny metal badge, and patrol city streets in a black and white patrol car. I direct traffic when signals go out and keep our neighborhood peaceful and safe. What is my job?",
            "If someone gets lost in a crowded park, they come to me for help. I help children cross busy crosswalks safely and protect everyone in the community. What is my job?",
        ],
    },
    {
        "key": "chef",
        "en": "Chef / Cook",
        "ko": "요리사 / 셰프",
        "hint": "주방에서 칼과 프라이팬으로 맛있는 음식을 요리하는 분이에요! 초성 힌트는 ㅇㄹㅅ!",
        "expl": "정답은 Chef, 요리사예요. 'tall white hat'은 셰프의 높은 모자예요.",
        "accepted": ("chef", "cook", "셰프", "쉐프", "요리사"),
        "stories": [
            "I wear a tall white hat and work in a bustling restaurant kitchen filled with sizzles and steam. I chop fresh vegetables, season savory sauces, and cook gourmet meals for hungry guests. What is my job?",
            "I taste my simmering soups with a silver spoon and flip hot pancakes in heavy pans. Guests come to my dining room to celebrate special dinners with delicious food. What is my job?",
        ],
    },
    {
        "key": "library",
        "en": "Library",
        "ko": "도서관",
        "hint": "수많은 책이 꽂혀 있고 조용히 책을 읽거나 빌려 갈 수 있는 곳이에요! 초성 힌트는 ㄷㅅㄱ!",
        "expl": "정답은 Library, 도서관이에요. 'borrow books'는 책을 대출한다는 뜻이에요.",
        "accepted": ("library", "라이브러리", "도서관", "도서실"),
        "stories": [
            "This quiet building is packed with thousands of books on tall wooden shelves from floor to ceiling. You can sit peacefully at desks to study and use your card to borrow exciting novels for free. What place is this?",
            "Everyone whispers softly inside this peaceful room so other people can concentrate on reading. You can find mystery stories, encyclopedias, and fairy tales here. What place is this?",
        ],
    },
    {
        "key": "hospital",
        "en": "Hospital",
        "ko": "병원",
        "hint": "의사와 간호사가 있고 아픈 사람들을 치료해 주는 곳이에요! 초성 힌트는 ㅂㅇ!",
        "expl": "정답은 Hospital, 병원이에요. 'ambulance'는 구급차예요.",
        "accepted": ("hospital", "clinic", "호스피털", "병원"),
        "stories": [
            "Ambulances with flashing red lights bring injured people to this large medical building. Skilled doctors and kind nurses look after patients in comfortable beds until they feel better. What place is this?",
            "You visit this clean place when you have a high fever or need a regular checkup. You can find x-ray machines, medicine, and warm care here. What place is this?",
        ],
    },
    {
        "key": "airport",
        "en": "Airport",
        "ko": "공항",
        "hint": "여권을 챙기고 큰 비행기를 타러 가는 곳이에요! 초성 힌트는 ㄱㅎ!",
        "expl": "정답은 Airport, 공항이에요. 'board an airplane'은 비행기에 탑승한다는 뜻이에요.",
        "accepted": ("airport", "에어포트", "공항"),
        "stories": [
            "Travelers wheel their heavy suitcases through this busy terminal with their passports in hand. Giant airplanes land and take off on long concrete runways outside the windows. What place is this?",
            "You check your flight departure board, walk to your gate, and walk down a ramp to board a big plane flying overseas. What place is this?",
        ],
    },
    {
        "key": "pencil",
        "en": "Pencil",
        "ko": "연필",
        "hint": "나무 속에 흑연이 들어 있고 지우개로 지울 수 있는 필기도구예요! 초성 힌트는 ㅇㅍ!",
        "expl": "정답은 Pencil, 연필이에요. 'lead'는 연필심이에요.",
        "accepted": ("pencil", "펜슬", "연필"),
        "stories": [
            "I am made of cedar wood with dark graphite lead running through my center. Students sharpen my tip to write spelling words in notebooks, and I often have a pink rubber eraser on top. What am I?",
            "When I get dull from writing lots of math problems, you twist me inside a pencil sharpener. You can erase my marks easily if you make a mistake. What am I?",
        ],
    },
]

_DAILY_BLUEPRINTS: list[dict[str, Any]] = [
    {
        "key": "umbrella",
        "en": "Umbrella",
        "ko": "우산",
        "hint": "비가 올 때 머리 위에 펼쳐서 옷이 젖지 않게 해 주는 물건이에요! 초성 힌트는 ㅇㅅ!",
        "expl": "정답은 Umbrella, 우산이에요. 'stay dry'는 젖지 않고 마른 상태를 유지한다는 뜻이에요.",
        "accepted": ("umbrella", "엄브렐라", "우산"),
        "stories": [
            "Dark gray rainclouds gathered over the rooftops and heavy raindrops started tapping on the window. Before walking out the front door, Minho opened a colorful canopy over his head to stay dry. What did Minho use?",
            "You press a small silver button on my handle and whoosh, I pop open like a mushroom shield. I protect your clothes and hair from stormy raindrops. What am I?",
        ],
    },
    {
        "key": "sleep",
        "en": "Sleep / Go to bed",
        "ko": "잠자기 / 잠",
        "hint": "양치하고 잠옷을 입고 불을 끈 다음 이불 속에서 하는 행동이에요! 초성 힌트는 ㅈ!",
        "expl": "정답은 Sleep, 잠자기예요. 'cozy blanket'은 포근한 이불이에요.",
        "accepted": ("sleep", "go to sleep", "go to bed", "sleeping", "잠", "잠자기", "자다", "자요"),
        "stories": [
            "Sarah finished her bedtime story at nine o'clock and brushed her teeth with minty paste. She pulled up her cozy quilt, turned off the bedside lamp, and closed her heavy eyelids. What is Sarah going to do?",
            "After a long exciting day at school, Leo felt very drowsy. He nestled into his soft pillow and shut his eyes to rest until morning sunshine arrived. What is Leo going to do?",
        ],
    },
    {
        "key": "birthday",
        "en": "Birthday",
        "ko": "생일",
        "hint": "촛불을 켠 케이크와 함께 축하 노래를 부르고 소원을 비는 특별한 날이에요! 초성 힌트는 ㅅㅇ!",
        "expl": "정답은 Birthday, 생일이에요. 'make a wish'는 소원을 빌다는 멋진 표현이에요.",
        "accepted": ("birthday", "벌스데이", "생일", "생일파티"),
        "stories": [
            "Today is Chloe's favorite celebration of the whole year. Her friends gathered around a sweet strawberry cake with glowing candles and sang a cheerful song before she blew them out and made a wish. What special day is it?",
            "You receive colorful wrapped gift boxes, wear a paper party cone hat, and eat sweet cake with family on the day you were born. What celebration is this?",
        ],
    },
    {
        "key": "snowman",
        "en": "Snowman",
        "ko": "눈사람",
        "hint": "하얀 눈을 동그랗게 굴려 쌓고 당근으로 코를 만든 겨울 친구예요! 초성 힌트는 ㄴㅅㄹ!",
        "expl": "정답은 Snowman, 눈사람이에요. 'roll snowballs'는 눈덩이를 굴린다는 뜻이에요.",
        "accepted": ("snowman", "스노우맨", "스노맨", "눈사람"),
        "stories": [
            "After a massive winter blizzard covered the front yard in deep white powder, Yuna put on her thick mittens. She rolled two gigantic spheres of snow, stacked them up, and gave it an orange carrot nose and pebble eyes. What did Yuna build?",
            "I stand still in the freezing cold yard with a wool scarf around my neck and tree twig arms. I smile at the chilly snowflakes and melt away when spring arrives. What am I?",
        ],
    },
    {
        "key": "beach",
        "en": "Beach / Ocean",
        "ko": "해변 / 바다",
        "hint": "수영복과 선글라스를 챙겨 따뜻한 모래와 파도를 즐기러 가는 곳이에요! 초성 힌트는 ㅎㅂ!",
        "expl": "정답은 Beach, 바닷가(해변)예요. 'sandy shore'는 모래사장이에요.",
        "accepted": ("beach", "ocean", "sea", "비치", "해변", "바닷가", "바다"),
        "stories": [
            "On a sizzling hot July morning, the family packed towels, flip-flops, and sunscreen in the trunk. When they arrived, they ran barefoot on soft warm sand and splashed in the salty ocean waves. Where did they go?",
            "You can build sandcastles with plastic buckets and collect shiny seashells along the shoreline while seagulls call out overhead. Where is this place?",
        ],
    },
    {
        "key": "hungry",
        "en": "Hungry",
        "ko": "배고픔 / 배고파요",
        "hint": "신나게 운동하고 나서 배에서 꼬르륵 소리가 날 때 느끼는 기분이에요! 초성 힌트는 ㅂㄱㅍ!",
        "expl": "정답은 Hungry, 배고파요예요. 'rumble'은 배에서 꼬르륵 소리가 나는 거예요.",
        "accepted": ("hungry", "헝그리", "배고파", "배고파요", "배고픔", "배고프다"),
        "stories": [
            "Lucas played tag in the soccer field all afternoon without having any lunch. Suddenly his stomach made a loud rumbling growl and he thought of a giant bowl of pasta. How does Lucas feel right now?",
            "Your tummy feels completely empty after a long hike in the woods. You cannot wait for dinner time to arrive so you can eat delicious food. How do you feel?",
        ],
    },
]

_RIDDLES_BLUEPRINTS: list[dict[str, Any]] = [
    {
        "key": "clock",
        "en": "Clock / Watch",
        "ko": "시계",
        "hint": "1부터 12까지 숫자가 적혀 있고 바늘이 돌며 시간을 알려주는 물건이에요! 초성 힌트는 ㅅㄱ!",
        "expl": "정답은 Clock, 시계예요. 'ticking hands'는 째깍거리는 시계 바늘이에요.",
        "accepted": ("clock", "watch", "클락", "시계", "손목시계", "벽시계"),
        "stories": [
            "I hang high on the classroom wall with numbers from one to twelve arranged in a circle. My two slender hands never stop moving round and round so you always know when recess starts. What am I?",
            "I make a gentle ticking sound second after second. You look at me to check if you are early or running late for the school bus. What am I?",
        ],
    },
    {
        "key": "mirror",
        "en": "Mirror",
        "ko": "거울",
        "hint": "얼굴을 비춰보고 내가 웃으면 똑같이 웃어주는 유리 물건이에요! 초성 힌트는 ㄱㅇ!",
        "expl": "정답은 Mirror, 거울이에요. 'reflection'은 거울에 비친 모습이에요.",
        "accepted": ("mirror", "미러", "거울"),
        "stories": [
            "You stand in front of me in the bathroom every morning to brush your teeth and comb your tangled hair. Whenever you blink or make a funny face, I instantly copy you exactly. What am I?",
            "I am made of smooth reflective glass hanging on the wall. I show you your own cheerful reflection whenever you look into me. What am I?",
        ],
    },
    {
        "key": "toothbrush",
        "en": "Toothbrush",
        "ko": "칫솔",
        "hint": "치약을 묻혀 이를 닦을 때 쓰는 세면도구예요! 초성 힌트는 ㅊㅅ!",
        "expl": "정답은 Toothbrush, 칫솔이에요. 'bristles'는 칫솔모예요.",
        "accepted": ("toothbrush", "투스브러시", "칫솔"),
        "stories": [
            "I live in a small cup beside the bathroom sink with soft nylon bristles on my head. When paired with minty toothpaste, I scrub away germs and make your teeth sparkle white. What am I?",
            "Dentists say you should visit me for two whole minutes after every meal. I have a slender handle and tiny bristles that clean your smile. What am I?",
        ],
    },
    {
        "key": "bicycle",
        "en": "Bicycle / Bike",
        "ko": "자전거",
        "hint": "바퀴 2개와 페달이 있고 탈 때 헬멧을 써야 하는 탈것이에요! 초성 힌트는 ㅈㅈㄱ!",
        "expl": "정답은 Bicycle, 자전거예요. 'pedal'은 페달이에요.",
        "accepted": ("bicycle", "bike", "바이시클", "바이크", "자전거"),
        "stories": [
            "I have two rubber wheels, handlebars to steer, and two pedals for your sneakers to push. I don't use gasoline, and you should always buckle a helmet before riding me along the path. What am I?",
            "When you pedal faster, my metal chain spins my back wheel and a bell rings on my handlebars. What two-wheeled vehicle am I?",
        ],
    },
    {
        "key": "book",
        "en": "Book",
        "ko": "책",
        "hint": "표지 속에 종이 페이지가 가득하고 이야기를 읽을 수 있는 물건이에요! 초성 힌트는 ㅊ!",
        "expl": "정답은 Book, 책이에요. 'pages'는 쪽(페이지)이에요.",
        "accepted": ("book", "북", "책", "동화책"),
        "stories": [
            "I have a colorful front cover, hundreds of printed paper pages, and a spine holding them together. When you open me, you can travel to magical worlds, meet brave heroes, and learn interesting facts. What am I?",
            "You turn my pages one by one as you read before falling asleep. You slip a thin bookmark between my pages when you take a break. What am I?",
        ],
    },
    {
        "key": "shoes",
        "en": "Shoes",
        "ko": "신발 / 운동화",
        "hint": "외출할 때 발에 신고 끈을 묶는 물건이에요! 초성 힌트는 ㅅㅂ!",
        "expl": "정답은 Shoes, 신발이에요. 'shoelaces'는 신발 끈이에요.",
        "accepted": ("shoes", "shoe", "sneakers", "슈즈", "신발", "운동화"),
        "stories": [
            "We always travel as a matching pair, one for the left foot and one for the right foot. You tie our laces into a bow before running, skipping, and kicking soccer balls in the yard. What are we?",
            "We wait patiently near the front entrance door until you slide your feet inside us. We protect your toes from sharp pebbles and hot pavement outside. What are we?",
        ],
    },
]

_NATURE_CELESTIAL_BLUEPRINTS: list[dict[str, Any]] = [
    {
        "key": "spring",
        "en": "Spring",
        "ko": "봄",
        "hint": "추운 겨울이 지나고 벚꽃이 피며 따뜻한 바람이 부는 계절이에요! 초성 힌트는 ㅂ!",
        "expl": "정답은 Spring, 봄이에요. 'cherry blossoms'는 벚꽃이에요.",
        "accepted": ("spring", "스프링", "봄", "봄철"),
        "stories": [
            "In this fresh season, chilly winter frost melts away and gentle warm breezes return. Pink cherry blossoms bloom in parks and baby birds chirp happily in green trees. Which season is this?",
            "Green grass sprouts in the fields and colorful tulips open up after winter. Hibernating animals wake from their long slumber to greet the warm sun. Which season is this?",
        ],
    },
    {
        "key": "summer",
        "en": "Summer",
        "ko": "여름",
        "hint": "일 년 중 가장 덥고 수영장에서 물놀이를 하며 매미가 우는 계절이에요! 초성 힌트는 ㅇㄹ!",
        "expl": "정답은 Summer, 여름이에요. 'hot and sunny'는 덥고 맑은 날씨예요.",
        "accepted": ("summer", "썸머", "서머", "여름", "여름철"),
        "stories": [
            "The days grow long, hot, and sunny, and children splash in cool swimming pools. People eat juicy watermelon and listen to buzzing cicadas in the trees. Which season is this?",
            "The blazing sun shines bright in blue skies as families head to the beach for summer vacation. People wear sunglasses, short sleeves, and eat cold ice cream. Which season is this?",
        ],
    },
    {
        "key": "autumn",
        "en": "Autumn / Fall",
        "ko": "가을",
        "hint": "나뭇잎이 붉고 노랗게 물들고 곡식과 과일을 수확하는 계절이에요! 초성 힌트는 ㄱㅇ!",
        "expl": "정답은 Autumn(Fall), 가을이에요. 'harvest'는 수확한다는 뜻이에요.",
        "accepted": ("autumn", "fall", "어텀", "폴", "가을", "가을철"),
        "stories": [
            "Green leaves on mountain trees turn brilliant shades of red, orange, and gold before tumbling to the ground. Farmers harvest sweet apples and golden rice as crisp cool breezes blow. Which season is this?",
            "Squirrels scurry across crunchy dry leaves gathering brown acorns for winter. The air becomes crisp and cool, and pumpkins ripen in farm patches. Which season is this?",
        ],
    },
    {
        "key": "winter",
        "en": "Winter",
        "ko": "겨울",
        "hint": "일 년 중 가장 춥고 눈이 내리며 연못이 꽁꽁 어는 계절이에요! 초성 힌트는 ㄱㅇ!",
        "expl": "정답은 Winter, 겨울이에요. 'freezing cold'는 꽁꽁 얼 듯 추운 날씨예요.",
        "accepted": ("winter", "윈터", "겨울", "겨울철"),
        "stories": [
            "This is the coldest season of the year when ponds freeze into slippery ice. People put on thick puffer coats, wrap wool scarves, and catch white snowflakes on their gloves. Which season is this?",
            "Branches are bare, breath turns into white puffs in the frosty air, and children build friendly snowmen in the backyard. Which season is this?",
        ],
    },
    {
        "key": "rainbow",
        "en": "Rainbow",
        "ko": "무지개",
        "hint": "비가 그친 뒤 햇빛이 비칠 때 하늘에 뜨는 알록달록한 아치예요! 초성 힌트는 ㅁㅈㄱ!",
        "expl": "정답은 Rainbow, 무지개예요. 'colorful arch'는 알록달록한 아치예요.",
        "accepted": ("rainbow", "레인보우", "레인보", "무지개"),
        "stories": [
            "Right after a sudden afternoon rain shower, bright sunlight breaks through clearing clouds. A glorious arch of red, orange, yellow, green, blue, and violet curves across the sky. What appeared?",
            "You only see me when both sunshine and rain share the sky at the same time. I make a colorful bridge in the air that delights everyone who spots me. What am I?",
        ],
    },
    {
        "key": "sun",
        "en": "Sun",
        "ko": "해 / 태양",
        "hint": "매일 아침 동쪽에서 떠올라 지구를 밝고 따뜻하게 비춰주는 커다란 별이에요! 초성 힌트는 ㅌㅇ!",
        "expl": "정답은 Sun, 태양(해)이에요. 'rise in the east'는 동쪽에서 뜬다는 뜻이에요.",
        "accepted": ("sun", "the sun", "선", "썬", "해", "태양", "햇님", "해님"),
        "stories": [
            "I rise in the eastern sky every morning, bringing bright daylight and warmth to the whole world. Green plants need my golden rays to grow tall, and I set below the horizon in the west at dusk. What am I?",
            "I am a giant blazing star at the center of our solar system. Earth orbits around me, and without my light, our planet would be dark and freezing cold. What am I?",
        ],
    },
    {
        "key": "moon",
        "en": "Moon",
        "ko": "달",
        "hint": "밤하늘에 반짝이는 별들 사이로 뜨며 보름달이나 초승달로 변해요! 초성 힌트는 ㄷ!",
        "expl": "정답은 Moon, 달이에요. 'crescent'는 초승달이에요.",
        "accepted": ("moon", "the moon", "문", "달", "달님", "보름달", "초승달"),
        "stories": [
            "I glow softly in the dark night sky surrounded by twinkling stars. Sometimes I look like a round silver coin, and sometimes I look like a thin curved banana. What am I?",
            "Astronauts flew across space in rockets and left footprints on my dusty gray surface. I orbit our Earth once every month and shine down through your bedroom window. What am I?",
        ],
    },
]


def _gen_math_question(rng: random.Random) -> QuizQuestion:
    names = ["Minho", "Yuna", "Leo", "Chloe", "David", "Emma", "Tom", "Sarah", "Lucas", "Hana"]
    items = [
        ("sweet apple", "sweet apples", "사과"),
        ("red strawberry", "red strawberries", "딸기"),
        ("chocolate cookie", "chocolate cookies", "쿠키"),
        ("colorful sticker", "colorful stickers", "스티커"),
        ("wooden pencil", "wooden pencils", "연필"),
        ("ripe orange", "ripe oranges", "오렌지"),
        ("blue marble", "blue marbles", "구슬"),
    ]
    num_words = {
        1: ("One", ("one", "1", "원", "하나", "한개", "한 개", "1개", "일")),
        2: ("Two", ("two", "2", "투", "둘", "두개", "두 개", "2개", "이")),
        3: ("Three", ("three", "3", "쓰리", "셋", "세개", "세 개", "3개", "삼")),
        4: ("Four", ("four", "4", "포", "넷", "네개", "네 개", "4개", "사")),
        5: ("Five", ("five", "5", "파이브", "다섯", "다섯개", "다섯 개", "5개", "오")),
        6: ("Six", ("six", "6", "식스", "여섯", "여섯개", "여섯 개", "6개", "육")),
        7: ("Seven", ("seven", "7", "세븐", "일곱", "일곱개", "일곱 개", "7개", "칠")),
        8: ("Eight", ("eight", "8", "에이트", "여덟", "여덟개", "여덟 개", "8개", "팔")),
        9: ("Nine", ("nine", "9", "나인", "아홉", "아홉개", "아홉 개", "9개", "구")),
        10: ("Ten", ("ten", "10", "텐", "열", "열개", "열 개", "10개", "십")),
        11: ("Eleven", ("eleven", "11", "일레븐", "열하나", "11개", "십일")),
        12: ("Twelve", ("twelve", "12", "트웰브", "열둘", "12개", "십이")),
        13: ("Thirteen", ("thirteen", "13", "서틴", "열셋", "13개", "십삼")),
        14: ("Fourteen", ("fourteen", "14", "포틴", "열넷", "14개", "십사")),
        15: ("Fifteen", ("fifteen", "15", "피프틴", "열다섯", "15개", "십오")),
        16: ("Sixteen", ("sixteen", "16", "식스틴", "열여섯", "16개", "십육")),
    }

    name = rng.choice(names)
    s_item, p_item, item_ko = rng.choice(items)
    mode = rng.choice(["subtraction", "addition", "legs"])

    if mode == "subtraction":
        a = rng.randint(3, 9)
        b = rng.randint(1, a - 1)
        res = a - b
        word_en, accepted = num_words[res]
        res_phrase = s_item if res == 1 else p_item
        en_text = (
            f"{name} had {a} {p_item} in a small bag. "
            f"On the way home, {name} gave {b} of them to a good friend. "
            f"How many {res_phrase} does {name} have left?"
        )
        ko_meaning = f"{word_en} ({res}개)"
        hint = f"{a}개 중에서 {b}개를 친구에게 주었어요. {a} 빼기 {b}는 얼마일까요?"
        expl = f"정답은 {word_en}, {res}개예요. '{a} minus {b} is {res}'예요!"
        return QuizQuestion(
            id=f"inf_math_sub_{rng.randint(1000, 9999)}",
            category="colors_numbers",
            kind="story_quiz",
            english_text=en_text,
            korean_meaning=ko_meaning,
            accepted_answers=accepted,
            hint_ko=hint,
            explanation_ko=expl,
        )
    elif mode == "addition":
        a = rng.randint(1, 5)
        b = rng.randint(1, 5)
        res = a + b
        word_en, accepted = num_words[res]
        a_phrase = f"1 {s_item}" if a == 1 else f"{a} {p_item}"
        b_phrase = f"1 more {s_item}" if b == 1 else f"{b} more {p_item}"
        res_phrase = s_item if res == 1 else p_item
        en_text = (
            f"In the morning, {name} collected {a_phrase}. "
            f"In the afternoon, {name} found {b_phrase}. "
            f"How many {res_phrase} did {name} collect altogether?"
        )
        ko_meaning = f"{word_en} ({res}개)"
        hint = f"오전에 {a}개, 오후에 {b}개를 모았어요. {a} 더하기 {b}는 얼마일까요?"
        expl = f"정답은 {word_en}, {res}개예요. '{a} plus {b} is {res}'예요!"
        return QuizQuestion(
            id=f"inf_math_add_{rng.randint(1000, 9999)}",
            category="colors_numbers",
            kind="story_quiz",
            english_text=en_text,
            korean_meaning=ko_meaning,
            accepted_answers=accepted,
            hint_ko=hint,
            explanation_ko=expl,
        )
    else:
        creatures = [
            ("spider", 8, "거미"),
            ("puppy", 4, "강아지"),
            ("bird", 2, "새"),
            ("ant", 6, "개미"),
            ("cat", 4, "고양이"),
            ("duck", 2, "오리"),
        ]
        c1, l1, k1 = rng.choice(creatures)
        c2, l2, k2 = rng.choice([c for c in creatures if c[0] != c1])
        res = l1 + l2
        word_en, accepted = num_words[res]
        en_text = (
            f"In the garden, there is one {c1} and one {c2}. "
            f"If you count carefully, how many legs do they have in total?"
        )
        ko_meaning = f"{word_en} ({res}개)"
        hint = f"{k1} 다리 {l1}개와 {k2} 다리 {l2}개를 합치면 몇 개일까요?"
        expl = f"정답은 {word_en}, {res}개예요. {l1} 더하기 {l2}는 {res}이죠!"
        return QuizQuestion(
            id=f"inf_math_legs_{rng.randint(1000, 9999)}",
            category="colors_numbers",
            kind="story_quiz",
            english_text=en_text,
            korean_meaning=ko_meaning,
            accepted_answers=accepted,
            hint_ko=hint,
            explanation_ko=expl,
        )


def _gen_calendar_time_question(rng: random.Random) -> QuizQuestion:
    mode = rng.choice(["days_after", "day_next", "clock"])
    days = [
        ("Monday", "월요일", "먼데이", ("monday", "먼데이", "월요일", "월")),
        ("Tuesday", "화요일", "튜스데이", ("tuesday", "튜스데이", "화요일", "화")),
        ("Wednesday", "수요일", "웬즈데이", ("wednesday", "웬즈데이", "수요일", "수")),
        ("Thursday", "목요일", "썰스데이", ("thursday", "썰스데이", "써스데이", "목요일", "목")),
        ("Friday", "금요일", "프라이데이", ("friday", "프라이데이", "금요일", "금")),
        ("Saturday", "토요일", "새터데이", ("saturday", "새터데이", "세터데이", "토요일", "토")),
        ("Sunday", "일요일", "선데이", ("sunday", "선데이", "일요일", "일")),
    ]

    if mode == "days_after":
        idx = rng.randint(0, 6)
        day_en, day_ko, _, _ = days[idx]
        target_idx = (idx + 2) % 7
        t_en, t_ko, _, t_acc = days[target_idx]
        en_text = (
            f"Every week has seven days in order. "
            f"Suppose today is {day_en} and your class is visiting the aquarium exactly two days from now. "
            f"What day of the week is the field trip?"
        )
        return QuizQuestion(
            id=f"inf_day_after_{rng.randint(1000, 9999)}",
            category="colors_numbers",
            kind="story_quiz",
            english_text=en_text,
            korean_meaning=f"{t_en} ({t_ko})",
            accepted_answers=t_acc,
            hint_ko=f"오늘이 {day_ko}이면 이틀 뒤는 무슨 요일일까요? 초성 힌트는 {t_ko[0]}!",
            explanation_ko=f"정답은 {t_en}, {t_ko}이에요! {day_en} 이틀 뒤는 {t_en}이죠.",
        )
    elif mode == "day_next":
        idx = rng.randint(0, 6)
        day_en, day_ko, _, _ = days[idx]
        target_idx = (idx + 1) % 7
        t_en, t_ko, _, t_acc = days[target_idx]
        en_text = (
            f"Think about the days of the week starting from Sunday to Saturday. "
            f"If today is {day_en}, which day of the week comes right after today?"
        )
        return QuizQuestion(
            id=f"inf_day_next_{rng.randint(1000, 9999)}",
            category="colors_numbers",
            kind="story_quiz",
            english_text=en_text,
            korean_meaning=f"{t_en} ({t_ko})",
            accepted_answers=t_acc,
            hint_ko=f"{day_ko} 바로 다음 요일은 무엇일까요? 초성 힌트는 {t_ko[0]}!",
            explanation_ko=f"정답은 {t_en}, {t_ko}이에요! {day_en} 다음 날은 {t_en}이에요.",
        )
    else:
        hours = [
            (1, "one", "One", ("one", "1", "1시", "한시", "one o'clock")),
            (2, "two", "Two", ("two", "2", "2시", "두시", "two o'clock")),
            (3, "three", "Three", ("three", "3", "3시", "세시", "three o'clock")),
            (4, "four", "Four", ("four", "4", "4시", "네시", "four o'clock")),
            (5, "five", "Five", ("five", "5", "5시", "다섯시", "five o'clock")),
            (6, "six", "Six", ("six", "6", "6시", "여섯시", "six o'clock")),
            (7, "seven", "Seven", ("seven", "7", "7시", "일곱시", "seven o'clock")),
            (8, "eight", "Eight", ("eight", "8", "8시", "여덟시", "eight o'clock")),
            (9, "nine", "Nine", ("nine", "9", "9시", "아홉시", "nine o'clock")),
            (10, "ten", "Ten", ("ten", "10", "10시", "열시", "ten o'clock")),
            (11, "eleven", "Eleven", ("eleven", "11", "11시", "열한시", "eleven o'clock")),
            (12, "twelve", "Twelve", ("twelve", "12", "12시", "열두시", "twelve o'clock")),
        ]
        h_num, h_word, h_cap, h_acc = rng.choice(hours)
        en_text = (
            f"The round clock on the wall has two hands. "
            f"Its long minute hand points straight up at twelve, and its short hour hand points directly at {h_word}. "
            f"What time is it?"
        )
        return QuizQuestion(
            id=f"inf_clock_{rng.randint(1000, 9999)}",
            category="colors_numbers",
            kind="story_quiz",
            english_text=en_text,
            korean_meaning=f"{h_cap} o'clock ({h_num}시)",
            accepted_answers=h_acc,
            hint_ko=f"긴바늘이 12, 짧은바늘이 {h_num}을 가리키고 있어요! 몇 시일까요?",
            explanation_ko=f"정답은 {h_cap} o'clock, {h_num}시예요! 짧은바늘(hour hand)이 시간을 알려줘요.",
        )


def _gen_from_blueprint(bp: dict[str, Any], category: str, rng: random.Random) -> QuizQuestion:
    story = rng.choice(bp["stories"])
    key = bp["key"]
    qid = f"inf_{category[:3]}_{key}_{rng.randint(1000, 9999)}"
    return QuizQuestion(
        id=qid,
        category=category,
        kind="story_quiz",
        english_text=story,
        korean_meaning=f"{bp['en']} ({bp['ko']})",
        accepted_answers=tuple(bp["accepted"]),
        hint_ko=bp["hint"],
        explanation_ko=bp["expl"],
    )


def generate_infinite_quiz_questions(
    topic: str = "all",
    count: int = 3,
    exclude_texts: set[str] | list[str] | None = None,
    rng: random.Random | None = None,
) -> list[QuizQuestion]:
    """Procedurally generate endless bilingual English listening questions without using a static database."""
    r = rng or random.Random()
    norm_topic = (topic or "all").strip().lower()
    norm_exclude = {t.strip().lower() for t in (exclude_texts or []) if t and t.strip()}

    categories = ["animals", "food", "colors_numbers", "family_school", "daily", "riddles"]
    if norm_topic in categories:
        target_cats = [norm_topic]
    else:
        target_cats = list(categories)
        r.shuffle(target_cats)

    results: list[QuizQuestion] = []
    seen_texts: set[str] = set(norm_exclude)
    attempts = 0
    max_attempts = max(60, count * 35)

    cat_cycle_idx = 0
    while len(results) < count and attempts < max_attempts:
        attempts += 1
        current_cat = target_cats[cat_cycle_idx % len(target_cats)]
        cat_cycle_idx += 1

        cand: QuizQuestion | None = None
        if current_cat == "animals":
            cand = _gen_from_blueprint(r.choice(_ANIMALS_BLUEPRINTS), "animals", r)
        elif current_cat == "food":
            cand = _gen_from_blueprint(r.choice(_FOOD_BLUEPRINTS), "food", r)
        elif current_cat == "family_school":
            cand = _gen_from_blueprint(r.choice(_FAMILY_SCHOOL_BLUEPRINTS), "family_school", r)
        elif current_cat == "daily":
            cand = _gen_from_blueprint(r.choice(_DAILY_BLUEPRINTS), "daily", r)
        elif current_cat == "riddles":
            cand = _gen_from_blueprint(r.choice(_RIDDLES_BLUEPRINTS), "riddles", r)
        elif current_cat == "colors_numbers":
            sub_type = r.choice(["math", "calendar_time", "nature"])
            if sub_type == "math":
                cand = _gen_math_question(r)
            elif sub_type == "calendar_time":
                cand = _gen_calendar_time_question(r)
            else:
                cand = _gen_from_blueprint(r.choice(_NATURE_CELESTIAL_BLUEPRINTS), "colors_numbers", r)

        if cand is None:
            continue

        clean_text = cand.english_text.strip().lower()
        if clean_text in seen_texts:
            continue

        seen_texts.add(clean_text)
        results.append(cand)

    return results


class EnglishQuizSession:
    """Thread-safe state machine for PocoBox's voice-conversational English Listening Quiz."""

    def __init__(self, rng: random.Random | None = None) -> None:
        self._lock = threading.RLock()
        self._rng = rng or random.Random()
        self.active: bool = False
        self.topic: str = "all"
        self.topic_label: str = TOPIC_LABELS["all"]
        self.questions: list[QuizQuestion] = []
        self.current_index: int = 0
        self.score: int = 0
        self.streak: int = 0
        self.best_streak: int = 0
        self.attempts_on_current: int = 0
        self.history: list[dict[str, Any]] = []
        self.last_feedback: str = ""
        self._recent_ids: collections.deque[str] = collections.deque(maxlen=60)
        self._recent_english: collections.deque[str] = collections.deque(maxlen=60)
        self.use_infinite_generation: bool = _env_bool("QUIZ_USE_INFINITE_GENERATION", True)

    def current_question(self) -> QuizQuestion | None:
        with self._lock:
            if not self.active or self.current_index >= len(self.questions):
                return None
            return self.questions[self.current_index]

    def public_state(self) -> dict[str, Any]:
        with self._lock:
            total = len(self.questions)
            q_num = min(self.current_index + 1, total) if self.active and total > 0 else len(self.history)
            cur = self.questions[self.current_index] if (self.active and self.current_index < total) else None
            return {
                "active": self.active,
                "topic": self.topic,
                "topic_label": self.topic_label,
                "question_num": q_num,
                "total": total,
                "score": self.score,
                "streak": self.streak,
                "best_streak": self.best_streak,
                "attempts_on_current": self.attempts_on_current,
                "current_kind": cur.kind if cur else None,
                "current_category": cur.category if cur else None,
                "last_feedback": self.last_feedback,
                "history": list(self.history[-10:]),
            }

    def recent_english_texts(self) -> list[str]:
        with self._lock:
            if self._recent_english:
                return list(self._recent_english)
            by_id = {q.id: q.english_text for q in BUILTIN_QUIZ_BANK}
            return [by_id[qid] for qid in self._recent_ids if qid in by_id]

    def _select_questions(self, topic: str, count: int, custom_questions: list[QuizQuestion] | None = None) -> list[QuizQuestion]:
        selected: list[QuizQuestion] = []
        seen_en: set[str] = set()

        if custom_questions:
            for q in custom_questions:
                key = q.english_text.strip().lower()
                if key and key not in seen_en:
                    seen_en.add(key)
                    selected.append(q)
                    if len(selected) >= count:
                        return selected[:count]

        needed = count - len(selected)
        if needed <= 0:
            return selected[:count]

        # Use infinite dynamic question generation by default (never depends on static question bank)
        if self.use_infinite_generation:
            exclude = set(seen_en) | {t.strip().lower() for t in self.recent_english_texts()}
            inf_qs = generate_infinite_quiz_questions(
                topic=topic,
                count=needed,
                exclude_texts=exclude,
                rng=self._rng,
            )
            for q in inf_qs:
                key = q.english_text.strip().lower()
                if key not in seen_en:
                    seen_en.add(key)
                    selected.append(q)
                    if len(selected) >= count:
                        return selected[:count]

        # Legacy fallback if infinite generation is disabled or underfilled
        if len(selected) < count:
            if topic in TOPIC_LABELS and topic != "all":
                pool = [q for q in BUILTIN_QUIZ_BANK if q.category == topic]
            else:
                pool = list(BUILTIN_QUIZ_BANK)

            fresh = [q for q in pool if q.id not in self._recent_ids]
            stale = [q for q in pool if q.id in self._recent_ids]
            self._rng.shuffle(fresh)
            self._rng.shuffle(stale)
            ordered = fresh + stale

            # For "all", diversify across categories so consecutive questions feel fresh and varied
            if topic == "all" and len(ordered) >= count:
                by_cat: dict[str, list[QuizQuestion]] = {}
                for q in ordered:
                    by_cat.setdefault(q.category, []).append(q)
                cat_keys = list(by_cat.keys())
                self._rng.shuffle(cat_keys)
                interleaved: list[QuizQuestion] = []
                while len(interleaved) < count and any(by_cat.values()):
                    for k in cat_keys:
                        if by_cat.get(k):
                            interleaved.append(by_cat[k].pop(0))
                            if len(interleaved) >= count:
                                break
                ordered = interleaved + [q for q in ordered if q.id not in {x.id for x in interleaved}]

            for q in ordered:
                key = q.english_text.strip().lower()
                if key not in seen_en:
                    seen_en.add(key)
                    selected.append(q)
                    if len(selected) >= count:
                        break
        return selected[:count]

    def _header(self, q_num: int | None = None) -> str:
        total = len(self.questions)
        n = q_num if q_num is not None else min(self.current_index + 1, total)
        return f"🎧 영어 리스닝 퀴즈 ({n}/{total} · ⭐ {self.score}점)"

    def start(
        self,
        topic: str = "all",
        count: int = DEFAULT_QUIZ_COUNT,
        custom_questions: list[QuizQuestion] | None = None,
    ) -> QuizTurnResponse:
        with self._lock:
            norm_topic = (topic or "all").strip().lower()
            label = TOPIC_LABELS.get(norm_topic, norm_topic if norm_topic else TOPIC_LABELS["all"])
            q_count = max(1, min(10, int(count)))
            picked = self._select_questions(norm_topic, q_count, custom_questions=custom_questions)
            if not picked:
                return QuizTurnResponse(
                    ok=False,
                    event="error",
                    active=False,
                    chime="error",
                    segments=[("ko", "퀴즈 문제를 준비하지 못했어요.")],
                    display_header="⚠️ 영어 퀴즈",
                    display_text="문제를 준비하지 못했습니다.",
                    display_status="",
                    spoken_summary="퀴즈 문제를 준비하지 못했어요.",
                    state=self.public_state(),
                )

            self.active = True
            self.topic = norm_topic
            self.topic_label = label
            self.questions = picked
            self.current_index = 0
            self.score = 0
            self.streak = 0
            self.best_streak = 0
            self.attempts_on_current = 0
            self.history = []
            for q in picked:
                self._recent_ids.append(q.id)
                self._recent_english.append(q.english_text)

            first = self.questions[0]
            intro_en = f"Let's start the English listening quiz! We have {len(picked)} questions. Answer after the chime."
            segs: list[tuple[str, str]] = [("en", intro_en), ("pause", "0.35")]
            segs.extend(_prompt_segments_for_question(first, 1))
            self.last_feedback = f"1/{len(picked)}번 문제 출제 ({label})"
            return QuizTurnResponse(
                ok=True,
                event="started",
                active=True,
                chime=None,
                segments=segs,
                display_header=self._header(1),
                display_text=_display_prompt_text(first),
                display_status='🔔 소리가 울리면 영어나 한국어로 대답해 보세요! ("다시", "힌트", "그만")',
                spoken_summary=f"영어 리스닝 퀴즈 시작! ({label}, {len(picked)}문제) 영어 이야기를 듣고 신호음 후에 답해 보세요.",
                state=self.public_state(),
                expect_response=True,
            )

    def stop(self, reason: str = "user") -> QuizTurnResponse:
        with self._lock:
            if not self.active:
                msg = "진행 중인 영어 퀴즈가 없어요."
                return QuizTurnResponse(
                    ok=True,
                    event="stopped",
                    active=False,
                    chime=None,
                    segments=[("ko", msg)],
                    display_header="🎧 영어 리스닝 퀴즈",
                    display_text=msg,
                    display_status="",
                    spoken_summary=msg,
                    state=self.public_state(),
                )

            answered = len(self.history)
            score = self.score
            self.active = False
            if reason == "timeout":
                msg = f"응답이 없어서 영어 퀴즈를 마칠게요. 지금까지 {answered}문제 중 {score}문제를 맞혔어요! 다음에 또 같이 해요!"
            else:
                msg = f"영어 퀴즈를 마칠게요! 지금까지 {answered}문제 중 {score}문제를 맞혔어요. 정말 잘했어요!"
            self.last_feedback = msg
            return QuizTurnResponse(
                ok=True,
                event="stopped",
                active=False,
                chime="quiz_finish" if score > 0 else "ok",
                segments=[("ko", msg)],
                display_header=f"🏁 영어 퀴즈 종료 · ⭐ {score}점",
                display_text=f"{answered}문제 중 {score}문제 정답!",
                display_status="다음에 또 '포코야 영어 퀴즈'라고 불러주세요",
                spoken_summary=msg,
                state=self.public_state(),
            )

    def _advance_or_finish(
        self,
        *,
        event: str,
        chime: str,
        lead_segments: list[tuple[str, str]],
        result_display_text: str,
        summary_prefix: str,
    ) -> QuizTurnResponse:
        self.current_index += 1
        self.attempts_on_current = 0
        total = len(self.questions)

        if self.current_index >= total:
            self.active = False
            encourage = _final_encouragement(self.score, total)
            final_ko = f"영어 퀴즈가 모두 끝났어요! {total}문제 중에서 {self.score}문제를 맞혔어요! {encourage}"
            segs = list(lead_segments) + [("pause", "0.45"), ("ko", final_ko)]
            self.last_feedback = f"완료! {self.score}/{total} 정답"
            return QuizTurnResponse(
                ok=True,
                event="finished",
                active=False,
                chime="quiz_finish",
                segments=segs,
                display_header=f"🏆 영어 퀴즈 완료! ⭐ {self.score} / {total}",
                display_text=f"{result_display_text}\n최종 점수: {total}문제 중 {self.score}문제 정답!",
                display_status=encourage,
                spoken_summary=f"{summary_prefix} {final_ko}",
                state=self.public_state(),
            )

        next_q = self.questions[self.current_index]
        next_num = self.current_index + 1
        segs = list(lead_segments) + [("pause", "0.45")] + _prompt_segments_for_question(next_q, next_num)
        self.last_feedback = summary_prefix
        return QuizTurnResponse(
            ok=True,
            event=event,
            active=True,
            chime=chime,
            segments=segs,
            display_header=self._header(next_num),
            display_text=f"{result_display_text}\n\n{_display_prompt_text(next_q)}",
            display_status=f"🔔 {next_num}번 문제 — 신호음 후 대답해 보세요!",
            spoken_summary=f"{summary_prefix} 다음은 {next_num}번 문제예요.",
            state=self.public_state(),
            expect_response=True,
        )

    def handle_input(
        self,
        text: str,
        *,
        llm_command: str | None = None,
        llm_is_correct: bool | None = None,
    ) -> QuizTurnResponse:
        with self._lock:
            if not self.active or self.current_index >= len(self.questions):
                msg = "진행 중인 영어 퀴즈가 없어요. '포코야 영어 퀴즈 시작해줘'라고 말해보세요!"
                return QuizTurnResponse(
                    ok=False,
                    event="error",
                    active=False,
                    chime="error",
                    segments=[("ko", msg)],
                    display_header="🎧 영어 퀴즈",
                    display_text=msg,
                    display_status="",
                    spoken_summary=msg,
                    state=self.public_state(),
                )

            q = self.questions[self.current_index]
            q_num = self.current_index + 1

            cmd = classify_quiz_command(text)
            if cmd is None and llm_command in ("stop", "repeat", "repeat_slow", "hint", "skip", "score"):
                cmd = llm_command

            if cmd == "stop":
                return self.stop(reason="user")

            if cmd in ("repeat", "repeat_slow"):
                is_slow = cmd == "repeat_slow" or self.attempts_on_current >= 1
                en_role = "en_slow" if is_slow else "en"
                lead_en = "Let's listen slowly." if is_slow else "Let's listen one more time."
                lead_ko = "조금 더 천천히 들려드릴게요!" if is_slow else "다시 한 번 들려드릴게요!"
                segs: list[tuple[str, str]] = [("en", lead_en), ("pause", "0.25"), (en_role, q.english_text)]
                if q.kind == "word_meaning":
                    segs.extend([
                        ("pause", "0.4"),
                        (en_role, q.english_text),
                        ("pause", "0.25"),
                        ("en", "What does it mean in Korean?"),
                    ])
                elif q.kind == "sentence_meaning":
                    segs.extend([("pause", "0.25"), ("en", "What does it mean in Korean?")])
                return QuizTurnResponse(
                    ok=True,
                    event="repeat",
                    active=True,
                    chime=None,
                    segments=segs,
                    display_header=self._header(q_num),
                    display_text=f"🔁 {lead_ko}",
                    display_status="🔔 신호음 후 대답해 보세요!",
                    spoken_summary=lead_ko,
                    state=self.public_state(),
                    expect_response=True,
                )

            if cmd == "hint":
                segs = (
                    split_bilingual_segments(f"힌트를 드릴게요! {q.hint_ko}")
                    + [
                        ("pause", "0.25"),
                        ("en", "Listen again."),
                        ("pause", "0.2"),
                        ("en_slow", q.english_text),
                    ]
                )
                self.last_feedback = f"힌트: {q.hint_ko}"
                return QuizTurnResponse(
                    ok=True,
                    event="hint",
                    active=True,
                    chime=None,
                    segments=segs,
                    display_header=self._header(q_num),
                    display_text=f"💡 힌트: {q.hint_ko}",
                    display_status="🔔 신호음 후 다시 대답해 보세요!",
                    spoken_summary=f"힌트: {q.hint_ko}",
                    state=self.public_state(),
                    expect_response=True,
                )

            if cmd == "score":
                done = len(self.history)
                total = len(self.questions)
                msg = f"지금까지 {done}문제 중에 {self.score}문제를 맞혔어요! 총 {total}문제 중 {q_num}번 문제를 풀고 있어요."
                segs = [
                    ("ko", msg),
                    ("pause", "0.3"),
                    ("en", f"Question {q_num}. Listen again."),
                    ("pause", "0.2"),
                    ("en", q.english_text),
                ]
                return QuizTurnResponse(
                    ok=True,
                    event="score",
                    active=True,
                    chime=None,
                    segments=segs,
                    display_header=self._header(q_num),
                    display_text=f"⭐ 현재 점수: {self.score}점 ({done}/{total} 완료)",
                    display_status="🔔 신호음 후 대답해 보세요!",
                    spoken_summary=msg,
                    state=self.public_state(),
                    expect_response=True,
                )

            if cmd == "skip":
                self.streak = 0
                self.history.append({
                    "id": q.id,
                    "english_text": q.english_text,
                    "korean_meaning": q.korean_meaning,
                    "user_answer": text,
                    "correct": False,
                    "skipped": True,
                })
                lead_ko = f"괜찮아요! {q.explanation_ko}"
                return self._advance_or_finish(
                    event="reveal_next",
                    chime="quiz_wrong",
                    lead_segments=split_bilingual_segments(lead_ko),
                    result_display_text=f"📖 정답: {q.korean_meaning}",
                    summary_prefix=f"정답은 {q.korean_meaning}이에요.",
                )

            # Evaluate actual answer
            local_ok = is_local_answer_match(text, q)
            is_correct = local_ok or (llm_is_correct is True)

            if is_correct:
                self.score += 1
                self.streak += 1
                if self.streak > self.best_streak:
                    self.best_streak = self.streak
                self.history.append({
                    "id": q.id,
                    "english_text": q.english_text,
                    "korean_meaning": q.korean_meaning,
                    "user_answer": text,
                    "correct": True,
                    "skipped": False,
                })
                praise = _praise_line(self.streak)
                lead_ko = f"{praise} {q.explanation_ko}"
                return self._advance_or_finish(
                    event="correct",
                    chime="quiz_correct",
                    lead_segments=split_bilingual_segments(lead_ko),
                    result_display_text=f"🎉 정답! {q.korean_meaning}",
                    summary_prefix=f"정답! {q.korean_meaning}. {praise}",
                )

            # Wrong answer: 1st attempt -> friendly hint & slow replay!
            self.attempts_on_current += 1
            if self.attempts_on_current == 1:
                retry_ko = f"아쉬워요! 힌트를 드릴게요. {q.hint_ko}"
                segs = (
                    split_bilingual_segments(retry_ko)
                    + [
                        ("pause", "0.25"),
                        ("en", "Listen slowly and try again."),
                        ("pause", "0.2"),
                        ("en_slow", q.english_text),
                    ]
                )
                self.last_feedback = f"힌트 재도전: {q.hint_ko}"
                return QuizTurnResponse(
                    ok=True,
                    event="retry_hint",
                    active=True,
                    chime="quiz_wrong",
                    segments=segs,
                    display_header=self._header(q_num),
                    display_text=f"💡 아쉬워요! 힌트: {q.hint_ko}",
                    display_status=f"내 대답: “{text}” · 🔔 신호음 후 다시 도전해 보세요!",
                    spoken_summary=retry_ko,
                    state=self.public_state(),
                    expect_response=True,
                )

            # 2nd wrong attempt -> reveal gently and advance
            self.streak = 0
            self.history.append({
                "id": q.id,
                "english_text": q.english_text,
                "korean_meaning": q.korean_meaning,
                "user_answer": text,
                "correct": False,
                "skipped": False,
            })
            reveal_ko = f"괜찮아요, 정말 잘 도전했어요! {q.explanation_ko}"
            return self._advance_or_finish(
                event="reveal_next",
                chime="quiz_wrong",
                lead_segments=split_bilingual_segments(reveal_ko),
                result_display_text=f"📖 정답: {q.korean_meaning}",
                summary_prefix=f"아쉽지만 정답은 {q.korean_meaning}이에요.",
            )
