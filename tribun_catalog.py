"""Справочник интересов «Своей Трибуны» (MVP для первых участников): виды спорта, чемпионаты, клубы + нормализация запросов «➕ Другое».

Это каталог ВЫБОРА, а не список того, что SPORTBOT реально отслеживает: убрать клуб из своих интересов можно, это не отключает клуб
в прежней автоматике (она живёт в sport_bot.CLUBS и от профилей не зависит). Расширять — только здесь.
Цепочка выбора: вид спорта → чемпионаты → клубы выбранных чемпионатов (SEASONS)."""
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

# Чемпионаты (согласованный MVP): (ключ, название, вид спорта, псевдонимы). Порядок = порядок на экране.
COMPETITIONS = [
    ("khl", "КХЛ", "hockey", ("чемпионат кхл", "khl")),
    ("nhl", "NHL", "hockey", ("нхл", "национальная хоккейная лига")),
    ("rpl", "РПЛ / Премьер-лига", "football", ("рпл", "rpl", "российская премьер лига", "премьер лига", "мир рпл")),
    ("fnl1", "Первая лига", "football", ("фнл", "betboom первая лига")),
    ("apl", "АПЛ", "football", ("апл", "английская премьер лига", "premier league", "epl")),
    ("laliga", "Ла Лига", "football", ("la liga", "примера")),
    ("seriea", "Серия А", "football", ("serie a",)),
    ("ucl", "Лига чемпионов", "football", ("лч", "champions league", "лига чемпионов уефа")),
    ("uel", "Лига Европы", "football", ("ле", "europa league", "лига европы уефа")),
    ("uecl", "Лига конференций", "football", ("лк", "conference league", "лига конференций уефа")),
    ("superliga", "Суперлига", "futsal", ("бетсити суперлига", "суперлига по мини футболу")),
    ("vtb", "Единая лига ВТБ", "basketball", ("лига втб", "vtb")),
]

# Клубы каталога: ключ → название. Клубы шести команд SPORTBOT имеют ключи SPORTBOT (avtomobilist, sinara, ural, real, arsenal, milan).
_N = {
    # КХЛ (одноимённые с футбольными помечены «хк»)
    "avtomobilist": "Автомобилист", "avangard": "Авангард", "akbars": "Ак Барс", "amur": "Амур", "admiral": "Адмирал", "barys": "Барыс",
    "vityaz": "Витязь", "dinamo_msk_h": "Динамо Москва (хк)", "dinamo_mn_h": "Динамо Минск (хк)", "lada": "Лада",
    "lokomotiv_h": "Локомотив (хк)", "metallurg_mg": "Металлург Мг", "neftekhimik_h": "Нефтехимик (хк)", "salavat": "Салават Юлаев",
    "severstal": "Северсталь", "ska": "СКА", "sibir": "Сибирь", "sochi_h": "Сочи (хк)", "spartak_h": "Спартак (хк)",
    "torpedo_h": "Торпедо (хк)", "traktor": "Трактор", "cska_h": "ЦСКА (хк)",
    # NHL
    "nhl_ana": "Анахайм Дакс", "nhl_utah": "Юта Мамонт", "nhl_bos": "Бостон Брюинз", "nhl_buf": "Баффало Сейбрз", "nhl_cgy": "Калгари Флэймз",
    "nhl_car": "Каролина Харрикейнз", "nhl_chi": "Чикаго Блэкхокс", "nhl_col": "Колорадо Эвеланш", "nhl_cbj": "Коламбус Блю Джекетс",
    "nhl_dal": "Даллас Старз", "nhl_det": "Детройт Ред Уингз", "nhl_edm": "Эдмонтон Ойлерз", "nhl_fla": "Флорида Пантерз",
    "nhl_lak": "Лос-Анджелес Кингз", "nhl_min": "Миннесота Уайлд", "nhl_mtl": "Монреаль Канадиенс", "nhl_nsh": "Нэшвилл Предаторз",
    "nhl_njd": "Нью-Джерси Девилз", "nhl_nyi": "Нью-Йорк Айлендерс", "nhl_nyr": "Нью-Йорк Рейнджерс", "nhl_ott": "Оттава Сенаторз",
    "nhl_phi": "Филадельфия Флайерз", "nhl_pit": "Питтсбург Пингвинз", "nhl_sjs": "Сан-Хосе Шаркс", "nhl_sea": "Сиэтл Кракен",
    "nhl_stl": "Сент-Луис Блюз", "nhl_tbl": "Тампа-Бэй Лайтнинг", "nhl_tor": "Торонто Мейпл Лифс", "nhl_van": "Ванкувер Кэнакс",
    "nhl_vgk": "Вегас Голден Найтс", "nhl_wsh": "Вашингтон Кэпиталз", "nhl_wpg": "Виннипег Джетс",
    # РПЛ
    "zenit": "Зенит", "krasnodar": "Краснодар", "lokomotiv": "Локомотив", "spartak": "Спартак", "cska": "ЦСКА", "dinamo_msk": "Динамо Москва",
    "rostov": "Ростов", "rubin": "Рубин", "akhmat": "Ахмат", "krylya": "Крылья Советов", "dinamo_mkh": "Динамо Махачкала", "akron": "Акрон",
    "orenburg": "Оренбург", "sochi": "Сочи", "pari_nn": "Пари НН", "baltika": "Балтика",
    # Первая лига (стартовый список по страницам, которые бот уже читает)
    "ural": "Урал", "torpedo": "Торпедо Москва", "enisey": "Енисей", "chelyabinsk": "Челябинск", "veles": "Велес",
    "spartak_kostroma": "Спартак Кострома", "shinnik": "Шинник", "kamaz": "КАМАЗ", "alania": "Алания",
    # АПЛ
    "arsenal": "Арсенал", "aston_villa": "Астон Вилла", "bournemouth": "Борнмут", "brentford": "Брентфорд", "brighton": "Брайтон",
    "burnley": "Бернли", "chelsea": "Челси", "crystal_palace": "Кристал Пэлас", "everton": "Эвертон", "fulham": "Фулхэм", "leeds": "Лидс",
    "liverpool": "Ливерпуль", "man_city": "Манчестер Сити", "man_united": "Манчестер Юнайтед", "newcastle": "Ньюкасл",
    "nottingham": "Ноттингем Форест", "sunderland": "Сандерленд", "tottenham": "Тоттенхэм", "west_ham": "Вест Хэм", "wolves": "Вулверхэмптон",
    # Ла Лига
    "alaves": "Алавес", "athletic": "Атлетик Бильбао", "atletico": "Атлетико Мадрид", "barcelona": "Барселона", "celta": "Сельта", "elche": "Эльче",
    "espanyol": "Эспаньол", "getafe": "Хетафе", "girona": "Жирона", "levante": "Леванте", "mallorca": "Мальорка", "osasuna": "Осасуна",
    "rayo": "Райо Вальекано", "betis": "Бетис", "real": "Реал Мадрид", "oviedo": "Овьедо", "real_sociedad": "Реал Сосьедад",
    "sevilla": "Севилья", "valencia": "Валенсия", "villarreal": "Вильярреал",
    # Серия А
    "atalanta": "Аталанта", "bologna": "Болонья", "cagliari": "Кальяри", "como": "Комо", "cremonese": "Кремонезе", "fiorentina": "Фиорентина",
    "genoa": "Дженоа", "verona": "Верона", "inter": "Интер", "juventus": "Ювентус", "lazio": "Лацио", "lecce": "Лечче", "milan": "Милан",
    "napoli": "Наполи", "parma": "Парма", "pisa": "Пиза", "roma": "Рома", "sassuolo": "Сассуоло", "torino": "Торино", "udinese": "Удинезе",
    # еврокубки: клубы, которых нет в лигах выше
    "bayern": "Бавария", "psg": "ПСЖ", "sporting": "Спортинг", "leverkusen": "Байер", "dortmund": "Боруссия Дортмунд",
    "olympiacos": "Олимпиакос", "club_brugge": "Брюгге", "galatasaray": "Галатасарай", "monaco": "Монако", "qarabag": "Карабах",
    "bodo": "Буде-Глимт", "benfica": "Бенфика", "marseille": "Марсель", "pafos": "Пафос", "union_sg": "Юнион СЖ", "psv": "ПСВ",
    "copenhagen": "Копенгаген", "ajax": "Аякс", "frankfurt": "Айнтрахт Франкфурт", "slavia": "Славия Прага", "kairat": "Кайрат",
    "lyon": "Лион", "porto": "Порту", "ferencvaros": "Ференцварош", "feyenoord": "Фейеноорд", "celtic": "Селтик", "stuttgart": "Штутгарт",
    "braga": "Брага", "lille": "Лилль", "fenerbahce": "Фенербахче", "freiburg": "Фрайбург", "panathinaikos": "Панатинаикос",
    "paok": "ПАОК", "rangers": "Рейнджерс", "salzburg": "Зальцбург", "basel": "Базель", "genk": "Генк",
    "mainz": "Майнц", "strasbourg": "Страсбур", "shakhtar": "Шахтёр Донецк", "aek": "АЕК Афины", "legia": "Легия", "lech": "Лех",
    # футзал
    "sinara": "Синара",
}
# Прежние написания шести клубов SPORTBOT
_ALIASES = {"avtomobilist": ("хк автомобилист", "avtomobilist"), "sinara": ("мфк синара", "sinara"), "ural": ("фк урал", "ural"),
            "real": ("real madrid",), "arsenal": ("arsenal", "арсенал лондон"), "milan": ("ac milan", "milan")}

# Модель SPORT → COMPETITION → SEASON → CLUBS. Состав меняется по сезонам: чтобы обновить сезон, достаточно заменить запись здесь —
# интерфейс (tribun.py) ничего не знает о конкретных клубах. ВНИМАНИЕ: это СТАРТОВЫЙ справочник сезона 2025/26 (составы национальных чемпионатов
# и еврокубков следующего сезона нужно сверить и обновить); он не влияет на то, что реально отслеживает SPORTBOT.
CURRENT_SEASON = "2025/26"
SEASONS = {
    "khl": ["avtomobilist", "avangard", "akbars", "amur", "admiral", "barys", "vityaz", "dinamo_msk_h", "dinamo_mn_h", "lada", "lokomotiv_h",
            "metallurg_mg", "neftekhimik_h", "salavat", "severstal", "ska", "sibir", "sochi_h", "spartak_h", "torpedo_h", "traktor", "cska_h"],
    "nhl": [k for k in _N if k.startswith("nhl_")],
    "rpl": ["zenit", "krasnodar", "lokomotiv", "spartak", "cska", "dinamo_msk", "rostov", "rubin", "akhmat", "krylya", "dinamo_mkh", "akron",
            "orenburg", "sochi", "pari_nn", "baltika"],
    "fnl1": ["ural", "torpedo", "enisey", "chelyabinsk", "veles", "spartak_kostroma", "shinnik", "kamaz", "alania"],
    "apl": ["arsenal", "aston_villa", "bournemouth", "brentford", "brighton", "burnley", "chelsea", "crystal_palace", "everton", "fulham", "leeds",
            "liverpool", "man_city", "man_united", "newcastle", "nottingham", "sunderland", "tottenham", "west_ham", "wolves"],
    "laliga": ["alaves", "athletic", "atletico", "barcelona", "celta", "elche", "espanyol", "getafe", "girona", "levante", "mallorca", "osasuna",
               "rayo", "betis", "real", "oviedo", "real_sociedad", "sevilla", "valencia", "villarreal"],
    "seriea": ["atalanta", "bologna", "cagliari", "como", "cremonese", "fiorentina", "genoa", "verona", "inter", "juventus", "lazio", "lecce",
               "milan", "napoli", "parma", "pisa", "roma", "sassuolo", "torino", "udinese"],
    "ucl": ["arsenal", "bayern", "liverpool", "tottenham", "barcelona", "chelsea", "sporting", "man_city", "real", "inter", "psg", "newcastle",
            "juventus", "atletico", "atalanta", "leverkusen", "dortmund", "olympiacos", "club_brugge", "galatasaray", "monaco", "qarabag", "bodo",
            "benfica", "marseille", "pafos", "union_sg", "psv", "athletic", "napoli", "copenhagen", "ajax", "frankfurt", "slavia", "villarreal", "kairat"],
    "uel": ["aston_villa", "roma", "lyon", "porto", "ferencvaros", "feyenoord", "betis", "celtic", "stuttgart", "nottingham", "bologna", "braga",
            "lille", "fenerbahce", "freiburg", "panathinaikos", "paok", "rangers", "celta", "salzburg", "basel", "genk"],
    "uecl": ["fiorentina", "mainz", "strasbourg", "rayo", "shakhtar", "aek", "legia", "lech"],
    "superliga": ["sinara"],
    "vtb": [],
}

_SPORT_OF_CLUB: dict = {}
for _c in COMPETITIONS:
    for _k in SEASONS[_c[0]]:
        _SPORT_OF_CLUB.setdefault(_k, _c[2])
# (ключ, название, вид спорта, псевдонимы)
CLUBS = [(k, name, _SPORT_OF_CLUB[k], _ALIASES.get(k, ())) for k, name in _N.items() if k in _SPORT_OF_CLUB]

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
        return [(c[0], [c[1], *c[3]]) for c in COMPETITIONS]
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


def comp_club_keys(comp_key: str) -> list:
    """Клубы чемпионата в текущем сезоне (SEASONS)."""
    return list(SEASONS.get(comp_key, []))


def clubs_for_competitions(comp_keys) -> list:
    """Объединение клубов выбранных чемпионатов: порядок — по каталогу, клуб из нескольких турниров — один раз."""
    wanted = {k for ck in comp_keys for k in comp_club_keys(ck)}
    return [c for c in CLUBS if c[0] in wanted]
