"""
Central listing filters — the single implementation of CLAUDE.md §4 (keywords)
and §5 (filter logic). Every scraper and main.py import from here.

All Latin-script matching is WORD-BOUNDARY based (regex), never plain
substring, so that "hard shell case" is not an Arc'teryx jacket, "sold as is"
is not a sold listing and "no defects" is not a defective instrument.
Japanese terms are matched as substrings because Japanese has no word spaces.

Precision vs recall (Prompt 16): when the TITLE alone proves a Zeta violin,
the description is only checked for a short "not actually for sale" list.
Object-of-sale words (pedal, cover, cello, copy, sticker, broken ...) are
title-only — sellers mention them in bodies of perfectly good listings.
"""

import re
from urllib.parse import urlsplit


def _rx(terms: list) -> "re.Pattern":
    """Compile a case-insensitive alternation of whole-word terms.
    Spaces/hyphens inside a term match any run (including none) of spaces or
    hyphens, so "jean-luc ponty" also matches "Jean Luc Ponty" / "JeanLuc Ponty"."""
    parts = []
    for term in terms:
        tokens = [re.escape(tok) for tok in re.split(r"[\s\-]+", term.strip()) if tok]
        parts.append(r"[\s\-]*".join(tokens))
    return re.compile(r"(?<![\w])(?:" + "|".join(parts) + r")(?![\w])", re.IGNORECASE)


# ---------------------------------------------------------------------------
# §4.1 CLASS A — strong Zeta identifiers
# ---------------------------------------------------------------------------
ZETA_BRAND_RX = _rx(["zeta", "zetta", "zetamusic", "zeta music", "zeta music systems"])
ZETA_BRAND_JP = ["ゼータ"]

# Unique Zeta model codes, optional hyphen (SV-24). Yamaha SV-200/SV-250/EV-205
# do NOT match because the digits must end at a word boundary.
MODEL_CODE_RX = re.compile(r"(?<![\w])(?:jv[\s\-]?4[45]|sv[\s\-]?2[45]|sv[\s\-]?43|cv[\s\-]?44|ev[\s\-]?25|ev[\s\-]?44)(?![\w])", re.IGNORECASE)

ZETA_ONLY_ARTIST_RX = _rx(["jean-luc ponty", "jean luc ponty", "jlp", "jlp5", "boyd tinsley", "eileen ivers"])

# ---------------------------------------------------------------------------
# §4.2 CLASS B — model names
# ---------------------------------------------------------------------------
STRADOS_RX = _rx(["strados"])  # alone is OK
MODEL_NAME_RX = _rx([
    "jazz fusion", "jazz standard", "jazz modern", "jazz classic", "jazz acoustic pro",
    "jazz fusion legacy", "fusion legacy", "jazz legacy",
    "strados modern", "strados fusion", "strados acoustic pro", "strados standard", "strados legacy",
    "e-fusion", "e-modern", "ev acoustic pro", "acoustic pro", "acoustic-pro",
    "educator",  # Zeta Educator student series (requires Zeta context)
    "vanessa-mae", "vanessa mae",
])

# ---------------------------------------------------------------------------
# §4.3 CLASS C — violin words, multi-language
# ---------------------------------------------------------------------------
VIOLIN_RX = _rx([
    "violin", "violins", "fiddle", "fiddles", "e-violin",
    "violino", "violini", "violino elettrico",
    "violon", "violons", "violon électrique", "violon electrique",
    "violín", "violin", "violines", "violín eléctrico",
    "geige", "geigen", "e-geige", "elektrische geige",
    "viool", "violijn", "elektrische viool",
    "skrzypce", "skrzypce elektryczne",
    "vioara", "vioară", "viori",
    # Nordic / Slavic / Hungarian / Czech
    "fiolin", "fiol", "elfiol", "viulu", "sähköviulu", "hegedű", "housle",
    "цигулка", "скрипка", "электроскрипка", "електроскрипка",
])
VIOLIN_JP = ["バイオリン", "ヴァイオリン", "エレキバイオリン", "エレクトリックバイオリン", "エレクトリックヴァイオリン"]

# ---------------------------------------------------------------------------
# §4.5 BLACKLIST — noise
# ---------------------------------------------------------------------------
# Homonyms / non-instrument Zeta products — safe in title AND description.
NOISE_RX = _rx([
    "arcteryx", "arc'teryx", "arc teryx", "jacket", "jackets", "hoodie", "pants",
    "backpack", "snowboard", "skis",
    "zeta phi beta", "zeta reticuli", "zeta cartridge", "zeta pump", "zeta potential",
    "zeta-jones", "zeta jones", "dartboard", "zeta drive",
    # Zeta non-violin gear that is the OBJECT of the listing when in the title;
    # these words are common inside violin bodies too, so see NOISE_OBJECT_RX.
    "synthony",
    # Copies / look-alikes ("Replica del celebre violino Zeta", "Zeta violin copy")
    "replica", "zeta style", "zeta-style", "stile zeta", "style zeta",
    "tipo zeta", "type zeta", "like zeta", "similar to zeta", "inspired by zeta",
])
# Object-of-sale words: drop only when they are in the TITLE (or when the
# title alone does not prove a Zeta violin and the body must be trusted).
NOISE_OBJECT_RX = _rx([
    "ski", "overdrive", "footswitch", "pedal", "pedals", "midi controller", "midi interface",
    "combo amp", "padded cover", "amp cover", "cover for", "catalog", "catalogue",
    "brochure", "manual only", "strings only", "string set", "sticker", "decal",
    "cello", "cellos", "mandolin", "upright bass", "bass guitar",
    "viola", "violas",  # §1 scope: violins only, even when the title also says "violin"
    "copia", "copy", "kopie", "kopia", "copie", "clone",
    "fx", "effects", "effect processor", "preamp only", "pickup system only",
])
# Media / other instruments — title only; instrument words only when the
# title has no violin word ("Zeta violin + bass amp" must pass).
NOISE_TITLE_MEDIA_RX = _rx([
    "cd", "cds", "dvd", "vinyl", "lp", "album", "cassette", "book", "poster",
    "shirt", "t-shirt", "sheet music",
])
NOISE_TITLE_INSTRUMENT_RX = _rx(["bass", "guitar"])
# Japanese noise — substring match (no word spaces in Japanese).
NOISE_JP = [
    "カタログ",      # catalog
    "レコード",      # record (vinyl)
    "楽譜",          # sheet music
    "ポスター",      # poster
    "ジャケット",    # jacket
    "ケースのみ",    # case only
    "弓のみ",        # bow only
    "ジャンク",      # junk / for parts
    "パンフレット",  # brochure
    "チェロ",        # cello
]

# Non-purchase intent — safe in title AND description.
INTENT_RX = _rx([
    "wtb", "wanted to buy", "want to buy", "looking to buy", "in search of",
    "parts only", "part only", "for parts", "for parts or repair",
    "case only", "bow only", "bridge only", "pickup only", "gig bag only",
    "cover only", "bag only", "not for sale", "no longer for sale",
])
# Title only: "Wanted: Zeta violin", "ISO Zeta Strados", "Gezocht: Zeta viool",
# "broken Zeta violin".
INTENT_TITLE_RX = _rx([
    "wanted", "iso", "broken", "defective", "not working", "doesn't work", "does not work",
    "gezocht", "gesucht", "suche", "cerco", "recherche", "cherche", "busco",
    "szukam", "kupię", "kupie", "søges", "søkes", "sökes", "etsitään", "compro", "procuro",
])

# Sold / ended — title only. "sold as is" is NOT sold, and a mid-sentence
# "must be sold this week" is not either: only leading/trailing/bracketed SOLD.
SOLD_RX = re.compile(
    r"(?:^\W*(?:sold|ended|expired|sold out|out of stock)\b(?![\s\-]*as[\s\-]*is))"
    r"|(?:\b(?:sold|ended|expired|sold out)\W*$)"
    r"|(?:[\[(*]\s*(?:sold|ended|expired)\s*[\])*])"
    r"|(?:\bsold\s*[!*]+)",
    re.IGNORECASE,
)

# Other electric-violin brands — title only (descriptions often compare brands).
OTHER_BRAND_RX = _rx([
    "yamaha", "silent violin", "yev", "yev-104", "yev-105", "sv-200", "sv-250", "ev-205",
    "bridge violin", "bridge aquila", "bridge draco", "bridge lyra", "golden tasman",
    "ns design", "ns wav", "ns cr", "mark wood", "wood violin", "wood viper", "viper", "stingray",
    "fender", "fv-1", "stagg", "cantini", "cecilio", "kinglos", "glasser", "eco-ion",
    "electric violin lutherie", "evl", "vangoa",
    "jordan", "realist", "barcus berry", "barcus-berry", "skyinbow", "harley benton",
    "3dvarius", "ted brewer", "wood violins", "gewa", "aurora violin", "eastar",
])

# ---------------------------------------------------------------------------
# NEW STOCK — owner wants second-hand only (any model, any year).
# ---------------------------------------------------------------------------
# Platform condition labels meaning "new": eBay "New", "New with tags",
# "New other (see details)", "Open box"; Reverb "Brand New", "B-Stock".
# "Mint" / "Like New" / "wie neu" / "Neuwertig" are USED and must pass.
NEW_CONDITION_RX = re.compile(
    r"^\s*(?:brand[\s\-]*new\b|new(?:\s*\(|\s+with\b|\s+without\b|\s+other\b|\s*$)|b[\s\-]*stock\b|open[\s\-]*box\b"
    r"|nuovo\b|neuf\b|neu\b|nieuw\b|nuevo\b)(?!\s*(?:wertig|-?ähnlich))",
    re.IGNORECASE,
)
# Title-only shop language for new stock.
NEW_STOCK_TITLE_RX = _rx([
    "brand new", "brandneu", "nagelneu", "nuovissimo", "new in box", "nib", "bnib",
    "authorized dealer", "authorised dealer", "in stock", "financing", "free financing",
    "factory sealed", "nou nouț", "nou nout",
])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _text(listing: dict) -> tuple:
    title = str(listing.get("title", "") or "")
    desc = str(listing.get("description", "") or "")
    return title, desc


def has_zeta_brand(text: str) -> bool:
    return bool(ZETA_BRAND_RX.search(text)) or any(j in text for j in ZETA_BRAND_JP)


def has_violin_word(text: str) -> bool:
    return bool(VIOLIN_RX.search(text)) or any(j in text for j in VIOLIN_JP)


def has_zeta_signal(text: str) -> bool:
    """Broad pre-filter for scrapers that query generic terms: anything that
    could be a Zeta (brand, unique code, signature artist, Strados)."""
    return (
        has_zeta_brand(text)
        or bool(MODEL_CODE_RX.search(text))
        or bool(ZETA_ONLY_ARTIST_RX.search(text))
        or bool(STRADOS_RX.search(text))
    )


# ---------------------------------------------------------------------------
# §5 filter logic
# ---------------------------------------------------------------------------
def is_zeta_violin(title: str, description: str = "") -> bool:
    """CLASS A / B acceptance (positive side of §5)."""
    text = f"{title} {description}"
    if MODEL_CODE_RX.search(text):
        return True
    if ZETA_ONLY_ARTIST_RX.search(text):
        return True
    if STRADOS_RX.search(text):
        return True
    has_zeta = has_zeta_brand(text)
    has_violin = has_violin_word(text)
    has_model = bool(MODEL_NAME_RX.search(text))
    if has_zeta and (has_violin or has_model):
        return True
    # §4.2: model name with violin context, no brand string
    if has_model and has_violin:
        return True
    return False


def has_noise(title: str, description: str = "", title_conclusive: bool = None) -> bool:
    """title_conclusive=True → the title alone proves a Zeta violin, so the
    body is checked only for homonym noise, not for object-of-sale words."""
    if title_conclusive is None:
        title_conclusive = is_zeta_violin(title)
    if NOISE_RX.search(title) or NOISE_OBJECT_RX.search(title) or NOISE_TITLE_MEDIA_RX.search(title):
        return True
    if NOISE_TITLE_INSTRUMENT_RX.search(title) and not has_violin_word(title):
        return True
    if any(j in title for j in NOISE_JP):
        return True
    if not description:
        return False
    if NOISE_RX.search(description) or any(j in description for j in NOISE_JP):
        return True
    if not title_conclusive and NOISE_OBJECT_RX.search(description):
        return True
    return False


def is_excluded_intent(title: str, description: str = "", title_conclusive: bool = None) -> bool:
    if INTENT_RX.search(title) or INTENT_TITLE_RX.search(title):
        return True
    if description and INTENT_RX.search(description):
        return True
    if title_conclusive is None:
        title_conclusive = is_zeta_violin(title)
    if description and not title_conclusive and INTENT_TITLE_RX.search(description):
        return True
    return False


def is_other_brand(title: str) -> bool:
    return bool(OTHER_BRAND_RX.search(title))


def is_sold_or_ended(title: str) -> bool:
    return bool(SOLD_RX.search(title))


def is_new_stock(listing: dict, condition_mode: str, excluded_sellers: list) -> bool:
    """True when the listing is new merchandise or comes from a new-stock
    dealer. Only active when condition_mode == "used"."""
    if condition_mode != "used":
        return False
    condition = str(listing.get("condition", "") or "")
    if condition and NEW_CONDITION_RX.search(condition):
        return True
    title = str(listing.get("title", "") or "")
    if NEW_STOCK_TITLE_RX.search(title):
        return True
    haystack = " ".join([
        str(listing.get("seller", "") or ""),
        str(listing.get("platform", "") or ""),
        urlsplit(str(listing.get("url", "") or "")).netloc,
    ]).lower()
    return any(s and s in haystack for s in excluded_sellers)


def is_valid_listing_url(url: str) -> bool:
    if not url:
        return False
    raw = str(url).strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False
    # Only reject pages that are obviously NOT a listing: the site root or a
    # search/category/help landing page (path starts with it). A "?q=" inside
    # a real item URL or a tracking parameter is fine.
    path = parts.path.rstrip("/").lower()
    if path in ("", "/"):
        return False
    return not path.startswith(("/search", "/category", "/categories", "/help", "/about"))


def classify(listing: dict) -> str:
    """Return "" if the listing should be alerted, otherwise a short reason
    (used for per-reason counters in main.py)."""
    from config import Config  # local import keeps filters importable in tests
    title, desc = _text(listing)
    title_conclusive = is_zeta_violin(title)
    if is_other_brand(title):
        return "other_brand"
    if is_new_stock(listing, Config.CONDITION, Config.EXCLUDED_SELLERS):
        return "new_stock"
    if has_noise(title, desc, title_conclusive):
        return "noise"
    if is_excluded_intent(title, desc, title_conclusive):
        return "intent"
    if is_sold_or_ended(title):
        return "sold"
    if not (title_conclusive or is_zeta_violin(title, desc)):
        return "non_zeta"
    if not is_valid_listing_url(str(listing.get("url", ""))):
        return "url"
    return ""
