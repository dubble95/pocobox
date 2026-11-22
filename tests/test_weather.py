"""Unit tests for PocoBox live location-aware weather parsing, KMA/met.no/wttr parsers, and Bundang default."""

from __future__ import annotations

import datetime
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "services"))
sys.path.insert(0, str(REPO_ROOT / "services" / "lms_client"))

from voice_control.cloud_stt_intent import _build_system_prompt, _intent_from_llm_payload  # noqa: E402
from voice_control.daemon import VoiceController  # noqa: E402
from voice_control.executor import VoiceExecutor  # noqa: E402
from voice_control.intent_schema import IntentAction, VoiceIntent  # noqa: E402
from voice_control.rule_parser import parse_command  # noqa: E402
from voice_control.weather import (  # noqa: E402
    WeatherForecast,
    _parse_kma_rss_xml,
    _parse_metno_json,
    _parse_wttr_json,
    clear_weather_cache,
    format_weather_answer,
    get_default_location,
    parse_weather_query,
    resolve_location,
)

SAMPLE_KMA_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>기상청 1시간단위 동네예보 웹서비스 - 경기도 성남시분당구 분당동</title>
<item>
<category>경기도 성남시분당구 분당동</category>
<description>
<body>
<data seq="0">
  <hour>19</hour><day>0</day><temp>5.0</temp><tmx>-999.0</tmx><tmn>-999.0</tmn>
  <sky>4</sky><pty>1</pty><wfKor>비</wfKor><wfEn>Rain</wfEn><pop>60</pop><ws>4.0</ws><reh>70</reh>
</data>
<data seq="1">
  <hour>22</hour><day>0</day><temp>2.0</temp><tmx>-999.0</tmx><tmn>-999.0</tmn>
  <sky>1</sky><pty>0</pty><wfKor>맑음</wfKor><wfEn>Clear</wfEn><pop>0</pop><ws>3.2</ws><reh>65</reh>
</data>
<data seq="2">
  <hour>9</hour><day>1</day><temp>1.0</temp><tmx>5.0</tmx><tmn>-2.0</tmn>
  <sky>3</sky><pty>0</pty><wfKor>구름 많음</wfKor><wfEn>Mostly Cloudy</wfEn><pop>20</pop><ws>2.5</ws><reh>55</reh>
</data>
<data seq="3">
  <hour>14</hour><day>1</day><temp>5.0</temp><tmx>5.0</tmx><tmn>-2.0</tmn>
  <sky>3</sky><pty>0</pty><wfKor>구름 많음</wfKor><wfEn>Mostly Cloudy</wfEn><pop>20</pop><ws>2.8</ws><reh>50</reh>
</data>
<data seq="4">
  <hour>13</hour><day>2</day><temp>3.0</temp><tmx>3.0</tmx><tmn>-4.0</tmn>
  <sky>1</sky><pty>0</pty><wfKor>맑음</wfKor><wfEn>Clear</wfEn><pop>0</pop><ws>2.1</ws><reh>45</reh>
</data>
</body>
</description>
</item>
</channel>
</rss>
"""


class TestWeatherFeature(unittest.TestCase):
    def setUp(self) -> None:
        clear_weather_cache()

    def test_default_location_is_seoul(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("POCOBOX_LOCATION_NAME", None)
            os.environ.pop("POCOBOX_WEATHER_ZONE", None)
            loc = get_default_location()
        self.assertEqual(loc.name, "서울")
        self.assertEqual(loc.kma_zone, "1111061500")
        self.assertEqual(resolve_location("").name, "서울")
        self.assertEqual(resolve_location("여기").name, "서울")

    def test_default_location_env_override(self) -> None:
        with mock.patch.dict(os.environ, {"POCOBOX_LOCATION_NAME": "정자동"}, clear=False):
            loc = get_default_location()
        self.assertEqual(loc.name, "정자동")
        self.assertEqual(loc.kma_zone, "4113555000")

    def test_rule_parser_weather_intents_and_music_guard(self) -> None:
        it1 = parse_command("포코야 날씨")
        self.assertEqual(it1.action, IntentAction.WEATHER)
        self.assertTrue(it1.wake_word_detected)
        self.assertEqual(it1.query, "")

        it2 = parse_command("오늘 날씨 어때?")
        self.assertEqual(it2.action, IntentAction.WEATHER)
        self.assertEqual(it2.query, "")

        it3 = parse_command("서울 내일 날씨 알려줘")
        self.assertEqual(it3.action, IntentAction.WEATHER)
        self.assertEqual(it3.query, "서울")

        it4 = parse_command("판교 지금 비 와?")
        self.assertEqual(it4.action, IntentAction.WEATHER)
        self.assertEqual(it4.query, "판교")

        it5 = parse_command("우산 챙겨야 해?")
        self.assertEqual(it5.action, IntentAction.WEATHER)
        self.assertEqual(it5.query, "")

        it6 = parse_command("지금 몇 도야?")
        self.assertEqual(it6.action, IntentAction.WEATHER)

        it_en = parse_command("Hey Poco what's the weather in Busan")
        self.assertEqual(it_en.action, IntentAction.WEATHER)
        self.assertEqual(it_en.query.lower(), "busan")

        # Must NOT hijack music playback requests containing weather words
        mus1 = parse_command("비 오는 날 듣기 좋은 노래 틀어줘")
        self.assertEqual(mus1.action, IntentAction.PLAY_SEARCH)

        mus2 = parse_command("날씨 좋은 날 플레이리스트 재생해줘")
        self.assertEqual(mus2.action, IntentAction.PLAY_SEARCH)

        mus3 = parse_command("비 노래 틀어줘")
        self.assertEqual(mus3.action, IntentAction.PLAY_SEARCH)

    def test_parse_weather_query_details(self) -> None:
        self.assertEqual(parse_weather_query("날씨"), ("", 0, False))
        self.assertEqual(parse_weather_query("내일 비 와?"), ("", 1, True))
        self.assertEqual(parse_weather_query("모레 부산 날씨 알려줘"), ("부산", 2, False))
        self.assertEqual(parse_weather_query("삿포로 날씨 어때"), ("삿포로", 0, False))
        self.assertEqual(parse_weather_query("what's the weather in Tokyo tomorrow"), ("tokyo", 1, False))

    def test_kma_rss_parsing_and_spoken_korean_formatting(self) -> None:
        kst = datetime.timezone(datetime.timedelta(hours=9))
        now = datetime.datetime(2026, 11, 18, 19, 30, tzinfo=kst)
        loc = resolve_location("분당")

        fc_today = _parse_kma_rss_xml(SAMPLE_KMA_XML, loc, day_offset=0, now_kst=now)
        self.assertEqual(fc_today.location_name, "분당")
        self.assertEqual(fc_today.condition_ko, "비")
        self.assertEqual(fc_today.current_temp_c, 5.0)
        self.assertTrue(fc_today.has_precip_now)

        ans_today = format_weather_answer(fc_today, rain_focus=False, language="ko")
        self.assertIn("현재 분당은 비가 오고 기온은 5도입니다.", ans_today)
        self.assertIn("우산", ans_today)
        self.assertNotIn("서울", ans_today)

        ans_rain = format_weather_answer(fc_today, rain_focus=True, language="ko")
        self.assertIn("현재 분당에 비가 오고 있어 우산이 필요합니다", ans_rain)

        fc_tmrw = _parse_kma_rss_xml(SAMPLE_KMA_XML, loc, day_offset=1, now_kst=now)
        self.assertEqual(fc_tmrw.min_temp_c, -2.0)
        self.assertEqual(fc_tmrw.max_temp_c, 5.0)
        ans_tmrw = format_weather_answer(fc_tmrw, rain_focus=False, language="ko")
        self.assertIn("내일 분당은 구름이 많겠고", ans_tmrw)
        self.assertIn("최저 영하 2도, 최고 5도", ans_tmrw)

        ans_en = format_weather_answer(fc_today, rain_focus=False, language="en")
        self.assertIn("Bundang", ans_en)
        self.assertIn("5 degrees", ans_en)

    def test_metno_and_wttr_fallback_parsers(self) -> None:
        kst = datetime.timezone(datetime.timedelta(hours=9))
        now = datetime.datetime(2026, 11, 18, 19, 0, tzinfo=kst)
        loc = resolve_location("도쿄")
        metno_raw = json.dumps({
            "properties": {
                "timeseries": [
                    {
                        "time": "2026-11-18T10:00:00Z",
                        "data": {
                            "instant": {"details": {"air_temperature": 11.4, "relative_humidity": 55, "wind_speed": 2.1}},
                            "next_1_hours": {"summary": {"symbol_code": "clearsky_night"}, "details": {"precipitation_amount": 0.0}},
                        },
                    }
                ]
            }
        })
        fc_m = _parse_metno_json(metno_raw, loc, day_offset=0, now_kst=now)
        self.assertEqual(fc_m.location_name, "도쿄")
        self.assertEqual(fc_m.condition_ko, "맑음")
        self.assertAlmostEqual(fc_m.current_temp_c or 0.0, 11.4)

        wttr_raw = json.dumps({
            "current_condition": [{"temp_C": "9", "humidity": "60", "weatherDesc": [{"value": "Partly cloudy"}]}],
            "weather": [{"mintempC": "4", "maxtempC": "12", "hourly": [{"chanceofrain": "10"}]}],
        })
        fc_w = _parse_wttr_json(wttr_raw, loc, day_offset=0)
        self.assertEqual(fc_w.condition_ko, "구름 많음")
        self.assertEqual(fc_w.current_temp_c, 9.0)

    def test_executor_handles_weather_without_lms_and_uses_default_location(self) -> None:
        seen_calls: list[tuple[str, int]] = []

        def fake_fetch(loc_q: str, day_offset: int) -> WeatherForecast:
            seen_calls.append((loc_q, day_offset))
            loc = resolve_location(loc_q)
            return WeatherForecast(
                location_name=loc.name,
                en_location_name=loc.en_name,
                day_offset=day_offset,
                provider="mock-kma",
                condition_ko="맑음",
                condition_en="Clear",
                current_temp_c=7.0 if day_offset == 0 else None,
                min_temp_c=1.0,
                max_temp_c=10.0,
                precip_prob_pct=0,
            )

        executor = VoiceExecutor(lms=None, weather_fetcher=fake_fetch)  # type: ignore[arg-type]
        res = executor.execute(parse_command("날씨"))
        self.assertTrue(res.ok)
        self.assertEqual(res.action, "weather")
        self.assertEqual(seen_calls, [("", 0)])
        self.assertIn("서울", res.message)
        self.assertEqual(res.details["location"], "서울")

        # Regression: wake word prefix ("포코야") in raw_text must NOT be parsed as a city name!
        res_wake = executor.execute(parse_command("포코야 오늘 날씨 어때?"))
        self.assertTrue(res_wake.ok)
        self.assertEqual(seen_calls[-1], ("", 0))
        self.assertEqual(res_wake.details["location"], "서울")
        self.assertIn("서울", res_wake.message)

    def test_system_prompt_and_llm_weather_intent_and_spoken_action(self) -> None:
        prompt = _build_system_prompt()
        self.assertIn("서울", prompt)
        self.assertIn('action="weather"', prompt)

        intent = _intent_from_llm_payload(
            {"transcript": "포코야 밖에 추워?", "action": "weather", "query": "", "wake_word_detected": True},
            provider="gemini-text",
        )
        self.assertEqual(intent.action, IntentAction.WEATHER)
        self.assertEqual(intent.query, "")

        with mock.patch.dict(os.environ, {"CONTINUOUS_LISTEN": "0"}):
            ctrl = VoiceController()
        self.assertTrue(ctrl._is_spoken_action(IntentAction.WEATHER.value))


if __name__ == "__main__":
    unittest.main(verbosity=2)
