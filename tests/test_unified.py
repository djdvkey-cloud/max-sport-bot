"""ЕДИНЫЙ контур матчей: постоянные клубы владельца + выбор участников — один pipeline; результат публикуется только при доказанном «завершён»;
регрессии по инцидентам 04.10.2026 (Норильск — Синара 5:1, Автомобилист — Амур 0:3 вместо 0:2, тестовый Зенит в афише); восстановление пропущенного.
Страницы источников — реальные слепки (tests/fixtures/feed) и синтетические страницы в той же вёрстке; в интернет и MAX тесты не ходят."""
import asyncio
import datetime
import json
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_sport_v4 as TS  # noqa: E402
from test_sport_v4 import S, ekb, utc, sportsru_page  # noqa: E402
from test_dynamic import DynBase, FakeFetch, all_routes, fx, profile, QUIET_PAGE  # noqa: E402
import dynamic as D  # noqa: E402
import feed as F  # noqa: E402
import matchfeed as M  # noqa: E402

HOCKEY_FINAL = fx("hockey_2026-10-04_final.txt")                               # реальная страница sports.ru 04.10.2026 (КХЛ + NHL, всё завершено)
SUPERLIGA_REAL = fx("superliga_main_2026-10-06.txt")                           # реальная страница superliga.rfs.ru от 06.10.2026
QUIET_HOCKEY = "hockey\nМоя лента\nМатчи\nВсе матчи\nМой выбор"
AMUR_FINAL_RE = re.compile(r"Завершен(\s+)Автомобилист(\s+)0(\s+):(\s+)3(\s+)Амур")


def hockey_live(score_a=2, period="3 период"):
    """Та же реальная страница, но матч Автомобилист — Амур ещё ИДЁТ: «3 период», промежуточный счёт (так выглядел 04.10 в 13:30 UTC)."""
    page, n = AMUR_FINAL_RE.subn(lambda m: f"{period}{m.group(1)}Автомобилист{m.group(2)}0{m.group(3)}:{m.group(4)}{score_a}{m.group(5)}Амур", HOCKEY_FINAL)
    assert n == 1
    return page


def club_fixture(rows):
    return sportsru_page(rows)


class Unified(DynBase):
    """Единый контур: постоянные клубы владельца — subset `baseline`; профили участников — self.profiles."""

    def setUp(self):
        super().setUp()
        self.feed.now = lambda: utc(2026, 10, 4, 14, 0)
        for day in ("2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05", "2026-10-06"):             # «тихие» сутки по умолчанию: источник жив, матчей нет
            self.fetch.set(f"/hockey/match/{day}/", (200, QUIET_HOCKEY))
            self.fetch.set(f"/football/match/{day}/", (200, "football\nМоя лента\nМатчи\nВсе матчи\nМой выбор"))
        self.fetch.set("superliga.rfs.ru", (200, SUPERLIGA_REAL))
        self.fetch.set("api-web.nhle.com", (200, json.dumps({"gameWeek": []})))
        for slug in F.BASELINE_PAGES.values():
            self.fetch.set(f"/football/club/{slug}/", (200, QUIET_PAGE))
        self.feed.clear_cache()

    def dyn_for(self, *clubs):
        d = D.Dynamic(S, feed=self.feed, profiles=lambda: self.profiles, baseline=clubs)
        self.dyn = d
        self._patch(S, "DYNAMIC", d)
        return d

    def state(self):
        return self.dyn.load_state()

    def recs(self):
        return self.state()["matches"]

    async def tick_results(self, *times):
        for t in times:
            await self.dyn.results(self.bot, t)

    def only_rec(self):
        recs = list(self.recs().values())
        self.assertEqual(len(recs), 1, recs)
        return recs[0]


# ======================================================================================== A. Автомобилист — Амур: LIVE 0:2 → FINAL 0:3

class HockeyLiveNeverPublished(Unified):
    START = utc(2026, 10, 4, 11, 30)                                          # 14:30 мск

    async def test_A_live_0_2_is_not_published_and_final_0_3_is_published_once(self):
        self.dyn_for("avtomobilist")
        self.route("/hockey/match/2026-10-04/", (200, hockey_live(2)))
        now = utc(2026, 10, 4, 13, 31)                                         # ровно в минуту, когда 04.10 ушёл «0:2»
        self.assertEqual(await self.dyn.recover(self.bot, now, force=True), 1)
        await self.tick_results(now, now + datetime.timedelta(minutes=20))
        self.assertEqual(self.bot.group, [])                                    # LIVE 0:2 никогда не итог
        rec = self.only_rec()
        self.assertEqual((rec["phase"], rec["score"], rec["final_confirmed"], rec["result_text"]), ("LIVE", "0:2", False, None))
        self.route("/hockey/match/2026-10-04/", (200, HOCKEY_FINAL))            # матч закончился 0:3 (источник написал «Завершен»)
        await self.tick_results(utc(2026, 10, 4, 14, 1))
        self.assertEqual(len(self.bot.group), 1)
        self.assertIn("0:3", self.bot.group[0])
        self.assertNotIn("0:2", self.bot.group[0])
        self.assertIn("проиграли", self.bot.group[0])
        await self.tick_results(utc(2026, 10, 4, 14, 21), utc(2026, 10, 4, 14, 41))
        await self.dyn.recover(self.bot, utc(2026, 10, 4, 15, 30), force=True)
        await self.tick_results(utc(2026, 10, 4, 15, 31))
        self.assertEqual(len(self.bot.group), 1)                                # один раз
        rec = self.only_rec()
        self.assertEqual((rec["status"], rec["phase"], rec["score"], rec["final_confirmed"]), ("published", "FINAL", "0:3", True))

    async def test_live_period_markers_are_never_final_even_with_a_score(self):
        for token in ("1 период", "2 период", "3 период", "Перерыв между периодами", "Овертайм", "Буллиты"):
            page = hockey_live(2, token)
            matches, _ = M.parse_sportsru_day("hockey", page, datetime.date(2026, 10, 4), source_url="x", retrieved_at=utc(2026, 10, 4, 13, 31))
            amur = [m for m in matches if m["home_team_id"] == "avtomobilist"][0]
            self.assertEqual(amur["status"], M.LIVE, token)
            self.assertFalse(amur["final_explicit"], token)

    async def test_score_without_any_status_token_is_not_final(self):
        """Раньше запись без слова-статуса со счётом считалась «завершён» — так промежуточный счёт мог стать итогом."""
        page = AMUR_FINAL_RE.sub(lambda m: f"Автомобилист{m.group(2)}0{m.group(3)}:{m.group(4)}2{m.group(5)}Амур", HOCKEY_FINAL)
        matches, _ = M.parse_sportsru_day("hockey", page, datetime.date(2026, 10, 4), source_url="x", retrieved_at=utc(2026, 10, 4, 13, 31))
        found = [m for m in matches if m["home_team_id"] == "avtomobilist" and m["away_team_id"] == "amur"]
        self.assertTrue(all(m["status"] != M.FINISHED for m in found))

    async def test_real_page_final_is_explicit(self):
        matches, _ = M.parse_sportsru_day("hockey", HOCKEY_FINAL, datetime.date(2026, 10, 4), source_url="x", retrieved_at=utc(2026, 10, 4, 15))
        amur = [m for m in matches if m["home_team_id"] == "avtomobilist"][0]
        self.assertEqual((amur["status"], amur["score_home"], amur["score_away"], amur["final_explicit"], amur["kickoff"]),
                         (M.FINISHED, 0, 3, True, utc(2026, 10, 4, 11, 30)))


# ======================================================================================== B. Норильск — Синара: два матча, два match_id

class FutsalTwoMatchesTwoLifecycles(Unified):
    async def test_B_two_matches_of_the_same_pair_are_two_independent_lifecycles(self):
        self.dyn_for("sinara")
        self.feed.now = lambda: utc(2026, 10, 6, 10)
        self.route("superliga.rfs.ru", (200, SUPERLIGA_REAL))
        t1 = utc(2026, 10, 4, 14, 5)                                            # источник без явного статуса: нужен устойчивый счёт
        self.assertEqual(await self.dyn.recover(self.bot, t1, force=True), 2)
        ids = sorted(self.recs())
        self.assertEqual(ids, ["dyn|superliga|2026-10-03|norilsk|sinara", "dyn|superliga|2026-10-04|norilsk|sinara"])      # два матча — два match_id
        await self.tick_results(t1)
        self.assertEqual(self.bot.group, [])                                    # первое наблюдение: итог ещё не подтверждён
        await self.tick_results(t1 + datetime.timedelta(minutes=20))
        self.assertEqual(len(self.bot.group), 2)
        joined = "\n".join(self.bot.group)
        self.assertIn("2:3", joined)                                            # 03.10 — «Норильск 2:3 Синара» (победа Синары)
        self.assertIn("5:1", joined)                                            # 04.10 — «Норильск 5:1 Синара» (поражение)
        recs = self.recs()
        self.assertEqual({r["status"] for r in recs.values()}, {"published"})
        self.assertEqual({r["score"] for r in recs.values()}, {"2:3", "5:1"})
        self.assertNotEqual(recs["dyn|superliga|2026-10-03|norilsk|sinara"]["result_text"], recs["dyn|superliga|2026-10-04|norilsk|sinara"]["result_text"])
        await self.tick_results(t1 + datetime.timedelta(minutes=40))
        self.assertEqual(len(self.bot.group), 2)

    async def test_the_second_match_is_not_blocked_by_the_first(self):
        """Раньше результат 04.10 застревал на «конфликте» с чтением соседнего матча той же пары; теперь записи независимы."""
        self.dyn_for("sinara")
        self.feed.now = lambda: utc(2026, 10, 6, 10)
        only_second = SUPERLIGA_REAL                                            # 03.10 уже опубликован «прежним контуром»
        self.put(self.rec("sinara", utc(2026, 10, 3, 8, 0), rival="Норильск", status="published", day=datetime.date(2026, 10, 3), result_sent=True))
        t1 = utc(2026, 10, 4, 14, 5)
        await self.dyn.recover(self.bot, t1, force=True)
        self.assertEqual(list(self.recs()), ["dyn|superliga|2026-10-04|norilsk|sinara"])
        await self.tick_results(t1, t1 + datetime.timedelta(minutes=20))
        self.assertEqual(len(self.bot.group), 1)
        self.assertIn("5:1", self.bot.group[0])
        self.assertIsNone(self.only_rec()["conflict"])

    async def test_futsal_result_waits_for_the_latest_possible_end_of_the_match(self):
        """Часовой пояс superliga.rfs.ru неоднозначен (Москва / Екатеринбург): «матч точно закончился» считается от самого позднего возможного начала."""
        self.dyn_for("sinara")
        self.feed.now = lambda: utc(2026, 10, 6, 10)
        t1 = utc(2026, 10, 4, 10, 30)                                           # ЕКБ-начало 08:00Z, МСК-начало 10:00Z: матч может ещё идти
        await self.dyn.recover(self.bot, t1, force=True)
        rec = self.recs()["dyn|superliga|2026-10-04|norilsk|sinara"]
        rec["start_utc"] = utc(2026, 10, 4, 8, 0).isoformat()
        st = self.state()
        st["matches"][rec["match_id"]] = rec
        self.dyn.save_state(st)
        await self.tick_results(t1, t1 + datetime.timedelta(minutes=20), t1 + datetime.timedelta(minutes=40), t1 + datetime.timedelta(minutes=60))
        self.assertEqual([t for t in self.bot.group if "5:1" in t], [])         # 10:00Z (МСК-начало) + 105 мин = 11:45Z — раньше нельзя
        await self.tick_results(utc(2026, 10, 4, 11, 40), utc(2026, 10, 4, 12, 0))
        self.assertEqual(len([t for t in self.bot.group if "5:1" in t]), 1)


# ======================================================================================== C/D. счёт без «завершён»; конфликт источников

class ScriptedFeed:
    """Подмена feed: заранее заданные батчи источников → merge_matches (как в боевом сборе) → матчи дня."""

    def __init__(self, batches):
        self.batches = batches
        self.calls = 0

    async def collect(self, day, *, competitions=None, clubs=None, pages=None):
        self.calls += 1
        merged = [m for m in F.merge_matches(self.batches) if m["day"] == day]
        return F.FeedResult(day, merged, [], set())

    def clear_cache(self):
        pass


def fm(status, sh, sa, *, src="sportsru:center:football", explicit=False, comp="apl", home="Арсенал", away="Лидс", hid="arsenal", aid="leeds",
       day=datetime.date(2026, 10, 10), kickoff=None, sport="football", method=None, latest=None):
    kickoff = kickoff or datetime.datetime(2026, 10, 10, 11, 30, tzinfo=datetime.timezone.utc)
    return M.make_match(sport=sport, competition=comp, season="2026/27", home=home, away=away, kickoff=kickoff, tz="Europe/Moscow", status=status,
                        score_home=sh, score_away=sa, method=method, source_id=src, source_url="x", retrieved_at=kickoff, home_id=hid, away_id=aid,
                        day=day, explicit=explicit, kickoff_latest=latest)


class FinalityRules(Unified):
    def setup_dyn(self, batches, baseline=("arsenal",)):
        dyn = D.Dynamic(S, feed=ScriptedFeed(batches), profiles=lambda: [], baseline=baseline)
        self.dyn = dyn
        self._patch(S, "DYNAMIC", dyn)
        return dyn

    async def seed_and_check(self, dyn, times):
        m = (await dyn.feed.collect(datetime.date(2026, 10, 10)))
        match = m.matches[0]
        rec = dyn.new_record(match, dyn.describe(match, {"arsenal"}), "")
        rec.update({"start_utc": utc(2026, 10, 10, 11, 30).isoformat(), "status": "announced", "announce_sent": True, "announce_text": None})
        st = dyn.load_state()
        st["matches"][rec["match_id"]] = rec
        dyn.save_state(st)
        for t in times:
            await dyn.results(self.bot, t)
        return dyn.load_state()["matches"][rec["match_id"]]

    async def test_C_score_without_finished_status_is_never_published(self):
        dyn = self.setup_dyn([[fm(M.LIVE, 2, 0)]])
        rec = await self.seed_and_check(dyn, [utc(2026, 10, 10, 13, 40), utc(2026, 10, 10, 14, 0), utc(2026, 10, 10, 15, 0)])
        self.assertEqual(self.bot.group, [])
        self.assertEqual((rec["phase"], rec["score"], rec["result_text"]), ("LIVE", "2:0", None))

    async def test_C2_finished_without_explicit_status_needs_a_stable_score(self):
        dyn = self.setup_dyn([[fm(M.FINISHED, 2, 0, src="sportsru:club:football")]])           # страница клуба: счёт есть, слова «завершён» нет
        rec = await self.seed_and_check(dyn, [utc(2026, 10, 10, 13, 40)])
        self.assertEqual(self.bot.group, [])                                    # первое наблюдение
        await dyn.results(self.bot, utc(2026, 10, 10, 13, 50))
        self.assertEqual(self.bot.group, [])                                    # прошло меньше 15 минут
        await dyn.results(self.bot, utc(2026, 10, 10, 14, 0))
        self.assertEqual(len(self.bot.group), 1)
        self.assertIn("2:0", self.bot.group[0])

    async def test_C3_changing_score_resets_the_stability_clock(self):
        batches = [[fm(M.FINISHED, 1, 0, src="sportsru:club:football")]]
        dyn = self.setup_dyn(batches)
        await self.seed_and_check(dyn, [utc(2026, 10, 10, 13, 40), utc(2026, 10, 10, 14, 0)])
        self.assertEqual(len(self.bot.group), 1)                                # (1:0 было устойчиво) — публикуется
        self.bot.group.clear()
        dyn2 = self.setup_dyn([[fm(M.FINISHED, 1, 0, src="sportsru:club:football")]])
        dyn2.state_path and os.path.exists(dyn2.state_path) and os.unlink(dyn2.state_path)
        rec = await self.seed_and_check(dyn2, [utc(2026, 10, 10, 13, 40)])
        dyn2.feed.batches = [[fm(M.FINISHED, 2, 0, src="sportsru:club:football")]]          # пока шла проверка, счёт изменился
        await dyn2.results(self.bot, utc(2026, 10, 10, 14, 0))
        self.assertEqual(self.bot.group, [])
        self.assertEqual(list(dyn2.load_state()["matches"].values())[0]["obs"]["score"], "2:0")
        await dyn2.results(self.bot, utc(2026, 10, 10, 14, 20))
        self.assertEqual(len(self.bot.group), 1)
        self.assertIn("2:0", self.bot.group[0])

    async def test_D_live_source_and_final_source_never_publish_the_live_score(self):
        official = fm(M.FINISHED, 0, 3, src="nhl:api", explicit=True, comp="nhl", home="Автомобилист", away="Амур", hid="avtomobilist", aid="amur",
                      sport="hockey", day=datetime.date(2026, 10, 10))
        live = fm(M.LIVE, 0, 2, src="sportsru:center:hockey", comp="nhl", home="Автомобилист", away="Амур", hid="avtomobilist", aid="amur",
                  sport="hockey", day=datetime.date(2026, 10, 10))
        merged = F.merge_matches([[live], [official]])
        self.assertEqual(len(merged), 1)
        self.assertEqual((merged[0]["status"], merged[0]["score_home"], merged[0]["score_away"], merged[0]["final_confirmed"]), (M.FINISHED, 0, 3, True))
        self.assertEqual(F.merge_matches([[official], [live]])[0]["score_away"], 3)               # порядок источников не важен

    async def test_D4_explicit_final_beats_source_precedence_even_when_the_live_source_ranks_higher(self):
        live_top = fm(M.LIVE, 0, 2, src="nhl:api", comp="nhl", home="Автомобилист", away="Амур", hid="avtomobilist", aid="amur", sport="hockey")
        final_low = fm(M.FINISHED, 0, 3, src="sportsru:center:hockey", explicit=True, comp="nhl", home="Автомобилист", away="Амур", hid="avtomobilist",
                       aid="amur", sport="hockey")
        for batches in ([[live_top], [final_low]], [[final_low], [live_top]]):
            merged = F.merge_matches(batches)[0]
            self.assertEqual((merged["status"], merged["score_away"], merged["final_confirmed"]), (M.FINISHED, 3, True))

    async def test_D2_implicit_final_against_a_live_status_waits(self):
        live = fm(M.LIVE, 0, 2, src="sportsru:center:football")
        wiki = fm(M.FINISHED, 0, 3, src="wikipedia:ucl")
        merged = F.merge_matches([[live], [wiki]])[0]
        self.assertEqual(merged["status"], M.LIVE)                              # статус «идёт» главнее неявного «завершён»
        self.assertFalse(merged["final_confirmed"])
        dyn = self.setup_dyn([[live], [wiki]])
        rec = await self.seed_and_check(dyn, [utc(2026, 10, 10, 13, 40), utc(2026, 10, 10, 14, 10), utc(2026, 10, 10, 14, 40)])
        self.assertEqual((self.bot.group, rec["result_text"]), ([], None))

    async def test_D3_two_final_sources_with_different_scores_are_a_conflict(self):
        a = fm(M.FINISHED, 2, 1, explicit=True)
        b = fm(M.FINISHED, 2, 0, src="wikipedia:ucl")
        dyn = self.setup_dyn([[a], [b]])
        rec = await self.seed_and_check(dyn, [utc(2026, 10, 10, 13, 40)])
        self.assertEqual(self.bot.group, [])
        self.assertEqual(rec["phase"], "CONFLICT")
        await dyn.results(self.bot, utc(2026, 10, 10, 14, 45))                  # через час — одно предупреждение владельцу
        self.assertEqual(len([t for t in self.admin_texts() if "требует проверки" in t]), 1)
        self.assertEqual(self.bot.group, [])

    async def test_cancelled_match_is_closed_without_publication(self):
        dyn = self.setup_dyn([[fm(M.CANCELLED, None, None)]])
        rec = await self.seed_and_check(dyn, [utc(2026, 10, 10, 13, 40)])
        self.assertEqual((rec["status"], rec["phase"], self.bot.group), ("expired", "CANCELLED", []))


# ======================================================================================== E/F/G. рестарт, восстановление, дубли

class RestartAndRecovery(Unified):
    async def test_E_restart_between_the_match_and_the_result_keeps_the_lifecycle(self):
        dyn = self.dyn_for("avtomobilist")
        self.route("/hockey/match/2026-10-04/", (200, hockey_live(2)))
        now = utc(2026, 10, 4, 13, 31)
        await dyn.recover(self.bot, now, force=True)
        await dyn.results(self.bot, now)
        self.assertEqual(self.bot.group, [])
        again = D.Dynamic(S, feed=self.feed, profiles=lambda: [], baseline=("avtomobilist",))            # «рестарт процесса»: новый объект, состояние — файлы
        self._patch(S, "DYNAMIC", again)
        self.assertEqual(len(again.load_state()["matches"]), 1)
        self.route("/hockey/match/2026-10-04/", (200, HOCKEY_FINAL))
        await again.results(self.bot, utc(2026, 10, 4, 14, 1))
        self.assertEqual(len(self.bot.group), 1)
        again2 = D.Dynamic(S, feed=self.feed, profiles=lambda: [], baseline=("avtomobilist",))
        await again2.results(self.bot, utc(2026, 10, 4, 14, 21))
        await again2.recover(self.bot, utc(2026, 10, 4, 15, 0), force=True)
        await again2.results(self.bot, utc(2026, 10, 4, 15, 1))
        self.assertEqual(len(self.bot.group), 1)

    async def test_F_a_match_missed_at_first_detection_is_recovered_and_published(self):
        """Утром источник не отвечал / процесс перезапускался: записи матча нет. Позже матч найден recover() и доведён до публикации."""
        dyn = self.dyn_for("avtomobilist")
        self.assertEqual(self.recs(), {})
        self.route("/hockey/match/2026-10-04/", (200, HOCKEY_FINAL))
        added = await dyn.recover(self.bot, utc(2026, 10, 4, 18, 0), force=True)
        self.assertEqual(added, 1)
        rec = self.only_rec()
        self.assertEqual((rec["created_via"], rec["announce_sent"], rec["status"]), ("recovery", True, "awaiting_result"))
        await dyn.results(self.bot, utc(2026, 10, 4, 18, 1))
        self.assertEqual(len(self.bot.group), 1)
        self.assertIn("0:3", self.bot.group[0])
        self.assertEqual(self.only_rec()["status"], "published")

    async def test_F2_recovery_survives_a_temporary_source_failure(self):
        dyn = self.dyn_for("avtomobilist")
        self.route("/hockey/match/2026-10-04/", (503, "down"))
        self.assertEqual(await dyn.recover(self.bot, utc(2026, 10, 4, 15, 0), force=True), 0)
        self.assertEqual(self.recs(), {})
        self.route("/hockey/match/2026-10-04/", (200, HOCKEY_FINAL))                 # источник вернулся
        self.assertEqual(await dyn.recover(self.bot, utc(2026, 10, 4, 15, 40), force=True), 1)
        await dyn.results(self.bot, utc(2026, 10, 4, 15, 41))
        self.assertEqual(len(self.bot.group), 1)

    async def test_G_published_match_found_again_by_recovery_is_not_duplicated(self):
        dyn = self.dyn_for("avtomobilist")
        self.route("/hockey/match/2026-10-04/", (200, HOCKEY_FINAL))
        await dyn.recover(self.bot, utc(2026, 10, 4, 15, 0), force=True)
        await dyn.results(self.bot, utc(2026, 10, 4, 15, 1))
        self.assertEqual(len(self.bot.group), 1)
        st = dyn.load_state()
        for rec in st["matches"].values():                                           # запись давно удалена prune(), остался только published_ids
            pass
        st["matches"].clear()
        dyn.save_state(st)
        self.assertEqual(await dyn.recover(self.bot, utc(2026, 10, 5, 15, 0), force=True), 0)       # recover сам не заводит уже опубликованное (published_ids)
        self.assertEqual(self.recs(), {})
        await dyn.results(self.bot, utc(2026, 10, 5, 15, 1))
        self.assertEqual(len(self.bot.group), 1)

    async def test_recovery_is_limited_to_48_hours_and_to_started_matches(self):
        dyn = self.dyn_for("avtomobilist")
        self.route("/hockey/match/2026-10-04/", (200, HOCKEY_FINAL))
        self.assertEqual(await dyn.recover(self.bot, utc(2026, 10, 8, 15, 0), force=True), 0)       # 04.10 уже старше окна
        dyn2 = self.dyn_for("avtomobilist")
        future = "hockey\nМатчи\nВсе матчи\nFONBET Чемпионат КХЛ 2026/2027\n14:30\nНе начался\nАвтомобилист\nпревью\nАмур"
        self.route("/hockey/match/2026-10-04/", (200, future))
        self.assertEqual(await dyn2.recover(self.bot, utc(2026, 10, 4, 5, 0), force=True), 0)       # матч ещё не начался — анонс/афиша, не результат

    async def test_recovered_old_match_is_not_expired_by_the_36_hour_rule(self):
        """Матч, найденный восстановлением спустя >36 ч после начала, получает свои 12 часов с момента восстановления (иначе его бы сразу «закрыли»)."""
        self.dyn_for("sinara")
        self.feed.now = lambda: utc(2026, 10, 6, 10)
        t = utc(2026, 10, 5, 12, 0)                                                 # 03.10 08:00Z — это 52 часа назад
        self.assertEqual(await self.dyn.recover(self.bot, t, force=True), 2)
        await self.tick_results(t, t + datetime.timedelta(minutes=20))
        self.assertEqual(len(self.bot.group), 2)
        self.assertEqual({r["status"] for r in self.recs().values()}, {"published"})

    async def test_recovered_match_without_a_final_is_given_up_after_12_hours_once(self):
        self.dyn_for("avtomobilist")
        self.route("/hockey/match/2026-10-04/", (200, hockey_live(2)))              # источник «застрял» на LIVE
        t = utc(2026, 10, 4, 13, 31)
        await self.dyn.recover(self.bot, t, force=True)
        await self.tick_results(t, t + datetime.timedelta(hours=6))
        self.assertEqual(self.only_rec()["status"], "awaiting_result")
        await self.tick_results(t + datetime.timedelta(hours=12, minutes=5))
        self.assertEqual(self.only_rec()["status"], "expired")
        self.assertEqual(len([x for x in self.admin_texts() if "так и не получен" in x]), 1)
        self.assertEqual(self.bot.group, [])

    async def test_cup_matches_of_baseline_clubs_are_served_from_the_club_page(self):
        """Кубки и товарищеские постоянных клубов вне каталога (Кубок России Урала) читаются со страницы клуба тем же контуром: афиша, результат."""
        self.dyn_for("ural")
        self.fetch.set("/football/club/ural/", (200, club_fixture([
            ("04.10.2026", "15:00", "Россия. FONBET Кубок России", "Луки-Энергия", "В гостях", ["1 : 2", "–"]),
            ("09.10.2026", "15:00", "Товарищеские матчи (клубы)", "Челябинск", "Дома", ["превью", "–"]),
            ("10.10.2026", "12:00", "Россия. Первая лига", "Велес", "Дома", ["превью", "–"]),
        ])))
        self.feed.clear_cache()
        entries, _ = await self.dyn.weekly_entries(ekb(2026, 10, 5, 9).astimezone(TS.UTC), persist=False)
        self.assertEqual(sorted((e["date"], e["tournament"], e["rival"]) for e in entries),
                         [("2026-10-09", "Товарищеские матчи (клубы)", "Челябинск"), ("2026-10-10", "Первая лига", "Велес")])
        t = utc(2026, 10, 4, 18, 0)
        self.assertEqual(await self.dyn.recover(self.bot, t, force=True), 1)
        rec = self.only_rec()
        self.assertTrue(rec["competition"].startswith(M.PSEUDO_PREFIX))
        self.assertEqual(rec["tournament"], "Россия. FONBET Кубок России")
        await self.tick_results(t, t + datetime.timedelta(minutes=20))                       # страница клуба без слова «завершён» — устойчивый счёт
        self.assertEqual(len(self.bot.group), 1)
        self.assertIn("Луки-Энергия 1:2 Урал", self.bot.group[0])                         # хозяева : гости — гости выиграли
        self.assertIn("ПОБЕДА", self.bot.group[0])
        self.assertIn("Урал", self.bot.group[0])

    async def test_recovery_runs_at_most_every_30_minutes_unless_forced(self):
        dyn = self.dyn_for("avtomobilist")
        with mock.patch.object(self.feed, "collect", wraps=self.feed.collect) as spy:
            await dyn.recover(self.bot, utc(2026, 10, 4, 15, 0))
            first = spy.call_count
            self.assertGreater(first, 0)
            await dyn.recover(self.bot, utc(2026, 10, 4, 15, 10))
            self.assertEqual(spy.call_count, first)                                  # меньше 30 минут — источники не опрашиваются
            await dyn.recover(self.bot, utc(2026, 10, 4, 15, 40))
            self.assertGreater(spy.call_count, first)


# ======================================================================================== переход со старого контура

class LegacyTransition(Unified):
    async def test_incident_matches_are_not_republished_after_the_switch(self):
        """04.10: Автомобилист — Амур опубликован как 0:2, Синара — Норильск закрыта (expired). Их исправление — отдельное решение владельца."""
        self.dyn_for("avtomobilist", "sinara")
        self.feed.now = lambda: utc(2026, 10, 6, 10)
        self.route("/hockey/match/2026-10-04/", (200, HOCKEY_FINAL))
        a = self.rec("avtomobilist", utc(2026, 10, 4, 11, 30), rival="Амур", status="published", day=datetime.date(2026, 10, 4), result_sent=True,
                     result_text="😔 Увы, сегодня проиграли\n\n🏒 ХК «Автомобилист» 0:2 Амур")
        b = self.rec("sinara", utc(2026, 10, 4, 10, 0), rival="Норильск", status="expired", day=datetime.date(2026, 10, 4))
        self.put(a, b)
        self.assertEqual(await self.dyn.recover(self.bot, utc(2026, 10, 6, 13, 30), force=True), 0)       # оба матча закрыты прежним контуром — заново не заводятся
        for t in range(6):
            await self.dyn.results(self.bot, utc(2026, 10, 6, 13, 31) + datetime.timedelta(minutes=20 * t))
        texts = "\n".join(self.bot.group)
        self.assertNotIn("0:3", texts)
        self.assertNotIn("5:1", texts)
        self.assertEqual(self.dyn.legacy_closed(self.dyn.legacy_for(M.make_match(
            sport="hockey", competition="khl", season="2026/27", home="Автомобилист", away="Амур", kickoff=utc(2026, 10, 4, 11, 30), tz="x", status=M.FINISHED,
            score_home=0, score_away=3, source_id="s", source_url="x", retrieved_at=utc(2026, 10, 4, 15), home_id="avtomobilist", away_id="amur"))), True)

    async def test_pending_legacy_match_is_continued_without_a_second_announcement(self):
        """Матч, анонсированный старым контуром и ещё не доигранный, доводится до результата единым контуром; повторного анонса нет."""
        self.dyn_for("avtomobilist")
        self.route("/hockey/match/2026-10-06/", (200, hockey_live(2).replace("2026", "2026")))
        legacy = self.rec("avtomobilist", utc(2026, 10, 6, 14, 0), rival="Авангард", status="announced", day=datetime.date(2026, 10, 6), announce_sent=True)
        self.put(legacy)
        # источник: Автомобилист — Авангард 17:00 мск, идёт (синтетическая страница в вёрстке sports.ru)
        page = "hockey\nМатчи\nВсе матчи\nFONBET Чемпионат КХЛ 2026/2027\n17:00\n3 период\nАвтомобилист\n1\n:\n0\nАвангард"
        self.route("/hockey/match/2026-10-06/", (200, page))
        await self.dyn.recover(self.bot, utc(2026, 10, 6, 16, 0), force=True)
        rec = self.only_rec()
        self.assertTrue(rec["announce_sent"])
        await self.dyn.morning(self.bot, ekb(2026, 10, 6, 10).astimezone(TS.UTC))
        self.assertEqual(self.bot.group, [])                                      # ни анонса, ни результата, пока матч идёт
        done = page.replace("3 период", "Завершен")
        self.route("/hockey/match/2026-10-06/", (200, done))
        await self.dyn.results(self.bot, utc(2026, 10, 6, 16, 5))
        self.assertEqual(len(self.bot.group), 1)
        self.assertIn("1:0", self.bot.group[0])
        self.assertIn("ПОБЕДА", self.bot.group[0])


# ======================================================================================== H/I. ACTIVE CLUBS: постоянные + участники

class ActiveClubsUnion(Unified):
    def test_H_baseline_six_always_and_member_zenit_without_deploy(self):
        dyn = D.Dynamic(S, feed=self.feed, profiles=lambda: self.profiles)                                  # постоянные клубы = S.CLUBS
        self.profiles = []
        base = {"avtomobilist", "sinara", "ural", "real", "arsenal", "milan"}
        self.assertEqual(dyn.active()["dynamic"], base)
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        self.assertEqual(dyn.active()["dynamic"], base | {"zenit"})                                         # без деплоя
        self.profiles[0]["clubs"] = []                                                                      # участник снял Зенит
        self.assertEqual(dyn.active()["dynamic"], base)

    async def test_H2_removed_zenit_gets_no_new_matches_but_started_match_is_finished(self):
        self.profiles = [profile(["football"], ["rpl"], ["zenit"])]
        dyn = D.Dynamic(S, feed=self.feed, profiles=lambda: self.profiles, baseline=())
        self.dyn = dyn
        self._patch(S, "DYNAMIC", dyn)
        now = ekb(2026, 10, 10, 10).astimezone(TS.UTC)
        await dyn.morning(self.bot, now)                                                                    # Краснодар — Зенит 10.10 19:30 — анонсирован
        self.assertEqual(len(dyn.load_state()["matches"]), 1)
        self.profiles[0]["clubs"] = []                                                                      # участник снял Зенит
        entries, _ = await dyn.weekly_entries(ekb(2026, 10, 12, 9).astimezone(TS.UTC), persist=False)
        self.assertEqual([e for e in entries if e["club_key"] == "zenit"], [])                              # новые матчи — не попадают
        ids = list(dyn.load_state()["matches"])
        self.assertEqual(len(ids), 1)                                                                       # уже анонсированный матч остаётся в хранилище и доводится до результата
        self.assertNotIn(dyn.load_state()["matches"][ids[0]]["status"], D.CLOSED)

    async def test_I_zenit_is_not_in_the_weekly_when_nobody_chose_it(self):
        self.profiles = [profile(["football"], ["rpl"], [], uid=1)]                                         # участники есть, Зенита ни у кого нет
        dyn = D.Dynamic(S, feed=self.feed, profiles=lambda: self.profiles)
        for slug in F.BASELINE_PAGES.values():
            self.fetch.set(f"/football/club/{slug}/", (200, QUIET_PAGE))
        self.feed.clear_cache()
        entries, failed = await dyn.weekly_entries(ekb(2026, 10, 5, 9).astimezone(TS.UTC), persist=False)
        text = S.format_weekly(entries) or ""
        self.assertNotIn("Зенит", text)
        self.assertNotIn("zenit", {e["club_key"] for e in entries})
        self.profiles[0]["clubs"] = ["zenit"]                                                               # а если выбран — появляется сам
        entries, _ = await dyn.weekly_entries(ekb(2026, 10, 5, 9).astimezone(TS.UTC), persist=False)
        self.assertIn("Зенит", S.format_weekly(entries))


# ======================================================================================== AI не в критическом пути

class NoAiInCriticalPath(Unified):
    async def test_full_tick_works_without_ai_keys_and_never_calls_ai_or_tavily(self):
        for name in ("DEEPSEEK_API_KEY", "TAVILY_API_KEY", "OPENAI_API_KEY"):
            self._patch(S, name, "")
        calls = []

        async def forbidden(*a, **k):
            calls.append(1)
            raise AssertionError("ИИ/Tavily вызван в критическом пути")
        for name in ("deepseek_completion", "ask_deepseek", "ask_deepseek_with_context", "collect_web_context_tavily", "extract_urls_tavily",
                     "ask_openai_websearch", "fetch_result_openai", "fetch_result_football", "fetch_result_hockey", "check_morning"):
            self._patch(S, name, forbidden)
        self.dyn_for("avtomobilist")
        self.route("/hockey/match/2026-10-04/", (200, HOCKEY_FINAL))
        now = ekb(2026, 10, 4, 19, 30)
        self._patch(S, "utc_now", lambda: now.astimezone(TS.UTC))

        class Stop(Exception):
            pass

        async def stop(seconds):
            raise Stop()
        self._patch(S.asyncio, "sleep", stop)
        with self.assertRaises(Stop):
            await S.scheduler_loop(self.bot)
        self.assertEqual(calls, [])
        self.assertEqual(len(self.bot.group), 1)                                                          # результат пришёл из детерминированного источника
        self.assertIn("0:3", self.bot.group[0])

    def test_scheduler_does_not_reference_the_old_ai_pipeline(self):
        import ast
        import inspect
        import textwrap
        names = set()
        for fn in (S.scheduler_loop, S.job_weekly, S.seconds_until_next_event):
            for node in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(fn)))):
                if isinstance(node, ast.Name):
                    names.add(node.id)
                elif isinstance(node, ast.Attribute):
                    names.add(node.attr)
        forbidden = {"job_morning", "job_check_results", "check_morning", "resolve_result", "fetch_result_football", "fetch_result_hockey",
                     "fetch_result_openai", "ask_deepseek", "ask_deepseek_with_context", "deepseek_completion", "collect_web_context_tavily",
                     "ask_openai_websearch", "retry_failed_morning", "retry_unsent_announcements", "calendar_reading", "publish_result", "load_state"}
        self.assertEqual(names & forbidden, set())

    async def test_single_publication_path_for_results(self):
        """Результат матча публикуется только через Dynamic.publish: у прежнего контура нет вызовов из планировщика."""
        import inspect
        self.assertEqual(inspect.getsource(S.scheduler_loop).count("results("), 1)
        self.assertNotIn("publish_result", inspect.getsource(S.scheduler_loop))


# ======================================================================================== недельная афиша единого контура

class WeeklyUnified(Unified):
    async def test_weekly_for_baseline_is_independent_of_member_profiles(self):
        d = self.dyn_for("ural", "arsenal")
        week = ekb(2026, 10, 5, 9).astimezone(TS.UTC)
        self.fetch.set("/football/club/ural/", (200, club_fixture([("10.10.2026", "12:00", "Россия. Первая лига", "Велес", "Дома", ["превью", "–"])])))
        self.fetch.set("/football/match/2026-10-10/", (200, fx("football_2026-10-10.txt")))
        self.feed.clear_cache()
        self.profiles = []
        base, _ = await d.weekly_entries(week, persist=False)
        self.profiles = [profile(["basketball"], [], []), profile([], ["laliga"], ["barcelona"], uid=2)]                                # «враждебные» профили: ничего активного
        hostile, _ = await d.weekly_entries(week, persist=False)
        self.assertEqual(S.format_weekly(base), S.format_weekly(hostile))
        self.profiles = [profile(["football"], ["laliga"], ["barcelona"], uid=3)]                                                       # а настоящий выбор только ДОБАВЛЯЕТ клубы
        more, _ = await d.weekly_entries(week, persist=False)
        self.assertTrue({e["key"] for e in base} <= {e["key"] for e in more})
        self.assertIn("Урал» — Велес", S.format_weekly(base))
        self.assertIn("Лидс", S.format_weekly(base))

    async def test_weekly_message_is_one_and_no_matches_means_nothing_sent(self):
        d = self.dyn_for("ural")
        self._patch(S, "utc_now", lambda: ekb(2026, 10, 5, 9).astimezone(TS.UTC))
        self.assertTrue(await S.job_weekly(self.bot, ekb(2026, 10, 5, 9).astimezone(TS.UTC)))
        self.assertEqual(self.bot.group, [])
        self.assertEqual(S.last_weekly_date(), datetime.date(2026, 10, 5))

    async def test_weekly_source_down_is_not_an_empty_week(self):
        d = self.dyn_for("arsenal")
        self.route("/football/match/", (503, "down"))
        for day in ("2026-10-05", "2026-10-06", "2026-10-10"):
            self.route(f"/football/match/{day}/", (503, "down"))
        self.fetch.set("/football/club/arsenal/", (503, "down"))
        self.feed.clear_cache()
        ok = await S.job_weekly(self.bot, ekb(2026, 10, 5, 9).astimezone(TS.UTC))
        self.assertFalse(ok)
        self.assertIsNone(S.last_weekly_date())
        self.assertTrue(any("Не удалось получить расписание недели" in t for t in self.admin_texts()))

    async def test_scheduler_tick_isolates_a_failing_part(self):
        self.dyn_for("ural")
        now = ekb(2026, 10, 7, 12, 0)
        self._patch(S, "utc_now", lambda: now.astimezone(TS.UTC))

        async def boom(self_, bot, n):
            raise RuntimeError("внутренняя ошибка со stacktrace")
        count = {"n": 0}

        class Stop(Exception):
            pass

        async def limited(seconds):
            count["n"] += 1
            if count["n"] >= 4:
                raise Stop()
        self._patch(S.asyncio, "sleep", limited)
        self._patch(D.Dynamic, "results", boom)
        with self.assertRaises(Stop):
            await S.scheduler_loop(self.bot)
        alerts = [t for t in self.admin_texts() if "Внутренний сбой планировщика" in t]
        self.assertEqual(len(alerts), 1)
        self.assertNotIn("stacktrace", alerts[0])


if __name__ == "__main__":
    unittest.main()
