"""Сопоставление названий из источников с id клубов каталога (tribun_catalog). Только явные таблицы — без нечёткого поиска и без LLM.

Алиасы действуют ВНУТРИ турнира: «Динамо» в РПЛ = dinamo_msk, «Торпедо» в КХЛ = torpedo_h, в Первой лиге = torpedo, в Суперлиге = torpedo_fz.
Названия — как их печатает источник (sports.ru, superliga.rfs.ru), проверены на реальных страницах сезона 2026/27 (см. tests/fixtures/feed).
Точные совпадения с названием каталога (после norm) подхватываются автоматически; здесь — только отличия."""
import re

import tribun_catalog as C

# Официальные аббревиатуры NHL API (api-web.nhle.com) → id клуба каталога
NHL_ABBREV = {
    "ANA": "nhl_ana", "UTA": "nhl_utah", "BOS": "nhl_bos", "BUF": "nhl_buf", "CGY": "nhl_cgy", "CAR": "nhl_car", "CHI": "nhl_chi", "COL": "nhl_col",
    "CBJ": "nhl_cbj", "DAL": "nhl_dal", "DET": "nhl_det", "EDM": "nhl_edm", "FLA": "nhl_fla", "LAK": "nhl_lak", "MIN": "nhl_min", "MTL": "nhl_mtl",
    "NSH": "nhl_nsh", "NJD": "nhl_njd", "NYI": "nhl_nyi", "NYR": "nhl_nyr", "OTT": "nhl_ott", "PHI": "nhl_phi", "PIT": "nhl_pit", "SJS": "nhl_sjs",
    "SEA": "nhl_sea", "STL": "nhl_stl", "TBL": "nhl_tbl", "TOR": "nhl_tor", "VAN": "nhl_van", "VGK": "nhl_vgk", "WSH": "nhl_wsh", "WPG": "nhl_wpg",
}

# Только отличия от названия каталога: название в источнике → id клуба
_OVERRIDES = {
    "nhl": {"Айлендерс": "nhl_nyi", "Анахайм": "nhl_ana", "Баффало": "nhl_buf", "Бостон": "nhl_bos", "Ванкувер": "nhl_van", "Вашингтон": "nhl_wsh",
            "Вегас": "nhl_vgk", "Виннипег": "nhl_wpg", "Даллас": "nhl_dal", "Детройт": "nhl_det", "Калгари": "nhl_cgy", "Каролина": "nhl_car",
            "Коламбус": "nhl_cbj", "Колорадо": "nhl_col", "Лос-Анджелес": "nhl_lak", "Миннесота": "nhl_min", "Монреаль": "nhl_mtl",
            "Нью-Джерси": "nhl_njd", "Нэшвилл": "nhl_nsh", "Оттава": "nhl_ott", "Питтсбург": "nhl_pit", "Рейнджерс": "nhl_nyr", "Сан-Хосе": "nhl_sjs",
            "Сент-Луис": "nhl_stl", "Сиэтл": "nhl_sea", "Тампа-Бэй": "nhl_tbl", "Торонто": "nhl_tor", "Филадельфия": "nhl_phi", "Флорида": "nhl_fla",
            "Чикаго": "nhl_chi", "Эдмонтон": "nhl_edm", "Юта": "nhl_utah"},
    "rpl": {"Динамо": "dinamo_msk"},
    "khl": {"Шанхай Дрэгонс": "shanghai", "Динамо Минск": "dinamo_mn_h", "Динамо Москва": "dinamo_msk_h", "Локомотив": "lokomotiv_h",
            "Нефтехимик": "neftekhimik_h", "Сочи": "sochi_h", "Спартак": "spartak_h", "Торпедо": "torpedo_h", "ЦСКА": "cska_h"},
    "apl": {"Ипсвич": "ipswich", "Ковентри": "coventry", "Халл": "hull"},
    "laliga": {"Атлетик": "athletic", "Атлетико": "atletico", "Депортиво": "deportivo", "Расинг": "racing"},
    "vtb": {"БЕТСИТИ Парма": "parma_b", "МБА-МАИ": "mba", "Динамо-Владивосток": "dinamo_vl", "Зенит": "zenit_b", "ЦСКА": "cska_b", "Енисей": "enisey_b",
            "Астана": "astana_b"},
    "fnl1": {"Торпедо": "torpedo", "Нефтехимик": "neftekhimik_f", "Енисей": "enisey", "СКА Хабаровск": "ska_khb"},
    "superliga": {"Норильск": "norilsk", "Торпедо": "torpedo_fz"},
}
_EURO = {
    "АЕК": "aek", "Атлетико": "atletico", "Боруссия Д": "dortmund", "Слован Б": "slovan", "Шахтер": "shakhtar", "Славия": "slavia",
    "Хапоэль Беер-Шева": "hapoel_bs", "ОФИ": "ofi", "Ред Булл": "salzburg", "Штурм": "sturm", "Торреенсе": "torreense", "Юнион": "union_sg",
    "Мьелльбю": "mjallby", "Сент-Труйден": "sint_truiden", "Хартс": "hearts", "Линкольн": "red_imps", "Борац Баня-Лука": "borac",
    "Мидтьюлланд": "midtjylland", "Нордшелланд": "nordsjaelland", "Лиллестрем": "lillestrom", "Арарат-Армения": "ararat",
}
# Алиас есть, но название ещё не встречалось в реальных данных источника (клуб в каталоге, ещё не играл): покрытие считается «не проверено».
UNVERIFIED = {"vtb": {"astana_b"}}

for _c in ("ucl", "uel", "uecl"):
    _OVERRIDES[_c] = _EURO


# Английские названия клубов лиги чемпионов 2026/27 в Википедии (независимый источник сверки) → id каталога. Названия сняты с реальной страницы
# «2026–27 UEFA Champions League league phase» (36 команд).
WIKI_UCL = {
    "AEK Athens": "aek", "Arsenal": "arsenal", "Aston Villa": "aston_villa", "Atlético Madrid": "atletico", "Barcelona": "barcelona",
    "Bayern Munich": "bayern", "Bodø/Glimt": "bodo", "Borussia Dortmund": "dortmund", "Club Brugge": "club_brugge", "Como": "como",
    "Fenerbahçe": "fenerbahce", "Feyenoord": "feyenoord", "Galatasaray": "galatasaray", "Inter Milan": "inter", "LASK": "lask", "Lens": "lens",
    "Lille": "lille", "Liverpool": "liverpool", "Manchester City": "man_city", "Manchester United": "man_united", "Napoli": "napoli",
    "PSV Eindhoven": "psv", "Paris Saint-Germain": "psg", "Porto": "porto", "RB Leipzig": "leipzig", "Real Betis": "betis",
    "Real Madrid": "real", "Roma": "roma", "Sabah": "sabah", "Shakhtar Donetsk": "shakhtar", "Slavia Prague": "slavia",
    "Slovan Bratislava": "slovan", "Sporting CP": "sporting", "VfB Stuttgart": "stuttgart", "Viking": "viking", "Villarreal": "villarreal",
}


def norm(name: str) -> str:
    s = (name or "").lower().replace("ё", "е")
    s = re.sub(r"[«»\"'’′`]", "", s)
    s = re.sub(r"\b(фк|хк|мфк|бк|fc|bc)\b", " ", s)
    s = re.sub(r"[\s\-–—]+", " ", s)
    return s.strip()


def _plain_name(key: str) -> str:
    return re.sub(r"\s*\(.*?\)", "", C.NAME_BY_KEY[key])


def _build() -> dict:
    out = {}
    for comp in C.SEASONS:
        table = {}
        for key in C.comp_club_keys(comp):
            table[norm(_plain_name(key))] = key
        for name, key in _OVERRIDES.get(comp, {}).items():
            if key in C.comp_club_keys(comp):                  # клуб чужого турнира сюда не подмешивается
                table[norm(name)] = key
        out[comp] = table
    return out


ALIASES = _build()


def resolve(comp: str, name: str):
    return ALIASES.get(comp, {}).get(norm(name))

ALIASES["wiki:ucl"] = {norm(name): key for name, key in WIKI_UCL.items()}
