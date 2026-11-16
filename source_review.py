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


def active_holds(conn=None) -> dict:
    holds = {}
    if conn is None:
        with store() as current:
            rows = current.execute('SELECT key,value FROM provider_state WHERE key GLOB ?', (PREFIX+'*',)).fetchall()
    elif conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='provider_state'").fetchone():
        rows = conn.execute('SELECT key,value FROM provider_state WHERE key GLOB ?', (PREFIX+'*',)).fetchall()
    else:
        rows = []
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
    from listing_feedback import active_holds as listing_holds, hold_reason as listing_hold
    name = host(url)
    if holds is not None:
        return holds.get(name,'') or listing_hold(url)
    with store() as conn:
        source = active_holds(conn).get(name,'') if name else ''
        return source or listing_hold(url,listing_holds(conn))


def bind(conn, *, include_ended=False) -> dict:
    """Snapshot for one SQL claim; last-mile checks read current state again."""
    holds = active_holds(conn)
    from listing_feedback import active_holds as listing_holds, hold_reason as listing_hold
    offers = listing_holds(conn)
    if include_ended:
        from listing_feedback import active_ended
        offers.update({key:'owner marked ended' for key in active_ended(conn)})
    conn.create_function('source_review_held',1,
        lambda url:int(bool(holds.get(host(url),'') or listing_hold(url,offers))),deterministic=True)
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
