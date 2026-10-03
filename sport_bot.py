"""
Спортивный бот для группы в MAX — версия 3, постоянный процесс на Amvera.

6 клубов: Автомобилист (КХЛ), Синара, Урал, Реал Мадрид, Арсенал Лондон, Милан.
Любые официальные турниры. Без турнирных таблиц — только результат.

14.09.2026 перенесли с GitHub Actions cron на постоянный процесс: утро
14.09.2026 показало, что GitHub Actions может молча не запустить scheduled
job вообще (без единой ошибки в логах) — расписание `0 5 * * *` просто не
сработало, оповещение по Уралу чуть не потерялось. Постоянный процесс сам
следит за временем и не зависит от чужого внешнего расписания — тот же
принцип уже проверен на соседнем боте того же стека (myach-bot), который
именно поэтому стабильно работает на Amvera.

10:00 ЕКБ — если клуб играет сегодня, подробное оповещение с временем
            начала, местом и соперником; время старта сохраняется.
            Один раз в сутки (дедуп через LAST_MORNING_FILE).
Каждые 20 минут — проверка: если с начала сохранённого матча прошло
2,5 часа и результат ещё не отправлен, ищем счёт и шлём сообщение с
нужной эмоцией. Раньше это было ограничено окном 8-22 UTC (нужно было
для GitHub Actions cron) — постоянному процессу такое ограничение уже не
нужно, проверяем круглосуточно.

Хоккей (Автомобилист): ничьих не бывает, отдельно отмечаем победу/
поражение в овертайме или по буллитам — это важно из-за очков.

ИИ — связка Tavily Search + DeepSeek. Раньше здесь был Yandex Search +
собственное скачивание страниц через BeautifulSoup — сравнительный тест
показал, что Tavily даёт заметно более чистые и точные результаты (Yandex
как-то отдал ссылку на федерацию настольного тенниса вместо футбольного
«Арсенала»), плюс сама возвращает очищенный текст, без ручного парсинга
HTML. Поле "answer" из ответа Tavily НЕ используется — в тесте оно дважды
путало часовой пояс (мск вместо екб, московское вместо местного) — весь
разбор по-прежнему делает DeepSeek по сырым "content" из источников.
Защита от галлюцинаций (проверено на реальных инцидентах в соседнем
боте того же стека): поле «Турнир» не может совпадать с именем самого
клуба, а заявленный соперник должен реально встречаться в собранных
материалах — иначе один повтор запроса с уточнением, и только потом
пропуск, а не отправка недостоверных данных.

15.09.2026: добавлен необязательный резерв на OpenAI web_search
(OPENAI_API_KEY) — если основной путь Tavily+DeepSeek+extract_urls не
нашёл результат матча спустя RESULT_DELAY_HOURS + OPENAI_FALLBACK_
AFTER_HOURS, пробуем ещё раз через OpenAI. Не замена основному пути —
только точечный резерв для редких "зависших" случаев (дороже по
токенам, см. ask_openai_websearch). Если ключ не задан — просто не
используется, ничего не меняется.

17.09.2026 живой инцидент: реальный результат матча Автомобилиста был
Авангард 3:2 ОТ Автомобилист (гостевое поражение), а в чат ушло
«ПОБЕДА!!! Автомобилист 3:2 Авангард». Причина — код раньше всегда
пересобирал счёт как «клуб:соперник» и печатал клуб первым, а
источник (sports.ru) показывает счёт как «домашняя:гостевая»; модель
(зная, что вопрос про Автомобилист) подставила ему первую цифру не
сверяясь с реальным порядком команд в источнике. Исправлено: команды
и голы теперь везде печатаются строго в том порядке, в котором их
прислала модель (см. resolve_reported_result / format_result_*), без
перестановки клуба на первое место — промпт тоже явно просит не
переставлять. Исход (ПОБЕДА/НИЧЬЯ/ПОРАЖЕНИЕ) вычисляется отдельно и
на порядок отображения не влияет.

01.10.2026 (v4): личные уведомления Дмитрию о существенных сбоях (один раз, без спама,
и одно сообщение о восстановлении); устойчивое состояние матча и публикации результата
(результат сохраняется до подтверждённой отправки, не публикуется дважды); первая проверка
результата через 2 часа после начала; фразы утреннего анонса по виду спорта; недельная афиша
(понедельник 09:00 ЕКБ); контроль изменений расписания (время, перенос, отмена) вместо двух
сообщений; защита от публикации неверного счёта при конфликте источников.

03.10.2026 (2026-10-03.1, «Своя Трибуна» этап 2): личка участника — только три раздела интересов (виды спорта,
чемпионаты, клубы; «➕ Другое» = запрос), без персональных спортивных сообщений; реестр источников
(здоровье по реальным запросам, покрытие, разрывы) и учёт API (Tavily / DeepSeek / OpenAI: вызовы, токены,
кредиты, оценка стоимости, защита от лишних повторов) — всё это только наблюдение: логика афиши, утренних
анонсов, результатов и шести клубов не изменена и от профилей участников не зависит (см. tribun*.py,
sources.py, costs.py, tribun_hooks.py).

03.10.2026 (2026-10-03.2): выбор интересов — вид спорта → чемпионаты → клубы только выбранных чемпионатов (согласованный
список чемпионатов, справочник клубов по сезонам), «Назад» ведёт на предыдущий уровень. SPORTBOT и шесть клубов не затронуты.

03.10.2026 (2026-10-03.3): справочник клубов для выбора интересов обновлён до сезона 2026/27 по внешним источникам (UEFA.com, LALIGA,
NHL.com и др.; в каталоге хранятся источник и дата проверки). SPORTBOT и шесть клубов не затронуты.

ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ:
    MAX_BOT_TOKEN, MAX_CHAT_ID,
    DEEPSEEK_API_KEY, TAVILY_API_KEY
    OPENAI_API_KEY (необязательно, см. выше)
    ADMIN_USER_ID или ADMIN_CHAT_ID (необязательно) — адресат личных уведомлений о сбоях;
        если не заданы, используется admin_config.json рядом со скриптом

ЗАПУСК (постоянный процесс, сам следит за временем — не нужен внешний cron):
    python sport_bot.py
"""

import asyncio
import datetime
import json
import os
import random
import re
import shutil
import tempfile
from zoneinfo import ZoneInfo

import aiohttp
from maxapi import Bot, Dispatcher

import tribun
import tribun_hooks as hooks
import sources as tribun_sources

MAX_BOT_TOKEN = os.environ["MAX_BOT_TOKEN"]
MAX_CHAT_ID = int(os.environ["MAX_CHAT_ID"])
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
TAVILY_API_KEY = os.environ["TAVILY_API_KEY"]
# Необязательный резерв для проверки результатов (см. ask_openai_websearch
# и OPENAI_FALLBACK_AFTER_HOURS ниже) — если не задан, просто не используется,
# работа продолжается как раньше, только на Tavily+DeepSeek.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
# Тихий тестовый режим — вся логика (поиск, разбор, сохранение состояния)
# работает как обычно, меняется только последний шаг: send_to_group не
# шлёт в реальный MAX-чат, а печатает текст в лог. Нужен, чтобы гонять
# ручные проверки без риска задублировать реальные сообщения (как было
# несколько раз за один день с Автомобилистом при живой отладке).
DRY_RUN = os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")

YEKB_TZ = ZoneInfo("Asia/Yekaterinburg")
MORNING_HOUR = 10
RESULT_DELAY_HOURS = 2.0   # первая проверка результата — через 2 часа после начала матча (было 2,5)
# Резерв на OpenAI (см. ask_openai_websearch) пробуем только для матчей,
# которые "зависли" — основной путь уже проверял их и не нашёл результат
# спустя RESULT_DELAY_HOURS. Доп. запас в 1.5 часа — чтобы не дёргать
# платный и дорогой по токенам OpenAI на каждой рутинной проверке, а
# только когда Tavily+DeepSeek реально не справились.
OPENAI_FALLBACK_AFTER_HOURS = 1.5
CHECK_INTERVAL_SECONDS = 20 * 60

# На Amvera — постоянный диск /data (как у myach-bot), переживает рестарты
# и пересборки контейнера; локально (тесты, отладка на своей машине) —
# обычная папка data/ рядом со скриптом.
DATA_DIR = os.environ.get("DATA_DIR", "/data" if os.path.isdir("/data") else "data")
DATA_FILE = os.path.join(DATA_DIR, "today_matches.json")   # что сегодня играет и отправлен ли результат
LAST_MORNING_FILE = os.path.join(DATA_DIR, "last_morning.txt")   # дедуп: утро уже было сегодня
# Утренний прогон (10:00 ЕКБ) должен пережить проверки результатов вплоть до
# раннего утра следующих суток — поздние вечерние матчи (22:00+ мск) с учётом
# RESULT_DELAY_HOURS проверяются уже ПОСЛЕ полуночи по Екатеринбургу, то есть
# калидарные сутки по ЕКБ успевают смениться прямо посреди рабочего окна.
# Поэтому годность состояния определяем не по совпадению календарной даты
# (это стирало бы данные ровно в такой ситуации), а по давности сохранения.
#
# Было 20 часов — вживую выяснилось, что этого мало: интервал между двумя
# соседними утренними прогонами (10:00 -> 10:00) составляет ровно 24 часа,
# то есть состояние ГАРАНТИРОВАННО успевало устареть до следующего утра, даже
# если результат матча ещё ни разу не нашёлся (источник не успел опубликовать
# счёт, поиск не туда попал и т.п.) — утренний прогон следующего дня тихо
# стирал ещё не проверенный матч, и результат по нему уже никогда не
# отправлялся. Подняли запас до 32 часов, чтобы неразрешённый матч пережил
# границу суток и получил ещё окно вечерних проверок на следующий день.
STATE_MAX_AGE_HOURS = 32

TZ_OFFSET = {"мск": 3, "москва": 3, "екб": 5, "екатеринбург": 5}

# icon: 🏒 хоккей, ⚽ футбол, 🥅 футзал
CLUBS = [
    {"key": "avtomobilist", "name": "ХК «Автомобилист»", "sport": "hockey", "icon": "🏒",
     "aliases": ["Автомобилист", "Avtomobilist"],
     "sites": ["khl.ru", "hc-avto.ru", "championat.com"],
     # Живой инцидент 13.09.2026: Tavily Search раз за разом приносил
     # только архивные календарные страницы news.sportbox.ru без разбора
     # конкретной игры (khl.ru отдаёт 403 боту, туда напрямую тоже не
     # достучаться). sports.ru/.../calendar/ — стабильная, серверно
     # отрендеренная страница с историей результатов и статусом
     # "завершен" — проверено вручную, содержит точный счёт сразу после
     # финального свистка. Забираем её напрямую (см. extract_urls_tavily
     # / fetch_url_direct), в обход и поиска, и кэша Tavily.
     "extract_urls": ["https://www.sports.ru/hockey/club/avtomobilist/calendar/"]},
    {"key": "sinara", "name": "МФК «Синара»", "sport": "futsal", "icon": "🥅",
     "aliases": ["Синара", "Sinara"],
     "sites": ["superliga.rfs.ru", "mfkviz.ru", "futsal.rfs.ru"],
     # mfkviz.ru — собственный сайт клуба — Tavily Search стабильно не
     # ранжирует нужный раздел в топ выдачи, сколько ни меняй текст
     # запроса (пробовали search_hint — не помогло). Поэтому вдобавок
     # забираем страницу клуба на сайте лиги НАПРЯМУЮ (см.
     # extract_urls_tavily / fetch_url_direct), в обход и ранжирования
     # поиска, и (что оказалось важнее) собственного кэша Tavily.
     "search_hint": "БЕТСИТИ Суперлига мини-футбол",
     "extract_urls": ["https://superliga.rfs.ru/team/1258508"]},
    {"key": "ural", "name": "ФК «Урал»", "sport": "football", "icon": "⚽",
     "aliases": ["Урал", "Ural"],
     "sites": ["fc-ural.ru", "fnl.pro", "championat.com"],
     # 13.09.2026: тот же принцип для всех 6 клубов — известная стабильная
     # серверно отрендеренная страница sports.ru/.../calendar/ забирается
     # напрямую (extract_urls_tavily / fetch_url_direct), в обход и
     # поиска, и кэша Tavily. Проверено вручную на всех 4 клубах ниже:
     # чистый текст, счёт + статус "завершен" сразу после матча.
     "extract_urls": ["https://www.sports.ru/football/club/ural/calendar/"]},
    {"key": "real", "name": "«Реал Мадрид»", "sport": "football", "icon": "⚽",
     "aliases": ["Реал Мадрид", "Real Madrid"],
     "sites": ["realmadrid.com", "championat.com", "soccer.ru"],
     "extract_urls": ["https://www.sports.ru/football/club/real/calendar/"]},
    {"key": "arsenal", "name": "«Арсенал» Лондон", "sport": "football", "icon": "⚽",
     "aliases": ["Арсенал", "Arsenal"],
     "sites": ["arsenal.com", "championat.com", "soccer.ru"],
     "extract_urls": ["https://www.sports.ru/football/club/arsenal/calendar/"]},
    {"key": "milan", "name": "«Милан»", "sport": "football", "icon": "⚽",
     "aliases": ["Милан", "Milan", "AC Milan"],
     "sites": ["acmilan.com", "championat.com", "soccer.ru"],
     "extract_urls": ["https://www.sports.ru/football/club/milan/calendar/"]},
]

# Глобальный (не per-club) список доменов для Tavily include_domains — по
# выбору пользователя, вместо неограниченного поиска: тот показал точность
# выше, чем старые узкие per-club sites, но иногда всё равно промахивался
# на нерелевантные источники (например, UFC/MMA для Автомобилиста). Список
# осознанно с запасом на будущее — под расширение числа клубов и добавление
# КХЛ/НХЛ, поэтому включает источники сверх текущих 6 клубов.
#
# championat.com сюда НЕ должен попадать — уже убирали его раньше за то же
# самое (см. историю), но он вернулся при более поздней правке. Причина
# та же: его короткие "теговые"/списочные страницы (везде <170 символов
# контента) проходят фильтр по длине и залипают в топ-3 источника вместо
# страниц с реальным разбором матча, из-за чего модель не находит точный
# счёт, даже когда он текстом упомянут в заголовке (проверено на Реал
# Мадриде 12.09 — заголовок "Реал разгромил Райо Вальекано" был в
# контексте, но без цифр, и модель честно не смогла придумать счёт).
TAVILY_INCLUDE_DOMAINS = [
    "hc-avto.ru", "khl.ru", "news.sportbox.ru", "sports.ru",
    "mfkviz.ru", "superliga.rfs.ru", "rfs.ru", "fnl.pro", "fc-ural.ru",
    "fapl.ru", "arsenal.com", "legaseriea.it", "realmadrid.com", "acmilan.com",
    "laliga.com", "sport-express.ru", "ria.ru", "bundesliga.com", "bvb.de",
    "nhl.ru", "nhl.com", "allhockey.ru", "nhl-news.ru",
]


# --- Настройки v4: личные уведомления, афиша, контроль расписания --------------

BOT_VERSION = "2026-10-03.3"
# Первая проверка результата — через RESULT_DELAY_HOURS после начала матча
# (раньше 2,5 ч, теперь 2 ч). Цикл не ждёт полного интервала: планировщик
# просыпается точно к этому моменту (см. seconds_until_next_event).
RESULT_ALERT_AFTER_HOURS = 4.5        # результата всё ещё нет — один раз сообщаем Дмитрию
RESULT_GIVE_UP_HOURS = 36             # дальше не ищем (и один раз сообщаем Дмитрию)
CONFLICT_ALERT_AFTER_MINUTES = 60     # источники спорят столько — сообщаем Дмитрию
SOURCE_FAIL_ALERT_TICKS = 3           # сбой источников подряд столько проверок — сообщаем
MORNING_RETRY_UNTIL_HOUR = 15         # до этого часа (ЕКБ) повторяем сорвавшуюся утреннюю проверку
WEEKLY_WEEKDAY = 0                    # понедельник
WEEKLY_HOUR = 9
CROSSCHECK_MIN_INTERVAL_MINUTES = 60  # как часто можно платно перепроверять счёт через OpenAI
PUBLISHED_IDS_KEEP = 300

ALERTS_FILE = os.path.join(DATA_DIR, "admin_alerts.json")
SCHEDULE_FILE = os.path.join(DATA_DIR, "schedule.json")
LAST_WEEKLY_FILE = os.path.join(DATA_DIR, "last_weekly.txt")
META_FILE = os.path.join(DATA_DIR, "sport_meta.json")

# Вид спорта для утренних фраз определяется ТОЛЬКО по этой явной таблице
# (по полю sport клуба), нейросеть вид спорта не угадывает. Мини-футбол
# относится к семейству «футбол».
SPORT_FAMILY = {"football": "football", "futsal": "football", "hockey": "hockey"}

FOOTBALL_INTROS = [
    "⚽ Сегодня футбольный день!",
    "🔥 Сегодня болеем за наших!",
    "⚽ Планы на вечер определены!",
    "⚽ Сегодня есть что посмотреть!",
    "🏟 Сегодня наши выходят на поле!",
    "⚽ Матч уже сегодня!",
    "🔥 Вечером будет жарко!",
    "⚽ Готовимся болеть!",
    "🏟 Сегодня ждём красивой игры!",
    "⚽ Время футбола!",
]
HOCKEY_INTROS = [
    "🏒 Сегодня хоккей!",
    "🔥 Сегодня болеем за наших!",
    "🏒 Планы на вечер определены!",
    "🥅 Сегодня ждём шайбы!",
    "🏒 Наши выходят на лёд!",
    "🏒 Матч уже сегодня!",
    "🔥 Сегодня на льду будет жарко!",
    "🏒 Готовимся болеть!",
    "🥅 Ждём голов… то есть шайб! 😄",
    "🏒 Время хоккея!",
]
MIXED_INTROS = [
    "🔥 Сегодня болеем за наших!",
    "📣 Сегодня игровой день!",
    "🔥 Сегодня есть что посмотреть!",
    "💪 Сегодня играют наши!",
    "📣 Матчи уже сегодня!",
    "🔥 Готовимся болеть!",
    "💪 Планы на вечер определены!",
    "📣 Сегодня будет интересно!",
    "🔥 Большой игровой день!",
    "💪 Наши сегодня в деле!",
]
INTRO_SETS = {"football": FOOTBALL_INTROS, "hockey": HOCKEY_INTROS, "mixed": MIXED_INTROS}

WEEKDAYS_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
MONTHS_RU_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
                 "сентября", "октября", "ноября", "декабря"]
MONTHS_RU_NUM = {name: i + 1 for i, name in enumerate(MONTHS_RU_GEN)}
# Сайт лиги футзала сокращает месяцы («СЕНТ.», «ОКТ.», «НОЯБ.») — сравниваем по первым 3 буквам.
MONTHS_ABBR_NUM = {"ЯНВ": 1, "ФЕВ": 2, "МАР": 3, "АПР": 4, "МАЯ": 5, "ИЮН": 6, "ИЮЛ": 7, "АВГ": 8,
                   "СЕН": 9, "ОКТ": 10, "НОЯ": 11, "ДЕК": 12}


class SourceError(Exception):
    """Источник данных недоступен или ответ не разобран — это СБОЙ, а не
    «матча нет»: «нет матча» возвращается как None, сбой — этим исключением."""


# --- Вспомогательное: атомарные файлы, время, клубы -------------------------

def atomic_write_json(path: str, data) -> None:
    """Запись через временный файл и атомарную подмену — обрыв процесса
    посреди записи не оставляет наполовину записанное состояние."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json_safe(path: str, default):
    """Чтение служебного файла: при ошибке разбора исходник сохраняется
    копией рядом, возвращается default (файл не затирается молча)."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[DEBUG] не удалось прочитать {path}: {type(e).__name__}: {e}")
        try:
            stamp = datetime.datetime.now(YEKB_TZ).strftime("%Y%m%d-%H%M%S")
            shutil.copy2(path, f"{path}.corrupt-{stamp}")
        except Exception:
            pass
        return default


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def ekb_now() -> datetime.datetime:
    return datetime.datetime.now(YEKB_TZ)


def parse_iso(value) -> datetime.datetime | None:
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def club_by_key(key: str) -> dict | None:
    for club in CLUBS:
        if club["key"] == key:
            return club
    return None


def sport_family(club: dict) -> str:
    """football | hockey — строго по явной таблице, без догадок."""
    return SPORT_FAMILY[club["sport"]]


def match_identity(club_key: str, match_date, rival: str) -> str:
    return f"{club_key}|{match_date}|{normalize_team_name(rival)}"


def local_to_utc(day: datetime.date, time_str: str, zone_str: str) -> datetime.datetime | None:
    """Дата + «ЧЧ:ММ» + пояс («мск»/«екб») → момент в UTC."""
    m = re.match(r"^(\d{1,2}):(\d{2})$", (time_str or "").strip())
    if not m:
        return None
    offset_hours = TZ_OFFSET.get((zone_str or "").strip().lower(), 3)
    tz = datetime.timezone(datetime.timedelta(hours=offset_hours))
    try:
        local_dt = datetime.datetime(day.year, day.month, day.day, int(m.group(1)), int(m.group(2)), tzinfo=tz)
    except ValueError:
        return None
    return local_dt.astimezone(datetime.timezone.utc)


# --- Состояние матчей (версия 2) --------------------------------------------
#
# Файл today_matches.json (имя прежнее). Формат v2:
#   {"version": 2, "saved_at": ..., "matches": {match_id: запись},
#    "published_ids": [...], "meta": {...}}
# Запись матча, поле status:
#   found           — найден утром, анонс ещё не отправлен
#   announced       — анонс отправлен
#   awaiting_result — подошло время проверки результата, результата пока нет
#   result_found    — результат получен и СОХРАНЁН, но ещё не опубликован
#   published       — результат подтверждённо отправлен в MAX (result_sent=True)
#   expired         — результат так и не получен (сообщено Дмитрию)
# Старый формат (по ключу клуба) читается и переводится в v2 при первой загрузке;
# уже опубликованные результаты остаются published и повторно не уйдут.

MATCH_DEFAULTS = {
    "status": "found", "announce_sent": False, "announce_text": None, "result_sent": False,
    "result_text": None, "result_found_at": None, "published_at": None, "conflict": None,
    "source_fail_ticks": 0, "last_crosscheck_at": None, "first_check_at": None,
}


def normalize_match(rec: dict) -> dict:
    for key, value in MATCH_DEFAULTS.items():
        rec.setdefault(key, value)
    if rec.get("result_sent"):
        rec["status"] = "published"
    return rec


def empty_state() -> dict:
    return {"version": 2, "saved_at": None, "matches": {}, "published_ids": [], "meta": {}}


def migrate_v1_record(club: dict, rec: dict, saved_at: datetime.datetime) -> dict:
    start = parse_iso(rec.get("start_utc"))
    match_date = (start.astimezone(YEKB_TZ).date() if start else saved_at.astimezone(YEKB_TZ).date()).isoformat()
    published = bool(rec.get("result_sent"))
    migrated = {
        "match_id": match_identity(club["key"], match_date, rec.get("rival", "")),
        "club_key": club["key"], "sport": sport_family(club), "match_date": match_date,
        "tournament": rec.get("tournament", ""), "time": rec.get("time", ""), "zone": rec.get("zone", ""),
        "place": rec.get("place", ""), "rival": rec.get("rival", ""), "start_utc": rec.get("start_utc"),
        "status": "published" if published else "announced",
        "announce_sent": True,          # старые записи уже были анонсированы — повторно не анонсируем
        "result_sent": published,
    }
    return normalize_match(migrated)


def migrate_state(raw, now: datetime.datetime) -> dict:
    if not isinstance(raw, dict):
        return empty_state()
    if raw.get("version") == 2 and isinstance(raw.get("matches"), dict):
        state = raw
        state.setdefault("published_ids", [])
        state.setdefault("meta", {})
        for rec in state["matches"].values():
            normalize_match(rec)
        return state
    state = empty_state()
    saved_at = parse_iso(raw.get("saved_at"))
    if not saved_at or now - saved_at > datetime.timedelta(hours=STATE_MAX_AGE_HOURS):
        return state                    # как и раньше: слишком старое состояние не используем
    for club in CLUBS:
        rec = raw.get(club["key"])
        if isinstance(rec, dict):
            migrated = migrate_v1_record(club, rec, saved_at)
            state["matches"][migrated["match_id"]] = migrated
            if migrated["result_sent"]:
                state["published_ids"].append(migrated["match_id"])
    return state


def prune_state(state: dict, now: datetime.datetime) -> None:
    """Убирает давно закрытые записи. Неопубликованные результаты не трогает —
    ими занимается job_check_results (ищет до RESULT_GIVE_UP_HOURS и сообщает)."""
    for mid, rec in list(state["matches"].items()):
        start = parse_iso(rec.get("start_utc"))
        if start is None:
            day = rec.get("match_date")
            start = parse_iso(f"{day}T00:00:00+05:00") if day else None
        if start is None:
            continue
        closed = rec["status"] in ("published", "expired")
        if closed and now - start > datetime.timedelta(hours=48):
            del state["matches"][mid]
    state["published_ids"] = state["published_ids"][-PUBLISHED_IDS_KEEP:]


def load_state(now: datetime.datetime | None = None) -> dict:
    now = now or utc_now()
    raw = read_json_safe(DATA_FILE, {})
    state = migrate_state(raw, now)
    prune_state(state, now)
    return state


def save_state(state: dict) -> None:
    state["saved_at"] = ekb_now().isoformat()
    atomic_write_json(DATA_FILE, state)


# --- Личные уведомления Дмитрию ---------------------------------------------
#
# Канал: личное сообщение в MAX. Адресат — ADMIN_USER_ID (или ADMIN_CHAT_ID —
# id личного диалога) из переменных окружения либо из необязательного файла
# admin_config.json рядом со скриптом. Токены и ключи сюда не попадают.
# Правила: при нормальной работе бот молчит; одна и та же проблема —
# одно уведомление; после восстановления — одно сообщение «✅».

def admin_target() -> dict | None:
    user_id = os.environ.get("ADMIN_USER_ID", "").strip()
    chat_id = os.environ.get("ADMIN_CHAT_ID", "").strip()
    if not user_id and not chat_id:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin_config.json")
        cfg = read_json_safe(path, {})
        user_id = str(cfg.get("admin_user_id", "") or "").strip()
        chat_id = str(cfg.get("admin_chat_id", "") or "").strip()
    try:
        if chat_id:
            return {"chat_id": int(chat_id)}
        if user_id:
            return {"user_id": int(user_id)}
    except ValueError:
        print("[DEBUG] ADMIN_USER_ID/ADMIN_CHAT_ID заданы неверно")
    return None


async def admin_notify(bot: Bot, text: str, *, silent: bool = False) -> bool:
    """Личное сообщение Дмитрию. True — доставлено."""
    if DRY_RUN:
        print(f"[DRY RUN] личное уведомление:\n{text}")
        return False
    target = admin_target()
    if not target:
        print(f"[DEBUG] канал личных уведомлений не настроен, сообщение не отправлено:\n{text}")
        return False
    try:
        await bot.send_message(text=text, notify=not silent, **target)
        return True
    except Exception as e:
        print(f"[DEBUG] не удалось отправить личное уведомление: {type(e).__name__}: {e}")
        return False


async def raise_alert(bot: Bot, key: str, text: str, now: datetime.datetime | None = None) -> bool:
    """Первое событие → одно уведомление; повторы с тем же key — молча."""
    now = now or utc_now()
    alerts = read_json_safe(ALERTS_FILE, {})
    cur = alerts.get(key)
    if cur and cur.get("active"):
        cur["count"] = cur.get("count", 1) + 1
        cur["last"] = now.isoformat()
        last_try = parse_iso(cur.get("last_attempt"))
        if not cur.get("delivered") and (not last_try or now - last_try > datetime.timedelta(minutes=30)):
            cur["last_attempt"] = now.isoformat()
            cur["delivered"] = await admin_notify(bot, text)
        atomic_write_json(ALERTS_FILE, alerts)
        return False
    delivered = await admin_notify(bot, text)
    alerts[key] = {"active": True, "since": now.isoformat(), "last": now.isoformat(), "count": 1,
                   "delivered": delivered, "last_attempt": now.isoformat()}
    atomic_write_json(ALERTS_FILE, alerts)
    return delivered


async def clear_alert(bot: Bot, key: str, text: str, now: datetime.datetime | None = None) -> bool:
    """Проблема ушла → одно сообщение о восстановлении (только если о
    самой проблеме Дмитрий действительно был уведомлён)."""
    now = now or utc_now()
    alerts = read_json_safe(ALERTS_FILE, {})
    cur = alerts.get(key)
    if not cur or not cur.get("active"):
        return False
    cur["active"] = False
    cur["cleared"] = now.isoformat()
    atomic_write_json(ALERTS_FILE, alerts)
    if cur.get("delivered"):
        return await admin_notify(bot, text)
    return False


def alert_active(key: str) -> bool:
    return bool(read_json_safe(ALERTS_FILE, {}).get(key, {}).get("active"))


async def notify_startup(bot: Bot) -> None:
    """Один раз на версию — тихое личное сообщение «запущен»: заодно
    проверяет, что канал уведомлений работает."""
    meta = read_json_safe(META_FILE, {})
    if meta.get("startup_notice_version") == BOT_VERSION:
        return
    try:
        state = load_state()
        pending = [r for r in state["matches"].values() if r["status"] not in ("published", "expired")]
        sched = load_schedule()
        summary = (f"Матчей в работе: {len(pending)}. В афише записей: {len(sched['entries'])}. "
                   f"Афиша — по понедельникам в {WEEKLY_HOUR}:00 (ЕКБ), первая проверка результата — "
                   f"через {RESULT_DELAY_HOURS:g} ч после начала матча.")
    except Exception as e:
        summary = f"Состояние прочитать не удалось: {type(e).__name__}."
    sent = await admin_notify(
        bot,
        f"✅ Трибун запущен (версия {BOT_VERSION}).\n"
        "Личные уведомления включены: пишу только при сбоях и когда всё восстановилось.\n" + summary +
        "\nЛичное меню Трибуна — напишите мне в личку (кнопки: «Что сегодня у меня?», «Мои интересы», «Управление Трибуной»).",
        silent=True,
    )
    if sent:
        meta["startup_notice_version"] = BOT_VERSION
        atomic_write_json(META_FILE, meta)


# --- Фразы утреннего анонса (без повторов подряд) -----------------------------

def pick_intro(kind: str, rng=random) -> str:
    phrases = INTRO_SETS[kind]
    meta = read_json_safe(META_FILE, {})
    last = meta.get(f"last_intro_{kind}")
    choices = [i for i in range(len(phrases)) if i != last] or list(range(len(phrases)))
    idx = rng.choice(choices)
    meta[f"last_intro_{kind}"] = idx
    try:
        atomic_write_json(META_FILE, meta)
    except Exception as e:
        print(f"[DEBUG] не удалось сохранить ротацию фраз: {e}")
    return phrases[idx]


def intro_kind(clubs: list) -> str:
    """Только футбол → football; только хоккей → hockey; оба вида → mixed."""
    families = {sport_family(c) for c in clubs}
    if families == {"football"}:
        return "football"
    if families == {"hockey"}:
        return "hockey"
    return "mixed"


# --- Календарь клубов: детерминированный разбор уже используемых страниц -------

def calendar_kind(club: dict) -> str:
    urls = club.get("extract_urls") or [""]
    return "superliga" if "superliga.rfs.ru" in urls[0] else "sportsru"


def infer_year(month: int, day: int, ref: datetime.date) -> int:
    year = ref.year
    try:
        candidate = datetime.date(year, month, day)
    except ValueError:
        return year
    if candidate < ref - datetime.timedelta(days=180):
        return year + 1
    if candidate > ref + datetime.timedelta(days=180):
        return year - 1
    return year


def _clean_lines(text: str) -> list:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


SPORTSRU_DATE_RE = re.compile(r"^(\d{1,2}) ([а-я]+) (\d{1,2}):(\d{2})$")
TABLE_DATE_RE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")
TABLE_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
TABLE_SCORE_RE = re.compile(r"^(\d{1,2}) : (\d{1,2})$")
# sports.ru подставляет для ещё не назначенного времени «ночные» заглушки
# (00:00, 01:00, 02:00 …). Матчей, начинающихся ночью по Москве, у отслеживаемых
# клубов в сезоне нет, поэтому такое время считаем «не назначено» (время не угадываем).
PLACEHOLDER_HOUR_BELOW = 4


def _clock_or_none(hh: int, mm: int):
    if hh < PLACEHOLDER_HOUR_BELOW:
        return None
    return f"{hh:02d}:{mm:02d}"


def parse_sportsru_table(text: str, club: dict) -> list:
    """Таблица сезона sports.ru: «ДД.ММ.ГГГГ / | / ЧЧ:ММ / турнир / соперник /
    Дома|В гостях / [от|б] / a : b (или «превью –») / зрители». Счёт в таблице —
    в порядке «хозяева : гости», поэтому у «В гостях» он зеркальный. Время московское."""
    lines = _clean_lines(text)
    out = []
    i = 0
    while i < len(lines):
        m = TABLE_DATE_RE.match(lines[i])
        if not (m and i + 5 < len(lines) and lines[i + 1] == "|" and TABLE_TIME_RE.match(lines[i + 2])
                and lines[i + 5] in ("Дома", "В гостях")):
            i += 1
            continue
        try:
            date = datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            i += 6
            continue
        hh, mm = (int(x) for x in lines[i + 2].split(":"))
        tournament, rival, home = lines[i + 3], lines[i + 4], lines[i + 5] == "Дома"
        j, cells = i + 6, []
        while j < len(lines) and len(cells) < 6:
            if TABLE_DATE_RE.match(lines[j]) and j + 1 < len(lines) and lines[j + 1] == "|":
                break
            cells.append(lines[j])
            j += 1
        score = next((TABLE_SCORE_RE.match(c) for c in cells if TABLE_SCORE_RE.match(c)), None)
        method = "ОТ" if "от" in cells else ("БУЛЛИТЫ" if "б" in cells else "ОСНОВНОЕ")
        fixture = {
            "date": date, "time": _clock_or_none(hh, mm), "zone": "мск", "tournament": tournament,
            "rival": rival, "home": home, "finished": score is not None, "cancelled": False,
            "postponed": False, "club_goals": None, "rival_goals": None, "method": None,
        }
        if score and not has_forbidden_squad_label(rival):
            a, b = int(score.group(1)), int(score.group(2))
            fixture["club_goals"], fixture["rival_goals"] = (a, b) if home else (b, a)
            fixture["method"] = method
        if not has_forbidden_squad_label(rival):
            out.append(fixture)
        i = j
    return out


def parse_sportsru_top(text: str, club: dict, ref: datetime.date) -> list:
    """Верхний блок sports.ru: «Команда1 / – / Команда2 / 02 октября 17:00 /
    Турнир / | / [завершен]» — даёт пометки «отменён»/«перенесён»."""
    lines = _clean_lines(text)
    club_names = [club["name"], *club.get("aliases", [])]
    out = []
    for i, line in enumerate(lines):
        m = SPORTSRU_DATE_RE.match(line)
        if not m or m.group(2) not in MONTHS_RU_NUM or i < 3:
            continue
        if lines[i - 2] not in ("–", "-", "—"):
            continue
        team1, team2 = lines[i - 3], lines[i - 1]
        is1, is2 = team_name_matches(team1, club_names), team_name_matches(team2, club_names)
        if is1 == is2 or has_forbidden_squad_label(team1, team2):
            continue
        day, month = int(m.group(1)), MONTHS_RU_NUM[m.group(2)]
        try:
            date = datetime.date(infer_year(month, day, ref), month, day)
        except ValueError:
            continue
        tail = " ".join(lines[i + 1:i + 5]).lower()
        out.append({
            "date": date, "time": _clock_or_none(int(m.group(3)), int(m.group(4))), "zone": "мск",
            "tournament": lines[i + 1] if i + 1 < len(lines) else "", "rival": team2 if is1 else team1,
            "home": is1, "finished": "заверш" in tail, "cancelled": "отмен" in tail,
            "postponed": "перенес" in tail, "club_goals": None, "rival_goals": None, "method": None,
        })
    return out


def parse_sportsru_calendar(text: str, club: dict, ref: datetime.date) -> list:
    """sports.ru: таблица сезона — основа (даты, время, соперники, счёт); верхний блок
    добавляет пометки «отменён/перенесён» и матчи, которых нет в таблице."""
    merged = {}
    for f in parse_sportsru_table(text, club):
        merged[(f["date"], normalize_team_name(f["rival"]))] = f
    for f in parse_sportsru_top(text, club, ref):
        key = (f["date"], normalize_team_name(f["rival"]))
        if key in merged:
            merged[key]["cancelled"] = merged[key]["cancelled"] or f["cancelled"]
            merged[key]["postponed"] = merged[key]["postponed"] or f["postponed"]
            if merged[key]["time"] is None and f["time"]:
                merged[key]["time"] = f["time"]
        else:
            merged[key] = f
    return sorted(merged.values(), key=lambda f: (f["date"], f["time"] or ""))


SUPERLIGA_DATE_RE = re.compile(r"^(\d{1,2}) ([А-Яа-я]{3,5})\.? / [А-Яа-я]{2} / (\d{1,2}):(\d{2})$")


def _superliga_section(lines: list, start_marker: str, end_markers: tuple) -> list:
    try:
        begin = lines.index(start_marker) + 1
    except ValueError:
        return []
    end = len(lines)
    for k in range(begin, len(lines)):
        if lines[k] in end_markers:
            end = k
            break
    return lines[begin:end]


def parse_superliga_team(text: str, club: dict, ref: datetime.date) -> list:
    """superliga.rfs.ru/team/...: «Будущие матчи» — «Турнир / 03 ОКТ. / СБ / 13:00 /
    Команда1 / - / Команда2 / -»; «Прошедшие матчи» — то же, но со счётом
    («Команда1 / 4 / [(3:0)] / Команда2 / 1»). Время 00:00 — «не назначено».
    Пояс — екатеринбургский (подтверждено записями прошлых матчей бота)."""
    lines = _clean_lines(text)
    club_names = [club["name"], *club.get("aliases", [])]
    out = []

    def fixture(date_line_match, date_line_index, sect, team1, team2, finished, s1=None, s2=None):
        month = MONTHS_ABBR_NUM.get(date_line_match.group(2).upper()[:3])
        is1, is2 = team_name_matches(team1, club_names), team_name_matches(team2, club_names)
        if not month or is1 == is2 or has_forbidden_squad_label(team1, team2):
            return None
        day = int(date_line_match.group(1))
        try:
            date = datetime.date(infer_year(month, day, ref), month, day)
        except ValueError:
            return None
        club_goals = rival_goals = None
        if finished:
            club_goals, rival_goals = (s1, s2) if is1 else (s2, s1)
        return {
            "date": date, "time": _clock_or_none(int(date_line_match.group(3)), int(date_line_match.group(4))),
            "zone": "екб", "tournament": sect[date_line_index - 1] if date_line_index > 0 else "",
            "rival": team2 if is1 else team1, "home": is1, "finished": finished, "cancelled": False,
            "postponed": False, "club_goals": club_goals, "rival_goals": rival_goals,
            "method": "ОСНОВНОЕ" if finished else None,
        }

    future = _superliga_section(lines, "Будущие матчи", ("Все расписание", "Статистика"))
    i = 0
    while i < len(future):
        m = SUPERLIGA_DATE_RE.match(future[i])
        if m and i + 3 < len(future):
            f = fixture(m, i, future, future[i + 1], future[i + 3], False)
            if f:
                out.append(f)
            i += 4
            continue
        i += 1

    past = _superliga_section(lines, "Прошедшие матчи", ("Будущие матчи",))
    i = 0
    while i < len(past):
        m = SUPERLIGA_DATE_RE.match(past[i])
        if m and i + 4 < len(past) and past[i + 2].isdigit():
            k = i + 3
            while k < len(past) and past[k].startswith("("):
                k += 1
            if k + 1 < len(past) and past[k + 1].isdigit():
                f = fixture(m, i, past, past[i + 1], past[k], True, int(past[i + 2]), int(past[k + 1]))
                if f:
                    out.append(f)
                i = k + 2
                continue
        i += 1
    return sorted(out, key=lambda f: (f["date"], f["time"] or ""))

def parse_calendar(club: dict, text: str, ref: datetime.date) -> list:
    if calendar_kind(club) == "superliga":
        return parse_superliga_team(text, club, ref)
    return parse_sportsru_calendar(text, club, ref)


async def fetch_club_calendar(club: dict, ref: datetime.date) -> list:
    """Страница календаря клуба из уже существующего списка extract_urls.
    SourceError — страница не скачалась или не содержит ни одного матча
    (вёрстка могла измениться)."""
    urls = club.get("extract_urls") or []
    if not urls:
        raise SourceError("у клуба нет страницы календаря")
    source_id = hooks.source_for_url(urls[0])
    text = await fetch_url_direct(urls[0])
    if not text:
        hooks.source_event(source_id, "failed", "страница недоступна")
        raise SourceError(f"страница календаря {urls[0]} недоступна")
    fixtures = parse_calendar(club, text, ref)
    lines = _clean_lines(text)
    has_layout = "Будущие матчи" in lines or any(SPORTSRU_DATE_RE.match(ln) or TABLE_DATE_RE.match(ln) for ln in lines)
    if not fixtures and not has_layout:
        hooks.source_event(source_id, "failed", "вёрстка изменилась")
        raise SourceError("в календаре не найдено ни одного матча — вёрстка изменилась?")
    # страница получена и разобрана: пустой список — подтверждённое «матчей нет» (CONFIRMED_EMPTY), а не сбой источника
    hooks.source_event(source_id, "ok" if fixtures else "confirmed_empty")
    return fixtures


# --- Расписание недели (schedule.json) ------------------------------------------

def load_schedule() -> dict:
    data = read_json_safe(SCHEDULE_FILE, {"entries": []})
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return {"entries": []}
    return data


def save_schedule(sched: dict) -> None:
    sched["saved_at"] = ekb_now().isoformat()
    atomic_write_json(SCHEDULE_FILE, sched)


def schedule_entry(club: dict, date: datetime.date, time_str, zone, tournament, rival, source) -> dict:
    return {
        "key": match_identity(club["key"], date.isoformat(), rival),
        "club_key": club["key"], "sport": sport_family(club), "date": date.isoformat(),
        "time": time_str, "zone": zone, "tournament": tournament, "rival": rival, "source": source,
        "updated": ekb_now().isoformat(),
    }


def schedule_upsert(sched: dict, entry: dict) -> None:
    sched["entries"] = [e for e in sched["entries"] if e.get("key") != entry["key"]]
    sched["entries"].append(entry)


def schedule_remove(sched: dict, key: str) -> None:
    sched["entries"] = [e for e in sched["entries"] if e.get("key") != key]


def week_bounds(day: datetime.date):
    monday = day - datetime.timedelta(days=day.weekday())
    return monday, monday + datetime.timedelta(days=6)


def fmt_clock(time_str, zone) -> str:
    if not time_str:
        return "время уточняется"
    return f"{time_str} ({zone})" if zone else time_str


def fmt_day(date: datetime.date, *, long: bool = False) -> str:
    name = WEEKDAYS_RU[date.weekday()]
    return f"{name}, {date.day} {MONTHS_RU_GEN[date.month - 1]}" if long else name


def week_entry_sort_key(entry: dict):
    date = datetime.date.fromisoformat(entry["date"])
    utc = local_to_utc(date, entry["time"], entry["zone"]) if entry.get("time") else None
    return (date, 0 if utc else 1, utc or datetime.datetime.max.replace(tzinfo=datetime.timezone.utc))


def format_weekly(entries: list) -> str | None:
    if not entries:
        return None
    lines = ["📅 Наши матчи на этой неделе"]
    for e in sorted(entries, key=week_entry_sort_key):
        club = club_by_key(e["club_key"])
        date = datetime.date.fromisoformat(e["date"])
        lines.append("")
        lines.append(f"{club['icon']} {club['name']} — {e['rival']}")
        lines.append(f"🗓 {fmt_day(date)} · {fmt_clock(e.get('time'), e.get('zone'))}")
    return "\n".join(lines)


# --- Поиск (Tavily) и обращение к DeepSeek ----------------------------------

def html_to_text(html: str) -> str:
    """Грубая, но достаточная очистка HTML до читаемого текста — без
    внешних зависимостей вроде BeautifulSoup (её сейчас нет в зависимостях,
    раньше была — см. историю в шапке файла)."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", "\n", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
            .replace("&laquo;", "«").replace("&raquo;", "»")
            .replace("&mdash;", "—").replace("&ndash;", "–").replace("&quot;", '"'))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


async def fetch_url_direct(url: str) -> str:
    """Скачивает страницу НАПРЯМУЮ по HTTP, в обход Tavily — своя очистка
    HTML в текст вместо Tavily Extract.

    Живой инцидент 13.09.2026: Tavily Search и даже Tavily Extract (тот
    самый метод, что был здесь раньше именно ради обхода их же поискового
    ранжирования) отдавали устаревший закэшированный вариант страницы
    Синары («-:-» вместо реального счёта), хотя сам сайт уже был обновлён.
    Прямой запрос в обход Tavily сработал мгновенно и точно — у Tavily
    свой отдельный слой кэширования/индексации, который не поспевает за
    часто обновляемыми спортивными страницами. Раз URL уже известен заранее
    (extract_urls per club) — искать и кэшировать через Tavily незачем,
    можно просто скачать самим."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SportBot/1.0; +sport-bot)"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status != 200:
                    print(f"[DEBUG] прямой фетч {url}: статус {resp.status}")
                    return ""
                raw = await resp.read()
    except (asyncio.TimeoutError, aiohttp.ClientError) as e:
        print(f"[DEBUG] прямой фетч {url}: {type(e).__name__}: {e}")
        return ""
    html = raw.decode("utf-8", errors="ignore")
    return html_to_text(html)


async def extract_urls_tavily(urls: list[str]) -> list[str]:
    """Забирает содержимое конкретных, заранее известных URL напрямую по
    HTTP (см. fetch_url_direct) — не через Tavily. Нужно для источников,
    которые Tavily Search стабильно не выбирает в топ выдачи, сколько ни
    меняй текст запроса (проверено на mfkviz.ru/superliga.rfs.ru для
    Синары — обычный поиск раз за разом приносил только общие страницы
    клуба, хотя нужная страница лиги технически доступна)."""
    if not urls:
        return []
    out = []
    for url in urls:
        content = await fetch_url_direct(url)
        if content:
            print(f"[DEBUG] прямой фетч {url}, символов: {len(content)}")
            # Без обрезки: это заведомо релевантная страница, а нужная
            # строка расписания/результата может оказаться в любом месте
            # дампа страницы.
            out.append(f"Источник {url}:\n{content}")
        else:
            print(f"[DEBUG] прямой фетч {url}: пусто")
            hooks.source_event(hooks.source_for_url(url), "failed", "страница недоступна")
    return out


@hooks.tracked_api("tavily", "search", model="search", source_id="search:tavily",
                   key_from=lambda query, sites=None, extract_urls=None: (query, tuple(extract_urls or ())))
async def collect_web_context_tavily(query: str, sites: list[str], extract_urls: list[str] | None = None) -> str:
    """Поиск через Tavily — сразу очищенный текст, без ручного
    скачивания страниц и парсинга HTML. НЕ используем поле answer —
    оно иногда путает часовые пояса, полагаемся только на content
    из результатов и отдаём это на разбор DeepSeek, как раньше.

    include_domains — глобальный TAVILY_INCLUDE_DOMAINS, а не per-club
    sites (последний параметр сейчас не используется внутри функции,
    но сохранён в сигнатуре — вызывающий код по-прежнему передаёт
    club["sites"]). Полностью неограниченный поиск в живом тесте иногда
    приносил нерелевантные источники (например, UFC/MMA для Автомобилиста);
    старые узкие per-club sites — наоборот, резко ухудшали результаты.
    Этот список — осознанный выбор пользователя как компромисс.

    extract_urls — известные заранее адреса (club["extract_urls"]), которые
    подмешиваются В НАЧАЛО контекста через Tavily Extract, независимо от
    того, что вернул обычный поиск — гарантирует, что модель увидит именно
    эту страницу, а не только то, что Tavily Search решил проранжировать."""
    extracted = await extract_urls_tavily(extract_urls or [])
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.tavily.com/search",
            headers={
                "Authorization": f"Bearer {TAVILY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "query": query,
                # advanced вместо basic — 2 кредита вместо 1, но живой тест
                # показал, что basic плохо ранжирует официальный сайт клуба
                # среди похожих страниц (пропустил кубковый матч Урала,
                # хотя fc-ural.ru был в белом списке). При 6 клубах и
                # редких проверках результата это остаётся далеко в рамках
                # бесплатного лимита Tavily.
                "search_depth": "advanced",
                "max_results": 8,
                "include_domains": TAVILY_INCLUDE_DOMAINS,
            },
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            raw = await resp.text()
            if resp.status != 200:
                print(f"[DEBUG] Tavily статус={resp.status}, тело={raw[:300]!r}")
                hooks.report_failure(f"HTTP {resp.status}")
                return "\n\n---\n\n".join(extracted)
            try:
                data = json.loads(raw)
            except ValueError as e:
                print(f"[DEBUG] не удалось разобрать ответ Tavily: {type(e).__name__}: {e}")
                hooks.report_failure("ответ не разобран")
                return "\n\n---\n\n".join(extracted)

    results = data.get("results", [])
    hooks.report_usage(units=2)          # search_depth=advanced — 2 кредита за запрос
    print(f"[DEBUG] Tavily нашёл {len(results)} источников по «{query}»")
    print(f"[DEBUG] все URL от Tavily: {[r.get('url', '') for r in results]}")
    combined = list(extracted)
    # max_results подняли с 5 до 8, и смотрим все 8, а не только первые 4 —
    # в живом тесте нужный источник (fc-ural.ru) оказался ниже 4-й позиции
    # (Tavily ставил впереди календари клуба-дубля «Урал-2» и чужих команд).
    #
    # Живой прогон 13.09.2026 показал ещё один случай той же болезни: для
    # Синары среди 8 источников были и страница самого матча
    # (superliga.rfs.ru/match/...), и новость «итоги матча» — но обе стояли
    # ниже двух страниц с составами игроков (там счёта нет в принципе), а
    # код брал первые 2 подряд по порядку Tavily и на этом останавливался —
    # DeepSeek получал только составы и честно отвечал «НЕТ», хотя счёт уже
    # был опубликован. Поэтому сначала берём источники, где в тексте похоже
    # на счёт матча (цифра:цифра), и только потом — остальные по порядку.
    score_pattern = re.compile(r"\b\d{1,2}\s*[:\-]\s*\d{1,2}\b")
    candidates = [r for r in results if len(r.get("content", "")) > 150]
    candidates.sort(key=lambda r: 0 if score_pattern.search(r.get("content", "")) else 1)
    for r in candidates:
        content = r.get("content", "")
        url = r.get("url", "")
        print(f"[DEBUG] источник {url}, символов: {len(content)}, фрагмент: {content[:200]!r}")
        combined.append(f"Источник {url}:\n{content}")
        if len(combined) >= 3:
            break
    return "\n\n---\n\n".join(combined)


@hooks.tracked_api("deepseek", "chat", model="deepseek-v4-flash", key_from=lambda messages: (messages,))
async def deepseek_completion(messages: list[dict]) -> str:
    """Вызов DeepSeek через OpenAI-совместимый API с постоянным ключом."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-v4-flash",
                    "messages": messages,
                    "stream": False,
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                raw = await resp.text()
                status = resp.status
    except (asyncio.TimeoutError, aiohttp.ClientError) as e:
        raise RuntimeError(f"DeepSeek: сетевая ошибка {type(e).__name__}: {e}") from e

    try:
        data = json.loads(raw)
    except ValueError as e:
        raise RuntimeError(
            f"DeepSeek вернул не-JSON ответ (статус {status}): {raw[:200]}"
        ) from e
    if status != 200:
        error = data.get("error", {})
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise RuntimeError(message or f"DeepSeek: ошибка запроса, статус {status}")
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"DeepSeek вернул неожиданный ответ: {raw[:200]}") from e
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("DeepSeek вернул пустой ответ")
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    hooks.report_usage(input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"))
    return content


async def ask_deepseek_with_context(question: str, context: str, system_extra: str = "") -> str:
    """Спрашивает DeepSeek по уже готовому контексту, без нового похода в
    поиск — используется для повторных попыток: не тратим лишний запрос
    к Tavily на то же самое, раз материалы уже собраны, и вопрос-уточнение
    может быть длинной инструкцией с цитатой прошлого плохого ответа."""
    today = datetime.datetime.now(YEKB_TZ).strftime("%d.%m.%Y")
    system_text = (
        f"Сегодня {today}. Отвечай по-русски, строго в запрошенном формате, "
        f"без пояснений и markdown-разметки.\n"
        f"Время не пересчитывай между часовыми поясами — бери как в источнике, "
        f"но обязательно указывай пояс словом «мск» или «екб».\n"
        f"Никогда не придумывай факты, которых нет в материалах ниже. Если по "
        f"материалам нельзя точно и однозначно установить ответ — считай, что "
        f"события нет, и отвечай «НЕТ», а не давай предположительный ответ.\n"
        f"Учитывай только основную взрослую команду указанного клуба. Всегда "
        f"игнорируй U-19/U-21/U-23, молодёжные, юношеские, резервные, вторые, "
        f"женские команды, дубли и академии, даже если они стоят выше в выдаче.\n"
        f"Счёт матча в сыром тексте источника не всегда написан привычно "
        f"(«2:1» или «2-1») — иногда это просто два числа рядом с названиями "
        f"команд без разделителя, например «Команда А 6 Команда Б 4». "
        f"Внимательно ищи такие пары чисел рядом с названиями обеих команд "
        f"за нужную дату, а не только явный формат с двоеточием или тире."
    )
    if system_extra:
        system_text += f"\n{system_extra}"
    if context:
        system_text += f"\n\nМатериалы из интернета по теме вопроса:\n{context}"
    else:
        system_text += "\n\nВ интернете ничего найти не удалось."
    messages = [{"role": "system", "content": system_text}, {"role": "user", "content": question}]
    return await deepseek_completion(messages)


async def ask_deepseek(
    question: str,
    sites: list[str],
    search_query: str | None = None,
    extract_urls: list[str] | None = None,
) -> tuple[str, str]:
    """Ищет материалы в интернете и спрашивает DeepSeek. Возвращает
    (ответ, собранный контекст) — контекст нужен, чтобы потом проверить,
    не придумала ли модель факты, и чтобы можно было переспросить без
    нового поиска."""
    context = await collect_web_context_tavily(search_query or question, sites, extract_urls)
    answer = await ask_deepseek_with_context(question, context)
    return answer, context


def find_data_line(answer: str, min_pipes: int = 2) -> str | None:
    """Модель иногда оформляет ответ markdown-таблицей (шапка + разделитель +
    данные, или наоборот — сначала пояснение, потом НЕТ). Поэтому: сначала
    ищем НЕТ по всему ответу целиком — если есть где угодно, данных нет,
    и не важно, что стоит рядом с шапкой таблицы. Только если НЕТ нигде
    не нашлось, ищем строку с данными, с конца — так меньше шанс попасть
    на шапку, если она вообще есть."""
    lines = [ln.strip() for ln in answer.splitlines() if ln.strip()]
    if any(ln.upper() == "НЕТ" for ln in lines):
        return None
    for line in reversed(lines):
        if re.fullmatch(r"[\s|:\-]+", line):
            continue  # разделитель markdown-таблицы вида |---|---|
        if line.count("|") >= min_pipes:
            return line
    return None


FORBIDDEN_SQUAD_RE = re.compile(
    r"(?:\b(?:u|ю)\s*[-–—]?\s*\d{1,2}\b|"
    r"\b(?:молод[её]ж\w*|юнош\w*|резерв\w*|дубл\w*|академ\w*|"
    r"фарм[-\s]?клуб\w*|женск\w*|youth\w*|junior\w*|reserve\w*|"
    r"academy\w*|women\w*|ladies\w*)\b|"
    r"\b(?:вторая|резервная)\s+команд\w*\b)",
    re.IGNORECASE,
)
SECOND_TEAM_SUFFIX_RE = re.compile(r"(?:[-–—]\s*2\b|\s+ii\b|\s+b\s*$)", re.IGNORECASE)


def has_forbidden_squad_label(*texts: str) -> bool:
    """Программный запрет на матчи неосновных составов."""
    return any(
        FORBIDDEN_SQUAD_RE.search(text or "")
        or SECOND_TEAM_SUFFIX_RE.search(text or "")
        for text in texts
    )


def answer_says_no(answer: str) -> bool:
    return any(
        line.strip().upper() == "НЕТ"
        for line in answer.splitlines()
        if line.strip()
    )


def normalize_team_name(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[«»\"'().,]", " ", text)
    text = re.sub(r"[-–—_/]+", " ", text)
    words = re.findall(r"[а-яa-z0-9]+", text)
    generic = {"фк", "хк", "мфк", "fc", "hc", "cf", "afc", "club"}
    return " ".join(word for word in words if word not in generic)


def team_name_matches(reported: str, expected_names: list[str]) -> bool:
    """Сопоставляет русское/латинское имя команды и допускает клубные префиксы."""
    reported_norm = normalize_team_name(reported)
    if not reported_norm:
        return False
    reported_tokens = set(reported_norm.split())
    for expected in expected_names:
        expected_norm = normalize_team_name(expected)
        if not expected_norm:
            continue
        if reported_norm == expected_norm:
            return True
        if len(expected_norm) >= 4 and (
            reported_norm in expected_norm or expected_norm in reported_norm
        ):
            return True
        expected_tokens = {token for token in expected_norm.split() if len(token) >= 4}
        if expected_tokens and reported_tokens & expected_tokens:
            return True
    return False


def parse_goals(text: str) -> int:
    if not re.fullmatch(r"\d{1,2}", text.strip()):
        raise ValueError(f"число голов имеет неверный формат: {text!r}")
    return int(text)


def score_pair_mentioned(first: int, second: int, context: str) -> bool:
    """Проверяет счёт в исходном порядке источника, включая переносы строк."""
    if not context:
        return False
    pattern = rf"(?<!\d){first}\s*[:\-–—]\s*{second}(?!\d)"
    return bool(re.search(pattern, context))


def resolve_reported_result(
    team1: str,
    goals1_raw: str,
    team2: str,
    goals2_raw: str,
    club: dict,
    rival_hint: str,
    context: str,
    *,
    require_context_score: bool = True,
) -> tuple[str, str, int, str, int]:
    """Проверяет участников, вычисляет исход с точки зрения нашего клуба —
    но НЕ переставляет клуб на первое место: возвращает команды и голы в
    том же порядке, в каком их прислала модель (team1/goals1 первыми).

    17.09.2026 живой инцидент: реальный счёт был Авангард 3:2 ОТ
    Автомобилист (Автомобилист — в гостях, проиграл), но раньше здесь
    счёт всегда пересобирался как «club_goals:rival_goals» и клуб
    печатался первым в сообщении — источник (sports.ru) показывает счёт
    как «домашняя:гостевая», и модель, зная, что вопрос именно про
    Автомобилист, подставила ему первую попавшуюся цифру (3) вместо
    того чтобы сверить её с порядком команд в источнике. Результат:
    в чат ушла «ПОБЕДА» вместо поражения. Раз возможность подставить
    клуб первым в самом выводе провоцировала эту путаницу — теперь
    просто печатаем то, что прислала модель, как есть (см. новый
    промпт: «не переставляй») и вычисляем исход отдельно, не трогая
    порядок отображения.

    require_context_score=False — для источников без сырого текста контекста
    (OpenAI web_search отдаёт только готовый ответ модели + ссылки на
    источники, а не сам текст страниц, поэтому score_pair_mentioned для
    такого пути в принципе нечего проверять — это осознанное отличие от
    основного пути Tavily+DeepSeek, а не недосмотр)."""
    if has_forbidden_squad_label(team1, team2):
        raise ValueError(f"обнаружен неосновной состав: {team1!r} — {team2!r}")

    goals1 = parse_goals(goals1_raw)
    goals2 = parse_goals(goals2_raw)
    if require_context_score and not score_pair_mentioned(goals1, goals2, context):
        raise ValueError(
            f"счёт {goals1}:{goals2} в указанном порядке не подтверждается материалами"
        )

    club_names = [club["name"], *club.get("aliases", [])]
    team1_is_club = team_name_matches(team1, club_names)
    team2_is_club = team_name_matches(team2, club_names)
    team1_is_rival = team_name_matches(team1, [rival_hint])
    team2_is_rival = team_name_matches(team2, [rival_hint])

    if team1_is_club and team2_is_rival and not team2_is_club:
        club_goals, rival_goals = goals1, goals2
    elif team2_is_club and team1_is_rival and not team1_is_club:
        club_goals, rival_goals = goals2, goals1
    else:
        raise ValueError(
            f"пары команд не совпадают с ожидаемыми: {team1!r} — {team2!r}"
        )

    if club_goals > rival_goals:
        outcome = "ПОБЕДА"
    elif club_goals == rival_goals:
        outcome = "НИЧЬЯ"
    else:
        outcome = "ПОРАЖЕНИЕ"

    name1 = club["name"] if team1_is_club else rival_hint
    name2 = club["name"] if team2_is_club else rival_hint
    return outcome, name1, goals1, name2, goals2


def looks_like_own_name(tournament: str, club_name: str) -> bool:
    """«Турнир: ХК «Автомобилист»» или «Реал Мадрид — Интер» вместо
    названия турнира («Лига чемпионов») — модель по ошибке подставила в
    поле «Турнир» название самого клуба (иногда вместе с соперником через
    тире). Название турнира не может состоять из имени своего участника.
    Проверено на реальных инцидентах в соседнем боте той же связки."""
    club_core = re.sub(r'[«»"()]', '', club_name).strip().lower()
    tournament_core = re.sub(r'[«»"()]', '', tournament).strip().lower()
    if not club_core or not tournament_core:
        return False
    return club_core in tournament_core or tournament_core in club_core


def rival_mentioned(rival: str, context: str) -> bool:
    """Грубая проверка-страховка: хотя бы одно характерное слово из имени
    соперника должно встречаться в собранном веб-контексте (по стему, без
    учёта падежных окончаний) — иначе похоже, что модель прочитала общую
    сводку с кучей других матчей и приписала клубу чужого соперника."""
    if not context:
        return False
    words = re.findall(r"[А-Яа-яЁёA-Za-z]+", rival)
    candidates = [w for w in words if len(w) >= 4] or words
    if not candidates:
        return False
    context_lower = context.lower()
    for word in candidates:
        stem = word[:max(4, len(word) - 3)].lower()
        if stem in context_lower:
            return True
    return False


def parse_time_to_utc(date_str: str, time_str: str, zone_str: str) -> datetime.datetime | None:
    """«19:00», «мск» → datetime в UTC сегодняшнего дня."""
    m = re.match(r"^(\d{1,2}):(\d{2})$", time_str.strip())
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    zone_key = zone_str.strip().lower()
    if zone_key not in TZ_OFFSET:
        print(f"[DEBUG] неизвестный часовой пояс {zone_str!r}, беру мск по умолчанию")
    offset_hours = TZ_OFFSET.get(zone_key, 3)  # по умолчанию мск
    tz = datetime.timezone(datetime.timedelta(hours=offset_hours))
    try:
        today = datetime.datetime.now(YEKB_TZ).date()
        local_dt = datetime.datetime(today.year, today.month, today.day, hour, minute, tzinfo=tz)
        return local_dt.astimezone(datetime.timezone.utc)
    except ValueError:
        return None


# --- Утренняя проверка расписания -------------------------------------------

def parse_morning_line(answer: str, club_name: str, context: str):
    """Возвращает (Турнир, Время, Пояс, Место, Соперник) или None (искреннее
    «НЕТ» — переспрашивать незачем). Бросает ValueError, если строка с
    данными нашлась, но не разобралась, поле «Турнир» оказалось именем
    клуба, или соперник не подтверждается собранным контекстом — такое
    стоит переспросить у модели ещё раз."""
    line = find_data_line(answer, min_pipes=4)
    if line is None:
        return None
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 5:
        raise ValueError(f"меньше 5 полей: {parts!r}")
    tournament, time_str, zone_str, place, rival = parts[:5]
    if FORBIDDEN_SQUAD_RE.search(tournament) or has_forbidden_squad_label(rival):
        raise ValueError("обнаружен матч молодёжного, резервного или второго состава")
    if looks_like_own_name(tournament, club_name):
        raise ValueError(f"поле «Турнир» похоже на имя клуба/матча, не турнира: {tournament!r}")
    if not rival_mentioned(rival, context):
        raise ValueError(f"соперник '{rival}' не подтверждается материалами")
    return tournament, time_str, zone_str, place, rival


@hooks.with_purpose("schedule_search")
async def check_morning(club: dict, today: datetime.date | None = None) -> dict | None:
    """Если клуб играет сегодня — возвращает данные матча; None — матча
    действительно нет. Сбой источника/разбора — SourceError (а не None):
    «нет матча» и «не смогли проверить» раньше были неразличимы."""
    today = today or ekb_now().date()
    prompt = (
        f"Играет ли {club['name']} сегодня, {today:%d.%m.%Y}, в любом "
        f"официальном турнире? Если да, найди турнир, время начала, место "
        f"проведения и соперника.\n"
        f"Ответь СТРОГО последней строкой в таком виде:\n"
        f"Турнир|ЧЧ:ММ|ПОЯС|Место|Соперник\n"
        f"ПОЯС — слово «мск» или «екб» в зависимости от того, в каком поясе "
        f"указано время в источнике.\n"
        f"Учитывай ТОЛЬКО основную взрослую команду. Матчи U-19/U-21/U-23, "
        f"молодёжных, юношеских, резервных, вторых, женских команд, дублей "
        f"и академий считать матчами клуба НЕЛЬЗЯ.\n"
        f"Если сегодня матча нет — последней строкой напиши НЕТ."
    )
    # Отдельный (короче и без инструкций по формату) поисковый запрос —
    # с явным уточнением «основная команда»: полнотекстовый prompt выше
    # в качестве поискового запроса путал Tavily, тот путал клуб с его
    # дублирующим составом (например, «Урал» с «Урал-2») и вместо
    # официального сайта клуба приносил чужие календари.
    search_query = (
        f"{club['name']} официальный сайт расписание ближайший матч "
        f"{today:%d.%m.%Y} основная команда, не дубль и не молодёжный состав"
        + (f" {club['search_hint']}" if club.get("search_hint") else "")
    )
    try:
        answer, context = await ask_deepseek(prompt, club["sites"], search_query, club.get("extract_urls"))
        answer = answer.strip()
        print(f"[DEBUG] {club['name']} (утро): {answer!r}")
        try:
            parsed = parse_morning_line(answer, club["name"], context)
        except ValueError as e:
            print(f"[DEBUG] {club['name']}: {e}, переспрашиваю ещё раз (тот же контекст, без нового поиска)")
            retry_extra = (
                f"Твой предыдущий ответ был в неправильном формате, называл "
                f"соперника, которого нет в материалах выше, или в поле "
                f"«Турнир» указал название самого клуба вместо названия "
                f"соревнования — не годится: {answer!r}.\n"
                f"Любой матч U-19/U-21/U-23, молодёжной, юношеской, резервной, "
                f"второй или женской команды, дубля или академии запрещён.\n"
                f"Ответь ЕЩЁ РАЗ и ТОЛЬКО одной строкой, без пояснений, строго "
                f"в виде «Турнир|ЧЧ:ММ|ПОЯС|Место|Соперник». «Турнир» — это "
                f"название соревнования (например «Лига чемпионов»), а НЕ "
                f"название клуба и не пара «команда — соперник». Указывай "
                f"только то, что явно и однозначно написано в материалах выше "
                f"именно про {club['name']}. Если нет уверенности — одним "
                f"словом НЕТ."
            )
            answer = (await ask_deepseek_with_context(prompt, context, retry_extra)).strip()
            print(f"[DEBUG] {club['name']} (утро, повтор): {answer!r}")
            try:
                parsed = parse_morning_line(answer, club["name"], context)
            except ValueError as e2:
                print(f"[DEBUG] {club['name']}: после повтора всё ещё не разобралось ({e2}), пропускаю")
                raise SourceError(f"ответ не разобран: {e2}") from e2
    except SourceError:
        raise
    except Exception as e:
        print(f"[DEBUG] ошибка при утренней проверке {club['name']}: {type(e).__name__}: {e}")
        raise SourceError(f"{type(e).__name__}: {e}") from e
    if parsed is None:
        return None
    tournament, time_str, zone_str, place, rival = parsed
    start_utc = local_to_utc(today, time_str, zone_str)
    return {
        "tournament": tournament, "time": time_str, "zone": zone_str,
        "place": place, "rival": rival,
        "start_utc": start_utc.isoformat() if start_utc else None,
        "match_date": today.isoformat(),
    }


def format_morning(club: dict, match: dict) -> str:
    return (
        f"{club['icon']} Сегодня играет {club['name']}\n"
        f"Турнир: {match['tournament']}\n"
        f"Время: {match['time']} ({match['zone']})\n"
        f"Место: {match['place']}\n"
        f"Соперник: {match['rival']}"
    )


def format_time_changed(club: dict, match: dict, old: dict) -> str:
    return (
        f"⚠️ Внимание, изменилось время матча!\n"
        f"{club['icon']} {club['name']} — {match['rival']}\n"
        f"Сегодня · {match['time']} ({match['zone']})\n"
        f"Ранее было указано {old['time']} ({old['zone']}).\n"
        f"Турнир: {match['tournament']}\n"
        f"Место: {match['place']}"
    )


def format_moved_here(club: dict, match: dict, old: dict) -> str:
    old_day = datetime.date.fromisoformat(old["date"])
    return (
        f"⚠️ Внимание, матч перенесён на сегодня!\n"
        f"{club['icon']} {club['name']} — {match['rival']}\n"
        f"Сегодня · {match['time']} ({match['zone']})\n"
        f"Ранее: {fmt_day(old_day, long=True)} · {fmt_clock(old.get('time'), old.get('zone'))}\n"
        f"Турнир: {match['tournament']}\n"
        f"Место: {match['place']}"
    )


def format_moved_away(club: dict, old: dict, new_date: datetime.date, new_time, new_zone) -> str:
    return (
        f"⚠️ Внимание, матч перенесён!\n"
        f"{club['icon']} {club['name']} — {old['rival']}\n"
        f"Был назначен на сегодня · {fmt_clock(old.get('time'), old.get('zone'))}\n"
        f"Теперь: {fmt_day(new_date, long=True)} · {fmt_clock(new_time, new_zone)}"
    )


def format_cancelled(club: dict, old: dict) -> str:
    return (
        f"⚠️ Внимание, матч отменён!\n"
        f"{club['icon']} {club['name']} — {old['rival']}\n"
        f"Был назначен на сегодня · {fmt_clock(old.get('time'), old.get('zone'))}."
    )


def find_schedule_entry(sched: dict, club_key: str, rival: str, date_iso: str | None = None) -> dict | None:
    for e in sched["entries"]:
        if e.get("club_key") != club_key or not team_name_matches(rival, [e.get("rival", "")]):
            continue
        if date_iso is None or e.get("date") == date_iso:
            return e
    return None


def times_differ(old: dict, match: dict, day: datetime.date) -> bool:
    """Время изменилось, если отличаются И «часы на табло», И момент в UTC
    (иначе это всего лишь разное написание пояса у одного и того же времени)."""
    if not old.get("time") or not match.get("time"):
        return False
    if old["time"] == match["time"]:
        return False
    old_utc = local_to_utc(day, old["time"], old.get("zone"))
    new_utc = local_to_utc(day, match["time"], match.get("zone"))
    return old_utc != new_utc


def classify_morning_match(sched: dict, club: dict, match: dict, today: datetime.date, state: dict | None = None) -> dict:
    """Сравнивает найденный утром матч с ориентиром — недельной афишей.
    kind: normal | time_changed | moved_here. Запись афиши на другую дату считается
    «прежней датой» перенесённого матча, только если тот матч не отслеживался
    и не сыгран (у Синары бывают серии из двух игр с одним соперником подряд)."""
    same_day = find_schedule_entry(sched, club["key"], match["rival"], today.isoformat())
    if same_day:
        if times_differ(same_day, match, today):
            return {"kind": "time_changed", "old": same_day}
        return {"kind": "normal", "old": same_day}
    for other in sched["entries"]:
        if other.get("club_key") != club["key"] or not team_name_matches(match["rival"], [other.get("rival", "")]):
            continue
        try:
            other_day = datetime.date.fromisoformat(other["date"])
        except ValueError:
            continue
        if other_day == today or abs((other_day - today).days) > 21:
            continue
        already_played = state and any(
            r.get("club_key") == club["key"] and r.get("match_date") == other["date"]
            and team_name_matches(r.get("rival", ""), [match["rival"]])
            for r in state["matches"].values()
        )
        if not already_played:
            return {"kind": "moved_here", "old": other}
    return {"kind": "normal", "old": None}


async def resolve_missing_scheduled(club: dict, entry: dict, today: datetime.date) -> dict | None:
    """В афише матч был назначен на сегодня, а утренняя проверка его не нашла.
    Детерминированно смотрим календарь клуба: тот же соперник на другую
    дату → перенос; пометка «отменён» → отмена; иначе None (не гадаем)."""
    try:
        fixtures = await fetch_club_calendar(club, today)
    except SourceError as e:
        print(f"[DEBUG] {club['name']}: не удалось проверить перенос/отмену: {e}")
        return None
    same = [f for f in fixtures if team_name_matches(f["rival"], [entry["rival"]])]
    entry_day = datetime.date.fromisoformat(entry["date"])
    for f in same:
        if f["date"] == entry_day and f["cancelled"]:
            return {"kind": "cancelled"}
    for f in same:
        if f["date"] != entry_day and f["date"] >= today and not f["finished"] and not f["cancelled"]:
            return {"kind": "moved", "date": f["date"], "time": f["time"], "zone": f["zone"],
                    "tournament": f["tournament"]}
    return None


def build_match_record(club: dict, match: dict, today: datetime.date) -> dict:
    rec = {
        "match_id": match_identity(club["key"], today.isoformat(), match["rival"]),
        "club_key": club["key"], "sport": sport_family(club), "match_date": today.isoformat(),
        "tournament": match["tournament"], "time": match["time"], "zone": match["zone"],
        "place": match["place"], "rival": match["rival"], "start_utc": match.get("start_utc"),
    }
    return normalize_match(rec)


async def deliver_announcement(bot: Bot, state: dict, rec: dict, now: datetime.datetime) -> bool:
    """Отправка анонса. Отметка announce_sent — только после подтверждённой отправки."""
    if rec.get("announce_sent") or not rec.get("announce_text"):
        return True
    sent = await send_to_group(bot, rec["announce_text"])
    club = club_by_key(rec["club_key"])
    alert_key = f"announce:{rec['match_id']}"
    if sent:
        rec["announce_sent"] = True
        if rec["status"] == "found":
            rec["status"] = "announced"
        save_state(state)
        await clear_alert(bot, alert_key, f"✅ Трибун\nАнонс матча {club['name']} отправлен.", now)
    else:
        save_state(state)
        if not DRY_RUN:
            await raise_alert(
                bot, alert_key,
                f"⚠️ Трибун\nНе удалось отправить в MAX анонс матча {club['name']}.\n"
                f"Следующая попытка будет автоматически.", now)
    return sent


async def run_morning(bot: Bot, now: datetime.datetime, clubs: list, *, with_intro: bool) -> list:
    """Утренняя проверка для перечисленных клубов. Возвращает клубы, для
    которых проверка сорвалась (SourceError) — их повторяют позже."""
    today = now.astimezone(YEKB_TZ).date()
    state = load_state(now)
    sched = load_schedule()
    found, failed = [], []
    for club in clubs:
        try:
            match = await check_morning(club, today)
        except SourceError as e:
            print(f"[DEBUG] {club['name']}: утренняя проверка сорвалась: {e}")
            failed.append(club)
            if not DRY_RUN:
                await raise_alert(
                    bot, f"morning:{club['key']}",
                    f"⚠️ Трибун\nНе удалось проверить расписание на сегодня: {club['name']}.\n"
                    f"Повторю автоматически.", now)
            continue
        await clear_alert(bot, f"morning:{club['key']}",
                          f"✅ Трибун\nРасписание на сегодня получено: {club['name']}.", now)
        if match:
            found.append((club, match))

    # матчи, назначенные на сегодня в афише, но утром не найденные: перенос / отмена
    failed_keys = {c["key"] for c in failed}
    found_keys = {c["key"] for c, _ in found}
    extra_messages = []
    for entry in list(sched["entries"]):
        if entry.get("date") != today.isoformat() or entry["club_key"] in found_keys:
            continue
        club = club_by_key(entry["club_key"])
        if not club or club["key"] in failed_keys or club not in clubs:
            continue
        verdict = await resolve_missing_scheduled(club, entry, today)
        if not verdict:
            continue
        if verdict["kind"] == "moved":
            extra_messages.append(format_moved_away(club, entry, verdict["date"], verdict["time"], verdict["zone"]))
            schedule_remove(sched, entry["key"])
            schedule_upsert(sched, schedule_entry(club, verdict["date"], verdict["time"], verdict["zone"],
                                                  verdict["tournament"], entry["rival"], "calendar"))
        else:
            extra_messages.append(format_cancelled(club, entry))
            schedule_remove(sched, entry["key"])

    outgoing = []     # (record, is_normal)
    normal_clubs = []
    for club, match in found:
        verdict = classify_morning_match(sched, club, match, today, state)
        rec = build_match_record(club, match, today)
        existing = state["matches"].get(rec["match_id"])
        if existing:
            # повторный прогон в тот же день: ничего не анонсируем и не публикуем заново
            existing.update({k: rec[k] for k in ("tournament", "time", "zone", "place", "start_utc")})
            continue
        if verdict["kind"] == "time_changed":
            rec["announce_text"] = format_time_changed(club, match, verdict["old"])
        elif verdict["kind"] == "moved_here":
            rec["announce_text"] = format_moved_here(club, match, verdict["old"])
            schedule_remove(sched, verdict["old"]["key"])
        else:
            rec["announce_text"] = format_morning(club, match)
            normal_clubs.append(club)
        state["matches"][rec["match_id"]] = rec
        outgoing.append((rec, verdict["kind"] == "normal"))
        schedule_upsert(sched, schedule_entry(club, today, match["time"], match["zone"],
                                              match["tournament"], match["rival"], "morning"))

    # общая вводная фраза — один раз на утро, в первом обычном анонсе
    if with_intro and normal_clubs:
        intro = pick_intro(intro_kind([c for c, _ in found]))
        for rec, is_normal in outgoing:
            if is_normal:
                rec["announce_text"] = f"{intro}\n\n{rec['announce_text']}"
                break

    save_state(state)           # сначала фиксируем найденное, потом отправляем
    save_schedule(sched)
    for text in extra_messages:
        await send_to_group(bot, text)
    for rec, _ in outgoing:
        await deliver_announcement(bot, state, rec, now)
    return failed


async def job_morning(bot: Bot, now: datetime.datetime | None = None) -> None:
    now = now or utc_now()
    print("[DEBUG] === утренняя проверка расписания ===")
    failed = await run_morning(bot, now, CLUBS, with_intro=True)
    state = load_state(now)
    state["meta"]["morning_failed"] = {
        "date": now.astimezone(YEKB_TZ).date().isoformat(),
        "clubs": [c["key"] for c in failed],
    }
    save_state(state)


async def retry_failed_morning(bot: Bot, now: datetime.datetime) -> None:
    """Сорвавшиеся утренние проверки повторяем на следующих тиках (до MORNING_RETRY_UNTIL_HOUR)."""
    local = now.astimezone(YEKB_TZ)
    state = load_state(now)
    info = state["meta"].get("morning_failed") or {}
    if info.get("date") != local.date().isoformat() or not info.get("clubs") or local.hour >= MORNING_RETRY_UNTIL_HOUR:
        return
    clubs = [c for c in (club_by_key(k) for k in info["clubs"]) if c]
    failed = await run_morning(bot, now, clubs, with_intro=False)
    state = load_state(now)
    state["meta"]["morning_failed"] = {"date": info["date"], "clubs": [c["key"] for c in failed]}
    save_state(state)


async def retry_unsent_announcements(bot: Bot, now: datetime.datetime) -> None:
    """Анонс, который не удалось отправить, повторяем до начала матча."""
    state = load_state(now)
    for rec in list(state["matches"].values()):
        if rec["announce_sent"] or not rec.get("announce_text"):
            continue
        start = parse_iso(rec.get("start_utc"))
        if start and now >= start:
            continue
        await deliver_announcement(bot, state, rec, now)


# --- Проверка результата ----------------------------------------------------

def parse_result_line_football(
    answer: str, club: dict, rival_hint: str, context: str,
    *, require_context_score: bool = True,
) -> tuple[str, str, int, str, int] | None:
    """Возвращает исход и обе команды/голы в порядке, как прислала модель
    (без перестановки клуба на первое место — см. resolve_reported_result)."""
    line = find_data_line(answer, min_pipes=3)
    if line is None:
        if answer_says_no(answer):
            return None
        raise ValueError("нет строки результата из 4 полей")
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 4:
        raise ValueError(f"меньше 4 полей: {parts!r}")
    return resolve_reported_result(
        *parts[:4], club, rival_hint, context,
        require_context_score=require_context_score,
    )


def _candidate_text(club: dict, hockey: bool, parsed: tuple) -> str:
    if hockey:
        outcome, name1, goals1, name2, goals2, method = parsed
        return format_result_hockey(club, outcome, name1, goals1, name2, goals2, method)
    outcome, name1, goals1, name2, goals2 = parsed
    return format_result_football(club, outcome, name1, goals1, name2, goals2)


def _make_candidate(club: dict, hockey: bool, parsed: tuple, context: str, origin: str) -> dict:
    name1, goals1, name2, goals2 = parsed[1], parsed[2], parsed[3], parsed[4]
    club_goals, rival_goals = (goals1, goals2) if name1 == club["name"] else (goals2, goals1)
    return {
        "family": "hockey" if hockey else "football", "origin": origin, "context": context,
        "club_goals": club_goals, "rival_goals": rival_goals,
        "method": parsed[5] if hockey else None,
        "text": _candidate_text(club, hockey, parsed),
    }


@hooks.with_purpose("result_search")
async def fetch_result_football(club: dict, rival_hint: str, on_date: datetime.date | None = None) -> dict | None:
    """Результат матча по основному пути (Tavily + прямые страницы → DeepSeek).
    None — матч ещё не завершился; SourceError — сбой источника/разбора.
    Разбор и проверки прежние; вместо готового текста возвращается кандидат
    (счёт + контекст + текст), чтобы его можно было сверить и сохранить."""
    today = on_date or ekb_now().date()
    prompt = (
        f"Завершился ли сегодня, {today:%d.%m.%Y}, матч {club['name']} "
        f"против {rival_hint}? Если да, найди точный счёт.\n"
        f"Ответь СТРОГО последней строкой:\n"
        f"Команда 1|Голы 1|Команда 2|Голы 2\n"
        f"ВАЖНО про порядок: пиши команды и голы СТРОГО в том порядке, как "
        f"в источнике (обычно сначала домашняя команда, потом гостевая — "
        f"счёт на странице часто дан именно так, «домашние:гостевые»). НЕ "
        f"переставляй {club['name']} на первое место просто потому, что "
        f"вопрос про него, если в источнике он идёт вторым — это уже "
        f"приводило к ошибке, кто выиграл. Голы — только целые числа. "
        f"Исход матча не пиши: программа вычислит его сама.\n"
        f"Если матч ещё не завершился — последней строкой напиши НЕТ."
    )
    search_query = (
        f"{club['name']} против {rival_hint} счёт результат сегодня "
        f"{today:%d.%m.%Y} основная команда, не дубль и не молодёжный состав"
        + (f" {club['search_hint']}" if club.get("search_hint") else "")
    )
    try:
        answer, context = await ask_deepseek(prompt, club["sites"], search_query, club.get("extract_urls"))
        answer = answer.strip()
        print(f"[DEBUG] {club['name']} (результат): {answer!r}")
        try:
            parsed = parse_result_line_football(answer, club, rival_hint, context)
        except ValueError as e:
            print(f"[DEBUG] {club['name']}: {e}, переспрашиваю ещё раз (тот же контекст, без нового поиска)")
            retry_extra = (
                f"Твой предыдущий ответ был в неправильном формате — не "
                f"годится: {answer!r}.\n"
                f"Ответь ЕЩЁ РАЗ и ТОЛЬКО одной строкой, без пояснений, строго "
                f"в виде «Команда 1|Голы 1|Команда 2|Голы 2». Сохрани порядок "
                f"команд и счёта из источника КАК ЕСТЬ, не переставляй "
                f"{club['name']} вперёд; голы — только числа. Не пиши "
                f"исход матча. Если матч ещё не завершился — "
                f"ответь одним словом НЕТ."
            )
            answer = (await ask_deepseek_with_context(prompt, context, retry_extra)).strip()
            print(f"[DEBUG] {club['name']} (результат, повтор): {answer!r}")
            try:
                parsed = parse_result_line_football(answer, club, rival_hint, context)
            except ValueError as e2:
                print(f"[DEBUG] {club['name']}: после повтора всё ещё не разобралось ({e2}), пропускаю")
                raise SourceError(f"ответ не разобран: {e2}") from e2
    except SourceError:
        raise
    except Exception as e:
        print(f"[DEBUG] ошибка при проверке результата {club['name']}: {type(e).__name__}: {e}")
        raise SourceError(f"{type(e).__name__}: {e}") from e
    if parsed is None:
        return None
    return _make_candidate(club, False, parsed, context, "search")


async def check_result_football(club: dict, rival_hint: str) -> str | None:
    """Прежний интерфейс: готовый текст результата или None."""
    try:
        cand = await fetch_result_football(club, rival_hint)
    except SourceError:
        return None
    return cand["text"] if cand else None


@hooks.with_purpose("result_search")
async def fetch_result_hockey(club: dict, rival_hint: str, on_date: datetime.date | None = None) -> dict | None:
    """То же для хоккея (дополнительно — способ завершения: основное время / ОТ / буллиты)."""
    today = on_date or ekb_now().date()
    prompt = (
        f"Завершился ли сегодня, {today:%d.%m.%Y}, матч {club['name']} "
        f"против {rival_hint}? Если да, найди точный счёт и способ "
        f"завершения матча.\n"
        f"Ответь СТРОГО последней строкой:\n"
        f"Команда 1|Голы 1|Команда 2|Голы 2|СПОСОБ\n"
        f"ВАЖНО про порядок: пиши команды и голы СТРОГО в том порядке, как "
        f"в источнике (обычно сначала домашняя команда, потом гостевая — "
        f"счёт на странице часто дан именно так, «домашние:гостевые»). НЕ "
        f"переставляй {club['name']} на первое место просто потому, что "
        f"вопрос про него, если в источнике он идёт вторым — это уже "
        f"приводило к ошибке, кто выиграл. Голы — только целые числа. "
        f"Исход программа вычислит сама. "
        f"СПОСОБ — одно слово: ОСНОВНОЕ (решилось в основное время), ОТ "
        f"(овертайм) или БУЛЛИТЫ.\n"
        f"Если матч ещё не завершился — последней строкой напиши НЕТ."
    )
    search_query = (
        f"{club['name']} против {rival_hint} счёт результат сегодня "
        f"{today:%d.%m.%Y} основная команда, не дубль и не молодёжный состав"
        + (f" {club['search_hint']}" if club.get("search_hint") else "")
    )
    try:
        answer, context = await ask_deepseek(prompt, club["sites"], search_query, club.get("extract_urls"))
        answer = answer.strip()
        print(f"[DEBUG] {club['name']} (результат): {answer!r}")
        try:
            parsed = parse_result_line_hockey(answer, club, rival_hint, context)
        except ValueError as e:
            print(f"[DEBUG] {club['name']}: {e}, переспрашиваю ещё раз (тот же контекст, без нового поиска)")
            retry_extra = (
                f"Твой предыдущий ответ был в неправильном формате — не "
                f"годится: {answer!r}.\n"
                f"Ответь ЕЩЁ РАЗ и ТОЛЬКО одной строкой, без пояснений, строго "
                f"в виде «Команда 1|Голы 1|Команда 2|Голы 2|СПОСОБ». Сохрани "
                f"порядок команд и счёта из источника КАК ЕСТЬ, не переставляй "
                f"{club['name']} вперёд; голы — только числа. "
                f"Не пиши исход матча. Если матч ещё не "
                f"завершился — ответь одним словом НЕТ."
            )
            answer = (await ask_deepseek_with_context(prompt, context, retry_extra)).strip()
            print(f"[DEBUG] {club['name']} (результат, повтор): {answer!r}")
            try:
                parsed = parse_result_line_hockey(answer, club, rival_hint, context)
            except ValueError as e2:
                print(f"[DEBUG] {club['name']}: после повтора всё ещё не разобралось ({e2}), пропускаю")
                raise SourceError(f"ответ не разобран: {e2}") from e2
    except SourceError:
        raise
    except Exception as e:
        print(f"[DEBUG] ошибка при проверке результата {club['name']}: {type(e).__name__}: {e}")
        raise SourceError(f"{type(e).__name__}: {e}") from e
    if parsed is None:
        return None
    return _make_candidate(club, True, parsed, context, "search")


async def check_result_hockey(club: dict, rival_hint: str) -> str | None:
    """Прежний интерфейс: готовый текст результата или None."""
    try:
        cand = await fetch_result_hockey(club, rival_hint)
    except SourceError:
        return None
    return cand["text"] if cand else None


def format_result_football(club: dict, outcome: str, name1: str, goals1: int, name2: str, goals2: int) -> str:
    if "ПОБЕД" in outcome:
        head = "🎆🎆🎆 ПОБЕДА!!!"
    elif "НИЧЬ" in outcome:
        head = "🤝 НИЧЬЯ!"
    else:
        head = "😔 Увы, сегодня проиграли"
    return f"{head}\n\n{club['icon']} {name1} {goals1}:{goals2} {name2}"


def parse_result_line_hockey(
    answer: str, club: dict, rival_hint: str, context: str,
    *, require_context_score: bool = True,
) -> tuple[str, str, int, str, int, str] | None:
    """Возвращает исход, обе команды/голы в порядке, как прислала модель
    (см. resolve_reported_result), и способ завершения матча."""
    line = find_data_line(answer, min_pipes=4)
    if line is None:
        if answer_says_no(answer):
            return None
        raise ValueError("нет строки результата из 5 полей")
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 5:
        raise ValueError(f"меньше 5 полей: {parts!r}")
    outcome, name1, goals1, name2, goals2 = resolve_reported_result(
        *parts[:4], club, rival_hint, context,
        require_context_score=require_context_score,
    )
    if outcome == "НИЧЬЯ":
        raise ValueError("для хоккейного матча получен ничейный итоговый счёт")
    method = parts[4].upper()
    if method not in {"ОСНОВНОЕ", "ОТ", "БУЛЛИТЫ"}:
        raise ValueError(f"неизвестный способ завершения матча: {method!r}")
    return outcome, name1, goals1, name2, goals2, method


def format_result_hockey(club: dict, outcome: str, name1: str, goals1: int, name2: str, goals2: int, method: str) -> str:
    won = "ПОБЕД" in outcome
    extra = "ОТ" in method
    shootout = "БУЛЛИТ" in method

    if won and shootout:
        head = "🎆🎆🎆 ПОБЕДА ПО БУЛЛИТАМ!!!"
    elif won and extra:
        head = "🎆🎆🎆 ПОБЕДА В ОВЕРТАЙМЕ!!!"
    elif won:
        head = "🎆🎆🎆 ПОБЕДА!!!"
    elif shootout:
        head = "😔 Увы, сегодня проиграли по буллитам"
    elif extra:
        head = "😔 Увы, сегодня проиграли в овертайме"
    else:
        head = "😔 Увы, сегодня проиграли"

    tail = " ОТ" if extra else (" Б" if shootout else "")
    return f"{head}\n\n{club['icon']} {name1} {goals1}:{goals2}{tail} {name2}"


# --- Резерв: OpenAI web_search для "зависших" результатов -------------------

@hooks.tracked_api("openai", "responses_web_search", model="gpt-5.5", source_id="search:openai", key_from=lambda prompt: (prompt,))
async def ask_openai_websearch(prompt: str) -> str:
    """Запрос к OpenAI Responses API с инструментом web_search. Используется
    ТОЛЬКО как резерв (см. OPENAI_FALLBACK_AFTER_HOURS и check_result_openai
    ниже), когда основной путь Tavily+DeepSeek+extract_urls не смог найти
    результат — не как замена основному пути.

    Проверено вживую 15.09.2026 на реальном матче: ответ точный, но расход
    токенов большой (~9200 токенов на один запрос, из них подавляющее
    большинство — невидимые reasoning-токены), поэтому пускаем это в ход
    только для редких "зависших" случаев, а не на каждой проверке."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY не задан")
    payload = {
        "model": "gpt-5.5",
        "tools": [{"type": "web_search"}],
        "input": prompt,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.openai.com/v1/responses",
                data=data,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                },
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                raw = await resp.text()
                status = resp.status
    except (asyncio.TimeoutError, aiohttp.ClientError) as e:
        raise RuntimeError(f"OpenAI: сетевая ошибка {type(e).__name__}: {e}") from e

    try:
        result = json.loads(raw)
    except ValueError as e:
        raise RuntimeError(f"OpenAI вернул не-JSON ответ (статус {status}): {raw[:200]}") from e
    if status != 200:
        error = result.get("error", {})
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise RuntimeError(message or f"OpenAI: ошибка запроса, статус {status}")

    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    hooks.report_usage(input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"))
    for item in result.get("output", []):
        if item.get("type") == "message":
            for block in item.get("content", []):
                if block.get("type") == "output_text" and block.get("text", "").strip():
                    return block["text"]
    raise RuntimeError("OpenAI вернул ответ без итогового текста")


@hooks.with_purpose("fallback")
async def fetch_result_openai(club: dict, rival_hint: str, on_date: datetime.date | None = None) -> dict | None:
    """Резервная проверка результата через OpenAI web_search — см.
    ask_openai_websearch. OpenAI отдаёт только готовый текст ответа и
    ссылки на источники, без сырого текста страниц, поэтому здесь
    сознательно отключена проверка score_pair_mentioned (require_context_
    score=False) — её просто не с чем сверять; проверки на неосновной
    состав и совпадение названий команд/соперника остаются в силе. Без
    повторного переспроса при плохом формате (в отличие от DeepSeek-пути) —
    это редкий резерв, при неудаче попробуем на следующем тике. Возвращает
    кандидата или None (сбой/нет результата): OpenAI — дополнительный
    инструмент, сбой его НЕ блокирует публикацию и не считается сбоем бота."""
    today = on_date or ekb_now().date()
    is_hockey = club["sport"] == "hockey"
    order_warning = (
        f"ВАЖНО про порядок: пиши команды и голы СТРОГО в том порядке, как "
        f"в источнике (обычно сначала домашняя команда, потом гостевая — "
        f"счёт на странице часто дан именно так, «домашние:гостевые»). НЕ "
        f"переставляй {club['name']} на первое место просто потому, что "
        f"вопрос про него, если в источнике он идёт вторым — это уже "
        f"приводило к ошибке, кто выиграл.\n"
    )
    base = (
        f"Завершился ли сегодня, {today:%d.%m.%Y}, матч {club['name']} "
        f"против {rival_hint}? Учитывай только основную взрослую команду — "
        f"матчи U-19/U-21/U-23, молодёжных, юношеских, резервных, вторых, "
        f"женских команд, дублей и академий не считаются.\n"
        f"Если матч завершился, найди точный счёт"
        + (" и способ завершения матча.\n" if is_hockey else ".\n")
    )
    if is_hockey:
        prompt = (
            base
            + "Ответь СТРОГО последней строкой:\n"
            "Команда 1|Голы 1|Команда 2|Голы 2|СПОСОБ\n"
            + order_warning +
            "Голы — только целые числа. Исход не пиши: программа вычислит сама. "
            "СПОСОБ — одно слово: ОСНОВНОЕ (решилось в основное время), ОТ "
            "(овертайм) или БУЛЛИТЫ.\n"
            "Если матч ещё не завершился — последней строкой напиши НЕТ."
        )
    else:
        prompt = (
            base
            + "Ответь СТРОГО последней строкой:\n"
            "Команда 1|Голы 1|Команда 2|Голы 2\n"
            + order_warning +
            "Голы — только целые числа. Исход не пиши: программа вычислит сама.\n"
            "Если матч ещё не завершился — последней строкой напиши НЕТ."
        )
    try:
        answer = (await ask_openai_websearch(prompt)).strip()
        print(f"[DEBUG] {club['name']} (результат, OpenAI): {answer!r}")
        if is_hockey:
            parsed = parse_result_line_hockey(answer, club, rival_hint, "", require_context_score=False)
        else:
            parsed = parse_result_line_football(answer, club, rival_hint, "", require_context_score=False)
        if parsed is None:
            return None
        return _make_candidate(club, is_hockey, parsed, "", "openai")
    except Exception as e:
        print(f"[DEBUG] ошибка при OpenAI-проверке результата {club['name']}: {type(e).__name__}: {e}")
        return None


async def check_result_openai(club: dict, rival_hint: str) -> str | None:
    """Прежний интерфейс: готовый текст результата или None."""
    cand = await fetch_result_openai(club, rival_hint)
    return cand["text"] if cand else None


# --- Защита от неверного счёта: сверка источников -----------------------------
#
# Защитный слой НАД прежним разбором (разбор результата не менялся). Контекст,
# собранный для модели, состоит из отдельных источников («Источник <url>:»).
# Каждый источник читается независимо: ищем счёт рядом с названиями обеих
# команд и признаки «матч идёт» / «завершён». Правила:
#   • источники согласны или молчат → публикуем как раньше (один упавший
#     сайт публикацию не блокирует);
#   • источник с ДАТОЙ СЕГОДНЯШНЕГО МАТЧА показывает другой счёт, либо один
#     говорит «идёт», а другой «завершён» → конфликт: в MAX ничего не уходит;
#   • если подтверждающих источников меньше двух и настроен OpenAI — счёт
#     перепроверяется независимым поиском; расхождение → конфликт, а недоступность
#     OpenAI ничего не блокирует.
# Нейросеть счёт не «угадывает»: она лишь даёт ещё одно независимое чтение.

LIVE_RE = re.compile(r"(?:матч\s+идёт|матч\s+идет|идёт\s+матч|идет\s+матч|в\s+прямом\s+эфире|\blive\b|"
                     r"\d\s*-?й\s+(?:период|тайм)|перерыв\s+между)", re.IGNORECASE)
FINAL_RE = re.compile(r"(?:заверш[её]н|матч\s+окончен|окончен\b|финальный\s+счёт|финальный\s+счет|\bFT\b)", re.IGNORECASE)
MONTH_BEFORE_RE = re.compile(r"(?:" + "|".join(MONTHS_RU_GEN) + r")\s*$", re.IGNORECASE)
TIME_ZONE_AFTER_RE = re.compile(r"^\s*(?:мск|екб|по\s)", re.IGNORECASE)


def split_context_sources(context: str) -> list:
    chunks = []
    for part in re.split(r"\n\n---\n\n", context or ""):
        m = re.match(r"Источник (.+?):\n(.*)", part, re.S)
        if m:
            chunks.append((m.group(1), m.group(2)))
        elif part.strip():
            chunks.append(("?", part))
    return chunks


def _team_stems(names: list) -> list:
    """Основы слов названия (без падежных окончаний); короткие слова
    («ХК», «Ак») берём, только если длинных нет."""
    words = [w for name in names for w in re.findall(r"[А-Яа-яЁёA-Za-z]+", name)]
    chosen = [w for w in words if len(w) >= 4] or words
    return list(dict.fromkeys(re.escape(w[:max(4, len(w) - 3)].lower()) for w in chosen))


def _team_regex(names: list) -> str:
    stems = _team_stems(names)
    return "(?:" + "|".join(stems) + r")\w*" if stems else r"(?!x)x"


def _date_near(text: str, start: int, end: int, day: datetime.date) -> bool:
    """Рядом с упоминанием счёта указана дата матча (или «сегодня»)."""
    window = text[max(0, start - 250): end + 250].lower()
    forms = [f"{day.day:02d}.{day.month:02d}", f"{day.day}.{day.month:02d}",
             f"{day.day} {MONTHS_RU_GEN[day.month - 1]}", f"{day.day:02d} {MONTHS_RU_GEN[day.month - 1]}",
             "сегодня"]
    return any(form in window for form in forms)


def extract_score_claims(text: str, club: dict, rival: str, day: datetime.date) -> list:
    """Счёт рядом с названиями обеих команд в формате «А 3:2 Б» или «А — Б 3:2»
    (первая названная команда ↔ первое число). Время матча («17:00») и даты
    отсеиваются. dated — рядом указана дата матча."""
    club_rx = _team_regex([club["name"], *club.get("aliases", [])])
    rival_rx = _team_regex([rival])
    sep = r"[\s«»\"'()\-–—.,]{0,12}"
    score = r"(?P<s1>\d{1,2})\s*[:\-–—]\s*(?P<s2>\d{1,2})(?!\d)"
    claims, seen = [], set()
    for first, second, first_is_club in ((club_rx, rival_rx, True), (rival_rx, club_rx, False)):
        patterns = (
            rf"(?P<a>{first}){sep}{score}{sep}(?:(?:ОТ|OT|Б)\b[\s.,]*)?(?P<b>{second})",
            rf"(?P<a>{first}){sep}[\s–—\-]{{1,6}}(?P<b>{second}){sep}{score}",
        )
        for pattern in patterns:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                s1, s2 = int(m.group("s1")), int(m.group("s2"))
                raw2 = m.group("s2")
                if s1 > 30 or s2 > 30 or (len(raw2) == 2 and raw2.startswith("0")):
                    continue                                   # похоже на время «17:00»
                before = text[max(0, m.start("s1") - 16): m.start("s1")]
                if MONTH_BEFORE_RE.search(before) or TIME_ZONE_AFTER_RE.search(text[m.end("s2"): m.end("s2") + 6]):
                    continue                                   # «2 октября 17:00», «19:30 мск»
                pos = m.start("s1")
                if pos in seen:
                    continue
                seen.add(pos)
                club_goals, rival_goals = (s1, s2) if first_is_club else (s2, s1)
                claims.append({"club_goals": club_goals, "rival_goals": rival_goals,
                               "dated": _date_near(text, m.start(), m.end(), day)})
    return claims


def extract_status_flags(text: str, club: dict, rival: str) -> dict:
    """Признаки «идёт» / «завершён» там, где рядом названы обе команды."""
    club_rx = _team_regex([club["name"], *club.get("aliases", [])])
    rival_rx = _team_regex([rival])
    flags = {"live": False, "final": False}
    club_pos = [m.start() for m in re.finditer(club_rx, text, re.IGNORECASE)]
    rival_pos = [m.start() for m in re.finditer(rival_rx, text, re.IGNORECASE)]
    for a in club_pos:
        for b in rival_pos:
            if abs(a - b) <= 120:
                window = text[max(0, min(a, b) - 40): max(a, b) + 160]
                flags["live"] = flags["live"] or bool(LIVE_RE.search(window))
                flags["final"] = flags["final"] or bool(FINAL_RE.search(window))
    return flags


def assess_context(context: str, club: dict, rival: str, club_goals: int, rival_goals: int,
                   day: datetime.date) -> dict:
    """Независимое чтение источников. agree — сколько источников подтверждают
    счёт; disagree — источники с ДАТИРОВАННЫМ другим счётом."""
    agree_urls, disagree = set(), []
    live = final = False
    for url, text in split_context_sources(context):
        for claim in extract_score_claims(text, club, rival, day):
            if (claim["club_goals"], claim["rival_goals"]) == (club_goals, rival_goals):
                agree_urls.add(url)
            elif claim["dated"]:
                disagree.append({"url": url, "club_goals": claim["club_goals"], "rival_goals": claim["rival_goals"]})
        flags = extract_status_flags(text, club, rival)
        live = live or flags["live"]
        final = final or flags["final"]
    return {"agree": len(agree_urls), "disagree": disagree, "live": live, "final": final}


def assess_verdict(assessment: dict):
    """(ok|conflict, причина)."""
    if assessment["disagree"]:
        d = assessment["disagree"][0]
        return "conflict", f"другой источник показывает счёт {d['club_goals']}:{d['rival_goals']}"
    if assessment["live"] and (assessment["final"] or assessment["agree"]):
        return "conflict", "один источник пишет «матч идёт», другой — «завершён»"
    return "ok", ""


def candidate_signature(cand: dict) -> tuple:
    return (cand["club_goals"], cand["rival_goals"], cand.get("method"))


async def calendar_reading(club: dict, rival: str, day: datetime.date) -> dict | None:
    """Независимое детерминированное чтение: завершённый матч в календаре клуба
    (таблица сезона sports.ru / список прошедших матчей лиги). None — страница
    недоступна или матч в ней ещё не отмечен завершённым (это не конфликт)."""
    try:
        fixtures = await fetch_club_calendar(club, day)
    except SourceError as e:
        print(f"[DEBUG] {club['name']}: календарь для сверки недоступен: {e}")
        return None
    for f in fixtures:
        if (f["date"] == day and f["finished"] and f.get("club_goals") is not None
                and team_name_matches(rival, [f["rival"]])):
            return f
    return None


async def resolve_result(club: dict, rec: dict, now: datetime.datetime) -> dict:
    """Единая точка получения результата: кандидат → сверка → вердикт.
    state: ok (text) | none | conflict (reason) | error (reason)."""
    rival = rec["rival"]
    day = datetime.date.fromisoformat(rec["match_date"])
    start = parse_iso(rec["start_utc"])
    hockey = club["sport"] == "hockey"
    cand, error = None, None
    try:
        cand = await (fetch_result_hockey if hockey else fetch_result_football)(club, rival, day)
    except SourceError as e:
        error = str(e)
    if cand is None and OPENAI_API_KEY and now >= start + datetime.timedelta(
        hours=RESULT_DELAY_HOURS + OPENAI_FALLBACK_AFTER_HOURS
    ):
        print(f"[DEBUG] {club['name']}: основной путь не нашёл результат, пробую резерв OpenAI")
        cand = await fetch_result_openai(club, rival, day)
    if cand is None:
        return {"state": "error", "reason": error} if error else {"state": "none"}

    if cand["origin"] == "search":
        assessment = assess_context(cand["context"], club, rival, cand["club_goals"], cand["rival_goals"], day)
        verdict, reason = assess_verdict(assessment)
        if verdict == "conflict":
            return {"state": "conflict", "reason": reason}
        corroboration = assessment["agree"]
        reading = await calendar_reading(club, rival, day)
        if reading:
            if (reading["club_goals"], reading["rival_goals"]) != (cand["club_goals"], cand["rival_goals"]):
                return {"state": "conflict",
                        "reason": f"календарь клуба показывает счёт {reading['club_goals']}:{reading['rival_goals']}"}
            if hockey and reading.get("method") and reading["method"] != cand.get("method"):
                return {"state": "conflict", "reason": "способ завершения матча в календаре клуба отличается"}
            corroboration += 1
        last = parse_iso(rec.get("last_crosscheck_at"))
        due = not last or now - last >= datetime.timedelta(minutes=CROSSCHECK_MIN_INTERVAL_MINUTES)
        if OPENAI_API_KEY and corroboration < 2 and due:
            rec["last_crosscheck_at"] = now.isoformat()
            with hooks.purpose("result_crosscheck"):
                other = await fetch_result_openai(club, rival, day)
            if other and candidate_signature(other) != candidate_signature(cand):
                return {"state": "conflict",
                        "reason": f"независимая проверка даёт {other['club_goals']}:{other['rival_goals']}"}
    return {"state": "ok", "text": cand["text"], "candidate": cand}


async def job_check_results(bot: Bot, now: datetime.datetime | None = None) -> None:
    """Проверка результатов. Найденный результат СРАЗУ сохраняется в состоянии
    (result_found) и публикуется отдельным шагом: если отправка в MAX упала —
    результат не теряется и не ищется заново, а просто отправляется при
    следующей проверке. Статус «published» — только после подтверждённой отправки."""
    now = now or utc_now()
    print("[DEBUG] === проверка результатов ===")
    state = load_state(now)

    for rec in list(state["matches"].values()):
        if rec["status"] in ("published", "expired"):
            continue
        club = club_by_key(rec["club_key"])
        if not club:
            continue
        start = parse_iso(rec.get("start_utc"))
        if not start:
            if not DRY_RUN:
                await raise_alert(
                    bot, f"nostart:{rec['match_id']}",
                    f"⚠️ Трибун\nНе удалось определить время начала матча {club['name']} — {rec['rival']}, "
                    f"результат автоматически искать не смогу.", now)
            continue
        if now < start + datetime.timedelta(hours=RESULT_DELAY_HOURS):
            continue  # ещё рано: первая проверка — через RESULT_DELAY_HOURS после начала
        age_hours = (now - start).total_seconds() / 3600

        if rec["result_text"] is None and age_hours > RESULT_GIVE_UP_HOURS:
            rec["status"] = "expired"
            save_state(state)
            if not DRY_RUN:
                await raise_alert(
                    bot, f"giveup:{rec['match_id']}",
                    f"⚠️ Трибун\nРезультат матча {club['name']} — {rec['rival']} так и не получен "
                    f"за {RESULT_GIVE_UP_HOURS} ч. Автоматический поиск остановлен.", now)
            continue

        if rec["status"] in ("found", "announced"):
            rec["status"] = "awaiting_result"
            rec["first_check_at"] = now.isoformat()
            save_state(state)

        if rec["result_text"] is None:
            print(f"[DEBUG] пора проверить результат: {club['name']}")
            res = await resolve_result(club, rec, now)
            if res["state"] == "ok":
                rec["source_fail_ticks"] = 0
                rec["conflict"] = None
                rec["result_text"] = res["text"]
                rec["result_found_at"] = now.isoformat()
                rec["status"] = "result_found"
                save_state(state)          # результат сохранён ДО попытки отправки
            elif res["state"] == "conflict":
                conflict = rec.get("conflict") or {"since": now.isoformat(), "count": 0}
                conflict["count"] += 1
                conflict["last"] = now.isoformat()
                conflict["reason"] = res["reason"]
                rec["conflict"] = conflict
                save_state(state)
                print(f"[DEBUG] {club['name']}: конфликт источников ({res['reason']}) — в MAX не отправляю")
                since = parse_iso(conflict["since"])
                if not DRY_RUN and now - since >= datetime.timedelta(minutes=CONFLICT_ALERT_AFTER_MINUTES):
                    await raise_alert(
                        bot, f"conflict:{rec['match_id']}",
                        f"⚠️ Трибун\nРезультат матча {club['name']} — {rec['rival']} требует проверки: "
                        f"источники расходятся. В MAX ничего не отправлено, проверяю дальше.", now)
                continue
            elif res["state"] == "error":
                rec["source_fail_ticks"] += 1
                save_state(state)
                if not DRY_RUN and rec["source_fail_ticks"] >= SOURCE_FAIL_ALERT_TICKS:
                    await raise_alert(
                        bot, f"srcfail:{rec['match_id']}",
                        f"⚠️ Трибун\nНе удаётся получить результат матча {club['name']} — {rec['rival']}: "
                        f"источники недоступны. Следующая попытка будет автоматически.", now)
                continue
            else:  # матч ещё не завершён или результата нет
                rec["source_fail_ticks"] = 0
                if not DRY_RUN and age_hours >= RESULT_ALERT_AFTER_HOURS:
                    await raise_alert(
                        bot, f"nores:{rec['match_id']}",
                        f"⚠️ Трибун\nРезультат матча {club['name']} — {rec['rival']} пока не найден "
                        f"({RESULT_ALERT_AFTER_HOURS:g} ч после начала). Следующая попытка будет автоматически.", now)
                print(f"[DEBUG] {club['name']}: результата пока нет, попробуем в следующий раз")
                continue

        if rec["result_text"]:
            await publish_result(bot, state, rec, club, now)


async def publish_result(bot: Bot, state: dict, rec: dict, club: dict, now: datetime.datetime) -> bool:
    """Публикация сохранённого результата. «Опубликован» ставится ТОЛЬКО
    после подтверждённой отправки; второй раз тот же матч не публикуется."""
    if rec["status"] == "published" or rec["match_id"] in state["published_ids"]:
        rec["status"], rec["result_sent"] = "published", True
        save_state(state)
        return True
    sent = await send_to_group(bot, rec["result_text"])
    if sent:
        rec["status"] = "published"
        rec["result_sent"] = True
        rec["published_at"] = now.isoformat()
        if rec["match_id"] not in state["published_ids"]:
            state["published_ids"].append(rec["match_id"])
        save_state(state)
        mid = rec["match_id"]
        await clear_alerts(
            bot, [f"send:{mid}", f"srcfail:{mid}", f"nores:{mid}", f"conflict:{mid}", f"giveup:{mid}"],
            f"✅ Трибун\nРезультат матча {club['name']} — {rec['rival']} получен и опубликован.", now)
        return True
    save_state(state)
    if not DRY_RUN:
        await raise_alert(
            bot, f"send:{rec['match_id']}",
            f"⚠️ Трибун\nРезультат матча {club['name']} — {rec['rival']} найден, но не удалось отправить "
            f"его в MAX. Результат сохранён, повторю автоматически.", now)
    return False


async def clear_alerts(bot: Bot, keys: list, text: str, now: datetime.datetime) -> None:
    """Снимает несколько связанных алертов и шлёт ОДНО сообщение о восстановлении."""
    alerts = read_json_safe(ALERTS_FILE, {})
    delivered = False
    changed = False
    for key in keys:
        cur = alerts.get(key)
        if cur and cur.get("active"):
            cur["active"] = False
            cur["cleared"] = now.isoformat()
            delivered = delivered or bool(cur.get("delivered"))
            changed = True
    if changed:
        atomic_write_json(ALERTS_FILE, alerts)
    if delivered:
        await admin_notify(bot, text)


# --- Недельная афиша ----------------------------------------------------------

def last_weekly_date() -> datetime.date | None:
    if not os.path.exists(LAST_WEEKLY_FILE):
        return None
    try:
        with open(LAST_WEEKLY_FILE, "r", encoding="utf-8") as f:
            return datetime.date.fromisoformat(f.read().strip())
    except Exception:
        return None


def save_last_weekly(day: datetime.date) -> None:
    os.makedirs(os.path.dirname(LAST_WEEKLY_FILE), exist_ok=True)
    with open(LAST_WEEKLY_FILE, "w", encoding="utf-8") as f:
        f.write(day.isoformat())


async def job_weekly(bot: Bot, now: datetime.datetime | None = None) -> bool:
    """Понедельник: одно сообщение с матчами всех клубов на неделю пн–вс.
    Источник — календари клубов из существующего списка страниц (детерминированный
    разбор, время не выдумывается: нет времени — «время уточняется»).
    Возвращает True, если афиша отработана (отправлена или матчей нет)."""
    now = now or utc_now()
    today = now.astimezone(YEKB_TZ).date()
    monday, sunday = week_bounds(today)
    print("[DEBUG] === недельная афиша ===")
    sched = load_schedule()
    week_entries, failed = [], []
    for club in CLUBS:
        try:
            fixtures = await fetch_club_calendar(club, today)
        except SourceError as e:
            print(f"[DEBUG] {club['name']}: календарь для афиши недоступен: {e}")
            failed.append(club)
            if not DRY_RUN:
                await raise_alert(
                    bot, f"weekly:{club['key']}:{monday}",
                    f"⚠️ Трибун\nНе удалось получить расписание недели: {club['name']}. "
                    f"Этот клуб не попадёт в афишу. Повторю автоматически.", now)
            continue
        await clear_alert(bot, f"weekly:{club['key']}:{monday}",
                          f"✅ Трибун\nРасписание недели получено: {club['name']}.", now)
        fresh = []
        for f in fixtures:
            if monday <= f["date"] <= sunday and not f["finished"] and not f["cancelled"]:
                fresh.append(schedule_entry(club, f["date"], f["time"], f["zone"], f["tournament"], f["rival"], "calendar"))
        # актуальный календарь заменяет прежние записи этого клуба в пределах недели
        fresh_keys = {e["key"] for e in fresh}
        sched["entries"] = [
            e for e in sched["entries"]
            if not (e.get("club_key") == club["key"] and monday.isoformat() <= e.get("date", "") <= sunday.isoformat()
                    and e["key"] not in fresh_keys)
        ]
        for entry in fresh:
            schedule_upsert(sched, entry)
        week_entries.extend(fresh)
    save_schedule(sched)

    text = format_weekly(week_entries)
    if text is None:
        # матчей нет — ничего не отправляем; но если часть клубов не проверена, ещё раз попробуем позже
        if not failed:
            save_last_weekly(monday)
            return True
        return False
    sent = await send_to_group(bot, text)
    if sent:
        save_last_weekly(monday)
        await clear_alert(bot, f"weeklysend:{monday}", "✅ Трибун\nНедельная афиша отправлена.", now)
        return True
    if not DRY_RUN:
        await raise_alert(bot, f"weeklysend:{monday}",
                          "⚠️ Трибун\nНе удалось отправить недельную афишу в MAX. Повторю автоматически.", now)
    return False




# --- «Своя Трибуна»: данные для личного меню и пульта владельца --------------------

TRIBUN_ENABLED = os.environ.get("TRIBUN_ENABLED", "1").strip().lower() in ("1", "true", "yes")
TRIBUN_RESTART_DELAY = 30            # пауза перед перезапуском слушателя MAX после сбоя, секунд


def owner_user_id() -> int | None:
    """Владелец — по стабильному MAX user_id (ADMIN_USER_ID / admin_config.json), не по имени и не по username."""
    target = admin_target()
    return target.get("user_id") if target else None


def _fmt_ekb(value) -> str | None:
    dt = parse_iso(value)
    return dt.astimezone(YEKB_TZ).strftime("%d.%m.%Y %H:%M") if dt else None


def tribun_automation() -> list:
    """Существующие задачи планировщика: расписание, следующий запуск, последний успех/ошибка (Asia/Yekaterinburg)."""
    now = utc_now().astimezone(YEKB_TZ)
    jobs = tribun.read_job_status(DATA_DIR)
    off = DRY_RUN

    def stat(*names):
        oks = [jobs.get(n, {}).get("last_ok") for n in names if jobs.get(n, {}).get("last_ok")]
        errs = [(jobs[n].get("last_error"), jobs[n].get("error", "")) for n in names if jobs.get(n, {}).get("last_error")]
        err = max(errs) if errs else None
        last_ok = _fmt_ekb(max(oks)) if oks else None
        last_err = f"{_fmt_ekb(err[0])} — {err[1]}" if err else None
        if err and oks and max(oks) > err[0]:
            last_err = f"была {_fmt_ekb(err[0])}, затем восстановилось"
        return last_ok, last_err

    today = now.date()
    morning_today = datetime.datetime.combine(today, datetime.time(MORNING_HOUR), YEKB_TZ)
    if load_last_morning_date() == today or now >= datetime.datetime.combine(today, datetime.time(MORNING_RETRY_UNTIL_HOUR), YEKB_TZ):
        next_morning = morning_today + datetime.timedelta(days=1)
    else:
        next_morning = max(morning_today, now)
    monday, _ = week_bounds(today)
    days_to_weekly = (WEEKLY_WEEKDAY - today.weekday()) % 7
    next_weekly = datetime.datetime.combine(today + datetime.timedelta(days=days_to_weekly), datetime.time(WEEKLY_HOUR), YEKB_TZ)
    if days_to_weekly == 0:                                   # сегодня день афиши: задание срабатывает только в понедельник
        if last_weekly_date() == monday:
            next_weekly += datetime.timedelta(days=7)
        elif now > next_weekly:
            next_weekly = now                                 # время уже наступило, ждёт ближайшей проверки планировщика
    try:
        next_check = now + datetime.timedelta(seconds=seconds_until_next_event(utc_now()))
    except Exception:
        next_check = now + datetime.timedelta(seconds=CHECK_INTERVAL_SECONDS)
    members = tribun_members_meta()
    ok_m, err_m = stat("morning", "morning_retry", "announce_retry")
    ok_w, err_w = stat("weekly")
    ok_r, err_r = stat("results")
    return [
        {"name": "Утренний анонс матчей", "schedule": f"ежедневно в {MORNING_HOUR}:00 (повтор сорвавшегося до {MORNING_RETRY_UNTIL_HOUR}:00)",
         "next": next_morning.strftime("%d.%m.%Y %H:%M"), "last_ok": ok_m or (load_last_morning_date() and f"{load_last_morning_date():%d.%m.%Y}"),
         "last_error": err_m, "enabled": not off},
        {"name": "Недельная афиша", "schedule": f"по понедельникам с {WEEKLY_HOUR}:00, раз в неделю",
         "next": next_weekly.strftime("%d.%m.%Y %H:%M"), "last_ok": ok_w or (last_weekly_date() and f"неделя с {last_weekly_date():%d.%m.%Y}"),
         "last_error": err_w, "enabled": not off},
        {"name": "Проверка результатов матчей", "schedule": f"каждые {CHECK_INTERVAL_SECONDS // 60} мин; первая — через {RESULT_DELAY_HOURS:g} ч после начала матча",
         "next": next_check.strftime("%d.%m.%Y %H:%M"), "last_ok": ok_r, "last_error": err_r, "enabled": not off},
        {"name": "Сверка состава группы «Своя Трибуна»", "schedule": "при запуске и каждые 3 часа",
         "next": "по таймеру внутри процесса", "last_ok": members.get("last_sync"), "last_error": None, "enabled": TRIBUN_ENABLED},
    ]


def tribun_members_meta() -> dict:
    try:
        return tribun.read_strict(os.path.join(DATA_DIR, "tribun_members.json"), {}).get("meta", {})
    except Exception:
        return {}


def tribun_status() -> dict:
    jobs = tribun.read_job_status(DATA_DIR)
    oks = [(v.get("last_ok"), k) for k, v in jobs.items() if not k.startswith("_") and v.get("last_ok")]
    errs = [(v.get("last_error"), f"{k}: {v.get('error', '')}") for k, v in jobs.items() if not k.startswith("_") and v.get("last_error")]
    last_err = max(errs) if errs else None
    sources = [f"DeepSeek: ключ {'задан' if DEEPSEEK_API_KEY else 'не задан'}", f"Tavily: ключ {'задан' if TAVILY_API_KEY else 'не задан'}",
               f"OpenAI-резерв: {'включён' if OPENAI_API_KEY else 'не задан'}",
               "Календари: sports.ru, superliga.rfs.ru (напрямую)"]
    for key, rec in read_json_safe(ALERTS_FILE, {}).items():
        if rec.get("active"):
            sources.append(f"⚠️ активное предупреждение: {key.split(':')[0]}")
    return {"running": True, "last_tick": _fmt_ekb(jobs.get("_tick")), "last_ok": _fmt_ekb(max(oks)[0]) if oks else None,
            "last_error": f"{_fmt_ekb(last_err[0])} — {last_err[1]}" if last_err else None, "sources": sources}


def build_tribun(bot: Bot) -> "tribun.Tribun":
    return tribun.Tribun(bot, data_dir=DATA_DIR, group_chat_id=MAX_CHAT_ID, owner_id=owner_user_id, version=BOT_VERSION,
                         dry_run=DRY_RUN, automation=tribun_automation, status=tribun_status,
                         registry=hooks.REGISTRY, tracker=hooks.TRACKER)


def setup_tribun_hooks() -> None:
    """Учёт API и здоровье источников. Не настроится — SPORTBOT работает как раньше (хуки превращаются в прямой вызов)."""
    if not TRIBUN_ENABLED:
        return
    try:
        hooks.configure(DATA_DIR, tribun_sources.build_source_defs(CLUBS, bool(OPENAI_API_KEY)))
    except Exception as e:
        hooks.reset()
        print(f"[TRIBUN] учёт API не включён: {type(e).__name__}: {e}")


async def tribun_tick_alerts(bot: Bot, now: datetime.datetime) -> None:
    """Предупреждения владельцу по результатам учёта: критичный сбой источника (повторные падения, нет замены) и расход/защита.
    Одно сообщение на событие и одно о восстановлении (raise_alert/clear_alert), без спама."""
    reg = hooks.REGISTRY
    if reg is None or DRY_RUN:
        return
    critical = {row["def"].source_id: row for row in reg.critical_failures()}
    for source_id, d in reg.defs.items():
        key = f"source:{source_id}"
        if source_id in critical:
            row = critical[source_id]
            await raise_alert(bot, key, f"⚠️ Трибун\nИсточник «{d.name}» не отвечает {row['state'].get('consecutive_failures', 0)} раза подряд, "
                              f"рабочей замены нет. Матчи по нему могут не находиться автоматически.", now)
        elif alert_active(key):
            await clear_alert(bot, key, f"✅ Трибун\nИсточник «{d.name}» снова работает.", now)
    if hooks.TRACKER is not None:
        for i, text in enumerate(hooks.TRACKER.pending_warnings() + (hooks.GUARD.alerts if hooks.GUARD else [])):
            await admin_notify(bot, f"⚠️ Трибун · расходы API\n{text}")
        if hooks.GUARD:
            hooks.GUARD.alerts.clear()


async def run_tribun(bot: Bot) -> list:
    """Запускает слушатель MAX (приветствия, личное меню) и сверку состава группы. Любой сбой здесь НЕ должен останавливать
    расписание анонсов и результатов: ошибки только в лог, слушатель перезапускается."""
    tasks = []
    try:
        club = build_tribun(bot)
        try:
            me = await bot.get_me()
            club.bot_username = os.environ.get("TRIBUN_BOT_USERNAME", "").strip() or getattr(me, "username", None)
        except Exception as e:
            club.bot_username = os.environ.get("TRIBUN_BOT_USERNAME", "").strip() or None
            print(f"[TRIBUN] имя бота недоступно: {type(e).__name__}: {e}")
        try:
            await bot.delete_webhook()
        except Exception as e:
            print(f"[DEBUG] delete_webhook: {type(e).__name__}: {e}")
        dp = Dispatcher()
        club.register(dp)

        async def polling():
            while True:
                try:
                    await dp.start_polling(bot)
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    print(f"[TRIBUN] слушатель MAX остановился: {type(e).__name__}: {e}; перезапуск через {TRIBUN_RESTART_DELAY:g} с")
                    await asyncio.sleep(TRIBUN_RESTART_DELAY)
        tasks.append(asyncio.create_task(polling()))
        tasks.append(asyncio.create_task(club.sync_loop()))
        print(f"[TRIBUN] запущен: владелец {'настроен' if owner_user_id() else 'НЕ настроен'}, бот @{club.bot_username or '?'}")
    except Exception as e:
        print(f"[TRIBUN] не запущен: {type(e).__name__}: {e}")
    return tasks


# --- Отправка и точка входа -------------------------------------------------

async def send_to_group(bot: Bot, text: str) -> bool:
    """Возвращает True только при реально подтверждённой отправке —
    вызывающий код (job_check_results) использует это, чтобы не помечать
    result_sent при ошибке MAX (см. живой инцидент 24.09.2026: результат
    Северсталь — Автомобилист несколько суток не находился заново именно
    потому, что result_sent мог быть выставлен без подтверждённой
    отправки)."""
    if DRY_RUN:
        print(f"[DRY RUN] было бы отправлено:\n{text}")
        return False
    for attempt in range(3):
        try:
            await bot.send_message(chat_id=MAX_CHAT_ID, text=text)
            return True
        except Exception as e:
            print(f"[DEBUG] попытка {attempt + 1} отправить не удалась: {type(e).__name__}: {e}")
            if attempt < 2:
                await asyncio.sleep(10)
    print("[DEBUG] отправить не удалось ни с одной попытки")
    return False


def load_last_morning_date() -> datetime.date | None:
    if not os.path.exists(LAST_MORNING_FILE):
        return None
    try:
        with open(LAST_MORNING_FILE, "r", encoding="utf-8") as f:
            return datetime.date.fromisoformat(f.read().strip())
    except Exception:
        return None


def save_last_morning_date(day: datetime.date) -> None:
    os.makedirs(os.path.dirname(LAST_MORNING_FILE), exist_ok=True)
    with open(LAST_MORNING_FILE, "w", encoding="utf-8") as f:
        f.write(day.isoformat())


def seconds_until_next_event(now: datetime.datetime, interval: int = CHECK_INTERVAL_SECONDS) -> float:
    """Не ждём весь интервал, если первая проверка результата наступает раньше:
    так проверка стартует точно через RESULT_DELAY_HOURS после начала матча."""
    best = float(interval)
    state = load_state(now)
    for rec in state["matches"].values():
        if rec["status"] in ("published", "expired", "result_found"):
            continue
        start = parse_iso(rec.get("start_utc"))
        if not start:
            continue
        delta = (start + datetime.timedelta(hours=RESULT_DELAY_HOURS) - now).total_seconds()
        if 0 < delta < best:
            best = delta + 1
    return max(5.0, best)


async def scheduler_loop(bot: Bot) -> None:
    """Основной цикл постоянного процесса. На каждом тике по порядку:
    афиша (понедельник, с 09:00 ЕКБ, раз в неделю) → утренний анонс (10:00 ЕКБ,
    раз в сутки, дедуп через LAST_MORNING_FILE) → повтор сорвавшихся утренних
    проверок и неотправленных анонсов → проверка результатов. Сбой одной части
    не останавливает остальные; повторяющийся сбой цикла — одно уведомление."""
    last_morning = load_last_morning_date()
    loop_failures: dict = {}
    while True:
        now = utc_now()
        local = now.astimezone(YEKB_TZ)
        today = local.date()
        print(f"[DEBUG] тик планировщика: {local:%d.%m.%Y %H:%M}")
        tribun.record_tick(DATA_DIR, now)

        async def guarded(name, coro):
            try:
                await coro
                tribun.record_job(DATA_DIR, name, True, now=now)
                if loop_failures.pop(name, 0) >= SOURCE_FAIL_ALERT_TICKS:
                    await clear_alert(bot, f"loop:{name}", "✅ Трибун\nВнутренний сбой устранён, работаю в обычном режиме.", now)
            except Exception as e:
                loop_failures[name] = loop_failures.get(name, 0) + 1
                print(f"[DEBUG] ошибка части «{name}»: {type(e).__name__}: {e}")
                tribun.record_job(DATA_DIR, name, False, f"{type(e).__name__}: {e}", now=now)
                if loop_failures[name] >= SOURCE_FAIL_ALERT_TICKS and not DRY_RUN:
                    await raise_alert(bot, f"loop:{name}",
                                      "⚠️ Трибун\nВнутренний сбой планировщика, часть задач не выполняется. "
                                      "Повторяю автоматически.", now)

        monday, _ = week_bounds(today)
        if local.weekday() == WEEKLY_WEEKDAY and local.hour >= WEEKLY_HOUR and last_weekly_date() != monday:
            await guarded("weekly", job_weekly(bot, now))

        if local.hour == MORNING_HOUR and last_morning != today:
            async def morning_part():
                nonlocal last_morning
                await job_morning(bot, now)
                last_morning = today
                save_last_morning_date(today)
            await guarded("morning", morning_part())

        await guarded("morning_retry", retry_failed_morning(bot, now))
        await guarded("announce_retry", retry_unsent_announcements(bot, now))
        await guarded("results", job_check_results(bot, now))
        await guarded("tribun_alerts", tribun_tick_alerts(bot, now))

        await asyncio.sleep(seconds_until_next_event(utc_now()))


async def main():
    print(f"Постоянный запуск спортивного бота {datetime.datetime.now(YEKB_TZ)}")

    # FORCE_MODE — разовый внеочередной прогон конкретной ветки СРАЗУ при
    # старте, для ручной отладки или досева состояния после переезда на
    # новый постоянный диск (без этого пришлось бы ждать нужного часа или
    # полного цикла CHECK_INTERVAL_SECONDS). ВАЖНО: раньше после разового
    # прогона процесс завершался (return) — для постоянного сервиса это
    # означало, что контейнер просто останавливался и переставал что-либо
    # проверять вообще. Теперь разовый прогон — это только доп. действие
    # перед стартом обычного постоянного цикла, а не замена ему.
    force_mode = os.environ.get("FORCE_MODE", "").strip().lower()
    bot = Bot(MAX_BOT_TOKEN)
    setup_tribun_hooks()
    await notify_startup(bot)
    if force_mode in ("morning", "results"):
        print(f"[DEBUG] режим принудительно установлен: {force_mode} (разовая проверка перед стартом цикла)")
        if force_mode == "morning":
            await job_morning(bot)
        else:
            await job_check_results(bot)
    elif force_mode == "weekly":
        print("[DEBUG] режим принудительно установлен: weekly (разовая афиша перед стартом цикла)")
        await job_weekly(bot)
    elif force_mode == "test_openai":
        # Разовая ручная проверка живой связи с OpenAI API именно с IP
        # Amvera (см. историю: Tavily с этого IP отдаёт 403, а локальный
        # тест OpenAI 15.09.2026 проверялся только с домашней машины).
        # Дешёвый запрос без реальной нагрузки на web_search, но по тому
        # же пути кода, что и боевой резерв (ask_openai_websearch).
        print("[DEBUG] режим принудительно установлен: test_openai (проверка связи с OpenAI с этого сервера)")
        try:
            with hooks.purpose("admin_test"):
                reply = await ask_openai_websearch(
                    "Не ищи ничего в интернете, просто ответь одним словом: ОК."
                )
            print(f"[DEBUG] test_openai: успех, ответ={reply!r}")
        except Exception as e:
            print(f"[DEBUG] test_openai: ОШИБКА {type(e).__name__}: {e}")
    tribun_tasks = await run_tribun(bot) if TRIBUN_ENABLED else []
    try:
        await scheduler_loop(bot)
    finally:
        for task in tribun_tasks:
            task.cancel()
        session = getattr(bot, "session", None)
        if session is not None:
            close = getattr(session, "close", None)
            if close:
                result = close()
                if asyncio.iscoroutine(result):
                    await result


if __name__ == "__main__":
    asyncio.run(main())
