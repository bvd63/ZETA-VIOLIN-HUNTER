"""
Cross-platform duplicate detection: the same instrument listed on Reverb and
eBay (or re-listed under a new id) should alert once.

Two listings are duplicates when their title token sets overlap strongly
(Jaccard ≥ 0.75 after dropping filler words) and their USD prices are within
12 %. Compared against alerts from the last 60 days.
"""

import re

FILLER = {
    "the", "a", "an", "and", "or", "with", "w", "for", "of", "in", "by", "electric", "violin",
    "violins", "fiddle", "string", "strings", "str", "zeta", "used", "rare", "vintage",
    "very", "good", "excellent", "condition", "mint", "nice", "great", "case", "bow",
    "black", "white", "red", "blue", "gloss", "satin", "finish", "sale",
}


def tokens(title: str) -> set:
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return {w for w in words if w not in FILLER and len(w) > 1}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_duplicate(title: str, price_usd, recent_alerts: list) -> dict:
    """recent_alerts: iterable of {title, price_usd, platform, url}. Returns the
    matching earlier alert or {}."""
    t = tokens(title)
    if len(t) < 2:
        return {}
    for prev in recent_alerts:
        sim = jaccard(t, tokens(prev.get("title", "")))
        if sim < 0.75:
            continue
        p_prev = prev.get("price_usd")
        if price_usd and p_prev:
            if abs(price_usd - p_prev) / max(price_usd, p_prev) > 0.12:
                continue
        return prev
    return {}
