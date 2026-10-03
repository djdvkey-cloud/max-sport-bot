"""Справочник интересов «Своей Трибуны» (MVP для первых участников): виды спорта, чемпионаты, клубы + нормализация запросов «➕ Другое».

Это каталог ВЫБОРА, а не список того, что SPORTBOT реально отслеживает: убрать клуб из своих интересов можно, это не отключает клуб
в прежней автоматике (она живёт в sport_bot.CLUBS и от профилей не зависит), и наличие клуба здесь НЕ означает, что Трибун умеет получать
его расписание (покрытие источниками — отдельно, sources.py; «клуб есть в каталоге + покрытие GAP» — допустимое состояние).
Цепочка выбора: вид спорта → чемпионаты → клубы выбранных чемпионатов (SEASONS).

СНИМОК КАТАЛОГА (SPORT → COMPETITION → SEASON → CLUBS → SOURCE → VERIFIED_AT): проверен по внешним источникам и сохранён в коде,
интерфейс интернет не опрашивает. Состав НЕ берётся по памяти. Новый сезон = заменить записи в SEASONS (season, clubs, source, verified_at),
callbacks и экраны не меняются."""
import re

CAT_SPORT, CAT_CHAMP, CAT_CLUB = "sport", "championship", "club"
CODE_TO_CAT = {"sp": CAT_SPORT, "cp": CAT_CHAMP, "cl": CAT_CLUB}
CAT_TO_CODE = {v: k for k, v in CODE_TO_CAT.items()}

# (ключ, значок, название, псевдонимы)
SPORTS = [
    ("football", "⚽", "Футбол", ("football", "soccer")),
    ("hockey", "🏒", "Хоккей", ("hockey",)),
    ("futsal", "🥅", "Футзал", ("futsal", "мини футбол")),
]

# ОТЛОЖЕНО (не входит в стартовый пилот, в меню не показывается и в активном покрытии не участвует). Данные снимка и адаптеры источников
# оставлены в коде, чтобы вернуть направление без переписывания: достаточно перенести запись в SPORTS / COMPETITIONS.
DEFERRED_SPORTS = [("basketball", "🏀", "Баскетбол", ("basketball",))]

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
    ("superliga", "Суперлига", "futsal", ("бетсити суперлига", "суперлига по мини футболу")),
]
DEFERRED_COMPETITIONS = [
    ("uel", "Лига Европы", "football", ("ле", "europa league", "лига европы уефа")),
    ("uecl", "Лига конференций", "football", ("лк", "conference league", "лига конференций уефа")),
    ("vtb", "Единая лига ВТБ", "basketball", ("лига втб", "vtb")),
]
DEFERRED_KEYS = {c[0] for c in DEFERRED_COMPETITIONS}

# Клубы каталога: ключ → название (названия, совпадающие у разных видов спорта, помечены). Ключи шести клубов SPORTBOT: avtomobilist, sinara, ural, real, arsenal, milan.
_N = {
    # КХЛ 2026/27
    "avtomobilist": "Автомобилист", "avangard": "Авангард", "akbars": "Ак Барс", "amur": "Амур", "admiral": "Адмирал", "barys": "Барыс",
    "dinamo_msk_h": "Динамо Москва (хк)", "dinamo_mn_h": "Динамо Минск (хк)", "lada": "Лада", "lokomotiv_h": "Локомотив (хк)",
    "metallurg_mg": "Металлург Мг", "neftekhimik_h": "Нефтехимик (хк)", "salavat": "Салават Юлаев", "severstal": "Северсталь", "ska": "СКА",
    "sibir": "Сибирь", "sochi_h": "Сочи (хк)", "spartak_h": "Спартак (хк)", "torpedo_h": "Торпедо (хк)", "traktor": "Трактор", "cska_h": "ЦСКА (хк)",
    "shanghai": "Шанхайские Драконы",
    # NHL
    "nhl_ana": "Анахайм Дакс", "nhl_utah": "Юта Мамонт", "nhl_bos": "Бостон Брюинз", "nhl_buf": "Баффало Сейбрз", "nhl_cgy": "Калгари Флэймз",
    "nhl_car": "Каролина Харрикейнз", "nhl_chi": "Чикаго Блэкхокс", "nhl_col": "Колорадо Эвеланш", "nhl_cbj": "Коламбус Блю Джекетс",
    "nhl_dal": "Даллас Старз", "nhl_det": "Детройт Ред Уингз", "nhl_edm": "Эдмонтон Ойлерз", "nhl_fla": "Флорида Пантерз",
    "nhl_lak": "Лос-Анджелес Кингз", "nhl_min": "Миннесота Уайлд", "nhl_mtl": "Монреаль Канадиенс", "nhl_nsh": "Нэшвилл Предаторз",
    "nhl_njd": "Нью-Джерси Девилз", "nhl_nyi": "Нью-Йорк Айлендерс", "nhl_nyr": "Нью-Йорк Рейнджерс", "nhl_ott": "Оттава Сенаторз",
    "nhl_phi": "Филадельфия Флайерз", "nhl_pit": "Питтсбург Пингвинз", "nhl_sjs": "Сан-Хосе Шаркс", "nhl_sea": "Сиэтл Кракен",
    "nhl_stl": "Сент-Луис Блюз", "nhl_tbl": "Тампа-Бэй Лайтнинг", "nhl_tor": "Торонто Мейпл Лифс", "nhl_van": "Ванкувер Кэнакс",
    "nhl_vgk": "Вегас Голден Найтс", "nhl_wsh": "Вашингтон Кэпиталз", "nhl_wpg": "Виннипег Джетс",
    # РПЛ 2026/27
    "zenit": "Зенит", "krasnodar": "Краснодар", "lokomotiv": "Локомотив", "spartak": "Спартак", "cska": "ЦСКА", "dinamo_msk": "Динамо Москва",
    "rostov": "Ростов", "rubin": "Рубин", "akhmat": "Ахмат", "krylya": "Крылья Советов", "dinamo_mkh": "Динамо Махачкала", "akron": "Акрон",
    "orenburg": "Оренбург", "baltika": "Балтика", "fakel": "Факел", "rodina": "Родина",
    # Первая лига 2026/27
    "ural": "Урал", "torpedo": "Торпедо Москва", "enisey": "Енисей", "chelyabinsk": "Челябинск", "veles": "Велес", "sochi": "Сочи",
    "spartak_kostroma": "Спартак Кострома", "shinnik": "Шинник", "kamaz": "КАМАЗ", "arsenal_tula": "Арсенал Тула", "leningradets": "Ленинградец",
    "neftekhimik_f": "Нефтехимик Нижнекамск", "pari_nn": "Нижний Новгород", "rotor": "Ротор", "ska_khb": "СКА-Хабаровск",
    "tekstilshchik": "Текстильщик", "ufa": "Уфа", "volga": "Волга Ульяновск",
    # АПЛ 2026/27
    "arsenal": "Арсенал", "aston_villa": "Астон Вилла", "bournemouth": "Борнмут", "brentford": "Брентфорд", "brighton": "Брайтон",
    "chelsea": "Челси", "coventry": "Ковентри Сити", "crystal_palace": "Кристал Пэлас", "everton": "Эвертон", "fulham": "Фулхэм",
    "hull": "Халл Сити", "ipswich": "Ипсвич Таун", "leeds": "Лидс", "liverpool": "Ливерпуль", "man_city": "Манчестер Сити",
    "man_united": "Манчестер Юнайтед", "newcastle": "Ньюкасл", "nottingham": "Ноттингем Форест", "sunderland": "Сандерленд", "tottenham": "Тоттенхэм",
    # Ла Лига 2026/27
    "barcelona": "Барселона", "atletico": "Атлетико Мадрид", "betis": "Бетис", "real": "Реал Мадрид", "sevilla": "Севилья", "alaves": "Алавес",
    "deportivo": "Депортиво Ла-Корунья", "real_sociedad": "Реал Сосьедад", "villarreal": "Вильярреал", "athletic": "Атлетик Бильбао",
    "getafe": "Хетафе", "rayo": "Райо Вальекано", "osasuna": "Осасуна", "celta": "Сельта", "espanyol": "Эспаньол", "racing": "Расинг Сантандер",
    "levante": "Леванте", "elche": "Эльче", "valencia": "Валенсия", "malaga": "Малага",
    # Серия А 2026/27
    "atalanta": "Аталанта", "bologna": "Болонья", "cagliari": "Кальяри", "como": "Комо", "fiorentina": "Фиорентина", "frosinone": "Фрозиноне",
    "genoa": "Дженоа", "inter": "Интер", "juventus": "Ювентус", "lazio": "Лацио", "lecce": "Лечче", "milan": "Милан", "monza": "Монца",
    "napoli": "Наполи", "parma": "Парма", "roma": "Рома", "sassuolo": "Сассуоло", "torino": "Торино", "udinese": "Удинезе", "venezia": "Венеция",
    # клубы еврокубков 2026/27, которых нет в лигах выше
    "aek": "АЕК Афины", "dortmund": "Боруссия Дортмунд", "bayern": "Бавария", "bodo": "Буде-Глимт", "club_brugge": "Брюгге", "fenerbahce": "Фенербахче",
    "feyenoord": "Фейеноорд", "galatasaray": "Галатасарай", "lask": "ЛАСК", "leipzig": "РБ Лейпциг", "lens": "Ланс", "lille": "Лилль",
    "psg": "ПСЖ", "porto": "Порту", "psv": "ПСВ", "slovan": "Слован Братислава", "sabah": "Сабах", "shakhtar": "Шахтёр Донецк",
    "slavia": "Славия Прага", "sporting": "Спортинг", "stuttgart": "Штутгарт", "viking": "Викинг",
    "anderlecht": "Андерлехт", "ararat": "Арарат-Армения", "az": "АЗ Алкмар", "benfica": "Бенфика", "besiktas": "Бешикташ", "celje": "Целе",
    "celtic": "Селтик", "ferencvaros": "Ференцварош", "dinamo_zg": "Динамо Загреб", "hapoel_bs": "Хапоэль Беэр-Шева", "hoffenheim": "Хоффенхайм",
    "jagiellonia": "Ягеллония", "lech": "Лех", "leverkusen": "Байер", "levski": "Левски", "lillestrom": "Лиллестрём", "lyon": "Лион",
    "marseille": "Марсель", "nec": "НЕК", "ofi": "ОФИ Крит", "olympiacos": "Олимпиакос", "omonia": "Омония", "rennes": "Ренн", "salzburg": "Зальцбург",
    "sparta": "Спарта Прага", "sturm": "Штурм Грац", "torreense": "Торрензе", "union_sg": "Юнион СЖ", "plzen": "Виктория Пльзень",
    "aarhus": "Орхус", "ajax": "Аякс", "borac": "Борац", "braga": "Брага", "brann": "Бранн", "copenhagen": "Копенгаген", "crvena_zvezda": "Црвена Звезда",
    "cska_sofia": "ЦСКА София", "egnatia": "Эгнатия", "freiburg": "Фрайбург", "gent": "Гент", "hajduk": "Хайдук Сплит", "hearts": "Харт оф Мидлотиан",
    "iberia": "Иберия 1999", "inter_escaldes": "Интер Эскальдес", "jablonec": "Яблонец", "kairat": "Кайрат", "kauno": "Жальгирис Каунас",
    "kups": "КуПС", "red_imps": "Линкольн Ред Импс", "lugano": "Лугано", "midtjylland": "Мидтьюлланн", "mjallby": "Мьельбю", "monaco": "Монако",
    "nordsjaelland": "Нордшелланн", "pafos": "Пафос", "panathinaikos": "Панатинаикос", "riga": "Рига", "sint_truiden": "Синт-Трёйден",
    "thun": "Тун", "trabzonspor": "Трабзонспор", "twente": "Твенте", "craiova": "Университатя Крайова",
    # футзал, Суперлига 2026/27
    "sinara": "Синара", "gazprom_yugra": "Газпром-Югра", "iraero": "ИрАэро", "kprf": "КПРФ", "kristall": "Кристалл", "novaya_generatsiya": "Новая генерация",
    "norilsk": "Норильский никель", "sibiryak": "Сибиряк", "surgut": "Сургут", "tzms": "ТЗМС", "torpedo_fz": "Торпедо (футзал)", "tyumen": "Тюмень",
    # баскетбол, Единая лига ВТБ 2026/27
    "avtodor": "Автодор", "astana_b": "Астана (баскетбол)", "enisey_b": "Енисей (баскетбол)", "zenit_b": "Зенит (баскетбол)",
    "lokomotiv_kuban": "Локомотив-Кубань", "mba": "МБА", "parma_b": "Парма (баскетбол)", "dinamo_vl": "Динамо Владивосток", "samara": "Самара",
    "unics": "УНИКС", "uralmash": "Уралмаш", "cska_b": "ЦСКА (баскетбол)",
}
# Прежние написания шести клубов SPORTBOT
_ALIASES = {"avtomobilist": ("хк автомобилист", "avtomobilist"), "sinara": ("мфк синара", "sinara"), "ural": ("фк урал", "ural"),
            "real": ("real madrid",), "arsenal": ("arsenal", "арсенал лондон"), "milan": ("ac milan", "milan")}

CURRENT_SEASON = "2026/27"
VERIFIED_AT = "2026-10-03"
_WIKI = "https://en.wikipedia.org/wiki/2026%E2%80%9327_"


def _season(comp_source: tuple, clubs: list, official: bool) -> dict:
    name, url = comp_source
    return {"season": CURRENT_SEASON, "source_name": name, "source_url": url, "official": official, "verified_at": VERIFIED_AT, "clubs": clubs}


# Модель SPORT → COMPETITION → SEASON → CLUBS → SOURCE → VERIFIED_AT. Официальные страницы, которые не отдают список без JS/капчи/403, заменены
# агрегатором (official=False) — владелец может перепроверить; без внешнего подтверждения клубы в каталог не попадают.
SEASONS = {
    "khl": _season(("Спорт-Экспресс / T—J / Википедия (khl.ru закрыт для роботов: 403)", "https://www.sport-express.net/hockey/khl/reviews/khl-sezona-2026-2027-raspisanie-format-pley-off-uchastniki-izmeneniya-v-pravilah-i-gde-smotret-translyacii-2452314/"),
                   ["avtomobilist", "avangard", "akbars", "amur", "admiral", "barys", "dinamo_msk_h", "dinamo_mn_h", "lada", "lokomotiv_h",
                    "metallurg_mg", "neftekhimik_h", "salavat", "severstal", "ska", "sibir", "sochi_h", "spartak_h", "torpedo_h", "traktor", "cska_h",
                    "shanghai"], False),
    "nhl": _season(("NHL.com — Teams", "https://www.nhl.com/info/teams"), [k for k in _N if k.startswith("nhl_")], True),
    "rpl": _season(("Wikipedia 2026–27 Russian Premier League (premierliga.ru закрыт капчей)", _WIKI + "Russian_Premier_League"),
                   ["zenit", "krasnodar", "lokomotiv", "spartak", "cska", "dinamo_msk", "rostov", "rubin", "akhmat", "krylya", "dinamo_mkh", "akron",
                    "orenburg", "baltika", "fakel", "rodina"], False),
    "fnl1": _season(("Wikipedia 2026–27 Russian First League; Урал сверен со страницей sports.ru (Россия. Первая лига)", _WIKI + "Russian_First_League"),
                    ["ural", "torpedo", "enisey", "chelyabinsk", "veles", "sochi", "spartak_kostroma", "shinnik", "kamaz", "arsenal_tula", "leningradets",
                     "neftekhimik_f", "pari_nn", "rotor", "ska_khb", "tekstilshchik", "ufa", "volga"], False),
    "apl": _season(("Wikipedia 2026–27 Premier League (premierleague.com не отдаёт список без JS)", _WIKI + "Premier_League"),
                   ["arsenal", "aston_villa", "bournemouth", "brentford", "brighton", "chelsea", "coventry", "crystal_palace", "everton", "fulham", "hull",
                    "ipswich", "leeds", "liverpool", "man_city", "man_united", "newcastle", "nottingham", "sunderland", "tottenham"], False),
    "laliga": _season(("LALIGA EA SPORTS — Standing 2026/27", "https://www.laliga.com/en-GB/laliga-easports/standing"),
                      ["barcelona", "atletico", "betis", "real", "sevilla", "alaves", "deportivo", "real_sociedad", "villarreal", "athletic", "getafe", "rayo",
                       "osasuna", "celta", "espanyol", "racing", "levante", "elche", "valencia", "malaga"], True),
    "seriea": _season(("Wikipedia 2026–27 Serie A (legaseriea.it не отдаёт список без JS)", _WIKI + "Serie_A"),
                      ["atalanta", "bologna", "cagliari", "como", "fiorentina", "frosinone", "genoa", "inter", "juventus", "lazio", "lecce", "milan", "monza",
                       "napoli", "parma", "roma", "sassuolo", "torino", "udinese", "venezia"], False),
    "ucl": _season(("UEFA.com — Champions League 2026/27, league phase", "https://www.uefa.com/uefachampionsleague/clubs/"),
                   ["aek", "arsenal", "aston_villa", "atletico", "dortmund", "barcelona", "bayern", "bodo", "club_brugge", "como", "fenerbahce", "feyenoord",
                    "galatasaray", "inter", "lask", "leipzig", "lens", "lille", "liverpool", "man_city", "man_united", "napoli", "psg", "porto", "psv", "betis",
                    "real", "roma", "slovan", "sabah", "shakhtar", "slavia", "sporting", "stuttgart", "viking", "villarreal"], True),
    "uel": _season(("UEFA.com — Europa League 2026/27, league phase", "https://www.uefa.com/uefaeuropaleague/clubs/"),
                   ["anderlecht", "ararat", "az", "benfica", "besiktas", "bournemouth", "celje", "celta", "celtic", "crystal_palace", "ferencvaros", "dinamo_zg",
                    "hapoel_bs", "hoffenheim", "jagiellonia", "juventus", "lech", "leverkusen", "levski", "lillestrom", "lyon", "marseille", "milan", "nec",
                    "ofi", "olympiacos", "omonia", "real_sociedad", "rennes", "salzburg", "sparta", "sturm", "sunderland", "torreense", "union_sg", "plzen"], True),
    "uecl": _season(("UEFA.com — Conference League 2026/27, league phase", "https://www.uefa.com/uefaconferenceleague/clubs/"),
                    ["aarhus", "ajax", "atalanta", "borac", "braga", "brann", "brighton", "copenhagen", "crvena_zvezda", "cska_sofia", "egnatia", "freiburg", "gent",
                     "getafe", "hajduk", "hearts", "iberia", "inter_escaldes", "jablonec", "kairat", "kauno", "kups", "red_imps", "lugano", "midtjylland",
                     "mjallby", "monaco", "nordsjaelland", "pafos", "panathinaikos", "riga", "sint_truiden", "thun", "trabzonspor", "twente", "craiova"], True),
    "superliga": _season(("superliga.rfs.ru (страница показывает 2024/25) + srrb.ru, Чемпионат: состав 2026/27 по спортивным СМИ", "https://srrb.ru/translyacii-sportivnyx-sobytij/translyacii-mini-futbol/sinara-gazprom-yugra-superliga-rossii-po-futzalu-pryamaya-translyaciya-18-sentyabrya-2026.html"),
                         ["sinara", "gazprom_yugra", "iraero", "kprf", "kristall", "novaya_generatsiya", "norilsk", "sibiryak", "surgut", "tzms", "torpedo_fz", "tyumen"], False),
    "vtb": _season(("Единая лига ВТБ — календарный план 2026/27 (vtb-league.com) + Чемпионат (Астана)", "https://vtb-league.com/ru/news/kalendarnyj-plan-chempionata-2026-27-edinoj-ligi-vtb/"),
                   ["avtodor", "astana_b", "enisey_b", "zenit_b", "lokomotiv_kuban", "mba", "parma_b", "dinamo_vl", "samara", "unics", "uralmash", "cska_b"], False),
}

_SPORT_OF_CLUB: dict = {}
for _c in COMPETITIONS:
    for _k in SEASONS[_c[0]]["clubs"]:
        _SPORT_OF_CLUB.setdefault(_k, _c[2])
# (ключ, название, вид спорта, псевдонимы)
CLUBS = [(k, name, _SPORT_OF_CLUB[k], _ALIASES.get(k, ())) for k, name in _N.items() if k in _SPORT_OF_CLUB]

NAME_BY_KEY = dict(_N)                    # названия ВСЕХ клубов снимка, включая клубы отложенных турниров (нужны таблицам алиасов адаптеров)
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


def effective_selection(profile: dict) -> dict:
    """Что реально учитывается из выбора участника: вид спорта → его чемпионаты → клубы выбранных чемпионатов (клуб из двух турниров остаётся).
    Выбор, которого больше нет в активном каталоге (баскетбол, ВТБ, ЛЕ, ЛК), физически остаётся в профиле, но не показывается и не считается."""
    sports = [k for k, _, _, _ in SPORTS if k in profile.get("sports", [])]
    champs = [c[0] for c in competitions_for(sports) if c[0] in profile.get("championships", [])]
    return {"sp": sports, "cp": champs, "cl": [c[0] for c in clubs_for_competitions(champs) if c[0] in profile.get("clubs", [])]}


def competitions_for(sports) -> list:
    return [c for c in COMPETITIONS if c[2] in sports]


def comp_club_keys(comp_key: str) -> list:
    """Клубы чемпионата в снимке текущего сезона (SEASONS)."""
    return list(SEASONS.get(comp_key, {}).get("clubs", []))


def season_info(comp_key: str) -> dict:
    """Метаданные снимка: season, source_name, source_url, official, verified_at."""
    return {k: v for k, v in SEASONS[comp_key].items() if k != "clubs"}


def clubs_for_competitions(comp_keys) -> list:
    """Объединение клубов выбранных чемпионатов: порядок — по каталогу, клуб из нескольких турниров — один раз."""
    wanted = {k for ck in comp_keys for k in comp_club_keys(ck)}
    return [c for c in CLUBS if c[0] in wanted]
