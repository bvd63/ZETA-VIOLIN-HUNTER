"""
Price history tracker — records listing prices in SQLite, calculates
averages, and flags deals (listings priced significantly below average).
"""

import sqlite3
import logging
import re
from datetime import datetime

log = logging.getLogger(__name__)

DB_PATH = "zeta_listings.db"

CURRENCY_TO_USD = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "CAD": 0.74,
    "AUD": 0.65,
    "CHF": 1.13,
    "SEK": 0.096,
    "NOK": 0.094,
    "PLN": 0.25,
    "JPY": 0.0067,
}


class PriceTracker:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH)
        self._init_tables()

    def _init_tables(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_id TEXT,
                platform TEXT,
                title TEXT,
                price_raw TEXT,
                price_usd REAL,
                currency TEXT,
                url TEXT,
                recorded_at TEXT,
                UNIQUE(listing_id)
            )
        """)
        self.conn.commit()

    def _parse_price(self, price_str: str) -> tuple:
        """Extract numeric price and currency from price string.
        Returns (price_usd, currency) or (None, None) if unparseable."""
        if not price_str or price_str in ("N/A", "See post", "See listing"):
            return None, None

        price_str = price_str.strip()

        # Detect currency
        currency = "USD"
        for cur in CURRENCY_TO_USD:
            if cur in price_str.upper():
                currency = cur
                break
        if "€" in price_str:
            currency = "EUR"
        elif "£" in price_str:
            currency = "GBP"
        elif "¥" in price_str or "￥" in price_str:
            currency = "JPY"
        elif "CHF" in price_str.upper():
            currency = "CHF"

        # Extract numeric value
        nums = re.findall(r"[\d]+(?:[.,]\d+)?", price_str.replace(",", ""))
        if not nums:
            return None, None

        try:
            price_local = float(nums[0])
            rate = CURRENCY_TO_USD.get(currency, 1.0)
            price_usd = round(price_local * rate, 2)
            return price_usd, currency
        except (ValueError, IndexError):
            return None, None

    def record_listing(self, listing: dict) -> dict:
        """Record a listing's price and return price context.
        Returns dict with: price_usd, avg_price, deal_pct, is_deal."""

        price_usd, currency = self._parse_price(listing.get("price", ""))

        context = {
            "price_usd": price_usd,
            "avg_price": None,
            "deal_pct": None,
            "is_deal": False,
            "total_seen": 0,
        }

        if price_usd is None:
            return context

        # Record in history
        try:
            self.conn.execute("""
                INSERT OR IGNORE INTO price_history
                (listing_id, platform, title, price_raw, price_usd,
                 currency, url, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                listing.get("id", ""),
                listing.get("platform", ""),
                listing.get("title", ""),
                listing.get("price", ""),
                price_usd,
                currency,
                listing.get("url", ""),
                datetime.utcnow().isoformat(),
            ))
            self.conn.commit()
        except Exception as e:
            log.warning(f"Price record error: {e}")

        # Calculate average from history (all Zeta violins seen)
        try:
            cur = self.conn.execute("""
                SELECT AVG(price_usd), COUNT(*) FROM price_history
                WHERE price_usd > 50 AND price_usd < 50000
            """)
            row = cur.fetchone()
            if row and row[0] and row[1] >= 3:
                avg = round(row[0], 2)
                context["avg_price"] = avg
                context["total_seen"] = row[1]

                deal_pct = round((1 - price_usd / avg) * 100, 1)
                context["deal_pct"] = deal_pct

                # Flag as deal if 30%+ below average
                if deal_pct >= 30:
                    context["is_deal"] = True
                    log.info(
                        f"🔥 DEAL DETECTED: {listing.get('title', '')[:50]} "
                        f"— ${price_usd} vs avg ${avg} ({deal_pct}% below)"
                    )
        except Exception as e:
            log.warning(f"Price avg calc error: {e}")

        return context

    def close(self):
        self.conn.close()
