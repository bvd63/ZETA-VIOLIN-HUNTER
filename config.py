"""
Configuration — all values loaded from environment variables.
Copy .env.example to .env and fill in your keys.
"""

import os


class Config:
    # --- Telegram ---
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    # --- eBay ---
    EBAY_APP_ID = os.getenv("EBAY_APP_ID", "")

    # --- eBay Browse API (OAuth2) ---
    EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID", os.getenv("EBAY_APP_ID", ""))
    EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "")

    # --- Google Custom Search ---
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
    GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "")

    # --- Schedule ---
    SEARCH_HOUR = int(os.getenv("SEARCH_HOUR", "9"))  # 9:00 UTC = 12:00 Romania

    # --- Filters ---
    MIN_PRICE = float(os.getenv("MIN_PRICE", "0"))
    MAX_PRICE = float(os.getenv("MAX_PRICE", "99999"))
    CONDITION = os.getenv("CONDITION", "all")  # "new", "used", "all"

    # --- All Zeta keywords ---
    KEYWORDS = [
        # Model names
        "Zeta violin", "Zeta electric violin", "Zeta Strados", "Zeta Jazz Fusion",
        "Zeta JLP", "Zeta SV24", "Zeta SV25", "Zeta JV44", "Zeta JV45",
        "Zeta EV25", "Zeta EV44", "Zeta CV44", "Zeta SV43", "Zeta Strados Legacy",
        "Zeta Jean-Luc Ponty", "Zeta Acoustic-Pro", "Zeta Jazz Classic", "Zeta Jazz Modern",
        "Zeta MIDI violin", "Zeta Music violin", "ZetaMusic violin",
        # Misspellings
        "Zetta violin", "Zeta violine", "Zeta violon", "Zeta violino", "Zeta viool",
        "Zetta electric violin", "Zeta violijn",
        # German
        "Zeta Geige", "elektrische Geige Zeta",
        # French
        "violon électrique Zeta",
        # Italian
        "violino elettrico Zeta",
        # Spanish
        "violín eléctrico Zeta",
        # Polish
        "Zeta skrzypce",
        # Deep search
        "Strados violin", "Jazz Fusion violin",
    ]

    # Keywords to exclude (sold/ended listings)
    EXCLUDE_KEYWORDS = ["SOLD", "ENDED", "EXPIRED", "OUT OF STOCK", "sold out"]

    # Excluded countries/regions
    EXCLUDED_LOCATIONS = ["Romania"]
    EXCLUDED_COUNTRY_CODES = ["RO"]

    # --- Year filter ---
    MIN_YEAR = int(os.getenv("MIN_YEAR", "1980"))
    MAX_YEAR = int(os.getenv("MAX_YEAR", "2014"))

    # --- Runtime hardening ---
    SCRAPER_TIMEOUT_SEC = int(os.getenv("SCRAPER_TIMEOUT_SEC", "900"))
    SCRAPER_RETRIES = int(os.getenv("SCRAPER_RETRIES", "1"))
    SCRAPER_CONCURRENCY = int(os.getenv("SCRAPER_CONCURRENCY", "4"))

    # --- Reddit API (praw) ---
    REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
    REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
    REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "ZetaViolinHunter/1.0")

    # --- Craigslist coverage tuning ---
    CRAIGSLIST_CONCURRENCY = int(os.getenv("CRAIGSLIST_CONCURRENCY", "24"))
    # 0 means "use all discovered US Craigslist cities"
    CRAIGSLIST_MAX_US_CITIES = int(os.getenv("CRAIGSLIST_MAX_US_CITIES", "0"))
