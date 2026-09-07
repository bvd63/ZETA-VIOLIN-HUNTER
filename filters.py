"""
Central listing filters — the single implementation of CLAUDE.md §4 (keywords)
and §5 (filter logic). Every scraper and main.py import from here.

All Latin-script matching is WORD-BOUNDARY based (regex), never plain
substring, so that "hard shell case" is not an Arc'teryx jacket, "sold as is"
is not a sold listing and "no defects" is not a defective instrument.
Japanese terms are matched as substrings because Japanese has no word spaces.
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
    "strados modern", "strados fusion", "strados acoustic pro", "strados standard", "strados legacy",
    "e-fusion", "e-modern", "ev acoustic pro", "acoustic pro", "acoustic-pro",
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
# Checked in title + description (whole words).
NOISE_RX = _rx([
    # Non-violin Zeta-brand products / homonyms
    "arcteryx", "arc'teryx", "arc teryx", "jacket", "jackets", "hoodie", "pants",
    "backpack", "snowboard", "ski", "skis",
    "zeta phi beta", "zeta reticuli", "zeta cartridge", "zeta pump", "zeta potential",
    "zeta-jones", "zeta jones", "dartboard", "zeta drive", "overdrive",
    # Zeta accessories / non-violin Zeta gear
    "footswitch", "pedal", "pedals", "midi controller", "midi interface", "synthony",
    "combo amp", "padded cover", "amp cover", "cover for", "catalog", "catalogue",
    "brochure", "manual only", "strings only", "string set", "sticker", "decal",
    # Other instruments (scope = violins only)
    "cello", "cellos", "mandolin", "upright bass", "bass guitar",
    # Copies / look-alikes ("Replica del celebre violino Zeta", "Zeta violin copy")
    "replica", "copia", "copy", "kopie", "kopia", "copie", "clone",
    "zeta style", "zeta-style", "stile zeta", "style zeta",
    "tipo zeta", "type zeta", "like zeta", "similar to zeta", "inspired by zeta",
])
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
# Checked in TITLE only (would over-trigger in descriptions).
NOISE_TITLE_RX = _rx([
    "cd", "cds", "dvd", "vinyl", "lp", "album", "cassette", "book", "poster",
    "shirt", "t-shirt", "sheet music", "bass", "guitar", "viola", "violas",
])

# Non-purchase intent — title + description.
INTENT_RX = _rx([
    "wtb", "wanted to buy", "want to buy", "looking to buy", "in search of",
    "parts only", "part only", "for parts", "for parts or repair",
    "not working", "doesn't work", "does not work", "broken", "defective",
    "case only", "bow only", "bridge only", "pickup only", "gig bag only",
    "cover only", "bag only",
])
# Title only: "Wanted: Zeta violin", "ISO Zeta Strados", "Gezocht: Zeta viool".
INTENT_TITLE_RX = _rx([
    "wanted", "iso",
    "gezocht", "gesucht", "suche", "cerco", "recherche", "cherche", "busco",
    "szukam", "kupię", "kupie", "søges", "søkes", "sökes", "etsitään", "compro", "procuro",
])

# Sold / ended — title only, and "sold as is" is NOT sold.
SOLD_RX = re.compile(
    r"(?<![\w])(?:sold(?![\s\-]*as[\s\-]*is)|ended|expired|sold out|out of stock)(?![\w])",
    re.IGNORECASE,
)

# Other electric-violin brands — title only (descriptions often compare brands).
OTHER_BRAND_RX = _rx([
    "yamaha", "silent violin", "yev", "yev-104", "yev-105", "sv-200", "sv-250", "ev-205",
    "bridge violin", "bridge aquila", "bridge draco", "bridge lyra", "golden tasman",
    "ns design", "ns wav", "ns cr", "mark wood", "wood violin", "wood viper", "viper", "stingray",
    "fender", "fv-1", "stagg", "cantini", "cecilio", "kinglos", "glasser", "eco-ion",
    "electric violin lutherie", "evl", "vangoa",
])

# ---------------------------------------------------------------------------
# NEW STOCK — owner wants second-hand only (any model, any year).
# ---------------------------------------------------------------------------
# Platform condition labels meaning "new": eBay "New", "New with tags",
# "New other (see details)", "Open box"; Reverb "Brand New", "B-Stock".
# "Mint" / "Like New" / "wie neu" are USED and must pass.
NEW_CONDITION_RX = re.compile(
    r"^\s*(?:brand[\s\-]*new|new(?:\s*\(|\s+with|\s+without|\s+other|\s*$)|b[\s\-]*stock|open[\s\-]*box|nuovo|neuf|neu|nieuw|nuevo)",
    re.IGNORECASE,
)
# Title-only shop language for new stock.
NEW_STOCK_TITLE_RX = _rx([
    "brand new", "brandneu", "nagelneu", "nuovissimo", "new in box", "nib", "bnib",
    "authorized dealer", "authorised dealer", "in stock", "financing", "free financing",
    "factory sealed", "sealed", "nou nouț", "nou nout",
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


def has_noise(title: str, description: str = "") -> bool:
    text = f"{title} {description}"
    return (
        bool(NOISE_RX.search(text))
        or bool(NOISE_TITLE_RX.search(title))
        or any(j in text for j in NOISE_JP)
    )


def is_excluded_intent(title: str, description: str = "") -> bool:
    return bool(INTENT_RX.search(f"{title} {description}")) or bool(INTENT_TITLE_RX.search(title))


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
    low = raw.lower()
    # Drop obvious non-listing pages. NOTE: /forum intentionally allowed
    # (maestronet.com/forum/topic/... are valid classifieds).
    bad_fragments = ["/search", "?q=", "/category", "/categories", "/help", "/about"]
    return not any(fragment in low for fragment in bad_fragments)


def classify(listing: dict) -> str:
    """Return "" if the listing should be alerted, otherwise a short reason
    (used for per-reason counters in main.py)."""
    from config import Config  # local import keeps filters importable in tests
    title, desc = _text(listing)
    if is_other_brand(title):
        return "other_brand"
    if is_new_stock(listing, Config.CONDITION, Config.EXCLUDED_SELLERS):
        return "new_stock"
    if has_noise(title, desc):
        return "noise"
    if is_excluded_intent(title, desc):
        return "intent"
    if is_sold_or_ended(title):
        return "sold"
    if not is_zeta_violin(title, desc):
        return "non_zeta"
    if not is_valid_listing_url(str(listing.get("url", ""))):
        return "url"
    return ""
