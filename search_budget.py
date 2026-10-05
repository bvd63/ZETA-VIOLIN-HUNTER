"""Reserve search requests before HTTP, including failed requests and restarts."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from database import connect

log = logging.getLogger(__name__)


def reserve_request(engine: str, limit: int) -> bool:
    """Fail closed on ledger errors. UTC month for Brave, Pacific day for Google."""
    if limit <= 0:
        return False
    now = datetime.now(ZoneInfo("America/Los_Angeles") if engine == "google" else ZoneInfo("UTC"))
    period = now.strftime("%Y-%m-%d" if engine == "google" else "%Y-%m")
    key = f"{engine}_quota:{period}"
    conn = None
    try:
        conn = connect()
        conn.execute("CREATE TABLE IF NOT EXISTS scraper_runs (scraper TEXT PRIMARY KEY, last_run TEXT)")
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT last_run FROM scraper_runs WHERE scraper = ?", (key,)).fetchone()
        used = int(row[0]) if row else 0
        day_key = f"brave_daily:{now:%Y-%m-%d}"
        daily_used = 0
        if engine == "brave":
            daily_row = conn.execute("SELECT last_run FROM scraper_runs WHERE scraper = ?", (day_key,)).fetchone()
            if daily_row:
                daily_used = int(daily_row[0])
            else:
                # Upgrade old versions conservatively: each completed, non-skipped
                # Brave cycle may have spent the previous 32-query daily budget.
                has_stats = conn.execute("SELECT 1 FROM sqlite_master WHERE name='scraper_stats'").fetchone()
                if has_stats:
                    old_runs = conn.execute("SELECT COUNT(*) FROM scraper_stats WHERE scraper='Brave Search' AND raw_count >= 0 AND run_at >= ?",
                                            (f"{now:%Y-%m-%d}T00:00:00",)).fetchone()[0]
                    daily_used = min(32, old_runs * 32)
            if daily_used >= 32:
                conn.rollback()
                return False
        if used >= limit:
            conn.rollback()
            return False
        conn.execute("INSERT OR REPLACE INTO scraper_runs VALUES (?, ?)", (key, str(used + 1)))
        if engine == "brave":
            conn.execute("INSERT OR REPLACE INTO scraper_runs VALUES (?, ?)", (day_key, str(daily_used + 1)))
        conn.commit()
        return True
    except Exception as exc:
        log.warning("%s budget unavailable — no request sent (%s)", engine, type(exc).__name__)
        return False
    finally:
        if conn:
            conn.close()
