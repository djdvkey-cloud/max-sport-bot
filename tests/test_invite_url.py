"""Ссылка-приглашение: только настройка TRIBUN_INVITE_URL. Поле chat.link (get_chat_by_id) оказалось нерабочим и в приглашениях не участвует вообще."""
import os
import re
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_tribun import Base, OWNER  # noqa: E402
import tribun as T  # noqa: E402

URL = "https://max.ru/" + "join/TESTinviteLINK_0123456789"
WRONG = "https://max.ru/" + "join/WRONGchatLINK_0123456789"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHORT = "👉 Вступить в «Свою Трибуну»:\n{link}\n\n👥 Перешли приглашение своим друзьям-болельщикам."
WARNING = "⚠️ Ссылка-приглашение не настроена."


def env(**values):
    """Окружение теста: нужные значения заданы, остальное — как есть; пустая строка снимает переменную."""
    patcher = mock.patch.dict(os.environ, {k: v for k, v in values.items() if v})
    patcher.start()
    for k, v in values.items():
        if not v:
            os.environ.pop(k, None)
    return patcher


class InviteScreen(Base):
    async def open(self):
        await self.press(OWNER, "inv")
        return self.last_text()

    async def test_configured_url_is_used_and_text_is_short(self):
        p = env(TRIBUN_INVITE_URL=URL)
        self.addCleanup(p.stop)
        text = await self.open()
        self.assertEqual(text, SHORT.format(link=URL))
        for service in ("📨 Приглашение", "для пересылки", "⚠️", "ℹ️", "✅"):
            self.assertNotIn(service, text)
        self.assertEqual([lbl for lbl, _ in self.buttons()], ["🛠 Управление", "🏠 Меню"])
        self.assertEqual(self.bot.group(), [])                                          # ничего не публикуется в группу

    async def test_chat_link_from_max_api_is_never_used(self):
        self.bot.chat_link = WRONG
        calls = []
        original = self.bot.get_chat_by_id

        async def spy(*a, **k):
            calls.append((a, k))
            return await original(*a, **k)
        self.bot.get_chat_by_id = spy
        p = env(TRIBUN_INVITE_URL=URL)
        text = await self.open()
        p.stop()
        self.assertEqual(text, SHORT.format(link=URL))
        self.assertNotIn("WRONGchat", text)
        self.assertEqual(calls, [])                                                      # к MAX за ссылкой группы бот вообще не обращается
        p = env(TRIBUN_INVITE_URL="")                                                    # и без настройки найденная ссылка не подставляется
        self.addCleanup(p.stop)
        text = await self.open()
        self.assertNotIn("WRONGchat", text)
        self.assertEqual(calls, [])
        self.assertEqual(self.bot.group(), [])

    async def test_missing_setting_shows_warning_and_no_link(self):
        self.bot.chat_link = WRONG
        p = env(TRIBUN_INVITE_URL="")
        self.addCleanup(p.stop)
        text = await self.open()
        self.assertEqual(text, WARNING + "\n\nЗадай настройку TRIBUN_INVITE_URL в Amvera: рабочая ссылка-приглашение из интерфейса MAX (https://max.ru/join/…).")
        self.assertNotIn("Вступить", text)                                               # неготовый текст для пересылки не показывается
        self.assertEqual(self.bot.group(), [])

    async def test_value_that_is_not_an_invite_link_counts_as_not_configured(self):
        for bad in ("просто текст", "https://example.com/join/abcdefghijklmnop", "http://max.ru/join/abcdefghijklmnop", "https://max.ru/join/short",
                    "https://max.ru/join/abc def ghi jkl mno"):
            p = env(TRIBUN_INVITE_URL=bad)
            text = await self.open()
            p.stop()
            self.assertTrue(text.startswith(WARNING), bad)
            self.assertIn("не похоже на ссылку-приглашение MAX", text)
            self.assertNotIn(bad, text)

    async def test_value_is_read_on_every_open_without_restart(self):
        p = env(TRIBUN_INVITE_URL=URL)
        first = await self.open()
        p.stop()
        p = env(TRIBUN_INVITE_URL="https://max.ru/" + "join/ANOTHERlinkVALUE_01234")
        second = await self.open()
        p.stop()
        self.assertIn(URL, first)
        self.assertIn("ANOTHERlinkVALUE_01234", second)
        self.assertNotIn(URL, second)

    async def test_launch_texts_use_the_same_setting(self):
        p = env(TRIBUN_INVITE_URL=URL)
        await self.press(OWNER, "adm:txt")
        with_link = self.last_text()
        p.stop()
        self.assertTrue(with_link.endswith("Ссылка:\n" + URL))
        p = env(TRIBUN_INVITE_URL="")
        self.addCleanup(p.stop)
        await self.press(OWNER, "adm:txt")
        self.assertTrue(self.last_text().endswith("Ссылка:\n" + WARNING))
        self.assertEqual(self.bot.group(), [])


def project_files():
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for name in files:
            if name.endswith((".py", ".md", ".yaml", ".yml", ".json", ".txt", ".cfg", ".toml")):
                yield os.path.join(root, name)


import unittest  # noqa: E402


class RepoHygiene(unittest.TestCase):
    OLD = "22mxKdkX" + "VvWHUpBiXQrp1a2zcClhNynt4BAVd-2waWk"        # прежняя нерабочая ссылка
    NEW = "7JzRoltu0znVM6jd" + "sFDDMeTQWe8GKq5kD5NjS-TW7P0"        # рабочая — живёт только в настройке Amvera, не в коде

    def test_old_broken_link_is_nowhere_in_code_tests_or_config(self):
        for path in project_files():
            with open(path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
            self.assertNotIn(self.OLD, text, path)
            self.assertNotIn(self.OLD[:12], text, path)

    def test_working_link_is_configuration_not_code(self):
        for path in project_files():
            with open(path, encoding="utf-8", errors="ignore") as f:
                self.assertNotIn(self.NEW, f.read(), path)

    def test_no_group_invite_links_in_application_code(self):
        pattern = re.compile(r"max\.ru/join/[A-Za-z0-9_\-]{20,}")
        for path in project_files():
            if "tests" in path.split(os.sep) or path.endswith(".md"):
                continue
            with open(path, encoding="utf-8", errors="ignore") as f:
                self.assertIsNone(pattern.search(f.read()), path)

    def test_chat_link_field_is_not_read_anywhere(self):
        for name in ("tribun.py", "sport_bot.py", "dynamic.py", "feed.py", "sources.py"):
            with open(os.path.join(REPO, name), encoding="utf-8") as f:
                src = f.read()
            self.assertNotIn("get_chat_by_id", src, name)
            self.assertNotIn('getattr(chat, "link"', src, name)
            self.assertNotIn("group_link", src, name)

    def test_setting_is_documented_by_name_and_default_is_unset(self):
        self.assertEqual(T.INVITE_URL_ENV, "TRIBUN_INVITE_URL")
        with mock.patch.dict(os.environ):
            os.environ.pop("TRIBUN_INVITE_URL", None)
            self.assertIsNone(T.invite_url())
        with mock.patch.dict(os.environ, {"TRIBUN_INVITE_URL": "  " + URL + "  "}):
            self.assertEqual(T.invite_url(), URL)                                        # пробелы по краям не мешают


if __name__ == "__main__":
    unittest.main()
