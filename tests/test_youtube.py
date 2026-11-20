"""Unit tests for PocoBox YouTube & Continuous K-Pop Music Video Streaming Service."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "services"))
sys.path.insert(0, str(REPO_ROOT / "services" / "lms_client"))

from lms_client import LMSClient  # noqa: E402
from voice_control.aliases import normalize_asr_text  # noqa: E402
from voice_control.daemon import VoiceController  # noqa: E402
from voice_control.executor import (  # noqa: E402
    VoiceExecutor,
    format_command_ack,
)
from voice_control.intent_schema import ExecutionResult, IntentAction, VoiceIntent  # noqa: E402
from voice_control.rule_parser import parse_command  # noqa: E402
from voice_control.youtube import (  # noqa: E402
    CLASSIC_MV_CATALOG,
    KPOP_MV_CATALOG,
    YouTubePlaybackManager,
    extract_video_id_from_url_or_thumb,
    get_kpop_playlist,
    is_youtube_short,
    search_youtube_videos,
)


class TestYouTubeRuleParser(unittest.TestCase):
    def test_kpop_mv_phrasings(self) -> None:
        cases = [
            ("유튜브 틀어줘", "kpop", "K-POP 뮤직비디오"),
            ("kpop 뮤직비디오 틀어줘", "kpop", "K-POP 뮤직비디오"),
            ("kpop 뮤비 틀어줘", "kpop", "K-POP 뮤직비디오"),
            ("케이팝 뮤직비디오 틀어줘", "kpop", "K-POP 뮤직비디오"),
            ("케이팝 뮤비 틀어줘", "kpop", "K-POP 뮤직비디오"),
            ("뮤직비디오 틀어줘", "kpop", "K-POP 뮤직비디오"),
            ("뮤비 틀어줘", "kpop", "K-POP 뮤직비디오"),
            ("kpop mv 틀어줘", "kpop", "K-POP 뮤직비디오"),
            ("걸그룹 뮤비 틀어줘", "girlgroup", "K-POP 걸그룹 뮤직비디오"),
            ("보이그룹 뮤비 틀어줘", "boygroup", "K-POP 보이그룹 뮤직비디오"),
            ("아이돌 뮤비 틀어줘", "boygroup", "K-POP 보이그룹 뮤직비디오"),
            ("뉴진스 뮤비 틀어줘", "뉴진스", "뉴진스 뮤직비디오"),
            ("아이유 뮤직비디오 틀어줘", "아이유", "아이유 뮤직비디오"),
            ("에스파 뮤비 틀어줘", "에스파", "에스파 뮤직비디오"),
            ("유튜브에서 뉴진스 틀어줘", "뉴진스", "뉴진스 뮤직비디오"),
            ("유튜브로 아이유 틀어줘", "아이유", "아이유 뮤직비디오"),
            ("유튜브 뉴진스 하입보이 틀어줘", "뉴진스 하입보이", "뉴진스 하입보이 뮤직비디오"),
            ("play kpop music video", "kpop", "K-POP 뮤직비디오"),
            ("play kpop mv", "kpop", "K-POP 뮤직비디오"),
            ("play youtube", "kpop", "K-POP 뮤직비디오"),
            ("클래식 뮤직비디오 틀어줘", "classic", "클래식 명곡 연주"),
            ("클래식 뮤비 틀어줘", "classic", "클래식 명곡 연주"),
            ("오케스트라 뮤직비디오 틀어줘", "classic", "클래식 명곡 연주"),
            ("유튜브 클래식 틀어줘", "classic", "클래식 명곡 연주"),
        ]
        for text, expected_q, expected_disp in cases:
            with self.subTest(text=text):
                intent = parse_command(text)
                self.assertEqual(intent.action, IntentAction.PLAY_YOUTUBE, f"Failed for {text}")
                self.assertEqual(intent.query, expected_q, f"Wrong query for {text}")
                self.assertEqual(intent.display_query, expected_disp, f"Wrong display_query for {text}")

    def test_classic_mv_asr_normalization(self) -> None:
        raw_text = "플래식 뮤직비디오 타줘"
        norm_text = normalize_asr_text(raw_text)
        self.assertEqual(norm_text, "클래식 뮤직비디오 틀어줘")
        intent = parse_command(norm_text)
        self.assertEqual(intent.action, IntentAction.PLAY_YOUTUBE)
        self.assertEqual(intent.query, "classic")
        self.assertEqual(intent.display_query, "클래식 명곡 연주")

    def test_is_youtube_short(self) -> None:
        # Title hashtag or keyword matches
        self.assertTrue(is_youtube_short(title="베토벤 운명 1악장 #shorts"))
        self.assertTrue(is_youtube_short(title="임윤찬 라흐마니노프 #쇼츠"))
        self.assertTrue(is_youtube_short(title="Chopin Nocturne #Short"))
        self.assertTrue(is_youtube_short(title="Classical music Shorts"))
        self.assertTrue(is_youtube_short(title="[1분클래식] 쇼팽 녹턴 9-2"))
        self.assertTrue(is_youtube_short(title="바이올린 레슨 1분요약"))
        self.assertTrue(is_youtube_short(title="Piano performance #tiktok"))
        self.assertTrue(is_youtube_short(title="Cello melody #reels"))

        # URL path matches
        self.assertTrue(is_youtube_short(url="https://www.youtube.com/shorts/11cta61wi0g"))

        # Thumbnail matches sardefault (vertical Shorts thumbnail)
        self.assertTrue(is_youtube_short(image_url="https://i.ytimg.com/vi/11cta61wi0g/sardefault.jpg"))
        self.assertTrue(is_youtube_short(image_url="/imageproxy/https%3A%2F%2Fi.ytimg.com%2Fvi%2F11cta61wi0g%2Fsardefault.jpg/image.jpg"))

        # Standard videos must NOT match
        self.assertFalse(is_youtube_short(title="Chopin: Piano Concerto No. 1 in E minor Op. 11"))
        self.assertFalse(is_youtube_short(title="Seong-Jin Cho – Debussy: Suite bergamasque, L.75: III. Clair de lune"))
        self.assertFalse(is_youtube_short(title="Short Ride in a Fast Machine"))
        self.assertFalse(is_youtube_short(url="https://www.youtube.com/watch?v=614zno31dqg"))
        self.assertFalse(is_youtube_short(image_url="https://i.ytimg.com/vi/614zno31dqg/hqdefault.jpg"))

    def test_youtube_stop_phrasings(self) -> None:
        cases = [
            "유튜브 꺼줘",
            "유튜브 꺼",
            "유튜브 그만",
            "유튜브 닫아줘",
            "유튜브 종료",
            "뮤비 꺼줘",
            "뮤비 그만",
            "뮤직비디오 꺼줘",
            "뮤직비디오 닫아줘",
            "stop youtube",
            "close youtube",
            "exit youtube",
        ]
        for text in cases:
            with self.subTest(text=text):
                intent = parse_command(text)
                self.assertEqual(intent.action, IntentAction.STOP_YOUTUBE, f"Failed for {text}")

    def test_switch_output_phrasings(self) -> None:
        dac_cases = [
            "사운드는 dac 쪽으로 출력해줘",
            "사운드 dac으로 출력해줘",
            "소리 dac으로 바꿔줘",
            "dac으로 출력해줘",
            "사운드 dac으로 해줘",
        ]
        for text in dac_cases:
            with self.subTest(text=text):
                intent = parse_command(text)
                self.assertEqual(intent.action, IntentAction.SWITCH_OUTPUT)
                self.assertEqual(intent.query, "dac")

        speaker_cases = [
            "사운드 스피커로 출력해줘",
            "소리 내장 스피커로 바꿔줘",
        ]
        for text in speaker_cases:
            with self.subTest(text=text):
                intent = parse_command(text)
                self.assertEqual(intent.action, IntentAction.SWITCH_OUTPUT)
                self.assertEqual(intent.query, "speaker")

        # YouTube with DAC suffix strips DAC cleanly into query
        intent_yt = parse_command("kpop 뮤직비디오 dac 쪽으로 틀어줘")
        self.assertEqual(intent_yt.action, IntentAction.PLAY_YOUTUBE)
        self.assertEqual(intent_yt.query, "kpop")


class TestYouTubeCatalogAndPlaybackManager(unittest.TestCase):
    def test_catalog_integrity(self) -> None:
        self.assertGreaterEqual(len(KPOP_MV_CATALOG), 30)
        seen_ids = set()
        for item in KPOP_MV_CATALOG:
            self.assertIn("id", item)
            self.assertEqual(len(item["id"]), 11)
            self.assertIn("title", item)
            self.assertIn("artist", item)
            self.assertIn("category", item)
            self.assertNotIn(item["id"], seen_ids, f"Duplicate ID: {item['id']}")
            seen_ids.add(item["id"])

    def test_get_kpop_playlist_filters(self) -> None:
        all_vids = get_kpop_playlist("all", shuffle=False)
        self.assertEqual(len(all_vids), len(KPOP_MV_CATALOG))
        self.assertTrue(all_vids[0]["id"].isalnum() or "-" in all_vids[0]["id"] or "_" in all_vids[0]["id"])
        self.assertTrue(all_vids[0]["thumbnail"].startswith("https://i.ytimg.com/vi/"))

        girls = get_kpop_playlist("girlgroup", shuffle=False)
        self.assertGreaterEqual(len(girls), 10)
        for g in girls:
            self.assertEqual(g["category"], "girlgroup")

        boys = get_kpop_playlist("boygroup", shuffle=False)
        self.assertGreaterEqual(len(boys), 8)
        for b in boys:
            self.assertEqual(b["category"], "boygroup")

    def test_extract_video_id(self) -> None:
        self.assertEqual(
            extract_video_id_from_url_or_thumb("/imageproxy/https%3A%2F%2Fi.ytimg.com%2Fvi%2F11cta61wi0g%2Fmaxresdefault.jpg/image.jpg"),
            "11cta61wi0g",
        )
        self.assertEqual(
            extract_video_id_from_url_or_thumb("https://i.ytimg.com/vi/phuiAIQaU40/hqdefault.jpg"),
            "phuiAIQaU40",
        )
        self.assertEqual(
            extract_video_id_from_url_or_thumb("https://www.youtube.com/watch?v=ArmDp-zijuc"),
            "ArmDp-zijuc",
        )
        self.assertEqual(
            extract_video_id_from_url_or_thumb("https://youtu.be/XCkpalH6Ppk"),
            "XCkpalH6Ppk",
        )
        self.assertEqual(
            extract_video_id_from_url_or_thumb("vrM2mrI83uk"),
            "vrM2mrI83uk",
        )

    def test_search_youtube_videos_curated_fallback(self) -> None:
        res = search_youtube_videos("뉴진스")
        self.assertGreaterEqual(len(res), 3)
        self.assertTrue(any("NewJeans" in r["artist"] or "뉴진스" in r["artist"] for r in res))

        res_iu = search_youtube_videos("아이유")
        self.assertGreaterEqual(len(res_iu), 2)
        self.assertTrue(any("IU" in r["artist"] or "아이유" in r["artist"] for r in res_iu))

    def test_classic_catalog_integrity(self) -> None:
        self.assertGreaterEqual(len(CLASSIC_MV_CATALOG), 10)
        seen_ids = set()
        for item in CLASSIC_MV_CATALOG:
            self.assertEqual(len(item["id"]), 11)
            self.assertEqual(item["category"], "classic")
            self.assertNotIn(item["id"], seen_ids)
            seen_ids.add(item["id"])

        # Key artists present
        artists = " ".join(x["artist"] for x in CLASSIC_MV_CATALOG)
        self.assertIn("조성진", artists)
        self.assertIn("임윤찬", artists)
        self.assertIn("요요마", artists)

    def test_get_kpop_playlist_classic(self) -> None:
        classic_list = get_kpop_playlist("classic", shuffle=False)
        self.assertEqual(len(classic_list), len(CLASSIC_MV_CATALOG))
        for it in classic_list:
            self.assertEqual(it["category"], "classic")
            self.assertFalse(is_youtube_short(it["title"], it["thumbnail"], it["url"]))

    def test_search_youtube_videos_classic(self) -> None:
        vids = search_youtube_videos("classic")
        self.assertGreaterEqual(len(vids), 10)
        for v in vids:
            self.assertEqual(v["category"], "classic")
            self.assertFalse(is_youtube_short(v["title"], v["thumbnail"], v["url"]))

        # Artist search matching classic catalog
        res_cho = search_youtube_videos("조성진")
        self.assertTrue(any("조성진" in r["artist"] or "Seong-Jin Cho" in r["artist"] for r in res_cho))

    def test_search_youtube_videos_filters_shorts_from_lms(self) -> None:
        mock_lms = mock.MagicMock()
        mock_lms.resolve_player_id.return_value = "02:70:6f:63:6f:01"
        # LMS returns 1 normal video and 2 shorts
        mock_lms.call.return_value = {
            "loop_loop": [
                {
                    "name": "Beethoven Symphony No. 5 Full Concert",
                    "image": "https://i.ytimg.com/vi/abc12345678/hqdefault.jpg",
                },
                {
                    "name": "1분클래식 베토벤 운명 #shorts",
                    "image": "https://i.ytimg.com/vi/sht12345678/hqdefault.jpg",
                },
                {
                    "name": "쇼팽 에튀드 연주",
                    "image": "https://i.ytimg.com/vi/sar12345678/sardefault.jpg",
                },
            ]
        }
        res = search_youtube_videos("베토벤 운명", lms_client=mock_lms)
        # Verify -shorts was passed in search query to LMS
        call_args = mock_lms.call.call_args[0][1]
        search_param = [x for x in call_args if str(x).startswith("search:")][0]
        self.assertIn("-shorts", search_param)

        # Verify only the full concert video is kept, shorts are filtered out
        titles = [r["title"] for r in res]
        self.assertIn("Beethoven Symphony No. 5 Full Concert", titles)
        self.assertNotIn("1분클래식 베토벤 운명 #shorts", titles)
        self.assertNotIn("쇼팽 에튀드 연주", titles)

    def test_playback_manager_queue_and_looping(self) -> None:
        mgr = YouTubePlaybackManager()
        self.assertFalse(mgr.active)
        self.assertFalse(mgr.playing)

        items = [{"id": f"vid{i:08d}", "title": f"Song {i}"} for i in range(3)]
        st = mgr.load_queue(items, query="kpop", start_index=0)
        self.assertTrue(st["active"])
        self.assertTrue(st["playing"])
        self.assertEqual(mgr.current_video()["title"], "Song 0")

        # Next: advances 0 -> 1 -> 2 -> 0 (loop continuous)
        mgr.next_video()
        self.assertEqual(mgr.current_video()["title"], "Song 1")
        mgr.next_video()
        self.assertEqual(mgr.current_video()["title"], "Song 2")
        mgr.next_video()
        self.assertEqual(mgr.current_video()["title"], "Song 0")

        # Prev
        mgr.prev_video()
        self.assertEqual(mgr.current_video()["title"], "Song 2")

        # Explicit set_index (sync from frontend)
        mgr.set_index(1)
        self.assertEqual(mgr.current_video()["title"], "Song 1")
        self.assertEqual(mgr.index, 1)

        # to_dict includes videos
        d = mgr.to_dict()
        self.assertEqual(len(d["videos"]), 3)
        self.assertEqual(d["index"], 1)

        # Pause & stop
        mgr.set_playing(False)
        self.assertFalse(mgr.playing)
        mgr.stop()
        self.assertFalse(mgr.active)


class TestYouTubeExecutorIntegration(unittest.TestCase):
    def test_format_command_ack(self) -> None:
        kpop_intent = VoiceIntent(action=IntentAction.PLAY_YOUTUBE, query="kpop", display_query="K-POP 뮤직비디오")
        self.assertIn("K-POP 뮤직비디오", format_command_ack(kpop_intent))
        self.assertIn("연속 재생", format_command_ack(kpop_intent))

        nj_intent = VoiceIntent(action=IntentAction.PLAY_YOUTUBE, query="뉴진스", display_query="뉴진스 뮤직비디오")
        self.assertIn("뉴진스 뮤직비디오", format_command_ack(nj_intent))

        stop_intent = VoiceIntent(action=IntentAction.STOP_YOUTUBE)
        self.assertIn("유튜브 재생을 종료", format_command_ack(stop_intent))

    def test_executor_delegates_to_youtube_handler_when_active(self) -> None:
        mock_lms = mock.MagicMock()
        mock_lms.resolve_player_id.return_value = "02:70:6f:63:6f:01"
        yt_active = True
        handled_actions = []

        def mock_yt_handler(intent: VoiceIntent, dry_run: bool) -> ExecutionResult:
            handled_actions.append(intent.action)
            return ExecutionResult(ok=True, action=intent.action, message="handled_by_yt")

        executor = VoiceExecutor(
            mock_lms,
            youtube_handler=mock_yt_handler,
            is_youtube_active_fn=lambda: yt_active,
        )

        # NEXT intent while YouTube is active -> routed to YouTube handler!
        res_next = executor.execute(VoiceIntent(action=IntentAction.NEXT))
        self.assertTrue(res_next.ok)
        self.assertIn(IntentAction.NEXT, handled_actions)

        # PAUSE intent while YouTube is active -> routed to YouTube handler!
        res_pause = executor.execute(VoiceIntent(action=IntentAction.PAUSE))
        self.assertTrue(res_pause.ok)
        self.assertIn(IntentAction.PAUSE, handled_actions)

        # PLAY_SEARCH arrives while YouTube active -> stops YouTube, then plays LMS!
        mock_lms.tidal_search_and_play.return_value = {"ok": True, "category": "songs", "title": "Celebrity"}
        executor.execute(VoiceIntent(action=IntentAction.PLAY_SEARCH, query="아이유 Celebrity"))
        self.assertIn(IntentAction.STOP_YOUTUBE, handled_actions)
        mock_lms.tidal_search_and_play.assert_called_once()


class TestDaemonYouTubeEndpointsAndState(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with mock.patch.dict(os.environ, {"CONTINUOUS_LISTEN": "0", "LOCAL_STT_URL": ""}, clear=False):
            with mock.patch("voice_control.daemon.get_internal_speaker_volume", return_value=85):
                cls.ctrl = VoiceController()
        cls.ctrl.lms = mock.MagicMock()
        cls.ctrl.lms.resolve_player_id.return_value = "02:70:6f:63:6f:01"
        cls.ctrl.lms.now_playing.return_value = {"mode": "pause", "title": "Test"}

    def test_handle_youtube_play_intent(self) -> None:
        intent = VoiceIntent(action=IntentAction.PLAY_YOUTUBE, query="kpop", display_query="K-POP 뮤직비디오")
        res = self.ctrl.handle_youtube_intent(intent, dry_run=False)
        self.assertTrue(res.ok)
        self.assertTrue(self.ctrl.youtube.active)
        self.assertTrue(self.ctrl.youtube.playing)
        self.assertGreaterEqual(len(self.ctrl.youtube.queue), 10)

        # display_state reports keep_awake=True when YouTube is active!
        st = self.ctrl.display_state()
        self.assertTrue(st["display"]["youtube_active"])
        self.assertTrue(st["display"]["keep_awake"])

        # Stop YouTube
        res_stop = self.ctrl.handle_youtube_intent(VoiceIntent(action=IntentAction.STOP_YOUTUBE))
        self.assertTrue(res_stop.ok)
        self.assertFalse(self.ctrl.youtube.active)
        self.assertFalse(self.ctrl.display_state()["display"]["youtube_active"])

    def test_lms_client_youtube_helpers(self) -> None:
        client = LMSClient()
        with mock.patch.object(client, "call", return_value={"loop_loop": [{"name": "Test Song"}]}) as mock_call:
            res = client.youtube_search("pid1", "NewJeans", search_type="video")
            self.assertEqual(len(res), 1)
            mock_call.assert_called_with("pid1", ["youtube", "items", 0, 20, "item_id:1", "search:NewJeans"])

        with mock.patch.object(client, "play_url", return_value={"ok": True}) as mock_play:
            client.youtube_play("pid1", "11cta61wi0g")
            mock_play.assert_called_with("pid1", "youtube://www.youtube.com/v/11cta61wi0g")

    def test_youtube_dac_routing_lifecycle(self) -> None:
        from voice_control.feedback import (
            find_usb_dac_pulse_card,
            find_usb_dac_pulse_sink,
        )

        sample_cards = "1\talsa_card.platform-sound\tmodule-alsa-card.c\n8\talsa_card.usb-Burr-Brown_from_TI_USB_Audio_DAC-00\tmodule-alsa-card.c\n"
        self.assertEqual(
            find_usb_dac_pulse_card(sample_cards),
            "alsa_card.usb-Burr-Brown_from_TI_USB_Audio_DAC-00",
        )
        self.assertIsNone(find_usb_dac_pulse_card("1\talsa_card.platform-sound\tmodule-alsa-card.c\n"))

        sample_sinks = "1\talsa_output.platform-sound.Voice_Call__Speaker__sink\tmodule-alsa-card.c\n3\talsa_output.usb-Burr-Brown_from_TI_USB_Audio_DAC-00.analog-stereo\tmodule-alsa-card.c\n"
        self.assertEqual(
            find_usb_dac_pulse_sink(sample_sinks),
            "alsa_output.usb-Burr-Brown_from_TI_USB_Audio_DAC-00.analog-stereo",
        )

        with mock.patch.object(self.ctrl, "is_dac_mode", return_value=True), \
             mock.patch("voice_control.daemon.enable_usb_dac_pulseaudio", return_value="alsa_output.usb-dac.analog-stereo") as mock_enable, \
             mock.patch("voice_control.daemon.release_usb_dac_pulseaudio", return_value=True) as mock_release:

            # Play YouTube -> routes to DAC!
            intent = VoiceIntent(action=IntentAction.PLAY_YOUTUBE, query="kpop")
            res_play = self.ctrl.handle_youtube_intent(intent)
            self.assertTrue(res_play.ok)
            mock_enable.assert_called_once()

            # Switch to speaker
            res_spk = self.ctrl.handle_switch_output(VoiceIntent(action=IntentAction.SWITCH_OUTPUT, query="speaker"))
            self.assertTrue(res_spk.ok)
            self.assertIn("내장 스피커", res_spk.message)
            mock_release.assert_called_once()

            # Switch to DAC
            mock_enable.reset_mock()
            res_dac = self.ctrl.handle_switch_output(VoiceIntent(action=IntentAction.SWITCH_OUTPUT, query="dac"))
            self.assertTrue(res_dac.ok)
            self.assertIn("USB DAC", res_dac.message)
            mock_enable.assert_called_once()

            # Stop YouTube -> releases DAC back to direct ALSA!
            mock_release.reset_mock()
            res_stop = self.ctrl.handle_youtube_intent(VoiceIntent(action=IntentAction.STOP_YOUTUBE))
            self.assertTrue(res_stop.ok)
            mock_release.assert_called_once()

    def test_enable_and_release_usb_dac_pulseaudio_helpers(self) -> None:
        from voice_control.feedback import (
            enable_usb_dac_pulseaudio,
            release_usb_dac_pulseaudio,
        )

        sample_cards = "1\talsa_card.platform-sound\tmodule-alsa-card.c\n8\talsa_card.usb-Burr-Brown_from_TI_USB_Audio_DAC-00\tmodule-alsa-card.c\n"
        sample_sinks = "1\talsa_output.platform-sound.Voice_Call__Speaker__sink\tmodule-alsa-card.c\n3\talsa_output.usb-Burr-Brown_from_TI_USB_Audio_DAC-00.analog-stereo\tmodule-alsa-card.c\n"
        sample_inputs = "288\t3\tprotocol-native\tfirefox\n"

        def mock_run(cmd, *args, **kwargs):
            m = mock.MagicMock()
            m.returncode = 0
            if "cards" in cmd:
                m.stdout = sample_cards
            elif "sinks" in cmd:
                m.stdout = sample_sinks
            elif "sink-inputs" in cmd:
                m.stdout = sample_inputs
            else:
                m.stdout = ""
            return m

        with mock.patch("subprocess.run", side_effect=mock_run):
            sink = enable_usb_dac_pulseaudio()
            self.assertEqual(sink, "alsa_output.usb-Burr-Brown_from_TI_USB_Audio_DAC-00.analog-stereo")

            released = release_usb_dac_pulseaudio()
            self.assertTrue(released)


if __name__ == "__main__":
    unittest.main()
