"""Durable detail work, using expiring claims on the existing SQLite volume.

The queue pattern follows Crawlee's pending/handled request model. A cap limits
work per cycle, without discarding candidates when an index stops returning them.
"""
import hashlib
import json
import time
from database import canonical_url, connect


class DiscoveryQueue:
    def __init__(self, namespace: str):
        self.namespace = namespace
        self.conn = connect()
        self.conn.execute("""CREATE TABLE IF NOT EXISTS discovery_queue (
            namespace TEXT, key TEXT, payload TEXT NOT NULL, fingerprint TEXT,
            priority INTEGER, created REAL, next_attempt REAL, attempts INTEGER DEFAULT 0,
            PRIMARY KEY(namespace,key))""")
        self.conn.execute("CREATE INDEX IF NOT EXISTS discovery_queue_due ON discovery_queue(namespace,next_attempt)")
        self.conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.conn.close()

    def put(self, items: list) -> None:
        now = time.time()
        rows = []
        for item in items:
            key = canonical_url(item["url"])
            fingerprint = hashlib.sha256(json.dumps(
                [item.get(k) for k in ("title", "description", "price", "auction_end", "assetAuctionEndDateUtc")],
                sort_keys=True).encode()).hexdigest()
            rows.append((self.namespace, key, json.dumps(item), fingerprint,
                         item.get("_discovery_priority", 1), now, now))
        self.conn.executemany("""INSERT INTO discovery_queue
            (namespace,key,payload,fingerprint,priority,created,next_attempt) VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(namespace,key) DO UPDATE SET payload=excluded.payload,priority=excluded.priority,
            next_attempt=CASE WHEN discovery_queue.fingerprint!=excluded.fingerprint
                THEN min(discovery_queue.next_attempt,excluded.next_attempt) ELSE discovery_queue.next_attempt END,
            fingerprint=excluded.fingerprint""", rows)
        self.conn.commit()

    def claim(self, limit: int, now: float = None) -> list:
        now = time.time() if now is None else now
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            oldest = max(1, limit // 4) if limit > 0 else 0
            rows = self.conn.execute("""SELECT key,payload FROM discovery_queue
                WHERE namespace=? AND next_attempt<=? ORDER BY next_attempt,created LIMIT ?""",
                (self.namespace, now, oldest)).fetchall()
            keys = [row[0] for row in rows]
            other = self.conn.execute("""SELECT key,payload FROM discovery_queue
                WHERE namespace=? AND next_attempt<=? ORDER BY priority,next_attempt,created LIMIT ?""",
                (self.namespace, now, limit + oldest)).fetchall()
            rows += [row for row in other if row[0] not in keys][:max(0, limit - len(rows))]
            self.conn.executemany("""UPDATE discovery_queue SET next_attempt=?,attempts=attempts+1
                WHERE namespace=? AND key=?""", [(now + 1200, self.namespace, row[0]) for row in rows])
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return [json.loads(row[1]) for row in rows]

    def finish(self, item: dict, delay: int) -> None:
        self.conn.execute("UPDATE discovery_queue SET next_attempt=? WHERE namespace=? AND key=?",
                          (time.time() + delay, self.namespace, canonical_url(item["url"])))
        self.conn.commit()

    def due_count(self) -> int:
        return self.conn.execute("SELECT count(*) FROM discovery_queue WHERE namespace=? AND next_attempt<=?",
                                 (self.namespace, time.time())).fetchone()[0]


def stage(namespace: str, items: list, limit: int) -> tuple:
    with DiscoveryQueue(namespace) as queue:
        queue.put(items)
        selected = queue.claim(limit)
        return selected, queue.due_count()


def complete(namespace: str, item: dict, delay: int) -> None:
    with DiscoveryQueue(namespace) as queue:
        queue.finish(item, delay)
