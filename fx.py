"""
Live exchange rates (ECB via frankfurter.dev, free, no key) cached in SQLite
for 24 h and in memory for the process. Falls back to a static table.
"""

import json
import logging
import time
from datetime import datetime, timedelta

import httpx
from database import connect

log = logging.getLogger(__name__)

FX_URL = "https://api.frankfurter.dev/v1/latest"  # old host api.frankfurter.app 301s here
CURRENCIES = ["EUR", "GBP", "CAD", "AUD", "CHF", "SEK", "NOK", "DKK", "PLN", "JPY", "RON", "CZK", "HUF"]

# Units of currency per 1 USD (static fallback, mid-2026 ballpark)
STATIC_PER_USD = {
    "USD": 1.0, "EUR": 0.92, "GBP": 0.79, "CAD": 1.36, "AUD": 1.53, "CHF": 0.88,
    "SEK": 10.4, "NOK": 10.6, "DKK": 6.9, "PLN": 4.0, "JPY": 150.0, "RON": 4.6,
    "CZK": 23.0, "HUF": 360.0,
}

_cache: dict = {"rates": None, "loaded_at": 0.0}
_CACHE_TTL = 600  # re-read the SQLite copy every 10 min at most


def _kv():
    conn = connect()
    conn.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    return conn


def load_rates() -> dict:
    """Rates (units per USD): in-memory → SQLite → static. Never raises."""
    now = time.time()
    if _cache["rates"] and now - _cache["loaded_at"] < _CACHE_TTL:
        return _cache["rates"]
    rates = dict(STATIC_PER_USD)
    conn = None
    try:
        conn = _kv()
        row = conn.execute("SELECT value FROM kv WHERE key = 'fx_per_usd'").fetchone()
        if row:
            data = json.loads(row[0])
            if isinstance(data, dict) and data.get("EUR"):
                rates.update(data)
    except Exception as e:
        log.debug(f"fx cache read failed: {e}")
    finally:
        if conn:
            conn.close()
    _cache["rates"], _cache["loaded_at"] = rates, now
    return rates


async def refresh_rates() -> None:
    """Fetch fresh rates once per day. Silent on failure."""
    conn = None
    try:
        conn = _kv()
        row = conn.execute("SELECT updated_at FROM kv WHERE key = 'fx_per_usd'").fetchone()
        if row and datetime.utcnow() - datetime.fromisoformat(row[0]) < timedelta(hours=24):
            return
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(FX_URL, params={"base": "USD", "symbols": ",".join(CURRENCIES)})
            if resp.status_code != 200:
                log.warning(f"fx: frankfurter HTTP {resp.status_code}")
                return
            rates = resp.json().get("rates") or {}
        if rates.get("EUR"):
            rates["USD"] = 1.0
            conn.execute("INSERT OR REPLACE INTO kv VALUES ('fx_per_usd', ?, ?)",
                         (json.dumps(rates), datetime.utcnow().isoformat()))
            conn.commit()
            _cache["rates"], _cache["loaded_at"] = {**STATIC_PER_USD, **rates}, time.time()
            log.info(f"fx: rates refreshed (1 USD = {rates['EUR']:.3f} EUR)")
    except Exception as e:
        log.warning(f"fx: refresh failed: {e}")
    finally:
        if conn:
            conn.close()


def to_usd(amount: float, currency: str) -> float:
    per_usd = load_rates().get((currency or "USD").upper(), None)
    if not per_usd:
        return amount
    return round(amount / per_usd, 2)


def usd_to(amount_usd: float, currency: str) -> float:
    per_usd = load_rates().get(currency.upper(), None)
    if not per_usd:
        return amount_usd
    return round(amount_usd * per_usd, 2)
