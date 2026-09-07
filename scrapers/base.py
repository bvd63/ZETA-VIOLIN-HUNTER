"""
Base scraper class — all platform scrapers inherit from this.
"""

import re
import hashlib
import httpx
from config import Config
from filters import is_sold_or_ended

# Realistic browser UA. Several targets (Reverb API, Craigslist, Subito) answer
# 403 to bot-looking User-Agents, so every scraper uses this unless a site
# explicitly welcomes self-identification.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

BROWSER_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class BaseScraper:
    name = "Base"
    # Number of items fetched from the source BEFORE local Zeta filtering.
    # Set by each scraper during search(); main.py feeds it to the watchdog so
    # "the site answered but nothing matched" is distinguishable from
    # "the site returned nothing / blocked us". Reset per instance (instances are
    # rebuilt every cycle).
    fetched: int = 0

    def is_configured(self) -> bool:
        """False when required credentials/config are missing and search()
        will skip. The watchdog ignores unconfigured scrapers."""
        return True

    def make_client(self, us_proxy: bool = False, **kwargs) -> httpx.AsyncClient:
        """httpx client with sane defaults. us_proxy=True routes through
        Config.US_PROXY_URL when set (for sites that block EU datacenter IPs)."""
        kwargs.setdefault("timeout", 20)
        kwargs.setdefault("follow_redirects", True)
        if us_proxy and Config.US_PROXY_URL:
            kwargs["proxy"] = Config.US_PROXY_URL
        return httpx.AsyncClient(**kwargs)

    def _is_excluded(self, text: str) -> bool:
        """True if the TITLE says the listing is sold/ended/expired.
        Word-boundary match; 'sold as is' does not count (see filters.py)."""
        return is_sold_or_ended(text)

    def _is_excluded_location(self, location: str) -> bool:
        """Check if location should be excluded. Uses word-boundary
        matching to avoid 'ro' matching 'Rome' or 'Toronto'."""
        location_lower = (location or "").lower()
        for loc in Config.EXCLUDED_LOCATIONS:
            if re.search(r"\b" + re.escape(loc.lower()) + r"\b", location_lower):
                return True
        for code in getattr(Config, "EXCLUDED_COUNTRY_CODES", []):
            pattern = r"(?:^|[\s,])" + re.escape(code) + r"(?:$|[\s,])"
            if re.search(pattern, location or "", re.IGNORECASE):
                return True
        return False

    def _year_in_range(self, text: str) -> bool:
        """
        Returns True if:
        - No year is mentioned (include by default)
        - A year between MIN_YEAR and MAX_YEAR is mentioned
        Returns False only if EVERY year mentioned is outside the range.
        MAX_YEAR defaults to next year (config.py), so listing/purchase years
        never cause drops.
        """
        years_found = re.findall(r"\b(19[5-9]\d|20[0-9]\d)\b", text or "")
        if not years_found:
            return True
        for y in years_found:
            if Config.MIN_YEAR <= int(y) <= Config.MAX_YEAR:
                return True
        return False

    def _price_in_range(self, price_str: str) -> bool:
        """Extract numeric price (any locale format) and check range."""
        from price_tracker import parse_amount
        price = parse_amount(price_str or "")
        if price is None:
            return True  # Unknown price — include it
        return Config.MIN_PRICE <= price <= Config.MAX_PRICE

    def _make_id(self, platform: str, url: str) -> str:
        """Generate a stable unique ID for a listing."""
        return hashlib.md5(f"{platform}:{url}".encode()).hexdigest()

    def _relevance_score(self, title: str, description: str = "") -> int:
        """Score 1-10 how likely this is a genuine Zeta violin listing.
        Informational only — main.py does not filter on it."""
        text = (title + " " + description).lower()
        score = 1

        high = ["strados", "zeta sv24", "zeta sv25", "zeta jv44", "zeta jv45",
                "zeta ev25", "zeta ev44", "zeta cv44", "zeta sv43", "jean-luc ponty",
                "zeta jlp", "zeta acoustic-pro", "zeta acoustic pro", "zeta jazz fusion",
                "zeta jazz modern"]
        for kw in high:
            if kw in text:
                score += 3

        medium = ["zeta violin", "zeta electric", "zeta music", "zetta violin", "zeta violino",
                  "zeta geige", "zeta violon", "zeta viool"]
        for kw in medium:
            if kw in text:
                score += 2

        mild = ["electric violin", "midi violin", "5-string violin", "5 string", "violino elettrico",
                "violon électrique", "elektrische geige", "elektrische viool"]
        for kw in mild:
            if kw in text:
                score += 1

        return min(score, 10)

    async def search(self) -> list:
        raise NotImplementedError
