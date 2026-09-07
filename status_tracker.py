"""
Status tracker — records per-scraper stats in SQLite for the /status
dashboard endpoint and for the watchdog (consecutive zero/error streaks).
"""

import logging
from datetime import datetime
from database import connect

log = logging.getLogger(__name__)


class StatusTracker:
    def __init__(self):
        self.conn = connect()
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
        self._cycle_stats[name] = {"raw": raw, "new": new, "error": error, "duration": duration}
        try:
            self.conn.execute("""
                INSERT INTO scraper_stats
                (scraper, raw_count, new_count, error, duration_sec, run_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (name, raw, new, error, duration, datetime.utcnow().isoformat()))
            self.conn.commit()
        except Exception as e:
            log.warning(f"Status record error: {e}")

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
                  total_sent, total_ai_rejected, duration, self._cycle_start.isoformat()))
            self.conn.commit()
        except Exception as e:
            log.warning(f"Cycle summary record error: {e}")

    def get_streaks(self, lookback: int = 30) -> dict:
        """For each scraper: number of consecutive most-recent cycles with
        raw_count == 0 ('zero') and with an error ('error')."""
        streaks = {}
        try:
            names = [r[0] for r in self.conn.execute("SELECT DISTINCT scraper FROM scraper_stats")]
            for name in names:
                rows = self.conn.execute("""
                    SELECT raw_count, error FROM scraper_stats
                    WHERE scraper = ? ORDER BY id DESC LIMIT ?
                """, (name, lookback)).fetchall()
                zero = err = 0
                for raw, error in rows:
                    if error:
                        err += 1
                    else:
                        break
                for raw, error in rows:
                    if (raw or 0) == 0:
                        zero += 1
                    else:
                        break
                streaks[name] = {"zero": zero, "error": err}
        except Exception as e:
            log.warning(f"Streak calc error: {e}")
        return streaks

    def get_status(self) -> dict:
        """Get comprehensive bot status for /status endpoint."""
        status = {
            "bot": "Zeta Violin Hunter",
            "status": "running",
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }
        try:
            row = self.conn.execute("""
                SELECT total_scrapers, total_raw, total_new, total_sent,
                       total_ai_rejected, duration_sec, run_at
                FROM cycle_summary ORDER BY id DESC LIMIT 1
            """).fetchone()
            if row:
                status["last_cycle"] = {
                    "scrapers_ran": row[0],
                    "raw_listings": row[1],
                    "new_listings": row[2],
                    "sent_to_telegram": row[3],
                    "scrapers_in_error": row[4],
                    "duration_seconds": round(row[5], 1),
                    "run_at": row[6],
                }

            cur = self.conn.execute("""
                SELECT scraper, raw_count, new_count, error, duration_sec, run_at
                FROM scraper_stats
                WHERE id IN (SELECT MAX(id) FROM scraper_stats GROUP BY scraper)
                ORDER BY scraper
            """)
            streaks = self.get_streaks()
            scrapers = {}
            for row in cur.fetchall():
                scrapers[row[0]] = {
                    "raw": row[1],
                    "new": row[2],
                    "error": row[3] or None,
                    "duration_sec": round(row[4], 1) if row[4] else 0,
                    "last_run": row[5],
                    "zero_streak": streaks.get(row[0], {}).get("zero", 0),
                    "error_streak": streaks.get(row[0], {}).get("error", 0),
                }
            status["scrapers"] = scrapers

            row = self.conn.execute("""
                SELECT COUNT(*), SUM(total_raw), SUM(total_sent) FROM cycle_summary
            """).fetchone()
            if row:
                status["all_time"] = {
                    "total_cycles": row[0] or 0,
                    "total_raw_listings": row[1] or 0,
                    "total_sent": row[2] or 0,
                }

            row = self.conn.execute("""
                SELECT COUNT(*), AVG(price_usd), MIN(price_usd), MAX(price_usd)
                FROM price_history WHERE price_usd > 50
            """).fetchone()
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
