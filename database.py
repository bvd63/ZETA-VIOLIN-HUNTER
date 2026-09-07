"""
SQLite database — seen listings (dedup), live-listing activity, alert log.
Path comes from Config.DB_PATH (point it at a Railway volume to survive deploys).
"""

import os
import sqlite3
import json
import logging
from datetime import datetime, timedelta
from config import Config

log = logging.getLogger(__name__)

DB_PATH = Config.DB_PATH


def connect() -> sqlite3.Connection:
    """Open the shared SQLite file, creating its directory if needed."""
    directory = os.path.dirname(os.path.abspath(DB_PATH))
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as e:
        log.warning(f"Could not create DB directory {directory}: {e}")
    conn = sqlite3.connect(DB_PATH, timeout=30)
    return conn


class Database:
    def __init__(self):
        self.conn = connect()
        self._init()

    def _init(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_listings (
                id TEXT PRIMARY KEY,
                platform TEXT,
                title TEXT,
                price TEXT,
                location TEXT,
                url TEXT,
                data TEXT,
                first_seen TEXT
            )
        """)
        # Every cycle in which a listing passed the filters — powers /active,
        # the weekly digest and "sold" detection.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS listing_activity (
                id TEXT PRIMARY KEY,
                platform TEXT,
                title TEXT,
                price TEXT,
                url TEXT,
                first_seen TEXT,
                last_seen TEXT
            )
        """)
        # Alerts actually sent — used for cross-platform dedup.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY,
                platform TEXT,
                title TEXT,
                price_usd REAL,
                url TEXT,
                alerted_at TEXT
            )
        """)
        self.conn.commit()

    # --- dedup ---------------------------------------------------------------
    def is_seen(self, listing_id: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM seen_listings WHERE id = ?", (listing_id,))
        return cur.fetchone() is not None

    def mark_seen(self, listing_id: str, listing: dict):
        try:
            self.conn.execute(
                """INSERT OR IGNORE INTO seen_listings
                   (id, platform, title, price, location, url, data, first_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    listing_id,
                    listing.get("platform", ""),
                    listing.get("title", ""),
                    listing.get("price", ""),
                    listing.get("location", ""),
                    listing.get("url", ""),
                    json.dumps(listing, ensure_ascii=False, default=str),
                    datetime.utcnow().isoformat(),
                ),
            )
            self.conn.commit()
        except Exception as e:
            log.error(f"DB insert error: {e}")

    # --- activity --------------------------------------------------------------
    def touch_active(self, listing: dict) -> None:
        now = datetime.utcnow().isoformat()
        try:
            self.conn.execute("""
                INSERT INTO listing_activity (id, platform, title, price, url, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET price = excluded.price, last_seen = excluded.last_seen
            """, (listing.get("id", ""), listing.get("platform", ""), listing.get("title", ""),
                  listing.get("price", ""), listing.get("url", ""), now, now))
            self.conn.commit()
        except Exception as e:
            log.warning(f"activity update error: {e}")

    def active_listings(self, days: int = 3) -> list:
        since = (datetime.utcnow() - timedelta(days=days)).isoformat()
        try:
            rows = self.conn.execute("""
                SELECT id, platform, title, price, url, first_seen, last_seen FROM listing_activity
                WHERE last_seen >= ? ORDER BY last_seen DESC
            """, (since,)).fetchall()
        except Exception as e:
            log.warning(f"active listings error: {e}")
            return []
        return [dict(zip(("id", "platform", "title", "price", "url", "first_seen", "last_seen"), r)) for r in rows]

    def gone_since(self, days_active: int = 3, days_gone: int = 14) -> list:
        """Listings that were active recently but have not been seen for `days_active`
        days (probably sold). Used by the weekly digest."""
        now = datetime.utcnow()
        try:
            rows = self.conn.execute("""
                SELECT platform, title, price, url, last_seen FROM listing_activity
                WHERE last_seen < ? AND last_seen >= ? ORDER BY last_seen DESC
            """, ((now - timedelta(days=days_active)).isoformat(),
                  (now - timedelta(days=days_gone)).isoformat())).fetchall()
        except Exception as e:
            log.warning(f"gone listings error: {e}")
            return []
        return [dict(zip(("platform", "title", "price", "url", "last_seen"), r)) for r in rows]

    # --- alerts ------------------------------------------------------------------
    def record_alert(self, listing: dict, price_usd) -> None:
        try:
            self.conn.execute("""
                INSERT OR REPLACE INTO alerts (id, platform, title, price_usd, url, alerted_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (listing.get("id", ""), listing.get("platform", ""), listing.get("title", ""),
                  price_usd, listing.get("url", ""), datetime.utcnow().isoformat()))
            self.conn.commit()
        except Exception as e:
            log.warning(f"alert record error: {e}")

    def recent_alerts(self, days: int = 60) -> list:
        since = (datetime.utcnow() - timedelta(days=days)).isoformat()
        try:
            rows = self.conn.execute(
                "SELECT id, platform, title, price_usd, url FROM alerts WHERE alerted_at >= ?", (since,)
            ).fetchall()
        except Exception as e:
            log.warning(f"recent alerts error: {e}")
            return []
        return [dict(zip(("id", "platform", "title", "price_usd", "url"), r)) for r in rows]

    def close(self):
        self.conn.close()
