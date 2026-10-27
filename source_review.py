"""Owner-requested notification quarantine; discovery/history stay intact."""
import json
import re
import time
from urllib.parse import urlsplit

from provider_state import store

PREFIX = 'source_review:'


def host(value: str) -> str:
    if not isinstance(value,str) or not value:
        return ''
    try:
        p = urlsplit(value if '://' in value else 'https://' + value)
        name = (p.hostname or '').lower().removeprefix('www.')
        if (p.scheme not in ('http','https') or p.username or p.password
                or p.port not in (None,80,443) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]*\.[a-z]{2,}',name)):
            return ''
        return name
    except (ValueError,TypeError):
        return ''


def active_holds() -> dict:
    holds = {}
    with store() as conn:
        rows = conn.execute('SELECT key,value FROM provider_state WHERE key GLOB ?', (PREFIX+'*',)).fetchall()
    for key,raw in rows:
        name = host(key[len(PREFIX):])
        if not name:
            continue
        try:
            value = json.loads(raw)
        except (TypeError,ValueError):
            value = {'reason':'stare de verificare nevalidă'}
        if not isinstance(value,dict) or not value:
            value = {'reason':'stare de verificare nevalidă'}
        if value.get('active',True) is False:
            continue
        holds[name] = str(value.get('reason') or 'comerciant în verificare')
    return holds


def hold_reason(url: str, holds=None) -> str:
    name = host(url)
    return (active_holds() if holds is None else holds).get(name,'') if name else ''


def bind(conn) -> dict:
    """Snapshot for one SQL claim; last-mile checks read current state again."""
    holds = active_holds()
    conn.create_function('source_review_held',1,lambda url:int(bool(hold_reason(url,holds))),deterministic=True)
    return holds


def set_hold(domain: str, reason: str) -> int:
    name = host(domain)
    if not name or not isinstance(reason,str) or not reason.strip():
        raise ValueError('valid host and review reason required')
    with store() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('INSERT OR REPLACE INTO provider_state VALUES (?,?)',
            (PREFIX+name,json.dumps({'active':True,'reason':reason[:500],'created':time.time()})))
        count = 0
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alert_receipts'").fetchone():
            conn.create_function('review_source_host',1,host,deterministic=True)
            count = conn.execute('''UPDATE alert_receipts SET next_reminder=NULL,push_next_attempt=NULL
                WHERE review_source_host(url)=?''',(name,)).rowcount
        conn.commit()
        return count


def clear_hold(domain: str) -> None:
    """Allow fresh verification/delivery; never automatically restart old alarms."""
    name = host(domain)
    if not name:
        raise ValueError('valid host required')
    with store() as conn:
        conn.execute('INSERT OR REPLACE INTO provider_state VALUES (?,?)',
            (PREFIX+name,json.dumps({'active':False,'reviewed':time.time()})))
        conn.commit()
