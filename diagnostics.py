"""
Startup reachability diagnostic. The container's location (Railway: Singapore)
decides which marketplaces answer. Logs one line per candidate site so the
owner can see from the logs which sources could be added or need a proxy.
"""

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# (label, url, text that marks a block/challenge page)
PROBES = [
    ("Yahoo Auctions JP (web)", "https://auctions.yahoo.co.jp/search/search?p=zeta%20violin", "EEA"),
    ("Buyee (Yahoo JP proxy)", "https://buyee.jp/item/search/query/zeta%20violin", "Forbidden"),
    ("ZenMarket", "https://zenmarket.jp/en/yahoo.aspx?q=zeta+violin", "Just a moment"),
    ("OfferUp (US)", "https://offerup.com/search?q=zeta%20violin", "Geolocation"),
    ("Mercari US", "https://www.mercari.com/search/?keyword=zeta%20violin", "Just a moment"),
    ("Etsy", "https://www.etsy.com/search?q=zeta%20electric%20violin", ""),
    ("Guitar Center (US)", "https://www.guitarcenter.com/search?q=zeta%20violin&t=used", ""),
    ("Facebook Marketplace", "https://www.facebook.com/marketplace/search/?query=zeta%20violin", "login"),
    ("Catawiki", "https://www.catawiki.com/en/s?q=zeta%20violin", ""),
    ("Proxibid", "https://www.proxibid.com/Search?q=zeta+violin", ""),
    ("LiveAuctioneers", "https://www.liveauctioneers.com/search/?keyword=zeta%20violin", ""),
    ("Oodle (US classifieds)", "https://forsale.oodle.com/musical-instruments/?q=zeta+violin", "Just a moment"),
    ("Allegro (PL)", "https://allegro.pl/listing?string=zeta%20violin", ""),
    ("Ricardo (CH)", "https://www.ricardo.ch/de/s/zeta%20violin", "Captcha"),
    ("Wallapop API (ES)", "https://api.wallapop.com/api/v3/general/search?keywords=zeta%20violin&latitude=40.4&longitude=-3.7", ""),
]


async def _probe(client: httpx.AsyncClient, label: str, url: str, block_marker: str) -> str:
    try:
        r = await client.get(url, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.8",
                                           "Accept-Language": "en-US,en;q=0.9"})
        blocked = r.status_code in (401, 403, 429, 503) or (block_marker and block_marker.lower() in r.text[:20000].lower())
        # A 200/202 with a tiny body is a JS challenge or an empty shell, not content.
        shell = r.status_code < 400 and len(r.text) < 5000
        verdict = "BLOCKED" if blocked else ("JS-SHELL" if shell else ("ok" if r.status_code < 400 else f"http {r.status_code}"))
        return f"{verdict:<8} {label} (HTTP {r.status_code}, {len(r.text) // 1024} KB)"
    except Exception as e:
        return f"{'TIMEOUT' if 'timeout' in type(e).__name__.lower() else 'ERROR':<8} {label} ({type(e).__name__})"


async def run_reachability() -> None:
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            lines = await asyncio.gather(*[_probe(client, *p) for p in PROBES])
        log.info("🔎 Reachability from this container (sites without direct scrapers):")
        for line in lines:
            log.info(f"   {line}")
        log.info("   → 'ok' = real content (candidate for a direct scraper); JS-SHELL = needs a browser; "
                 "BLOCKED = stays on Google/Brave or needs US_PROXY_URL")
    except Exception as e:
        log.warning(f"reachability diagnostic failed: {e}")
