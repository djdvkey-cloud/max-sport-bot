"""Уведомления владельцу, реестр участников, карточка участника, агрегат интересов, приватность."""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_tribun import Base, user, rd, T, OWNER, GROUP  # noqa: E402


class OwnerBase(Base):
    def owner_msgs(self):
        return [m[1] for m in self.bot.sent if m[0] == OWNER]

    def group_texts(self):
        return [m[1] for m in self.bot.group()]


class JoinNotice(OwnerBase):
    async def test_1_new_member_owner_gets_exactly_one_notice_group_gets_welcome(self):
        await self.join(1, "Алексей")
        self.assertEqual(len(self.group_texts()), 1)                          # приветствие в группе
        self.assertIn("Добро пожаловать", self.group_texts()[0])
        self.assertEqual(self.owner_msgs(), ["👤 Новый участник «Своей Трибуны»\n\nАлексей\nВ группе: 1 человек\n\n⚙️ Интересы: ещё не настроены"])

    async def test_group_count_and_plural(self):
        for uid, name in ((1, "А"), (2, "Б"), (3, "В")):
            await self.join(uid, name)
        self.assertIn("В группе: 3 человека", self.owner_msgs()[-1])
        for uid in range(4, 7):
            await self.join(uid, f"Н{uid}")
        self.assertIn("В группе: 6 человек", self.owner_msgs()[-1])
        self.assertEqual([T.people(n) for n in (1, 2, 5, 11, 12, 21, 22, 25, 111)],
                         ["1 человек", "2 человека", "5 человек", "11 человек", "12 человек", "21 человек", "22 человека", "25 человек", "111 человек"])

    async def test_2_repeated_technical_event_gives_no_second_notice(self):
        await self.join(1, "Алексей")
        for _ in range(3):
            await self.join(1, "Алексей")
        self.assertEqual(len(self.owner_msgs()), 1)
        self.assertEqual(len(self.group_texts()), 1)

    async def test_bot_and_foreign_chat_events_give_no_notice(self):
        await self.join(99, "Трибун", bot=True)
        await self.tribun.on_user_added(-5, user(2, "Чужой"))
        self.assertEqual(self.owner_msgs(), [])

    async def test_notice_goes_only_to_owner_never_to_group_or_member(self):
        await self.join(1, "Алексей")
        self.assertTrue(all(m[0] in (GROUP, OWNER) for m in self.bot.sent))
        self.assertEqual([m[0] for m in self.bot.sent if "Новый участник" in m[1]], [OWNER])

    async def test_owner_himself_is_not_announced_to_owner(self):
        await self.join(OWNER, "Дмитрий")
        self.assertEqual(self.owner_msgs(), [])
        self.assertTrue(self.profile(OWNER)["join_notified"])

    async def test_private_first_then_join_still_welcomed_and_reported(self):
        await self.tribun.on_bot_started(user(7, "Сергей"), 7, "interests", self.ctx(7).reply)
        self.assertFalse(self.profile(7)["active_in_group"])
        self.assertEqual(self.owner_msgs(), [])
        await self.join(7, "Сергей")
        self.assertEqual(len(self.group_texts()), 1)
        self.assertEqual(len(self.owner_msgs()), 1)
        self.assertIn("Сергей", self.owner_msgs()[0])

    async def test_undelivered_notice_is_retried_once_by_flush_and_not_duplicated(self):
        self.bot.fail_user = True
        await self.join(1, "Алексей")
        self.assertFalse(self.profile(1)["join_notified"])
        self.bot.fail_user = False
        await self.tribun.flush_owner_notifications()
        await self.tribun.flush_owner_notifications()
        self.assertEqual(len(self.owner_msgs()), 1)
        self.assertTrue(self.profile(1)["join_notified"])


class CompletionCard(OwnerBase):
    async def finish_profile(self, uid=1, name="Алексей"):
        await self.join(uid, name)
        self.bot.sent.clear()
        await self.tribun.on_bot_started(user(uid, name), uid, "interests", self.ctx(uid).reply)
        for key in ("hockey", "football"):
            await self.press(uid, f"t:sp:{key}")
        for key in ("khl", "laliga"):
            await self.press(uid, f"t:cp:{key}")
        for key in ("avtomobilist", "real"):
            await self.press(uid, f"t:cl:{key}")
        await self.press(uid, "sv")

    async def test_3_completion_sends_one_compact_card_to_owner(self):
        await self.finish_profile()
        self.assertEqual(self.owner_msgs(), [
            "✅ Алексей настроил интересы\n\n🏅 Спорт:\n⚽ Футбол\n🏒 Хоккей\n\n🏆 Чемпионаты:\nКХЛ\nЛа Лига\n\n❤️ Клубы:\n"
            "Автомобилист\nРеал Мадрид\n\n➕ Запросы:\n—"])
        self.assertEqual(self.group_texts()[1:], [])                           # в группу — ничего, кроме приветствия

    async def test_4_later_edits_send_no_extra_push(self):
        await self.finish_profile()
        count = len(self.owner_msgs())
        await self.press(1, "c:cp")
        await self.press(1, "t:cp:fnl1")                                       # добавил чемпионат
        await self.press(1, "c:cl")
        await self.press(1, "t:cl:real")                                       # убрал клуб
        await self.press(1, "t:cl:ural")                                       # добавил другой
        await self.press(1, "c:sp")
        await self.press(1, "t:sp:basketball")                                 # добавил вид спорта
        await self.press(1, "o:cp")
        await self.say(1, "Кубок Первого канала")                              # новый запрос
        await self.press(1, "sv")
        self.assertEqual(len(self.owner_msgs()), count)
        self.assertEqual(self.profile(1)["clubs"], ["avtomobilist", "ural"])

    async def test_card_not_resent_after_restart(self):
        await self.finish_profile()
        self.make()
        await self.tribun.flush_owner_notifications()
        await self.press(1, "c:sp")
        self.assertEqual(len([m for m in self.owner_msgs() if "настроил интересы" in m]), 1)

    async def test_owner_own_profile_is_not_reported_to_himself(self):
        await self.join(OWNER, "Дмитрий")
        await self.tribun.on_bot_started(user(OWNER, "Дмитрий"), OWNER, "interests", self.ctx(OWNER).reply)
        for payload in ("t:sp:hockey", "t:cp:khl", "t:cl:avtomobilist", "sv"):
            await self.press(OWNER, payload)
        self.assertTrue(self.profile(OWNER)["completion_notified"])
        self.assertEqual(self.owner_msgs(), [])

    async def test_incomplete_profile_is_not_reported_and_requests_only_card_is_readable(self):
        await self.join(1, "Алексей")
        self.bot.sent.clear()
        await self.tribun.on_bot_started(user(1, "Алексей"), 1, None, self.ctx(1).reply)
        await self.press(1, "sv")
        self.assertTrue(self.last_text().startswith("✅ Интересы сохранены"))
        self.assertEqual(self.owner_msgs(), [])                                 # в трёх разделах пока ничего — карточки нет
        for code, text in (("sp", "Теннис"), ("cp", "Ролан Гаррос"), ("cl", "Бока Хуниорс")):
            await self.press(1, f"o:{code}")
            await self.say(1, text)
        await self.press(1, "sv")
        card = self.owner_msgs()[0]
        self.assertIn("✅ Алексей настроил интересы", card)
        self.assertIn("🏅 Спорт:\n➕ Теннис", card)
        self.assertIn("❤️ Клубы:\n➕ Бока Хуниорс", card)
        self.assertIn("➕ Запросы:\nТеннис (спорт)\nРолан Гаррос (чемпионат)\nБока Хуниорс (клуб)", card)

    async def test_undelivered_card_is_retried_by_flush(self):
        self.bot.fail_user = True
        await self.finish_profile()
        self.assertFalse(self.profile(1)["completion_notified"])
        self.bot.fail_user = False
        await self.tribun.flush_owner_notifications()
        await self.tribun.flush_owner_notifications()
        self.assertEqual(len([m for m in self.owner_msgs() if "настроил интересы" in m]), 1)


class LeaveReturn(OwnerBase):
    async def prepared(self):
        await CompletionCard.finish_profile(self)

    async def test_6_leave_notifies_owner_deactivates_and_excludes_from_aggregate(self):
        await self.prepared()
        self.bot.sent.clear()
        self.assertEqual(await self.tribun.on_user_removed(GROUP, user(1)), "left")
        self.assertEqual(self.owner_msgs(), ["👋 Алексей покинул «Свою Трибуну»\n\nПрофиль интересов сохранён.\n"
                                             "В общей карте интересов больше не учитывается."])
        self.assertFalse(self.profile(1)["active_in_group"])
        m = self.tribun.interest_map(self.tribun.load_members())
        self.assertEqual((m["active"], m["sports"], m["clubs"]), (0, [], []))
        self.assertEqual(self.profile(1)["clubs"], ["avtomobilist", "real"])
        self.assertEqual(await self.tribun.on_user_removed(GROUP, user(1)), "already-left")      # повтор — без второго сообщения
        self.assertEqual(len(self.owner_msgs()), 1)
        self.assertEqual(self.group_texts(), [])

    async def test_7_return_restores_profile_notifies_owner_without_new_onboarding(self):
        await self.prepared()
        await self.tribun.on_user_removed(GROUP, user(1))
        self.bot.sent.clear()
        self.clock.dt += datetime.timedelta(days=30)
        await self.join(1, "Алексей")
        self.assertEqual(self.owner_msgs(), ["🔄 Алексей вернулся на «Свою Трибуну»\n\nЕго прежние интересы восстановлены."])
        self.assertTrue(self.profile(1)["active_in_group"])
        self.assertEqual(self.profile(1)["sports"], ["hockey", "football"])
        self.assertEqual(self.group_texts(), [])                                # повторного приветствия нет
        m = self.tribun.interest_map(self.tribun.load_members())
        self.assertEqual(m["active"], 1)
        self.assertEqual(m["sports"][0][1], 1)
        await self.tribun.on_bot_started(user(1, "Алексей"), 1, None, self.ctx(1).reply)
        self.assertIn("Своя Трибуна", self.last_text())                         # без повторного онбординга: главное меню
        self.assertNotIn("Начать настройку", str(self.buttons()))
        await self.join(1, "Алексей")                                           # техническое повторение — второго уведомления нет
        self.assertEqual(len(self.owner_msgs()), 1)

    async def test_return_without_profile_says_so(self):
        await self.join(2, "Пётр")
        await self.tribun.on_user_removed(GROUP, user(2))
        self.bot.sent.clear()
        await self.join(2, "Пётр")
        self.assertIn("Интересы он ещё не настраивал", self.owner_msgs()[0])

    async def test_unknown_user_leaving_is_ignored(self):
        self.assertEqual(await self.tribun.on_user_removed(GROUP, user(55, "Никто")), "unknown")
        self.assertEqual(self.owner_msgs(), [])

    async def test_sync_detects_missed_leave_and_return_once(self):
        await self.prepared()
        self.bot.members = {}
        self.bot.sent.clear()
        await self.tribun.sync_members()
        await self.tribun.sync_members()
        self.assertEqual(len([m for m in self.owner_msgs() if "покинул" in m]), 1)
        self.bot.members = {1: user(1, "Алексей")}
        await self.tribun.sync_members()
        await self.tribun.sync_members()
        self.assertEqual(len([m for m in self.owner_msgs() if "вернулся" in m]), 1)
        self.assertTrue(self.profile(1)["onboarding_completed"])


class AdminScreens(OwnerBase):
    async def test_5_member_card_shows_current_data_view_only(self):
        await CompletionCard.finish_profile(self)
        await self.press(OWNER, "adm:mem:0")
        self.assertIn(("1. Алексей", "adm:mc:1"), self.buttons())
        await self.press(OWNER, "adm:mc:1")
        text = self.last_text()
        for part in ("👤 Алексей", "✅ В группе", "✅ Настроен", "🏅 Виды спорта:\n⚽ Футбол, 🏒 Хоккей", "🏆 Чемпионаты:\nКХЛ, Ла Лига",
                     "❤️ Клубы:\nАвтомобилист, Реал Мадрид", "Дата вступления:\n03.10.2026 09:00", "Последнее изменение профиля:\n03.10.2026 09:00"):
            self.assertIn(part, text)
        for gone in ("Контент", "Уведомления", "Спортсмены", "Команды"):
            self.assertNotIn(gone, text)
        self.assertNotIn("✏️", str(self.buttons()))                                # никаких кнопок редактирования чужих предпочтений
        self.clock.dt += datetime.timedelta(hours=5)
        await self.press(1, "c:cp")
        await self.press(1, "t:cp:fnl1")
        await self.press(1, "c:cl")
        await self.press(1, "t:cl:real")
        await self.press(1, "t:cl:ural")
        await self.press(OWNER, "adm:mc:1")
        text = self.last_text()
        self.assertIn("❤️ Клубы:\nАвтомобилист, Урал", text)
        self.assertIn("Последнее изменение профиля:\n03.10.2026 14:00", text)
        self.assertNotIn("Реал", text)

    async def test_card_for_left_and_unconfigured_and_unknown(self):
        await self.join(2, "Михаил")
        await self.press(OWNER, "adm:mc:2")
        self.assertIn("⏳ Не настроен", self.last_text())
        await self.tribun.on_user_removed(GROUP, user(2))
        await self.press(OWNER, "adm:mc:2")
        self.assertIn("🚪 Вышел из группы", self.last_text())
        await self.press(OWNER, "adm:mc:999")
        self.assertIn("Участник не найден", self.last_text())

    async def test_participants_list_counts_statuses_and_pagination(self):
        for i in range(1, 31):
            await self.join(i, f"Участник{i:02d}")
        for uid in range(1, 6):
            await self.onboard(uid)
        await self.tribun.on_user_removed(GROUP, user(30))
        await self.press(OWNER, "adm:mem:0")
        text = self.last_text()
        self.assertIn("Участников группы: 29", text)
        self.assertIn("✅ Настроили интересы: 5", text)
        self.assertIn("⏳ Не настроили: 24", text)
        self.assertEqual(len([l for l, p in self.buttons() if p.startswith("adm:mc:")]), 8)
        self.assertIn("Стр. 1 из 4", text)
        self.assertLess(len(text), 3000)
        self.assertIn("adm:mem:1", [p for _, p in self.buttons()])
        await self.press(OWNER, "adm:mem:3")
        self.assertIn("🚪 вышел", self.last_text())                              # ушедшие — в конце списка
        await self.press(OWNER, "adm:mem:99")                                    # страница за пределами — не падает
        self.assertIn("Стр. 4 из 4", self.last_text())

    async def test_6_interest_screen_exact_shape_from_8_participants(self):
        spec = [("hockey", "khl", "avtomobilist")] * 5 + [("football", "laliga", "real")] * 2 + [("football", "apl", "arsenal")]
        for i, (sport, comp, club) in enumerate(spec, 1):
            await self.join(i, f"У{i}")
            await self.onboard(i, sports=(sport,), comps=(comp,), clubs=(club,))
        await self.join(9, "БезПрофиля")                                         # без профиля: в числе участников, фиктивных интересов нет
        await self.press(OWNER, "adm:int")
        text = self.last_text()
        self.assertIn("Участников: 9", text)
        self.assertIn("Настроили профиль: 8", text)
        self.assertIn("🏒 Хоккей — 5\n⚽ Футбол — 3", text)
        self.assertIn("🏆 Чемпионаты:\nКХЛ — 5\nЛа Лига — 2\nАПЛ — 1", text)
        self.assertIn("❤️ Клубы:\nАвтомобилист — 5\nРеал Мадрид — 2\nАрсенал — 1", text)
        self.assertNotIn("БезПрофиля", text)
        for gone in ("Спортсмены", "Контент", "из 9"):
            self.assertNotIn(gone, text)

    async def test_7_aggregate_recalculates_after_each_change(self):
        await self.join(1, "А")
        await self.onboard(1, sports=("hockey",), comps=("khl",), clubs=("avtomobilist",))
        await self.press(OWNER, "adm:int")
        self.assertIn("🏒 Хоккей — 1", self.last_text())
        await self.press(1, "c:sp")
        await self.press(1, "t:sp:football")
        await self.press(OWNER, "adm:int")
        self.assertIn("⚽ Футбол — 1", self.last_text())
        await self.join(2, "Б")
        await self.press(OWNER, "adm:int")
        self.assertIn("Участников: 2", self.last_text())
        await self.tribun.on_user_removed(GROUP, user(1))
        await self.press(OWNER, "adm:int")
        self.assertIn("Участников: 1", self.last_text())
        self.assertNotIn("Хоккей", self.last_text())
        await self.join(1, "А")
        await self.press(OWNER, "adm:int")
        self.assertIn("🏒 Хоккей — 1", self.last_text())

    async def test_editorial_service_returns_high_interest_sets(self):
        for i in range(1, 6):
            await self.join(i, f"У{i}")
            if i <= 3:
                await self.onboard(i, sports=("hockey",), comps=("khl",), clubs=("avtomobilist",))
            else:
                await self.onboard(i, sports=("football",), comps=("laliga",), clubs=("real",))
        await self.join(6, "Без")
        high = self.tribun.high_interest()
        self.assertEqual(high, {"sports": ["🏒 Хоккей"], "championships": ["КХЛ"], "clubs": ["Автомобилист"]})      # 3 из 6 = 50%
        ed = self.tribun.editorial_summary(self.tribun.load_members())
        self.assertEqual(ed["medium"], {"sports": ["⚽ Футбол"], "championships": ["Ла Лига"], "clubs": ["Реал Мадрид"]})   # 2 из 6 = 33%
        self.assertEqual(ed["niche"], {"sports": [], "championships": [], "clubs": []})

    async def test_notification_settings_default_and_toggle(self):
        self.assertEqual({k: v for k, v in self.tribun.owner_settings().items()},
                         {"new_member": True, "profile_completed": True, "left": True, "returned": True,
                          "interest_changes": False, "clicks": False, "menu_opens": False})
        await self.press(OWNER, "adm:ns")
        self.assertIn("✅ Новый участник", self.last_text())
        self.assertIn("❌ Каждое изменение интересов (не присылается)", self.last_text())
        await self.press(OWNER, "adm:nt:new_member")
        self.assertFalse(self.tribun.owner_settings()["new_member"])
        await self.join(1, "Алексей")
        self.assertEqual(self.owner_msgs(), [])                                  # выключено — не присылаем
        await self.tribun.flush_owner_notifications()
        self.assertEqual(self.owner_msgs(), [])
        await self.press(OWNER, "adm:nt:clicks")                                 # неизменяемые остаются выключенными
        self.assertFalse(self.tribun.owner_settings()["clicks"])
        with self.assertRaises(ValueError):
            self.tribun.set_owner_setting("menu_opens", True)
        self.make()                                                               # перезапуск: настройка сохранилась
        self.assertFalse(self.tribun.owner_settings()["new_member"])

    async def test_clicks_and_menu_opens_never_notify_owner(self):
        await self.join(1, "Алексей")
        self.bot.sent.clear()
        for payload in ("m", "c:sp", "c:cp", "c:cl", "t:sp:hockey", "o:sp", "rt:sp:zzz", "sv"):
            await self.press(1, payload)
        await self.say(1, "привет")
        self.assertEqual(self.owner_msgs(), [])


class AccessControl(OwnerBase):
    ADMIN_PAYLOADS = ("adm", "adm:int", "adm:mem:0", "adm:mc:2", "adm:ns", "adm:nt:new_member", "adm:nt:left", "adm:auto", "adm:state",
                      "adm:rq", "adm:rqd:cp:12345678", "adm:src", "adm:src:cov", "adm:src:all", "adm:src:prob", "adm:src:gap",
                      "adm:api", "adm:api:d", "adm:api:m", "adm:api:ai", "adm:api:api", "adm:api:pur", "adm:api:err", "pub:list", "pr:list", "inv")

    async def test_8_member_gets_access_denied_for_every_owner_screen(self):
        await self.join(1, "Алексей")
        await self.join(2, "Другой")
        await CompletionCard.finish_profile(self, 2, "Другой")
        before_settings = self.tribun.owner_settings()
        for payload in self.ADMIN_PAYLOADS:
            self.out = []
            await self.press(1, payload)
            self.assertEqual(self.out, [], payload)                                 # ни экрана, ни данных
        self.assertEqual(self.tribun.owner_settings(), before_settings)
        self.assertNotIn("Другой", str(self.out))

    async def test_9_handler_level_owner_check_even_if_dispatch_is_bypassed(self):
        await self.join(1, "Алексей")
        await self.join(2, "Другой")
        for head, parts in (("adm", ["adm", "mc", "2"]), ("adm", ["adm", "int"]), ("adm", ["adm", "mem", "0"]), ("adm", ["adm", "nt", "left"]),
                            ("pub", ["pub", "new"]), ("pr", ["pr", "add"])):
            ctx = self.ctx(1)                                                       # обычный участник, прямой вызов обработчика
            await self.tribun.route_admin(ctx, head, parts)
            self.assertEqual(self.out, [], parts)
        self.assertTrue(self.tribun.owner_settings()["left"])

    async def test_member_cannot_see_owner_data_through_text_either(self):
        await self.join(1, "Алексей")
        await self.say(1, "/members")
        await self.say(1, "участники")
        self.assertNotIn("Участников группы", "".join(m[1] for m in self.bot.sent if m[0] != GROUP and m[0] != OWNER))

    async def test_owner_screens_only_in_private_chat(self):
        await self.join(1, "Алексей")
        self.out = []
        await self.press(OWNER, "adm:mem:0", private=False)
        self.assertEqual(self.out, [])
        await self.press(OWNER, "adm:int", private=False)
        self.assertEqual(self.out, [])


class RestartAndPrivacy(OwnerBase):
    async def test_10_restart_keeps_participants_profiles_and_notification_state(self):
        await CompletionCard.finish_profile(self)
        await self.join(2, "Мария")
        self.tribun.set_owner_setting("left", False)
        snapshot = self.tribun.load_members()["members"]
        sent_before = len(self.bot.sent)
        self.make()                                                                 # рестарт / deploy
        self.assertEqual(self.tribun.load_members()["members"], snapshot)
        self.assertFalse(self.tribun.owner_settings()["left"])
        self.assertTrue(self.tribun.load_members()["members"]["1"]["join_notified"] and self.tribun.load_members()["members"]["1"]["completion_notified"])
        await self.tribun.flush_owner_notifications()
        self.assertEqual(len(self.bot.sent), sent_before)                           # ничего не продублировано
        await self.press(OWNER, "adm:mem:0")
        self.assertIn("Участников группы: 2", self.last_text())
        self.assertEqual(await self.join(1, "Алексей"), "duplicate")

    async def test_9_privacy_nothing_personal_ever_reaches_the_group(self):
        await CompletionCard.finish_profile(self)
        await self.tribun.on_user_removed(GROUP, user(1))
        await self.join(1, "Алексей")
        await self.press(1, "c:cl")
        await self.press(1, "t:cl:ural")
        for text in [m[1] for m in self.bot.group_log]:
            for secret in ("Автомобилист", "Реал", "Урал", "КХЛ", "Ла Лига", "выбрал", "болеет", "настроил"):
                self.assertNotIn(secret, text, text)
        self.assertEqual(len(self.bot.group_log), 1)                                # только приветствие при первом входе

    async def test_owner_sees_himself_as_current_participant(self):
        self.names[OWNER] = "Дмитрий"
        self.bot.members = {OWNER: user(OWNER, "Дмитрий")}
        await self.tribun.sync_members()
        await self.press(OWNER, "adm:mem:0")
        self.assertIn("Участников группы: 1", self.last_text())
        self.assertIn("1. Дмитрий", self.last_text())
        self.assertEqual(self.owner_msgs(), [])


if __name__ == "__main__":
    unittest.main()
