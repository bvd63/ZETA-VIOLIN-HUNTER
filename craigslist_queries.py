"""Durable unfinished fixed-endpoint searches; no URL or paid-provider replay."""
import hashlib
import json
import time
from provider_state import store

PREFIX = 'craigslist:pending_query:'


def query_key(work):
    area, query, path = work
    return PREFIX + hashlib.sha256(json.dumps([list(area),query,path],ensure_ascii=False).encode()).hexdigest()


def stage_queries(work):
    now = time.time()
    with store() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.executemany('INSERT OR IGNORE INTO provider_state VALUES (?,?)',
                         [(query_key(w),json.dumps({'work':w,'created':now,'last_attempt':0,'due':0,'attempts':0})) for w in work])
        rows = conn.execute('SELECT key,value FROM provider_state WHERE key LIKE ?', (PREFIX+'%',)).fetchall()
        conn.commit()
    return {key:json.loads(value) for key,value in rows}


def finish_queries(outcomes):
    """Commit only completed requests; cancellation leaves uncompleted jobs due."""
    now = time.time()
    with store() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for work, error, delay in outcomes:
            key = query_key(work)
            if not error:
                conn.execute('DELETE FROM provider_state WHERE key=?',(key,))
                continue
            row = conn.execute('SELECT value FROM provider_state WHERE key=?',(key,)).fetchone()
            value = json.loads(row[0]) if row else {'work':work,'created':now,'attempts':0}
            value.update(last_attempt=now,due=now+delay,error=error,attempts=value['attempts']+1)
            conn.execute('INSERT OR REPLACE INTO provider_state VALUES (?,?)',(key,json.dumps(value)))
        conn.commit()


def pending_count():
    with store() as conn:
        return conn.execute('SELECT count(*) FROM provider_state WHERE key LIKE ?', (PREFIX+'%',)).fetchone()[0]
