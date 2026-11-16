"""Read-only delivery timing; API acceptance and owner actions are distinct."""
from datetime import datetime, timezone
import time


def snapshot(conn, now=None):
    now = time.time() if now is None else now
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    result = {'checked':now,'physical_phone_sound_verified':False}
    if 'pending_alerts' in tables:
        count,oldest = conn.execute('SELECT count(*),min(queued_at) FROM pending_alerts').fetchone()
        result['outbox_pending'] = count
        try:
            stamp = datetime.fromisoformat(oldest)
            stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
            result['outbox_oldest_seconds'] = max(0,now-stamp.timestamp())
        except (TypeError,ValueError):
            result['outbox_oldest_seconds'] = None
    if {'alert_receipts','operator_alert_closures'} <= tables:
        rows = conn.execute('''SELECT a.created_at,a.delivered_at,a.acknowledged_at,c.closed_at
            FROM alert_receipts a LEFT JOIN operator_alert_closures c ON c.receipt_id=a.id
            ORDER BY a.created_at DESC LIMIT 100''').fetchall()
        delays = sorted(d-c for c,d,ack,closed in rows if d is not None and d>=c)
        result['recent_receipts'] = len(rows)
        result['delivery_latency_seconds'] = {
            'sample_count':len(delays),
            'p50':delays[(len(delays)-1)//2] if delays else None,
            'max':delays[-1] if delays else None,
            'meaning':'receipt creation to Telegram API acceptance; not publication-to-discovery delay'}
        result['owner_response_recorded'] = sum(a is not None for c,d,a,closed in rows)
        result['technical_closures'] = sum(closed is not None for c,d,a,closed in rows)
        result['open_delivered'] = sum(d is not None and a is None and closed is None for c,d,a,closed in rows)
        result['owner_response_meaning'] = 'explicit button/feedback action; not a Telegram read receipt'
    return result
