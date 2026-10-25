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
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(alert_receipts)")}
        for name, kind in (("push_receipt", "TEXT"), ("push_expires", "REAL"),
                           ("push_next_attempt", "REAL"), ("push_closed", "REAL"),
                           ("push_attempts", "INTEGER NOT NULL DEFAULT 0")):
            if name not in columns:
                self.conn.execute(f"ALTER TABLE alert_receipts ADD COLUMN {name} {kind}")
        self.conn.commit()

    def feedback(self, receipt_id: str, chat_id: str, label: str) -> bool:
        """Owner correction, separate from identity rules and explicit reading."""
        if label not in ('not_violin', 'ended'):
            return False
        if not self.conn.execute('SELECT 1 FROM alert_receipts WHERE id=? AND chat_id=?',
                                 (receipt_id, str(chat_id))).fetchone():
            return False
        self.conn.execute('''CREATE TABLE IF NOT EXISTS alert_feedback (
            receipt_id TEXT PRIMARY KEY, label TEXT NOT NULL, updated_at REAL NOT NULL)''')
        self.conn.execute('INSERT OR REPLACE INTO alert_feedback VALUES (?,?,?)',
                          (receipt_id, label, time.time()))
        self.conn.commit()
        return self.acknowledge(receipt_id, chat_id)

    def prepare(self, chat_id: str, listing: dict, text: str, event: str = "new") -> str:
        identity = "|".join((str(chat_id), canonical_url(listing.get("url", "")),
                             str(listing.get("auction_end", "")), event))
        receipt_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
        now = time.time()
        self.conn.execute("""INSERT INTO alert_receipts (id,chat_id,url,text,created_at,push_next_attempt)
            VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET text=excluded.text""",
            (receipt_id, str(chat_id), listing.get("url", ""), text, now,
             now + Config.PUSHOVER_DELAY_MINUTES * 60))
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
            next_reminder=NULL, push_next_attempt=NULL WHERE id=? AND chat_id=?""", (time.time(), receipt_id, str(chat_id)))
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

    def push_immediately(self, receipt_id: str) -> None:
        self.conn.execute("""UPDATE alert_receipts SET push_next_attempt=?
            WHERE id=? AND acknowledged_at IS NULL AND push_receipt IS NULL AND push_attempts=0""", (time.time(), receipt_id))
        self.conn.commit()

    def push_active(self, chat_id: str) -> list:
        rows = self.conn.execute("""SELECT id,push_receipt,acknowledged_at,push_expires FROM alert_receipts
            WHERE chat_id=? AND push_attempts>0 AND push_closed IS NULL LIMIT 100""", (str(chat_id),)).fetchall()
        return [dict(zip(("id", "receipt", "acknowledged_at", "expires"), row)) for row in rows]

    def claim_push_due(self, chat_id: str, now: float = None) -> list:
        now = time.time() if now is None else now
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            rows = self.conn.execute("""SELECT id,url,text,push_attempts FROM alert_receipts WHERE chat_id=?
                AND acknowledged_at IS NULL AND push_next_attempt<=?
                AND (push_receipt IS NULL OR push_expires<=?) ORDER BY push_next_attempt LIMIT 5""",
                (str(chat_id), now, now)).fetchall()
            self.conn.executemany("UPDATE alert_receipts SET push_next_attempt=? WHERE id=?",
                                   [(now + 600, row[0]) for row in rows])
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return [dict(zip(("id", "url", "text", "attempts"), row)) for row in rows]

    def push_starting(self, receipt_id: str) -> None:
        self.conn.execute("UPDATE alert_receipts SET push_attempts=push_attempts+1 WHERE id=?", (receipt_id,))
        self.conn.commit()

    def push_sent(self, receipt_id: str, receipt: str, now: float = None) -> None:
        now = time.time() if now is None else now
        expires = now + Config.PUSHOVER_EXPIRE_SECONDS
        # Save even if acknowledgement raced the network request: the next tick
        # must know the receipt so it can cancel the remote alarm.
        self.conn.execute("""UPDATE alert_receipts SET push_receipt=?,push_expires=?,push_closed=NULL,
            push_next_attempt=CASE WHEN acknowledged_at IS NULL THEN ? ELSE NULL END WHERE id=?""",
            (receipt, expires, expires, receipt_id))
        self.conn.commit()

    def push_finished(self, receipt_id: str) -> None:
        self.conn.execute("UPDATE alert_receipts SET push_closed=? WHERE id=?", (time.time(), receipt_id))
        self.conn.commit()

    def push_failed(self, receipt_id: str) -> None:
        self.conn.execute("""UPDATE alert_receipts SET push_next_attempt=?
            WHERE id=? AND acknowledged_at IS NULL""", (time.time() + 300, receipt_id))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
