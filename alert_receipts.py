"""Durable, explicit Telegram acknowledgements, independent of search quotas.

Telegram Bot API has no message-read receipt. Only a button press acknowledges
an alert. Old delivery history is deliberately not imported as unread.
"""

import hashlib
import time
from database import canonical_url, connect
from config import Config


class AlertReceipts:
    def __init__(self):
        self.conn = connect()
        self.conn.execute("""CREATE TABLE IF NOT EXISTS alert_receipts (
            id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, url TEXT NOT NULL,
            text TEXT NOT NULL, created_at REAL NOT NULL,
            delivered_at REAL, acknowledged_at REAL, next_reminder REAL,
            reminder_count INTEGER NOT NULL DEFAULT 0, message_id INTEGER
        )""")
        self.conn.commit()

    def prepare(self, chat_id: str, listing: dict, text: str, event: str = "new") -> str:
        identity = "|".join((str(chat_id), canonical_url(listing.get("url", "")),
                             str(listing.get("auction_end", "")), event))
        receipt_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
        self.conn.execute("""INSERT INTO alert_receipts (id,chat_id,url,text,created_at)
            VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET text=excluded.text""",
            (receipt_id, str(chat_id), listing.get("url", ""), text, time.time()))
        self.conn.commit()
        return receipt_id

    def delivered(self, receipt_id: str, message_id: int = None, now: float = None) -> None:
        now = time.time() if now is None else now
        self.conn.execute("""UPDATE alert_receipts SET
            delivered_at=coalesce(delivered_at,?), message_id=?,
            next_reminder=CASE WHEN delivered_at IS NULL AND acknowledged_at IS NULL
                THEN ? ELSE next_reminder END WHERE id=?""",
            (now, message_id, now + Config.ALERT_FIRST_REMINDER_MINUTES * 60, receipt_id))
        self.conn.commit()

    def acknowledge(self, receipt_id: str, chat_id: str) -> bool:
        cur = self.conn.execute("""UPDATE alert_receipts SET acknowledged_at=coalesce(acknowledged_at,?),
            next_reminder=NULL WHERE id=? AND chat_id=?""", (time.time(), receipt_id, str(chat_id)))
        self.conn.commit()
        return cur.rowcount > 0

    def is_pending(self, receipt_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM alert_receipts WHERE id=? AND acknowledged_at IS NULL",
                                 (receipt_id,)).fetchone() is not None

    def claim_due(self, chat_id: str, now: float = None, limit: int = 5) -> list:
        """Short persisted lease prevents concurrent/restarted workers duplicating a batch."""
        now = time.time() if now is None else now
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            rows = self.conn.execute("""SELECT id,url,text,reminder_count,message_id FROM alert_receipts
                WHERE chat_id=? AND delivered_at IS NOT NULL AND acknowledged_at IS NULL
                AND next_reminder<=? ORDER BY next_reminder LIMIT ?""", (str(chat_id), now, limit)).fetchall()
            self.conn.executemany("UPDATE alert_receipts SET next_reminder=? WHERE id=?",
                                   [(now + 120, row[0]) for row in rows])
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return [dict(zip(("id", "url", "text", "reminder_count", "message_id"), row)) for row in rows]

    def reminder_result(self, receipt_id: str, success: bool, now: float = None) -> None:
        now = time.time() if now is None else now
        # First reminder at 10min; subsequent ones every 30min, until confirmed.
        delay = Config.ALERT_REPEAT_MINUTES * 60 if success else 120
        self.conn.execute("""UPDATE alert_receipts SET next_reminder=?,
            reminder_count=reminder_count+? WHERE id=? AND acknowledged_at IS NULL""",
            (now + delay, int(success), receipt_id))
        self.conn.commit()

    def pending_count(self, chat_id: str) -> int:
        return self.conn.execute("""SELECT count(*) FROM alert_receipts WHERE chat_id=?
            AND delivered_at IS NOT NULL AND acknowledged_at IS NULL""", (str(chat_id),)).fetchone()[0]

    def close(self) -> None:
        self.conn.close()
