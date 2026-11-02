"""Exact-offer owner feedback; never a model, price or merchant blacklist."""
import re
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
