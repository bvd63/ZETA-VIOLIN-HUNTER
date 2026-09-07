"""
Configuration — all values loaded from environment variables.
Copy env.example to .env and fill in your keys.
"""

import os
from datetime import datetime


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


class Config:
    # --- Telegram ---
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
    # Send the "no new listings" message after every cycle (true) or never (false)
    SEND_NO_CHANGES = _bool("SEND_NO_CHANGES", "true")
    # Attach the listing photo (sendPhoto) when the source provides one
    SEND_PHOTOS = _bool("SEND_PHOTOS", "true")

    # --- HTTP server ---
    PORT = int(os.getenv("PORT", "8080"))
    # If set, POST /search requires header X-Search-Token or ?token=
    SEARCH_TOKEN = os.getenv("SEARCH_TOKEN", "")

    # --- Storage ---
    # Point at a Railway volume (e.g. /data/zeta_listings.db) to survive deploys.
    DB_PATH = os.getenv("DB_PATH", "zeta_listings.db")

    # --- eBay Browse API (OAuth2) ---
    EBAY_APP_ID = os.getenv("EBAY_APP_ID", "")
    EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID", os.getenv("EBAY_APP_ID", ""))
    EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "")

    # --- Schedule ---
    # Hours (comma separated) in SEARCH_TIMEZONE. Default: once a day at 12:00
    # Romania time (DST handled by the timezone). Legacy SEARCH_HOUR (UTC) is ignored.
    SEARCH_HOURS = os.getenv("SEARCH_HOURS", "12")
    SEARCH_TIMEZONE = os.getenv("SEARCH_TIMEZONE", "Europe/Bucharest")
    RUNS_PER_DAY = max(1, len([h for h in SEARCH_HOURS.split(",") if h.strip().isdigit()]))

    # --- Google Custom Search (retires 2027-01-01) ---
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
    GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "")
    # Free quota is 100/day (resets at midnight Pacific). Default budget splits
    # 96 queries across the day's runs: 96 with one run, 48 with two.
    GOOGLE_QUERIES_PER_RUN = int(os.getenv("GOOGLE_QUERIES_PER_RUN", str(max(1, 96 // RUNS_PER_DAY))))
    # Do not run Google again if the previous run was less than N hours ago
    # (protects the quota on container restarts). 20h with one run/day, 10h otherwise.
    GOOGLE_GUARD_HOURS = int(os.getenv("GOOGLE_GUARD_HOURS", "20" if RUNS_PER_DAY == 1 else "10"))

    # --- Brave Search API (Google replacement; $5 monthly credit ≈ 1000 queries) ---
    BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
    # 32 queries/day × 30 days ≈ 960/month, split across the day's runs
    BRAVE_QUERIES_PER_RUN = int(os.getenv("BRAVE_QUERIES_PER_RUN", str(max(1, 32 // RUNS_PER_DAY))))
    BRAVE_GUARD_HOURS = int(os.getenv("BRAVE_GUARD_HOURS", "20" if RUNS_PER_DAY == 1 else "10"))

    # --- US egress proxy (http://user:pass@host:port) for sites that block
    # European datacenter IPs: Guitar Center, Facebook Marketplace, ... ---
    US_PROXY_URL = os.getenv("US_PROXY_URL", "")
    # Filled at startup by main.detect_egress(): ISO country of the container's
    # public IP (e.g. "US", "NL"). Lets US-only scrapers run without a proxy
    # when Railway already places the container in the US.
    EGRESS_COUNTRY = os.getenv("EGRESS_COUNTRY", "")

    @classmethod
    def has_us_egress(cls) -> bool:
        return bool(cls.US_PROXY_URL) or cls.EGRESS_COUNTRY.upper() == "US"

    # --- Filters ---
    MIN_PRICE = float(os.getenv("MIN_PRICE", "0"))
    MAX_PRICE = float(os.getenv("MAX_PRICE", "99999"))
    # "used" (default): drop listings the platform marks as new / brand new /
    # open box, listings from new-stock dealers and titles with shop language.
    # "all": no condition filtering. Any model/year is fine as long as it is second-hand.
    CONDITION = os.getenv("CONDITION", "used").strip().lower()
    # Sellers / shops / domains that sell NEW Zeta stock — never alert their listings
    # (comma separated, matched case-insensitively against seller name, shop slug, URL host).
    EXCLUDED_SELLERS = [
        s.strip().lower() for s in os.getenv(
            "EXCLUDED_SELLERS",
            "electricviolinshop,electric violin shop,zetaviolins,zeta violins,zetamusic.com",
        ).split(",") if s.strip()
    ]

    # Excluded countries/regions (owner is in Romania, not buying local)
    EXCLUDED_LOCATIONS = ["Romania"]
    EXCLUDED_COUNTRY_CODES = ["RO"]

    # --- Year filter ---
    # MIN_YEAR = manufacture year lower bound (Zeta started ~1987).
    # MAX_YEAR = upper bound for TEXT mentions. Defaults to NEXT year so that
    # "bought in 2027" is never dropped once the calendar turns.
    MIN_YEAR = int(os.getenv("MIN_YEAR", "1980"))
    MAX_YEAR = int(os.getenv("MAX_YEAR", str(datetime.utcnow().year + 1)))

    # --- Alerts ---
    # Re-alert a known listing when its price drops by at least this percent
    PRICE_DROP_PCT = float(os.getenv("PRICE_DROP_PCT", "15"))
    # Warn on Telegram when a configured scraper fetched 0 items for N cycles in a row
    WATCHDOG_ZERO_STREAK = int(os.getenv("WATCHDOG_ZERO_STREAK", "3"))

    # --- Runtime hardening ---
    SCRAPER_TIMEOUT_SEC = int(os.getenv("SCRAPER_TIMEOUT_SEC", "900"))
    SCRAPER_RETRIES = int(os.getenv("SCRAPER_RETRIES", "1"))
    SCRAPER_CONCURRENCY = int(os.getenv("SCRAPER_CONCURRENCY", "4"))

    # --- Reddit API (praw) — optional, scraper skips if unset ---
    REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
    REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
    REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "ZetaViolinHunter/1.0")

    # --- Reverb API token — OPTIONAL. The API works without it when a browser
    # User-Agent is sent; a token only raises rate limits. ---
    REVERB_API_TOKEN = os.getenv("REVERB_API_TOKEN", "")

    # --- OpenAI API (kept for reference, AI re-verification removed) ---
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

    # --- Craigslist coverage tuning ---
    # Internal request concurrency against sapi.craigslist.org (4-16)
    CRAIGSLIST_CONCURRENCY = int(os.getenv("CRAIGSLIST_CONCURRENCY", "10"))
    # 0 = all areas of the selected countries (413 US + ~55 CA)
    CRAIGSLIST_MAX_US_CITIES = int(os.getenv("CRAIGSLIST_MAX_US_CITIES", "0"))
    # Craigslist country codes to crawl (from reference.craigslist.org/Areas)
    CRAIGSLIST_COUNTRIES = os.getenv("CRAIGSLIST_COUNTRIES", "US,CA")
