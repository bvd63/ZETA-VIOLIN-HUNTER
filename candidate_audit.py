"""Compact decision history for observed candidates, without provider secrets."""
import logging
import time

from database import canonical_url, connect

log = logging.getLogger(__name__)


def record_candidate(item: dict, stage: str, reason: str = '') -> None:
    conn = None
    try:
        url = canonical_url(item.get('url', ''))
        if not url:
            return
        conn = connect()
        conn.execute('''CREATE TABLE IF NOT EXISTS candidate_events (
            id INTEGER PRIMARY KEY, url TEXT NOT NULL, source TEXT, title TEXT,
            stage TEXT, reason TEXT, created REAL)''')
        conn.execute('CREATE INDEX IF NOT EXISTS candidate_events_url ON candidate_events(url,id)')
        previous = conn.execute('SELECT stage,reason FROM candidate_events WHERE url=? ORDER BY id DESC LIMIT 1', (url,)).fetchone()
        if previous != (stage, reason):
            inserted = conn.execute('INSERT INTO candidate_events (url,source,title,stage,reason,created) VALUES (?,?,?,?,?,?)',
                         (url, str(item.get('platform', ''))[:100], str(item.get('title', ''))[:300], stage, reason[:300], time.time()))
            if inserted.lastrowid % 256 == 0:
                conn.execute('DELETE FROM candidate_events WHERE id<?', (inserted.lastrowid - 50000,))
        conn.commit()
    except Exception as exc:
        log.warning('Candidate history unavailable (%s); discovery continues', type(exc).__name__)
    finally:
        if conn:
            conn.close()


def candidate_history(url: str, limit: int = 6) -> list:
    conn = connect()
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='candidate_events'").fetchone():
            return []
        return conn.execute('SELECT stage,reason,created FROM candidate_events WHERE url=? ORDER BY id DESC LIMIT ?',
                            (canonical_url(url), max(1, min(limit, 20)))).fetchall()
    finally:
        conn.close()
