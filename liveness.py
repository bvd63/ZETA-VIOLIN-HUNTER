"""
Liveness check for listings found through SEARCH ENGINES (Google / Brave).

Search engines index pages that stay online for years after the item was
sold ("This listing has ended" on Reverb/eBay, "This posting has expired" on
Craigslist, "annuncio non più disponibile" on Subito ...). Before alerting a
search-engine hit we open the page once and look for those markers.
Direct-API scrapers never need this — they only return live inventory.
"""

import logging
import re
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Phrases that only appear on dead listing pages (multi-language).
DEAD_MARKERS = [
    # EN
    "this listing has ended", "listing has ended", "this listing was ended", "listing ended",
    "this listing is no longer available", "is no longer available", "no longer available",
    "this item has been sold", "this item is sold", "item has sold", "has been sold",
    "this ad has expired", "ad has expired", "this ad is no longer", "ad has been removed",
    "this posting has expired", "posting has expired", "this posting has been deleted",
    "this posting has been flagged", "page not found", "this page doesn't exist",
    "we couldn't find that page", "auction has ended", "this auction has ended", "bidding has ended",
    "this product is no longer available", "sold out",
    # FR
    "cette annonce n'est plus disponible", "n'est plus disponible", "annonce désactivée",
    # DE
    "anzeige ist nicht mehr verfügbar", "nicht mehr verfügbar", "diese anzeige wurde gelöscht",
    "anzeige wurde deaktiviert",
    # IT
    "annuncio non è più disponibile", "non è più disponibile", "non più disponibile", "annuncio rimosso",
    # ES / PT
    "anuncio ya no está disponible", "ya no está disponible", "anúncio não está mais disponível",
    "já não está disponível",
    # NL
    "advertentie is niet meer beschikbaar", "niet meer beschikbaar", "deze advertentie is verlopen",
    # PL / CZ
    "ogłoszenie nieaktualne", "ogłoszenie zostało zakończone", "to ogłoszenie nie jest już dostępne",
    "inzerát již není k dispozici",
    # Nordics
    "annonsen er avsluttet", "annonsen är borttagen", "annoncen er slettet", "ilmoitus on poistettu",
    # JP
    "この商品は売り切れ", "売り切れました", "終了しました", "取引が完了", "出品が取り消され",
]
_DEAD_RX = re.compile("|".join(re.escape(m) for m in DEAD_MARKERS), re.IGNORECASE)

# Live-page markers that override a false "dead" hit caused by boilerplate.
_LIVE_HINTS_RX = re.compile(
    r"add to cart|buy it now|place bid|make an offer|add to watchlist|contact seller|"
    r"in den warenkorb|ajouter au panier|aggiungi al carrello|añadir al carrito",
    re.IGNORECASE,
)

REVERB_ITEM_RX = re.compile(r"reverb\.com/(?:[a-z]{2}/)?item/(\d+)", re.IGNORECASE)


def looks_dead(status_code: int, requested_url: str, final_url: str, text: str) -> str:
    """Return a short reason if the page is a dead listing, else ""."""
    if status_code in (404, 410):
        return f"http {status_code}"
    if status_code >= 400:
        return ""  # blocked / rate limited — can't tell, keep the listing
    try:
        req_path = urlsplit(requested_url).path.rstrip("/")
        fin_path = urlsplit(final_url).path.rstrip("/")
        # A listing URL that redirects to the homepage or a search/category
        # page means the item is gone.
        if len(req_path) > 8 and (fin_path in ("", "/") or any(
                seg in fin_path for seg in ("/search", "/marketplace", "/category", "/categories"))):
            return "redirected away"
    except Exception:
        pass
    snippet = text[:200000]
    m = _DEAD_RX.search(snippet)
    if m:
        if _LIVE_HINTS_RX.search(snippet[:60000]) and m.group(0).lower() in ("sold out", "no longer available"):
            return ""  # generic phrase on an otherwise live shop page
        return f"marker '{m.group(0)[:40]}'"
    return ""


async def is_live(url: str, client: httpx.AsyncClient) -> tuple:
    """(alive: bool, reason: str). Network errors count as alive (we prefer a
    stale alert over a missed instrument)."""
    m = REVERB_ITEM_RX.search(url)
    if m:
        try:
            r = await client.get(
                f"https://api.reverb.com/api/listings/{m.group(1)}",
                headers={"Accept": "application/hal+json", "Accept-Version": "3.0", "User-Agent": UA},
            )
            if r.status_code == 404:
                return False, "reverb: not found"
            if r.status_code == 200:
                state = ((r.json().get("state") or {}).get("slug") or "").lower()
                return (state == "live"), f"reverb state={state or '?'}"
        except Exception as e:
            log.debug(f"reverb liveness error {url}: {e}")
    try:
        r = await client.get(url, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.8",
                                           "Accept-Language": "en-US,en;q=0.9"})
        reason = looks_dead(r.status_code, url, str(r.url), r.text)
        return (not reason), reason or f"http {r.status_code}"
    except Exception as e:
        log.debug(f"liveness fetch error {url}: {e}")
        return True, "unreachable (kept)"
