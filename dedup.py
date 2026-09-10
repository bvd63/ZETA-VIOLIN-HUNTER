"""
Cross-platform duplicate detection: the same instrument listed on Reverb and
eBay (or re-listed under a new id) is ANNOTATED (never suppressed).

Two listings are flagged when their discriminating title tokens overlap
strongly (Jaccard ≥ 0.75 after dropping filler words, at least 3 tokens on
each side) AND both prices are known and within 8 %. Compared against
alerts delivered in the last 60 days.
"""

import re

FILLER = {
    "the", "a", "an", "and", "or", "with", "w", "for", "of", "in", "by", "electric", "violin",
    "violins", "fiddle", "string", "strings", "str", "zeta", "used", "rare", "vintage",
    "very", "good", "excellent", "condition", "mint", "nice", "great", "case", "bow",
    "black", "white", "red", "blue", "gloss", "satin", "finish", "sale",
}
MIN_TOKENS = 3
PRICE_TOLERANCE = 0.08


def tokens(title: str) -> set:
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    # single digits ("5" = 5-string) are discriminating, single letters are not
    return {w for w in words if w not in FILLER and (len(w) > 1 or w.isdigit())}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_duplicate(title: str, price_usd, recent_alerts: list) -> dict:
    """recent_alerts: iterable of {title, price_usd, platform, url}. Returns the
    matching earlier alert or {}. Unknown prices never match."""
    t = tokens(title)
    if len(t) < MIN_TOKENS or not price_usd:
        return {}
    for prev in recent_alerts:
        p_prev = prev.get("price_usd")
        if not p_prev:
            continue
        if abs(price_usd - p_prev) / max(price_usd, p_prev) > PRICE_TOLERANCE:
            continue
        pt = tokens(prev.get("title", ""))
        if len(pt) < MIN_TOKENS or jaccard(t, pt) < 0.75:
            continue
        return prev
    return {}
