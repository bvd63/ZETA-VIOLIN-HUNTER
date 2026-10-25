"""Shared discovery vocabulary; bounded rotation keeps marketplace traffic small."""

from datetime import datetime
import re
import unicodedata
from zoneinfo import ZoneInfo

MODEL_CODES = ("JV44", "JV45", "SV24", "SV25", "SV43", "SV244", "CV44", "EV25", "EV44", "JVS4", "JLP5", "JV5", "JLP4")
MODEL_NAMES = (
    "jazz fusion", "jazz standard", "jazz modern", "jazz classic", "jazz acoustic pro",
    "jazz fusion legacy", "fusion legacy", "jazz legacy", "strados modern",
    "strados fusion", "strados acoustic pro", "strados standard", "strados legacy",
    "e-fusion", "e-modern", "ev acoustic pro", "acoustic pro", "educator", "vanessa-mae", "imbus fusion", "modernist",
)
ARTISTS = ("Jean-Luc Ponty", "Boyd Tinsley", "Eileen Ivers")
PREFERRED_QUERIES = ("Zeta JLP", "Zeta sunburst", "Zeta tobacco burst", "Zeta JLP Burst")
JLP_RX = re.compile(r"\bjlp(?:[\s-]?[45])?\b|\bjean[\s-]*luc[\s-]*ponty\b", re.I)
BURST_RX = re.compile(r"\b(?:sun[\s-]*burst|(?:tobacco|honey|jlp)[\s-]*burst)\b", re.I)
CJK = r"\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af"
SCRIPT_BOUNDARY_RX = re.compile(r"(?<=[a-z0-9])(?=[" + CJK + r"])|(?<=[" + CJK + r"])(?=[a-z0-9])", re.I)


def normalize_identity_text(text: str) -> str:
    """Separate script boundaries: ZETAバイオリン / JV5小提琴 need no seller spaces."""
    return SCRIPT_BOUNDARY_RX.sub(" ", unicodedata.normalize("NFKC", text))


def preference_matches(listing: dict) -> list:
    """Owner's priorities, never an identity/eligibility gate or authenticity claim."""
    text = normalize_identity_text(" ".join(str(listing.get(k) or "") for k in
                                          ("title", "description", "model", "finish")))
    matches = []
    if JLP_RX.search(text):
        matches.append("Jean-Luc Ponty / JLP menționat")
    if BURST_RX.search(text):
        matches.append("finisaj sunburst / burst menționat")
    return matches
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
    "zh": "小提琴", "ko": "바이올린", "el": "βιολί", "tr": "keman",
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
    pool += list(PREFERRED_QUERIES)
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
    'Zeta 小提琴 OR Zeta 바이올린 OR Zeta βιολί OR Zeta keman',
    'Zeta JLP OR Zeta JLP4 OR Zeta JLP5 OR Zeta sunburst OR Zeta "tobacco burst" OR Zeta "JLP Burst"',
]
