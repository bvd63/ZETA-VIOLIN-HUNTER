"""
SQLite database — tracks seen listings so we only alert on NEW ones.
Path comes from Config.DB_PATH (point it at a Railway volume to survive deploys).
"""

import os
import sqlite3
import json
import logging
from datetime import datetime
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
    return sqlite3.connect(DB_PATH)


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
        self.conn.commit()

    def is_seen(self, listing_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM seen_listings WHERE id = ?", (listing_id,)
        )
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

    def close(self):
        self.conn.close()
