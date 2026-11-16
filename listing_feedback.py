"""Exact-offer owner feedback; never a model, price or merchant blacklist."""
import re
import hashlib
import json
import math
import time
from urllib.parse import urlsplit

from database import canonical_url, connect


def offer_key(url: str) -> str:
    try:
        parts = urlsplit(url or '')
        if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password
                or parts.port not in (None, 80 if parts.scheme == 'http' else 443)):
            return ''
        if parts.hostname.lower() in ('facebook.com', 'www.facebook.com', 'm.facebook.com', 'web.facebook.com'):
            match = re.fullmatch(r'/marketplace/item/(\d+)/?', parts.path)
            if match and parts.port in (None, 80, 443):
                return 'https://facebook.com/marketplace/item/' + match[1]
        if parts.hostname.lower() in ('bunjang.co.kr','www.bunjang.co.kr','m.bunjang.co.kr'):
            match=re.fullmatch(r'/products/(\d+)/?',parts.path)
            if match:return 'https://bunjang.co.kr/products/'+match[1]
        if parts.hostname.lower() in ('kleinanzeigen.de','www.kleinanzeigen.de','m.kleinanzeigen.de'):
            match=re.fullmatch(r'/s-anzeige/(?:[^/]+/)?(\d+)(?:-\d+-\d+)?/?',parts.path)
            if match:return 'https://kleinanzeigen.de/s-anzeige/'+match[1]
        return canonical_url(url)
    except (TypeError, ValueError):
        return ''


def active_holds(conn=None) -> dict:
    owned = conn is None
    conn = connect() if owned else conn
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alert_feedback'").fetchone():
            return {}
        rows = conn.execute('''SELECT r.url FROM alert_feedback f
            JOIN alert_receipts r ON r.id=f.receipt_id WHERE f.label='fake' ''').fetchall()
        return {key: 'FAKE — marcat de tine; anunț păstrat pentru verificare'
                for (url,) in rows if (key := offer_key(url))}
    finally:
        if owned:
            conn.close()


def hold_reason(url: str, holds=None) -> str:
    return (active_holds() if holds is None else holds).get(offer_key(url), '')


def _ended_key(url):
    identity=offer_key(url)
    return 'availability:owner_ended_cleared:'+hashlib.sha256(identity.encode()).hexdigest() if identity else ''


def active_ended(conn=None):
    """Owner's exact-offer negative survives queues; it is not a model ban."""
    owned=conn is None
    conn=connect() if owned else conn
    try:
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'alert_feedback' not in tables:return {}
        observations={}
        for url,checked in conn.execute("""SELECT r.url,f.updated_at FROM alert_feedback f
                JOIN alert_receipts r ON r.id=f.receipt_id WHERE f.label='ended'"""):
            identity=offer_key(url)
            if identity:observations[identity]=max(observations.get(identity,0),checked)
        if 'provider_state' in tables:
            for identity,observed in list(observations.items()):
                row=conn.execute('SELECT value FROM provider_state WHERE key=?',(_ended_key(identity),)).fetchone()
                try:cleared=json.loads(row[0]) if row else 0
                except (TypeError,ValueError):cleared=0
                if type(cleared) in (int,float) and math.isfinite(cleared) and observed<cleared<=time.time()+5:
                    observations.pop(identity)
        return observations
    finally:
        if owned:conn.close()


def ended_reason(url,conn=None):
    return 'Vândut/încheiat — marcat de tine; necesită dovadă primară actuală de disponibilitate' if offer_key(url) in active_ended(conn) else ''


def clear_ended_with_primary(url,checked):
    """Only an explicitly available primary observation NEWER than feedback.

    Keep original feedback/history/acknowledgements. Old alarms never restart.
    """
    if type(checked) not in (int,float) or not math.isfinite(checked) or not 0<=checked<=time.time()+5:return
    from provider_state import store
    with store() as conn:
        conn.execute('BEGIN IMMEDIATE')
        observed=active_ended(conn).get(offer_key(url))
        if observed is not None and checked>observed:
            row=conn.execute('SELECT value FROM provider_state WHERE key=?',(_ended_key(url),)).fetchone()
            try:previous=json.loads(row[0]) if row else 0
            except (TypeError,ValueError):previous=0
            if type(previous) not in (int,float) or not math.isfinite(previous):previous=0
            conn.execute('INSERT OR REPLACE INTO provider_state VALUES (?,?)',
                         (_ended_key(url),json.dumps(max(previous,checked))))
        conn.commit()
