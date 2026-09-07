"""
Price history tracker — records listing prices in SQLite, calculates
averages, flags deals (priced well below average) and detects PRICE DROPS
on listings we have already alerted.
"""

import logging
import re
from datetime import datetime
from config import Config
from database import connect

log = logging.getLogger(__name__)

CURRENCY_TO_USD = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "CAD": 0.74,
    "AUD": 0.65,
    "CHF": 1.13,
    "SEK": 0.096,
    "NOK": 0.094,
    "DKK": 0.145,
    "PLN": 0.25,
    "JPY": 0.0067,
}


def parse_amount(text: str) -> "float | None":
    """First monetary amount in `text` as float, handling both decimal
    conventions: '2,749.00', '6.900', '2.000,00', '1 500', '¥130000'."""
    if not text:
        return None
    m = re.search(r"\d[\d\s., ]*\d|\d", text)
    if not m:
        return None
    raw = re.sub(r"[\s ]", "", m.group(0))
    if "." in raw and "," in raw:
        # the LAST separator is the decimal one
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", raw) else raw.replace(",", ".")
    elif "." in raw:
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", raw):
            raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


class PriceTracker:
    def __init__(self):
        self.conn = connect()
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
        # Latest known price per listing, used for price-drop re-alerts.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS last_prices (
                listing_id TEXT PRIMARY KEY,
                price_raw TEXT,
                price_usd REAL,
                updated_at TEXT
            )
        """)
        self.conn.commit()

    def _parse_price(self, price_str: str) -> tuple:
        """Extract numeric price and currency from price string.
        Returns (price_usd, currency) or (None, None) if unparseable."""
        if not price_str or price_str in ("N/A", "See post", "See listing"):
            return None, None

        price_str = price_str.strip()

        currency = "USD"
        upper = price_str.upper()
        for cur in CURRENCY_TO_USD:
            if re.search(r"\b" + cur + r"\b", upper):
                currency = cur
                break
        if "€" in price_str:
            currency = "EUR"
        elif "£" in price_str:
            currency = "GBP"
        elif "¥" in price_str or "￥" in price_str or "円" in price_str:
            currency = "JPY"
        elif "KR" in upper and currency == "USD":
            currency = "SEK"
        elif "ZŁ" in upper or "ZL" in upper.replace(".", ""):
            currency = "PLN"
        elif "LEI" in upper:
            currency = "RON"

        price_local = parse_amount(price_str)
        if price_local is None:
            return None, None
        try:
            from fx import to_usd  # live ECB rates, cached; static fallback inside
            return to_usd(price_local, currency), currency
        except Exception:
            rate = CURRENCY_TO_USD.get(currency, 1.0)
            return round(price_local * rate, 2), currency

    def record_listing(self, listing: dict) -> dict:
        """Record a NEW listing's price and return price context
        (price_usd, avg_price, deal_pct, is_deal, total_seen)."""
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

        try:
            now = datetime.utcnow().isoformat()
            self.conn.execute("""
                INSERT OR IGNORE INTO price_history
                (listing_id, platform, title, price_raw, price_usd, currency, url, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                listing.get("id", ""), listing.get("platform", ""), listing.get("title", ""),
                listing.get("price", ""), price_usd, currency, listing.get("url", ""), now,
            ))
            self.conn.execute("""
                INSERT OR REPLACE INTO last_prices (listing_id, price_raw, price_usd, updated_at)
                VALUES (?, ?, ?, ?)
            """, (listing.get("id", ""), listing.get("price", ""), price_usd, now))
            self.conn.commit()
        except Exception as e:
            log.warning(f"Price record error: {e}")

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
                if deal_pct >= 30:
                    context["is_deal"] = True
                    log.info(
                        f"🔥 DEAL DETECTED: {listing.get('title', '')[:50]} "
                        f"— ${price_usd} vs avg ${avg} ({deal_pct}% below)"
                    )
        except Exception as e:
            log.warning(f"Price avg calc error: {e}")

        return context

    def update_price(self, listing: dict) -> dict:
        """For an ALREADY SEEN listing: compare the current price with the last
        known one. Returns {} if unchanged/unknown, otherwise a dict with
        old_price, new_price, drop_pct when the price fell by at least
        Config.PRICE_DROP_PCT. Always stores the new price."""
        price_usd, _ = self._parse_price(listing.get("price", ""))
        if price_usd is None:
            return {}
        listing_id = listing.get("id", "")
        try:
            row = self.conn.execute(
                "SELECT price_raw, price_usd FROM last_prices WHERE listing_id = ?", (listing_id,)
            ).fetchone()
            now = datetime.utcnow().isoformat()
            self.conn.execute("""
                INSERT OR REPLACE INTO last_prices (listing_id, price_raw, price_usd, updated_at)
                VALUES (?, ?, ?, ?)
            """, (listing_id, listing.get("price", ""), price_usd, now))
            self.conn.commit()
            if not row or not row[1]:
                return {}
            old_usd = float(row[1])
            if old_usd <= 0 or price_usd >= old_usd:
                return {}
            drop_pct = round((1 - price_usd / old_usd) * 100, 1)
            if drop_pct < Config.PRICE_DROP_PCT:
                return {}
            log.info(f"📉 PRICE DROP {drop_pct}%: {listing.get('title', '')[:50]} {row[0]} → {listing.get('price')}")
            return {"old_price": row[0], "new_price": listing.get("price", ""), "drop_pct": drop_pct}
        except Exception as e:
            log.warning(f"Price update error: {e}")
            return {}

    def close(self):
        self.conn.close()
