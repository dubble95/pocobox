"""Unit tests for DynamicMusicCatalog and LMS dynamic entity ingestion."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "services"))

from voice_control.aliases import expand_tidal_queries
from voice_control.dynamic_catalog import DynamicMusicCatalog, get_dynamic_catalog


class TestDynamicMusicCatalog(unittest.TestCase):
    """Test DynamicMusicCatalog indexing, LMS synchronization, and phonetic matching."""

    def setUp(self) -> None:
        self.cat = DynamicMusicCatalog()

    def test_add_entities_and_total_count(self) -> None:
        self.cat.add_artist("백예린")
        self.cat.add_track("Square (2017)", "백예린")
        self.cat.add_playlist("비 오는 날 듣기 좋은 노래")

        self.assertGreaterEqual(self.cat.total_entities, 3)

    def test_phonetic_jamo_matching_on_user_catalog(self) -> None:
        self.cat.add_artist("백예린")
        self.cat.add_artist("데이식스")
        self.cat.add_track("스물다섯 스물하나", "자우림")

        # Fuzzy ASR phonetic queries:
        # "배개린" -> "백예린"
        name, etype, meta, score = self.cat.match_entity("배개린", threshold=0.68)
        self.assertEqual(name, "백예린")
        self.assertEqual(etype, "artist")
        self.assertGreater(score, 0.70)

        # "데이식스" -> exact
        name2, etype2, _, score2 = self.cat.match_entity("데이식스")
        self.assertEqual(name2, "데이식스")
        self.assertEqual(score2, 1.0)

        # "스물다섯 스물하나"
        name3, etype3, meta3, _ = self.cat.match_entity("스물다섯 스물하나")
        self.assertEqual(name3, "스물다섯 스물하나")
        self.assertEqual(etype3, "track")
        self.assertEqual(meta3.get("artist"), "자우림")

    def test_update_from_lms_populates_catalog(self) -> None:
        mock_lms = MagicMock()
        mock_lms.list_favorites.return_value = [
            {"name": "IU - Love Poem"},
            {"name": "Chill K-Pop Morning"},
        ]
        mock_lms.player_status.return_value = {
            "playlist_loop": [
                {"title": "Ditto", "artist": "NewJeans"},
                {"title": "Hype Boy", "artist": "NewJeans"},
            ],
        }
        mock_lms.call.return_value = {
            "result": {
                "playlists_loop": [
                    {"playlist": "Favorite Classics"},
                ],
            },
        }

        count = self.cat.update_from_lms(mock_lms, "pocobox")
        self.assertGreater(count, 0)

        # Verify items were indexed
        name, etype, _, _ = self.cat.match_entity("Ditto")
        self.assertEqual(name, "Ditto")
        self.assertEqual(etype, "track")

        name_pl, etype_pl, _, _ = self.cat.match_entity("Chill K-Pop Morning")
        self.assertEqual(name_pl, "Chill K-Pop Morning")
        self.assertEqual(etype_pl, "playlist")

    def test_expand_tidal_queries_integrates_dynamic_catalog(self) -> None:
        global_cat = get_dynamic_catalog()
        global_cat.add_artist("검정치마")

        # Query with slightly distorted ASR: "검정치마"
        expanded = expand_tidal_queries("검정치마")
        self.assertTrue(any("검정치마" in c for c in expanded))


if __name__ == "__main__":
    unittest.main()
