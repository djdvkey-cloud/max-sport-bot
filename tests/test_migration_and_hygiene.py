"""Миграция состояния (бэкап + очистка ТЕСТОВОГО Зенита только при доказательстве) и гигиена тестов: тесты не трогают боевые данные."""
import datetime
import glob
import json
import os
import re
import sys
import tempfile
import unittest

os.environ.setdefault("MAX_BOT_TOKEN", "test-token")
os.environ.setdefault("MAX_CHAT_ID", "-100500")
os.environ.setdefault("DEEPSEEK_API_KEY", "test")
os.environ.setdefault("TAVILY_API_KEY", "test")
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="migration-import-")
os.environ["ADMIN_USER_ID"] = "777"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import migrations as G  # noqa: E402

NOW = datetime.datetime(2026, 10, 6, 14, 0, tzinfo=datetime.timezone.utc)
OWNER = 201512838


def write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def members(**extra):
    base = {str(OWNER): {"user_id": OWNER, "display_name": "Владелец", "active_in_group": True, "sports": ["football"], "championships": ["khl", "rpl"],
                         "clubs": ["avtomobilist", "arsenal", "zenit"]}}
    base.update(extra)
    return {"version": 1, "meta": {}, "members": base}


class ZenitCleanup(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="mig-")

    def read(self, name):
        with open(os.path.join(self.d, name), encoding="utf-8") as f:
            return json.load(f)

    def test_test_zenit_of_the_only_profile_is_removed_and_everything_else_stays(self):
        write(os.path.join(self.d, "tribun_members.json"), members())
        write(os.path.join(self.d, "today_matches.json"), {"matches": {}, "published_ids": ["x"]})
        lines = G.run(self.d, OWNER, NOW)
        self.assertTrue(any("тестовый выбор Зенит удалён" in l for l in lines))
        p = self.read("tribun_members.json")["members"][str(OWNER)]
        self.assertEqual(p["clubs"], ["avtomobilist", "arsenal"])                       # постоянные клубы владельца не тронуты
        self.assertEqual((p["sports"], p["championships"]), (["football"], ["khl", "rpl"]))
        self.assertEqual(p["test_choices_removed"][0]["club"], "zenit")
        self.assertEqual(self.read("today_matches.json"), {"matches": {}, "published_ids": ["x"]})

    def test_backup_is_made_before_any_change_and_originals_survive(self):
        write(os.path.join(self.d, "tribun_members.json"), members())
        write(os.path.join(self.d, "today_matches.json"), {"matches": {"a": 1}})
        G.run(self.d, OWNER, NOW)
        folder = os.path.join(self.d, "backups", f"migration-{G.MIGRATION_ID}")
        with open(os.path.join(folder, "tribun_members.json"), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertIn("zenit", saved["members"][str(OWNER)]["clubs"])                  # копия — до очистки
        self.assertTrue(os.path.exists(os.path.join(folder, "today_matches.json")))

    def test_second_run_does_nothing(self):
        write(os.path.join(self.d, "tribun_members.json"), members())
        G.run(self.d, OWNER, NOW)
        before = self.read("tribun_members.json")
        self.assertEqual(G.run(self.d, OWNER, NOW), [])
        self.assertEqual(self.read("tribun_members.json"), before)

    def test_zenit_is_not_touched_when_it_is_not_proven_to_be_a_test(self):
        real = {"user_id": 5, "display_name": "Реальный участник", "active_in_group": True, "sports": ["football"], "championships": ["rpl"], "clubs": ["zenit"]}
        write(os.path.join(self.d, "tribun_members.json"), members(**{"5": real}))
        lines = G.run(self.d, OWNER, NOW)
        self.assertTrue(any("не доказан" in l for l in lines))
        data = self.read("tribun_members.json")["members"]
        self.assertIn("zenit", data[str(OWNER)]["clubs"])
        self.assertEqual(data["5"]["clubs"], ["zenit"])                                  # реальные данные участников не удаляются

    def test_unknown_owner_or_unreadable_file_changes_nothing(self):
        write(os.path.join(self.d, "tribun_members.json"), members())
        self.assertTrue(any("владелец не определён" in l for l in G.run(self.d, None, NOW)))
        self.assertIn("zenit", self.read("tribun_members.json")["members"][str(OWNER)]["clubs"])
        d2 = tempfile.mkdtemp(prefix="mig-")
        with open(os.path.join(d2, "tribun_members.json"), "w") as f:
            f.write("{broken")
        G.run(d2, OWNER, NOW)
        with open(os.path.join(d2, "tribun_members.json")) as f:
            self.assertEqual(f.read(), "{broken")                                       # повреждённый файл не перезаписывается

    def test_no_members_file_is_fine(self):
        self.assertTrue(G.run(tempfile.mkdtemp(prefix="mig-"), OWNER, NOW))


class TestHygiene(unittest.TestCase):
    """Тесты не должны писать в боевой /data и в реальные tribun_members.json: каждый модуль задаёт DATA_DIR на временную папку ДО импорта кода."""

    def test_every_test_module_isolates_data_dir_before_importing_production_code(self):
        here = os.path.dirname(os.path.abspath(__file__))
        prod_import = re.compile(r"^\s*(?:import|from)\s+sport_bot\b", re.M)            # sport_bot читает DATA_DIR при импорте; остальные модули получают путь параметром
        for path in sorted(glob.glob(os.path.join(here, "test_*.py"))):
            text = open(path, encoding="utf-8").read()
            m = prod_import.search(text)
            if not m:
                continue
            head = text[:m.start()]
            sibling = re.search(r"^\s*(?:import|from)\s+test_\w+", head, re.M)                # DATA_DIR задаёт общий модуль, импортированный раньше
            sets = "DATA_DIR" in head
            self.assertTrue(sets or sibling, f"{os.path.basename(path)}: DATA_DIR не изолирован до импорта боевого кода")

    def test_no_test_refers_to_the_real_data_volume(self):
        here = os.path.dirname(os.path.abspath(__file__))
        for path in sorted(glob.glob(os.path.join(here, "test_*.py"))):
            if os.path.basename(path) == os.path.basename(__file__):
                continue
            text = open(path, encoding="utf-8").read()
            self.assertNotRegex(text, r"""["']/data[/"']""", os.path.basename(path))

    def test_production_data_dir_is_a_temp_folder_during_tests(self):
        import sport_bot as S
        self.assertNotEqual(os.path.abspath(S.DATA_DIR), os.path.abspath("/data"))
        self.assertIn(tempfile.gettempdir().lower(), os.path.abspath(S.DATA_DIR).lower())

    def test_tests_are_offline_by_default(self):
        import test_sport_v4 as TS
        self.assertTrue(hasattr(TS, "_OfflineSession"))


if __name__ == "__main__":
    unittest.main()
