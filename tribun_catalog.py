"""Справочник интересов «Своей Трибуны» (MVP для первых участников): виды спорта, чемпионаты, клубы + нормализация запросов «➕ Другое».

Это каталог ВЫБОРА, а не список того, что SPORTBOT реально отслеживает: убрать клуб из своих интересов можно, это не отключает клуб
в прежней автоматике (она живёт в sport_bot.CLUBS и от профилей не зависит). Расширять — только здесь.

Названия турниров взяты из того, что SPORTBOT реально получает для шести клубов (КХЛ, Суперлига и Кубок Лиги у футзала,
Первая лига и Кубок России у «Урала») плюс стандартные названия национальных лиг и Лиги чемпионов для «Реала», «Арсенала» и «Милана».
Сезонные названия не придумываются."""
import re

CAT_SPORT, CAT_CHAMP, CAT_CLUB = "sport", "championship", "club"
CODE_TO_CAT = {"sp": CAT_SPORT, "cp": CAT_CHAMP, "cl": CAT_CLUB}
CAT_TO_CODE = {v: k for k, v in CODE_TO_CAT.items()}

# (ключ, значок, название, псевдонимы)
SPORTS = [
    ("football", "⚽", "Футбол", ("football", "soccer")),
    ("hockey", "🏒", "Хоккей", ("hockey",)),
    ("futsal", "🥅", "Футзал", ("futsal", "мини футбол")),
    ("basketball", "🏀", "Баскетбол", ("basketball",)),
]

# (ключ, название, вид спорта, клубы SPORTBOT в турнире, псевдонимы)
COMPETITIONS = [
    ("khl", "КХЛ", "hockey", ("avtomobilist",), ("чемпионат кхл", "khl")),
    ("superliga", "Суперлига", "futsal", ("sinara",), ("бетсити суперлига", "суперлига по мини футболу")),
    ("cuplig", "Кубок Лиги", "futsal", ("sinara",), ()),
    ("fnl1", "Первая лига", "football", ("ural",), ("фнл", "betboom первая лига")),
    ("cuprus", "Кубок России", "football", ("ural",), ()),
    ("laliga", "Ла Лига", "football", ("real",), ("la liga", "примера")),
    ("apl", "АПЛ", "football", ("arsenal",), ("апл", "английская премьер лига", "premier league", "epl")),
    ("seriea", "Серия А", "football", ("milan",), ("serie a",)),
    ("ucl", "Лига чемпионов", "football", ("real", "arsenal", "milan"), ("лч", "champions league", "лига чемпионов уефа")),
    ("vtb", "Единая лига ВТБ", "basketball", (), ("лига втб", "vtb")),
]

# (ключ = ключ клуба в sport_bot.CLUBS, название, вид спорта, псевдонимы)
CLUBS = [
    ("avtomobilist", "Автомобилист", "hockey", ("хк автомобилист", "avtomobilist")),
    ("sinara", "Синара", "futsal", ("мфк синара", "sinara")),
    ("ural", "Урал", "football", ("фк урал", "ural")),
    ("real", "Реал Мадрид", "football", ("real madrid",)),
    ("arsenal", "Арсенал", "football", ("arsenal", "арсенал лондон")),
    ("milan", "Милан", "football", ("ac milan", "milan")),
]

SPORT_BY_KEY = {k: (icon, name) for k, icon, name, _ in SPORTS}
COMP_BY_KEY = {c[0]: c for c in COMPETITIONS}
CLUB_BY_KEY = {c[0]: c for c in CLUBS}

# Простые устойчивые совпадения для запросов «Другое» (БЕЗ нечёткого сопоставления: только точные известные написания)
REQUEST_ALIASES = {
    "нба": "nba", "nba": "nba", "нхл": "nhl", "nhl": "nhl",
    "формула 1": "формула-1", "формула1": "формула-1", "f1": "формула-1", "formula 1": "формула-1", "formula1": "формула-1",
}


def normalize_request(text: str) -> str:
    """Нормализация запроса: регистр, «ё/е», кавычки, дефисы/точки/слэши → пробел, повторные пробелы, затем таблица точных псевдонимов.
    «NBA» и «НБА» → «nba»; «Формула-1» и «формула 1» → «формула-1». Разные сущности не склеиваются."""
    s = (text or "").casefold().replace("ё", "е")
    s = re.sub(r"[«»\"'`]", "", s)
    s = re.sub(r"[-–—_./\\]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return REQUEST_ALIASES.get(s, s)


def _names(cat: str):
    if cat == CAT_SPORT:
        return [(k, [name, *aliases]) for k, icon, name, aliases in SPORTS]
    if cat == CAT_CHAMP:
        return [(c[0], [c[1], *c[4]]) for c in COMPETITIONS]
    return [(c[0], [c[1], *c[3]]) for c in CLUBS]


def match_catalog(cat: str, text: str):
    """Ключ каталога, если текст точно совпал (после нормализации) с названием или псевдонимом; иначе None."""
    norm = normalize_request(text)
    for key, names in _names(cat):
        if norm in {normalize_request(n) for n in names}:
            return key
    return None


def match_any(text: str):
    """(категория, ключ) для известной сущности любой категории — нужно для проверки покрытия запросов."""
    for cat in (CAT_CLUB, CAT_CHAMP, CAT_SPORT):
        key = match_catalog(cat, text)
        if key:
            return cat, key
    return None


def competitions_for(sports) -> list:
    return [c for c in COMPETITIONS if c[2] in sports]


def clubs_for(sports) -> list:
    return [c for c in CLUBS if c[2] in sports]
