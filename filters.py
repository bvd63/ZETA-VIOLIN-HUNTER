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
import unicodedata
from urllib.parse import urlsplit
from keywords import MODEL_CODES, MODEL_NAMES, ARTISTS, JLP_RX, normalize_identity_text
from source_policy import DISABLED_SCRAPERS, is_disabled_source


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
ZETA_BRAND_NATIVE = ["ゼータ", "제타"]

# Zeta model codes, optional hyphen (SV-24). Yamaha SV-200/SV-250/EV-205
# do NOT match because the digits must end at a word boundary.
MODEL_CODE_RX = re.compile(r"(?<![\w])(?:" + "|".join(
    r"[\s\-]?".join(re.escape(part) for part in re.findall(r"[A-Za-z]+|\d+", code)) for code in MODEL_CODES
) + r")(?![\w])", re.IGNORECASE)

ZETA_ONLY_ARTIST_RX = _rx([*ARTISTS, "jlp"])

# ---------------------------------------------------------------------------
# §4.2 CLASS B — model names
# ---------------------------------------------------------------------------
STRADOS_RX = _rx(["strados"])
MODEL_NAME_RX = _rx(list(MODEL_NAMES))

# ---------------------------------------------------------------------------
# §4.3 CLASS C — violin words, multi-language
# ---------------------------------------------------------------------------
VIOLIN_RX = _rx([
    "violin", "violins", "fiddle", "fiddles", "e-violin",
    "violino", "violini", "violino elettrico",
    "violon", "violons", "violon électrique", "violon electrique",
    "violín", "violin", "violines", "violín eléctrico",
    "geige", "geigen", "e-geige", "elektrische geige",
    "viool", "violen", "violijn", "elektrische viool",
    "skrzypce", "skrzypce elektryczne",
    "vioara", "vioară", "viori",
    # Nordic / Slavic / Hungarian / Czech
    "fiolin", "fiol", "elfiol", "viulu", "sähköviulu", "hegedű", "housle",
    "цигулка", "скрипка", "электроскрипка", "електроскрипка",
    "βιολί", "βιολι", "keman",
])
VIOLIN_JP = ["バイオリン", "ヴァイオリン", "エレキバイオリン", "エレクトリックバイオリン", "エレクトリックヴァイオリン",
             "小提琴", "바이올린"]

# Instrument configuration is useful when sellers omit its translated name.
# Accessories still go through the object-of-sale / case / intent checks below.
STRING_CONFIGURATION_RX = re.compile(
    r"(?<!\w)(?:[456]|four|five|six)[\s-]*(?:strings?|cordes?|corde|corzi|saiten|snaren|strenger)\b"
    r"|[４５６456]\s*弦", re.I)

# ---------------------------------------------------------------------------
# §4.5 BLACKLIST — noise
# ---------------------------------------------------------------------------
# Homonyms / non-instrument Zeta products — safe in title AND description.
NOISE_RX = _rx([
    "arcteryx", "arc'teryx", "arc teryx", "jacket", "jackets", "hoodie", "pants",
    "backpack", "snowboard", "skis",
    "zeta phi beta", "zeta reticuli", "zeta cartridge", "zeta pump", "zeta potential",
    "zeta-jones", "zeta jones", "dartboard", "zeta drive",
    # Copies / look-alikes ("Replica del celebre violino Zeta", "Zeta violin copy")
    "replica", "zeta style", "zeta-style", "stile zeta", "style zeta",
    "tipo zeta", "type zeta", "like zeta", "similar to zeta", "inspired by zeta",
])
# Object-of-sale words: drop only when they are in the TITLE (or when the
# title alone does not prove a Zeta violin and the body must be trusted).
NOISE_OBJECT_RX = _rx([
    "synthony", "ski", "overdrive", "footswitch", "pedal", "pedals", "midi controller", "midi interface",
    "combo amp", "padded cover", "amp cover", "cover for", "catalog", "catalogue",
    "brochure", "manual only", "strings only", "string set", "sticker", "decal",
    "cello", "cellos", "mandolin", "upright bass", "bass guitar",
    "copia", "copy", "kopie", "kopia", "copie", "clone",
    "fx", "effects", "effect processor", "preamp only", "pickup system only",
])
# Homonymous furniture/vehicle brands also use "Zeta" and "Modernist".
# These identify another object, not a violin model. Keep explicit instrument
# evidence and descriptions of a real violin photographed near furniture.
OTHER_PRODUCT_RX = _rx([
    "coffee table", "dining table", "side table", "armchair", "sofa",
    "bookshelf", "bookcase", "cabinet", "shelving", "desk", "chandelier",
    "stroller", "pushchair", "pram", "bicycle", "mountain bike", "motorcycle", "motorbike",
])
DESIGN_BRAND_RX = _rx(["studio zeta", "zeta studio"])
ACCESSORY_CLAUSE_RX = _rx([
    "pickup", "bridge", "amplifier", "amp", "controller", "interface", "pedal",
    "synthony", "bow", "case", "cover", "string set",
])
# Unambiguous pickup-object titles. Do not blacklist the word itself: complete
# violins routinely advertise an original or replacement pickup in the title.
PICKUP_OBJECT_TITLE_RX = re.compile(
    r"\s*(?:(?:used|original|vintage|genuine|electric|midi)\s+)*"
    r"(?:zeta\s+)?(?:violin\s+)?pickups?\s*[.!]?\s*", re.I)
# A case listing can contain both "Zeta" and "violin" without selling a violin.
# Require explicit bundle language to treat a case in the title as included gear.
CASE_RX = _rx(["case", "cases", "hardcase", "gig bag", "gigbag", "etui", "étui",
               "geigenkoffer", "geigenkasten", "cutie"])
# Hangul compounds do not have Latin word boundaries. Keep accessory objects
# separate from the manufacturer's Korean name and explicit instrument bundles.
CASE_RX = re.compile(CASE_RX.pattern + r"|케이스", re.I)
KOREAN_CASE_INCLUDED_RX = re.compile(r"\s*(?:포함|동봉|증정|미포함|불포함|별도|(?:와|랑)?\s*함께|도\s*드립)")
KOREAN_ACCESSORY_TITLE_RX = re.compile(
    r"\s*(?:(?:오리지널|정품|중고|빈티지)\s*)*(?:제타|zeta|zetta)\s*"
    r"(?:(?:오리지널|정품|중고|빈티지)\s*)*(?:전자\s*)?바이올린\s*(?:용\s*)?"
    r"(?:(?:오리지널|정품|중고|빈티지)\s*)*(?:줄|현|활|픽업|브리지|브릿지)\s*"
    r"(?:(?:오리지널|정품|중고|빈티지)\s*)*(?:세트\s*)?(?:만|단품)?\s*"
    r"(?:판매(?:합니다|해요)?|팝니다)?\s*", re.I)
KOREAN_INSTRUMENT_ABSENT_RX = re.compile(r"(?:전자\s*)?바이올린(?:은|는)?\s*(?:미포함|불포함)")
KOREAN_ACCESSORY_ONLY_SALE_RX = re.compile(
    r"(?:케이스|활|픽업|줄|현)\s*만\s*(?:판매합니다|판매해요|팝니다)(?=$|[^\w])"
    r"|(?:케이스|활|픽업|줄|현)\s*만\s*판매(?=\s*[.!]|\s*$)")
KOREAN_BUY_INTENT_RX = re.compile(r"구합니다|구매합니다|삽니다")
KOREAN_SOLD_TITLE_RX = re.compile(
    r"^\W*(?:판매|거래)\s*완료(?!\s*(?:예정|아님|아닙))(?=$|[^\w])"
    r"|(?:판매|거래)\s*완료\W*$|[\[(]\s*(?:판매|거래)\s*완료\s*[\])]")
KOREAN_ACCESSORY_MENTION_RX = re.compile(r"케이스|픽업|브리지|브릿지|바이올린\s*(?:용\s*)?(?:줄|현|활)")
CASE_BUNDLE_RX = re.compile(
    r"\b(?:with|includes?|including|plus|and|in|mit|avec|con|cu|met)\b|\bw/|[+&]", re.I)
INSTRUMENT_ABSENT_RX = _rx([
    "instrument not included", "instrument is not included", "instruments not included",
    "violin not included", "violin is not included", "no violin included",
    "instrument shown not included", "violin shown not included",
])
# Media / other instruments — title only; instrument words only when the
# title has no violin word ("Zeta violin + bass amp" must pass).
NOISE_TITLE_MEDIA_RX = _rx([
    "cd", "cds", "dvd", "vinyl", "lp", "album", "cassette", "book", "poster",
    "shirt", "t-shirt", "sheet music", "magazine", "magazines", "review", "interview",
    "press release", "advertisement", "print ad", "magazine ad", "article clipping",
    "newspaper", "pamphlet", "revista", "revue", "zeitschrift", "concert", "tickets", "ticket",
])
# Japanese titles have no spaces: "ドイツ盤LP" and "輸入ジャズCD" must
# still identify records. ASCII boundaries also avoid matching serial ABCD123.
# NFKC converts full-width ＣＤ/ＬＰ before this title-only check.
NOISE_TITLE_FORMAT_RX = re.compile(r"(?<![a-z0-9])(?:cd|dvd|lp|ep)(?![a-z0-9])", re.I)
NOISE_TITLE_MEDIA_JP = ["輸入盤", "国内盤", "アナログ盤", "紙ジャケット", "紙ジャケ", "ドイツ盤"]
OTHER_INSTRUMENT_RX = _rx(["viola", "violas"])
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
    title = normalize_identity_text(str(listing.get("title", "") or ""))
    desc = normalize_identity_text(str(listing.get("description", "") or ""))
    return title, desc


def has_zeta_brand(text: str) -> bool:
    text = normalize_identity_text(text)
    return bool(ZETA_BRAND_RX.search(text)) or any(j in text for j in ZETA_BRAND_NATIVE)


def has_violin_word(text: str) -> bool:
    text = normalize_identity_text(text)
    return bool(VIOLIN_RX.search(text)) or any(j in text for j in VIOLIN_JP)


def has_zeta_signal(text: str) -> bool:
    """Broad pre-filter for scrapers that query generic terms: anything that
    could be a Zeta (brand, unique code, signature artist, Strados)."""
    text = normalize_identity_text(text)
    return (
        has_zeta_brand(text)
        or bool(MODEL_CODE_RX.search(text))
        or bool(ZETA_ONLY_ARTIST_RX.search(text))
        or bool(STRADOS_RX.search(text))
    )


# ---------------------------------------------------------------------------
# §5 filter logic
# ---------------------------------------------------------------------------
def _violin_category(categories: object) -> bool:
    """Only a product's own instrument category, never a parent/accessory label."""
    if isinstance(categories, str):
        categories = [categories]
    if not isinstance(categories, (list, tuple)):
        return False
    for category in categories:
        if not isinstance(category, str):
            continue
        leaf = re.split(r"[/>]", category)[-1].strip()
        if (has_violin_word(leaf) and not CASE_RX.search(leaf) and not NOISE_OBJECT_RX.search(leaf)
                and not re.search(r"\b(?:accessor\w*|strings?|bows?|parts?)\b|케이스|줄|현\s*세트|활|부품|픽업", leaf, re.I)):
            return True
    return False


def _identity_metadata(listing: dict) -> dict:
    """Adapters supply metadata for the primary item, not surrounding products."""
    return {"brand": listing.get("brand", ""), "model": listing.get("model", ""),
            "categories": listing.get("categories", ())}


def has_primary_identity(listing: dict) -> bool:
    """Identity independent of an unverified search-engine description."""
    title, _ = _text(listing)
    return is_zeta_violin(title, **_identity_metadata(listing))


def sells_other_product(title: str, description: str, metadata: dict) -> bool:
    if not (OTHER_PRODUCT_RX.search(title) or DESIGN_BRAND_RX.search(title)
            or DESIGN_BRAND_RX.search(str(metadata.get("brand") or ""))):
        return False
    if has_violin_word(title) or _violin_category(metadata.get("categories")):
        return False
    # A miscategorized or vaguely titled violin can still identify itself in
    # its own description, in any supported language or by string configuration.
    for clause in re.split(r"[,;\n]|(?<=[.!?])\s+", description):
        if (not OTHER_PRODUCT_RX.search(clause)
                and (has_violin_word(clause) or STRING_CONFIGURATION_RX.search(clause))
                and is_zeta_violin(title, clause, **metadata)):
            return False
    return True


def instrument_sale_title(title: str) -> str:
    """Identify a complete instrument inside an explicit equipment bundle.

    Never waive a brand/accessory check merely because the whole title contains
    a model or 'violin': 'Yamaha violin with Zeta pickup' is still Yamaha.
    A recording's 'with Zeta violin' credit is not a physical instrument bundle.
    """
    parts = re.split(r"\s*[+&;,]\s*|\b(?:with|includes?|including|plus|and|mit|avec|con|cu|met)\b|\bw/", title, flags=re.I)
    if len(parts) < 2:
        return title
    for index, part in enumerate(parts):
        if (is_zeta_violin(part) and not OTHER_BRAND_RX.search(part)
                and not NOISE_RX.search(part) and not NOISE_OBJECT_RX.search(part)
                and not NOISE_TITLE_MEDIA_RX.search(part) and not ACCESSORY_CLAUSE_RX.search(part)
                and not INTENT_RX.search(part)):
            if any(NOISE_TITLE_MEDIA_RX.search(p) for p in parts[:index]):
                continue
            return part.strip()
    return title


def is_zeta_violin(title: str, description: str = "", *, brand: str = "", model: str = "",
                   categories: object = ()) -> bool:
    """Identity evidence may span title, full description and primary product fields."""
    text = normalize_identity_text(f"{title} {description} {brand if isinstance(brand, str) else ''} {model if isinstance(model, str) else ''}")
    has_code = bool(MODEL_CODE_RX.search(text))
    has_violin = has_violin_word(text) or _violin_category(categories)
    # JV44 also identifies aircraft kits. A bare code or artist is insufficient;
    # brand+model or code+instrument evidence does not require an English title.
    # Artist names are discovery terms only: album titles routinely mention
    # "violin" as a genre/instrument, including the user's Yahoo LP example.
    if has_code and has_violin:
        return True
    if STRADOS_RX.search(text) and (has_violin or has_zeta_brand(text)):
        return True
    has_zeta = has_zeta_brand(text)
    has_model = bool(MODEL_NAME_RX.search(text))
    if has_zeta and (has_violin or has_model or has_code or JLP_RX.search(text) or STRING_CONFIGURATION_RX.search(text)):
        return True
    # §4.2: model name with violin context, no brand string
    if has_model and has_violin:
        return True
    return False


def description_identifies_instrument(description: str) -> bool:
    """A clean identity clause can precede included pedals, manuals or other gear.

    'Zeta Strados, comes with a pedal' is an instrument. 'Zeta Synthony II for
    violin' has an accessory in its identity clause and is still rejected.
    """
    for entry in re.split(r"[,;\n]|(?<=[.!?])\s+", description):
        identity_clauses = [entry] + [entry[:bundle.start()] for bundle in CASE_BUNDLE_RX.finditer(entry)]
        for clause in identity_clauses:
            if (is_zeta_violin(clause) and not has_noise(clause)
                    and not is_excluded_intent(clause)):
                return True
    return False


def korean_instrument_sale(description: str) -> bool:
    """An accessory-shaped title needs an independent affirmative item clause."""
    for clause in re.split(r"[.;\n]", description):
        if (is_zeta_violin(clause) and has_violin_word(clause)
                and re.search(r'판매합니다|판매해요|팝니다', clause)
                and not KOREAN_ACCESSORY_MENTION_RX.search(clause)
                and not KOREAN_INSTRUMENT_ABSENT_RX.search(clause)
                and not KOREAN_BUY_INTENT_RX.search(clause)):
            return True
    return False


def has_noise(title: str, description: str = "", title_conclusive: bool = None,
              *, metadata: dict = None) -> bool:
    """title_conclusive=True → the title alone proves a Zeta violin, so the
    body is checked only for homonym noise, not for object-of-sale words."""
    if title_conclusive is None:
        title_conclusive = is_zeta_violin(title)
    if NOISE_RX.search(title) or NOISE_OBJECT_RX.search(title) or NOISE_TITLE_MEDIA_RX.search(title):
        return True
    if NOISE_TITLE_FORMAT_RX.search(unicodedata.normalize("NFKC", title)) or any(j in title for j in NOISE_TITLE_MEDIA_JP):
        return True
    if OTHER_INSTRUMENT_RX.search(title) and not mixed_lot_includes_violin(title):
        return True
    if NOISE_TITLE_INSTRUMENT_RX.search(title) and not has_violin_word(title):
        return True
    if any(j in title for j in NOISE_JP):
        return True
    if (not INTENT_RX.search(title) and is_standalone_case(title, metadata)
            and not ('케이스' in title and korean_instrument_sale(description))):
        return True
    if PICKUP_OBJECT_TITLE_RX.fullmatch(title):
        # A poorly named item may still be a whole violin. Only an independent
        # instrument clause can override a clear accessory title; "Zeta violin
        # pickup for sale" describes the pickup, not a bundled instrument.
        instrument_clause = False
        for entry in re.split(r"[,;\n]|(?<=[.!?])\s+", description):
            clauses = [entry] + [entry[:bundle.start()] for bundle in CASE_BUNDLE_RX.finditer(entry)]
            if any(is_zeta_violin(clause) and not ACCESSORY_CLAUSE_RX.search(clause)
                   and not has_noise(clause) and not is_excluded_intent(clause)
                   for clause in clauses):
                instrument_clause = True
                break
        if not instrument_clause:
            return True
    if KOREAN_ACCESSORY_TITLE_RX.fullmatch(title) and not korean_instrument_sale(description):
        return True
    if KOREAN_INSTRUMENT_ABSENT_RX.search(title) or KOREAN_INSTRUMENT_ABSENT_RX.search(description):
        return True
    if INSTRUMENT_ABSENT_RX.search(description):
        return True
    if not description:
        return False
    if NOISE_RX.search(description) or any(j in description for j in NOISE_JP):
        return True
    if not title_conclusive and (NOISE_OBJECT_RX.search(description) or OTHER_INSTRUMENT_RX.search(description)):
        return True
    return False


def is_standalone_case(title: str, metadata: dict = None) -> bool:
    """Keep 'Zeta violin with case', reject 'Zeta violin case' / 'case for Zeta'."""
    case = CASE_RX.search(title)
    if not case:
        return False
    before, after = title[:case.start()], title[case.end():]
    # The instrument must be identified independently of the accessory phrase.
    for bundle in CASE_BUNDLE_RX.finditer(before):
        instrument = before[:bundle.start()].strip()
        if instrument and (is_zeta_violin(instrument, **(metadata or {})) or has_violin_word(instrument)):
            return False
    if before.strip() and is_zeta_violin(before, **(metadata or {})) and re.match(r"\s+(?:included|inclus|inclusa|incluso)\b", after, re.I):
        return False
    if case.group() == '케이스' and KOREAN_CASE_INCLUDED_RX.match(after):
        if is_zeta_violin(before, **(metadata or {})) or is_zeta_violin(after, **(metadata or {})):
            return False
    # Reverse-order explicit bundles are also real offers of an instrument.
    if re.match(r"\s+(?:with|including|plus|and)\b|\s*[+&]", after, re.I) and is_zeta_violin(after):
        return False
    return True


def mixed_lot_includes_violin(title: str) -> bool:
    """Explicit bundle with a violin; 'violin/viola' alone is ambiguous."""
    if not is_zeta_violin(title) or not has_violin_word(title):
        return False
    return bool(re.search(r"\bviolins?\s*\(\s*[1-9]\d*\s*\)|\b(?:lot|bundle|collection)\b", title, re.I))


def estate_violin_evidence(title: str, description: str) -> str:
    """A coherent inventory entry, not 'Zeta jacket, violin' across items.

    Estate listings sell many unrelated goods. Only an explicit Zeta instrument
    entry can narrow body noise; the full listing's intent remains checked.
    """
    if not re.search(r"\b(?:estate|garage|yard|moving|rummage)\s+sales?\b", title, re.I):
        return ""
    brand = r"(?:zeta|zetta|zetamusic|zeta\s+music(?:\s+systems)?|strados|jazz\s+fusion|jv[ -]?4[45]|sv[ -]?2[45]|cv[ -]?44|ev[ -]?(?:25|44))"
    qualifier = r"(?:electric(?:al)?|midi|jazz|fusion|strados|acoustic|pro|classic|modern|standard|legacy|vintage|[45]|four|five|strings?|jv44|jv45|sv24|sv25|cv44|ev25|ev44)"
    instrument = re.compile(r"\b" + brand + r"\s+(?:" + qualifier + r"[\s-]+){0,6}(?:violins?|fiddles?)\b|"
                            r"\b(?:violins?|fiddles?)\s+(?:(?:by|made\s+by)\s+)?" + brand + r"\b", re.I)
    for entry in re.split(r"[,;\n•]", description):
        entry = entry.strip()
        match = instrument.search(entry)
        if not match:
            continue
        accessory = re.compile(r"\b(?:manual|catalogue?|brochure|poster|sticker|bow|pickup)\b", re.I)
        prefix, suffix = entry[:match.start()], entry[match.end():]
        object_after = accessory.search(suffix)
        if accessory.search(prefix) or (object_after and not CASE_BUNDLE_RX.search(suffix[:object_after.start()])):
            continue
        if (is_zeta_violin(entry) and not has_noise(entry) and not is_excluded_intent(entry)):
            return entry
    return ""


def is_editorial_url(url: str) -> bool:
    parts = urlsplit(url)
    path = parts.path.lower()
    if (parts.hostname or '').lower().removeprefix('www.') == 'equipboard.com' and path.startswith('/items/'):
        return True  # Gear catalog/reviews with affiliate offers, not a seller's listing.
    return bool(re.search(r"/(?:blog|blogs|news|article|articles|magazine|magazines|reviews?|press|authors?)(?:/|$)", path))


def is_excluded_intent(title: str, description: str = "", title_conclusive: bool = None) -> bool:
    if (INTENT_RX.search(title) or INTENT_TITLE_RX.search(title) or KOREAN_BUY_INTENT_RX.search(title)
            or KOREAN_ACCESSORY_ONLY_SALE_RX.search(title)):
        return True
    if title_conclusive is None:
        title_conclusive = is_zeta_violin(title)
    body = description
    if title_conclusive:
        # 'case only has scratches' describes included gear, not a case-only
        # sale. Explicit 'case only' / 'violin not for sale' must still reject.
        body = re.sub(r"\b(?:case|bow|gig\s+bag|cover|bag)\s+only\s+(?:has|have|needs?|shows?|looks?)\b",
                      "included accessory condition", body, flags=re.I)
        body = re.sub(r"\b(?:stand|pedal|amplifier|amp|controller)\s+(?:is\s+)?not\s+for\s+sale\b",
                      "accessory unavailable", body, flags=re.I)
    if body and (INTENT_RX.search(body) or KOREAN_ACCESSORY_ONLY_SALE_RX.search(body)):
        return True
    if description and not title_conclusive and INTENT_TITLE_RX.search(description):
        return True
    return False


def is_other_brand(title: str) -> bool:
    return bool(OTHER_BRAND_RX.search(title))


def is_sold_or_ended(title: str) -> bool:
    return bool(SOLD_RX.search(title) or KOREAN_SOLD_TITLE_RX.search(title))


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
    return not re.match(r"^/(?:[a-z]{2}/)?(?:search|category|categories|help|about)(?:/|$)", path)


def classify(listing: dict) -> str:
    """Return "" if the listing should be alerted, otherwise a short reason
    (used for per-reason counters in main.py)."""
    from config import Config  # local import keeps filters importable in tests
    if listing.get("platform") in DISABLED_SCRAPERS or is_disabled_source(str(listing.get("url", ""))):
        return "disabled_source"
    if listing.get('apify_is_sold') is True:
        return 'sold'
    if (listing.get('apify_is_pending') is True or listing.get('apify_is_hidden') is True
            or listing.get('apify_is_live') is False):
        return 'availability_hold'
    title, desc = _text(listing)
    if listing.get("source") == "search" and is_editorial_url(str(listing.get("url", ""))):
        return "non_sale"
    metadata = _identity_metadata(listing)
    if sells_other_product(title, desc, metadata):
        return "noise"
    object_title = instrument_sale_title(title)
    title_conclusive = is_zeta_violin(title, **metadata)
    # Recording liner notes may mention Zeta instruments. The CD category
    # plus an artist-only title identifies the actual object being sold.
    media_path = re.search(r"/(?:cd-s-en-dvd-s|cds?|dvds?|vinyl|records)(?:/|-|$)",
                           urlsplit(str(listing.get("url", ""))).path, re.I)
    media_host = (urlsplit(str(listing.get("url", ""))).hostname or "").removeprefix("www.")
    if media_host in ("2dehands.be", "2ememain.be") and media_path and not title_conclusive:
        return "noise"
    if is_other_brand(object_title):
        return "other_brand"
    if is_new_stock(listing, Config.CONDITION, Config.EXCLUDED_SELLERS):
        return "new_stock"
    inventory_evidence = estate_violin_evidence(title, desc)
    body_for_noise = inventory_evidence or desc
    body_conclusive = title_conclusive or description_identifies_instrument(f"{title} {body_for_noise}")
    if (INSTRUMENT_ABSENT_RX.search(desc) or INSTRUMENT_ABSENT_RX.search(title)
            or NOISE_RX.search(title)
            or has_noise(object_title, body_for_noise, body_conclusive, metadata=metadata)):
        return "noise"
    if is_excluded_intent(title, desc, body_conclusive):
        return "intent"
    if is_sold_or_ended(title):
        return "sold"
    if (not title_conclusive and re.search(r"\b(?:estate|garage|yard|moving|rummage)\s+sales?\b", title, re.I)
            and not inventory_evidence):
        return "non_zeta"
    if not (title_conclusive or is_zeta_violin(title, body_for_noise, **metadata)):
        return "non_zeta"
    if not is_valid_listing_url(str(listing.get("url", ""))):
        return "url"
    return ""
