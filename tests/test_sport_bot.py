import importlib.util
import os
import sys
import types
import unittest


os.environ.setdefault("MAX_BOT_TOKEN", "test")
os.environ.setdefault("MAX_CHAT_ID", "1")
os.environ.setdefault("DEEPSEEK_API_KEY", "test")
os.environ.setdefault("TAVILY_API_KEY", "test")

if importlib.util.find_spec("maxapi") is None:
    maxapi = types.ModuleType("maxapi")
    maxapi.Bot = object
    sys.modules["maxapi"] = maxapi

import sport_bot


class ResultSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ural = next(club for club in sport_bot.CLUBS if club["key"] == "ural")
        cls.arsenal = next(club for club in sport_bot.CLUBS if club["key"] == "arsenal")
        cls.avtomobilist = next(
            club for club in sport_bot.CLUBS if club["key"] == "avtomobilist"
        )

    def test_away_loss_is_reordered_and_calculated(self):
        parsed = sport_bot.parse_result_line_football(
            "Луки-Энергия|2|Урал|1",
            self.ural,
            "Луки-Энергия",
            "Луки-Энергия — Урал\n2 : 1\nМатч окончен",
        )
        self.assertEqual(parsed, ("1:2", "ПОРАЖЕНИЕ"))

    def test_home_win_is_calculated(self):
        parsed = sport_bot.parse_result_line_football(
            "Урал|3|Ротор|1",
            self.ural,
            "Ротор",
            "Урал — Ротор 3-1 Матч окончен",
        )
        self.assertEqual(parsed, ("3:1", "ПОБЕДА"))

    def test_latin_club_alias_is_supported(self):
        parsed = sport_bot.parse_result_line_football(
            "Спартак|0|Arsenal|2",
            self.arsenal,
            "Спартак",
            "Спартак — Arsenal 0:2 full time",
        )
        self.assertEqual(parsed, ("2:0", "ПОБЕДА"))

    def test_unconfirmed_score_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "не подтверждается"):
            sport_bot.parse_result_line_football(
                "Луки-Энергия|1|Урал|2",
                self.ural,
                "Луки-Энергия",
                "Луки-Энергия — Урал 2:1 Матч окончен",
            )

    def test_old_ambiguous_format_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "4 полей"):
            sport_bot.parse_result_line_football(
                "2:1|ПОБЕДА",
                self.ural,
                "Луки-Энергия",
                "Луки-Энергия — Урал 2:1 Матч окончен",
            )

    def test_youth_result_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "неосновной состав"):
            sport_bot.parse_result_line_football(
                "Рубин U-19|2|Урал U-19|1",
                self.ural,
                "Рубин U-19",
                "Рубин U-19 — Урал U-19 2:1",
            )

    def test_second_team_morning_match_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "молодёжного"):
            sport_bot.parse_morning_line(
                "Вторая лига|15:00|мск|Казань|Рубин-2",
                self.ural["name"],
                "Урал-2 — Рубин-2",
            )

    def test_hockey_away_win_is_reordered(self):
        parsed = sport_bot.parse_result_line_hockey(
            "Нефтехимик|2|Avtomobilist|3|ОТ",
            self.avtomobilist,
            "Нефтехимик",
            "Нефтехимик — Avtomobilist 2:3 ОТ",
        )
        self.assertEqual(parsed, ("3:2", "ПОБЕДА", "ОТ"))


if __name__ == "__main__":
    unittest.main()
