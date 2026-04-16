"""
Status tracker — records per-scraper stats in SQLite for the
/status dashboard endpoint.
"""

import sqlite3
import logging
from datetime import datetime

log = logging.getLogger(__name__)

DB_PATH = "zeta_listings.db"


class StatusTracker:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH)
        self._init_tables()
        self._cycle_stats = {}
        self._cycle_start = None

    def _init_tables(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS scraper_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scraper TEXT,
                raw_count INTEGER,
                filtered_count INTEGER,
                new_count INTEGER,
                ai_rejected INTEGER DEFAULT 0,
                sent_count INTEGER DEFAULT 0,
                error TEXT,
                duration_sec REAL,
                run_at TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS cycle_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                total_scrapers INTEGER,
                total_raw INTEGER,
                total_new INTEGER,
                total_sent INTEGER,
                total_ai_rejected INTEGER DEFAULT 0,
                duration_sec REAL,
                run_at TEXT
            )
        """)
        self.conn.commit()

    def start_cycle(self):
        self._cycle_start = datetime.utcnow()
        self._cycle_stats = {}

    def record_scraper(self, name: str, raw: int, new: int,
                       error: str = "", duration: float = 0):
        self._cycle_stats[name] = {
            "raw": raw, "new": new, "error": error,
            "duration": duration,
        }
        try:
            self.conn.execute("""
                INSERT INTO scraper_stats
                (scraper, raw_count, new_count, error, duration_sec, run_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (name, raw, new, error, duration,
                  datetime.utcnow().isoformat()))
            self.conn.commit()
        except Exception as e:
            log.warning(f"Status record error: {e}")

    def record_ai_rejection(self, platform: str, count: int):
        try:
            self.conn.execute("""
                UPDATE scraper_stats SET ai_rejected = ?
                WHERE scraper = ? AND run_at = (
                    SELECT MAX(run_at) FROM scraper_stats WHERE scraper = ?
                )
            """, (count, platform, platform))
            self.conn.commit()
        except Exception as e:
            log.warning(f"AI rejection record error: {e}")

    def end_cycle(self, total_sent: int, total_ai_rejected: int = 0):
        if not self._cycle_start:
            return
        duration = (datetime.utcnow() - self._cycle_start).total_seconds()
        total_raw = sum(s["raw"] for s in self._cycle_stats.values())
        total_new = sum(s["new"] for s in self._cycle_stats.values())
        try:
            self.conn.execute("""
                INSERT INTO cycle_summary
                (total_scrapers, total_raw, total_new, total_sent,
                 total_ai_rejected, duration_sec, run_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (len(self._cycle_stats), total_raw, total_new,
                  total_sent, total_ai_rejected, duration,
                  self._cycle_start.isoformat()))
            self.conn.commit()
        except Exception as e:
            log.warning(f"Cycle summary record error: {e}")

    def get_status(self) -> dict:
        """Get comprehensive bot status for /status endpoint."""
        status = {
            "bot": "Zeta Violin Hunter",
            "status": "running",
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }

        try:
            # Last cycle
            cur = self.conn.execute("""
                SELECT total_scrapers, total_raw, total_new, total_sent,
                       total_ai_rejected, duration_sec, run_at
                FROM cycle_summary ORDER BY id DESC LIMIT 1
            """)
            row = cur.fetchone()
            if row:
                status["last_cycle"] = {
                    "scrapers_ran": row[0],
                    "raw_listings": row[1],
                    "new_listings": row[2],
                    "sent_to_telegram": row[3],
                    "ai_rejected": row[4],
                    "duration_seconds": round(row[5], 1),
                    "run_at": row[6],
                }

            # Per-scraper latest stats
            cur = self.conn.execute("""
                SELECT scraper, raw_count, new_count, ai_rejected,
                       error, duration_sec, run_at
                FROM scraper_stats
                WHERE id IN (
                    SELECT MAX(id) FROM scraper_stats GROUP BY scraper
                )
                ORDER BY scraper
            """)
            scrapers = {}
            for row in cur.fetchall():
                scrapers[row[0]] = {
                    "raw": row[1],
                    "new": row[2],
                    "ai_rejected": row[3],
                    "error": row[4] or None,
                    "duration_sec": round(row[5], 1) if row[5] else 0,
                    "last_run": row[6],
                }
            status["scrapers"] = scrapers

            # Totals (all time)
            cur = self.conn.execute("""
                SELECT COUNT(*), SUM(total_raw), SUM(total_sent),
                       SUM(total_ai_rejected)
                FROM cycle_summary
            """)
            row = cur.fetchone()
            if row:
                status["all_time"] = {
                    "total_cycles": row[0] or 0,
                    "total_raw_listings": row[1] or 0,
                    "total_sent": row[2] or 0,
                    "total_ai_rejected": row[3] or 0,
                }

            # Price history stats
            cur = self.conn.execute("""
                SELECT COUNT(*), AVG(price_usd), MIN(price_usd),
                       MAX(price_usd)
                FROM price_history WHERE price_usd > 50
            """)
            row = cur.fetchone()
            if row and row[0]:
                status["price_stats"] = {
                    "total_tracked": row[0],
                    "avg_usd": round(row[1], 2) if row[1] else 0,
                    "min_usd": round(row[2], 2) if row[2] else 0,
                    "max_usd": round(row[3], 2) if row[3] else 0,
                }

        except Exception as e:
            status["error"] = str(e)

        return status

    def close(self):
        self.conn.close()
