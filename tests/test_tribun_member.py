"""MEMBER в личке Трибуна: три раздела интересов, «➕ Другое» как запрос, зависимости, агрегат, совместимость со старым профилем."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_tribun import Base, user, T, OWNER, GROUP  # noqa: E402
import tribun_catalog as C  # noqa: E402

THREE = ["🏅 Виды спорта", "🏆 Чемпионаты", "❤️ Клубы"]


class Member(Base):
    def labels(self):
        return [l for l, _ in self.buttons()]

    def payloads(self):
        return [p for _, p in self.buttons()]

    def requests(self, uid=None):
        items = self.tribun.load_requests()["items"]
        return [i for i in items if uid is None or i["user_id"] == uid]

    # 1
    async def test_01_menu_is_exactly_three_sections(self):
        await self.say(5, "/start")
        self.assertEqual(self.labels(), THREE)
        for forbidden in ("Что сегодня", "уведомлени", "Спортсмен", "Тип контента", "О Трибуне", "Мои интересы"):
            self.assertNotIn(forbidden, " ".join(self.labels()) + self.last_text(), forbidden)

    # 2
    async def test_02_old_features_are_gone_not_hidden(self):
        for name in ("today_view", "event_score", "interests_hub", "onboarding_screen", "nl_edit_screen", "set_notification"):
            self.assertFalse(hasattr(T.Tribun, name), name)
        for const in ("ATHLETES", "CONTENT", "NOTIFY", "ONBOARDING_STEPS", "ABOUT_TEXT", "TEAMS"):
            self.assertFalse(hasattr(T, const), const)
        await self.say(5, "что сегодня")
        self.assertEqual(self.labels(), THREE)

    # 3
    async def test_03_sports_multiselect_with_other(self):
        await self.press(5, "c:sp")
        self.assertEqual([l for l in self.labels() if l not in ("✅ Сохранить", "Дальше ▶️", "⬅️ Назад")],
                         ["⚽ Футбол", "🏒 Хоккей", "🥅 Футзал", "🏀 Баскетбол", "➕ Другое"])
        for key in ("football", "hockey", "futsal"):
            await self.press(5, f"t:sp:{key}")
        self.assertEqual(self.profile(5)["sports"], ["football", "hockey", "futsal"])
        self.assertIn("✅ ⚽ Футбол", self.labels())
        await self.press(5, "t:sp:hockey")
        self.assertEqual(self.profile(5)["sports"], ["football", "futsal"])

    # 4
    async def test_04_championships_follow_chosen_sports(self):
        await self.press(5, "c:cp")
        self.assertIn("сначала отметь вид спорта", self.last_text())
        self.assertFalse([p for p in self.payloads() if p.startswith("t:cp:")])
        await self.press(5, "t:sp:hockey")
        await self.press(5, "c:cp")
        self.assertEqual([p for p in self.payloads() if p.startswith("t:cp:")], ["t:cp:khl"])
        await self.press(5, "t:sp:futsal")
        await self.press(5, "c:cp")
        self.assertEqual(sorted(p for p in self.payloads() if p.startswith("t:cp:")), ["t:cp:cuplig", "t:cp:khl", "t:cp:superliga"])
        before = self.profile(5)["championships"]
        await self.press(5, "t:cp:fnl1")                                   # футбол не выбран — вариант недоступен
        self.assertEqual(self.profile(5)["championships"], before)
        self.assertIn("недоступен", self.last_text())

    # 5 + 6
    async def test_05_clubs_follow_sports_and_all_six_clubs_are_in_catalog(self):
        await self.press(5, "c:cl")
        self.assertIn("сначала отметь вид спорта", self.last_text())
        for key in ("football", "hockey", "futsal"):
            await self.press(5, f"t:sp:{key}")
        await self.press(5, "c:cl")
        self.assertEqual(sorted(p.split(":")[2] for p in self.payloads() if p.startswith("t:cl:")),
                         sorted(("avtomobilist", "sinara", "ural", "real", "arsenal", "milan")))
        self.assertEqual(len(C.CLUBS), 6)
        import sport_bot as S
        self.assertEqual({c[0] for c in C.CLUBS}, {c["key"] for c in S.CLUBS})        # каталог совпадает с шестью клубами SPORTBOT
        self.profile(5)
        await self.press(5, "t:sp:football")                                           # футбол убран → его клубы скрыты
        await self.press(5, "c:cl")
        self.assertEqual(sorted(p for p in self.payloads() if p.startswith("t:cl:")), ["t:cl:avtomobilist", "t:cl:sinara"])

    # 7
    async def test_06_basketball_has_no_big_catalog(self):
        await self.press(5, "t:sp:basketball")
        await self.press(5, "c:cl")
        self.assertFalse([p for p in self.payloads() if p.startswith("t:cl:")])
        self.assertIn("o:cl", self.payloads())
        await self.press(5, "c:cp")
        self.assertEqual([p for p in self.payloads() if p.startswith("t:cp:")], ["t:cp:vtb"])

    # 8
    async def test_07_other_is_a_request_not_support(self):
        await self.join(5, "Мария")
        await self.press(5, "o:cp")
        self.assertEqual(self.tribun.awaiting[5], {"kind": "other", "code": "cp"})
        await self.say(5, "NBA")
        await self.press(5, "o:cp")
        await self.say(5, "НБА")                                                       # то же самое — не второй запрос
        reqs = self.requests(5)
        self.assertEqual(len(reqs), 1)
        r = reqs[0]
        self.assertEqual((r["category"], r["raw_text"], r["normalized_text"], r["user_id"], r["active"]), ("championship", "NBA", "nba", 5, True))
        self.assertTrue(r["created_at"])
        self.assertEqual(self.profile(5)["championships"], [])                          # запрос ничего не выбирает и не подключает
        self.assertEqual(self.bot.group()[1:], [])                                      # ничего не опубликовано
        self.assertIn("➕ NBA", self.last_text())

    async def test_08_other_in_all_three_categories_and_known_names_become_normal_choices(self):
        await self.press(5, "o:sp")
        await self.say(5, "Теннис, Формула 1")
        await self.press(5, "o:cl")
        await self.say(5, "Ювентус")
        await self.press(5, "o:sp")
        await self.say(5, "футбол")                                                     # известный вид спорта — обычный выбор
        cats = sorted((r["category"], r["normalized_text"]) for r in self.requests(5))
        self.assertEqual(cats, [("club", "ювентус"), ("sport", "теннис"), ("sport", "формула-1")])
        self.assertEqual(self.profile(5)["sports"], ["football"])
        self.assertEqual(C.normalize_request("Формула-1"), C.normalize_request("формула 1"))
        self.assertNotEqual(C.normalize_request("Милан"), C.normalize_request("Интер"))   # без агрессивного fuzzy
        await self.press(5, "o:sp")
        await self.say(5, "   ")
        self.assertIn("Не понял", self.last_text())

    # 9
    async def test_09_removed_sport_hides_related_choices_in_aggregate_without_data_loss(self):
        await self.join(5, "Мария")
        await self.onboard(5, sports=("hockey", "football"), comps=("khl", "laliga"), clubs=("avtomobilist", "real"))
        m = self.tribun.interest_map(self.tribun.load_members())
        self.assertEqual([x[0] for x in m["clubs"]], ["Автомобилист", "Реал Мадрид"])
        await self.press(5, "c:sp")
        await self.press(5, "t:sp:football")                                            # убрали футбол
        m = self.tribun.interest_map(self.tribun.load_members())
        self.assertEqual([x[0] for x in m["clubs"]], ["Автомобилист"])
        self.assertEqual([x[0] for x in m["championships"]], ["КХЛ"])
        self.assertEqual(self.profile(5)["clubs"], ["avtomobilist", "real"])             # данные не потеряны
        await self.press(5, "t:sp:football")                                            # вернули — всё снова на месте
        m = self.tribun.interest_map(self.tribun.load_members())
        self.assertEqual([x[0] for x in m["clubs"]], ["Автомобилист", "Реал Мадрид"])

    # 10
    async def test_10_profile_is_configured_by_three_categories_incl_other(self):
        await self.join(5, "Мария")
        await self.press(5, "t:sp:hockey")
        self.assertFalse(self.profile(5)["onboarding_completed"])
        await self.press(5, "t:cp:khl")
        self.assertFalse(self.profile(5)["onboarding_completed"])
        await self.press(5, "o:cl")
        await self.say(5, "Ювентус")                                                    # «Другое» тоже считается выбором
        self.assertTrue(self.profile(5)["onboarding_completed"])
        await self.press(5, "t:cp:khl")                                                  # снял чемпионат → снова не настроен
        self.assertFalse(self.profile(5)["onboarding_completed"])
        self.assertEqual(self.tribun.interest_map(self.tribun.load_members())["configured"], 0)

    # 11
    async def test_11_each_section_edited_separately_and_save_message(self):
        await self.join(5, "Мария")
        await self.onboard(5, sports=("hockey",), comps=("khl",), clubs=("avtomobilist",))
        before = dict(self.profile(5))
        await self.press(5, "c:sp")
        await self.press(5, "t:sp:futsal")
        after = self.profile(5)
        self.assertEqual(after["championships"], before["championships"])
        self.assertEqual(after["clubs"], before["clubs"])
        await self.press(5, "sv")
        self.assertTrue(self.last_text().startswith("✅ Интересы сохранены"))
        self.assertEqual(self.labels(), THREE)
        await self.press(5, "c:cl")
        self.assertIn("✅ Автомобилист", self.labels())
        self.assertIn("o:cl", self.payloads())
        self.assertIn("sv", self.payloads())

    # 12
    async def test_12_persistence_restart_and_legacy_members_file(self):
        await self.join(5, "Мария")
        await self.onboard(5, sports=("hockey",), comps=("khl",), clubs=("avtomobilist",), other={"cl": "Ювентус"})
        snapshot = self.tribun.load_members()["members"]["5"]
        reqs = self.requests()
        self.make()                                                                      # рестарт
        self.assertEqual(self.tribun.load_members()["members"]["5"], snapshot)
        self.assertEqual(self.requests(), reqs)
        legacy = {"version": 1, "meta": {}, "members": {"42": {
            "user_id": 42, "display_name": "Старый", "sports": ["hockey", "tennis"], "teams": ["Автомобилист", "Зенит"], "athletes": ["Овечкин"],
            "competitions": ["КХЛ", "РПЛ"], "content_preferences": ["matches"], "notification_level": "all", "onboarding_completed": True,
            "welcomed": True, "active_in_group": True, "created_at": "2026-10-03T09:00:00+05:00", "updated_at": "2026-10-03T09:00:00+05:00",
            "ever_in_group": True, "joined_at": None, "joined_source": "event", "left_at": None, "returned_at": None, "join_notified": True,
            "completion_notified": True, "interests_updated_at": None}}}
        with open(self.tribun.members_path, "w", encoding="utf-8") as f:
            json.dump(legacy, f)
        await self.say(42, "привет", name="Старый")
        self.assertEqual(self.labels(), THREE)
        p = self.profile(42)
        self.assertEqual(p["clubs"], ["avtomobilist"])                                   # только то, что точно совпало с каталогом
        self.assertEqual(p["championships"], ["khl"])
        self.assertEqual((p["teams"], p["athletes"], p["notification_level"]), (["Автомобилист", "Зенит"], ["Овечкин"], "all"))   # ничего не удалено
        await self.press(OWNER, "adm:mc:42")
        self.assertIn("Старый", self.last_text())

    # 13
    async def test_13_interests_never_change_club_automation(self):
        import sport_bot as S
        keys = [c["key"] for c in S.CLUBS]
        await self.join(5, "Мария")
        await self.onboard(5, sports=("hockey",), comps=("khl",), clubs=("avtomobilist",))
        await self.press(5, "c:cl")
        await self.press(5, "t:cl:avtomobilist")                                         # убрали интерес
        await self.press(5, "t:sp:hockey")
        self.assertEqual([c["key"] for c in S.CLUBS], keys)                              # шесть клубов остались в автоматике
        self.assertEqual(len(keys), 6)
        import inspect
        for job in (S.job_weekly, S.job_morning, S.job_check_results, S.resolve_result):
            self.assertNotIn("tribun.", inspect.getsource(job), job.__name__)             # публикации не зависят от профилей

    async def test_member_cannot_touch_other_members_requests(self):
        await self.join(5, "А")
        await self.join(6, "Б")
        await self.press(5, "o:cl")
        await self.say(5, "Ювентус")
        rid = self.requests(5)[0]["id"]
        await self.press(6, f"rt:cl:{rid}")
        self.assertTrue(self.requests(5)[0]["active"])
        await self.press(5, f"rt:cl:{rid}")
        self.assertFalse(self.requests(5)[0]["active"])


if __name__ == "__main__":
    unittest.main()
