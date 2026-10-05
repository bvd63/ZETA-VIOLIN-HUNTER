"""Shared discovery vocabulary; bounded rotation keeps marketplace traffic small."""

from datetime import datetime
from zoneinfo import ZoneInfo

MODEL_CODES = ("JV44", "JV45", "SV24", "SV25", "SV43", "SV244", "CV44", "EV25", "EV44", "JVS4", "JLP5")
MODEL_NAMES = (
    "jazz fusion", "jazz standard", "jazz modern", "jazz classic", "jazz acoustic pro",
    "jazz fusion legacy", "fusion legacy", "jazz legacy", "strados modern",
    "strados fusion", "strados acoustic pro", "strados standard", "strados legacy",
    "e-fusion", "e-modern", "ev acoustic pro", "acoustic pro", "educator", "vanessa-mae", "imbus fusion", "modernist",
)
ARTISTS = ("Jean-Luc Ponty", "Boyd Tinsley", "Eileen Ivers")
STRING_VARIANTS = tuple(
    variant for count, word in ((4, "four"), (5, "five"))
    for variant in (f"{count}-string", f"{count}-strings", f"{count} string",
                    f"{count} strings", f"{count}string", f"{count}strings",
                    f"{word}-string", f"{word} string", f"{word} strings")
)
VIOLIN_WORDS = {
    "en": "violin", "fr": "violon", "it": "violino", "es": "violín",
    "de": "Geige", "nl": "viool", "pl": "skrzypce", "pt": "violino",
    "ja": "バイオリン", "no": "fiolin", "fi": "viulu", "da": "violin",
    "sv": "fiol", "bg": "цигулка", "uk": "скрипка",
}


def market_queries(language: str = "en", *, broad: bool = False, limit: int = 8,
                   extra: tuple = (), now: datetime = None) -> list:
    """Brand/model searches every run, then rotating codes/signatures/aliases.

    Bare brand searches belong in instrument categories or APIs with a local
    signal filter. Craigslist uses only four queries for hundreds of areas.
    Rotation is evaluated per search(), never frozen at container import time.
    """
    word = VIOLIN_WORDS.get(language, "violin")
    core = ["zeta" if broad else f"Zeta {word}", "Zeta Jazz Fusion", "strados"] + list(extra)
    if language == "ja":
        core += ["ゼータ バイオリン", "ゼータ エレキバイオリン"]
    core = list(dict.fromkeys(core))
    pool = [f"Zetta {word}", *MODEL_CODES, *[f"{artist} {word}" for artist in ARTISTS]]
    pool += [f"{model} {word}" for model in MODEL_NAMES if model != "educator"]
    pool += [f"Zeta {variant} {word}" for variant in STRING_VARIANTS]
    local = now or datetime.now(ZoneInfo("Europe/Bucharest"))
    slots = max(1, limit - len(core))
    offset = (local.toordinal() * 2 + int(local.hour >= 22)) * slots
    rotated = [pool[(offset + i) % len(pool)] for i in range(len(pool))]
    return list(dict.fromkeys(core + rotated))[:max(1, limit)]


WEB_KEYWORDS = [
    'Zeta violin OR Zeta fiddle', 'Zeta Strados OR Strados violin',
    'Zeta Jazz Fusion OR Jazz Fusion violin',
    ' OR '.join(MODEL_CODES),
    'Zetta violin OR Zeta violino OR Zeta violon OR Zeta Geige OR Zeta viool',
    'Zeta JLP OR Jean-Luc Ponty violin OR Boyd Tinsley violin OR Eileen Ivers violin',
    'ゼータ バイオリン OR ゼータ エレキバイオリン OR ZETA ヴァイオリン',
    'Zeta скрипка OR Zeta цигулка OR Zeta skrzypce OR Zeta hegedű',
    'Zeta violin (' + ' OR '.join(f'"{v}"' for v in STRING_VARIANTS[:9]) + ')',
    'Zeta violin (' + ' OR '.join(f'"{v}"' for v in STRING_VARIANTS[9:]) + ')',
]
