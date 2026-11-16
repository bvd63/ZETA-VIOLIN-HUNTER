"""Reconcile only authenticated terminal-run costs; uncertain caps stay reserved."""
import math
import re
import time
from datetime import datetime, timezone
from decimal import Decimal

from database import connect
from config import Config

TERMINAL = {"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"}


def ensure_table(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS apify_costs (
        run_id TEXT PRIMARY KEY, cost INTEGER NOT NULL, status TEXT, checked REAL)""")
    conn.execute('''CREATE TABLE IF NOT EXISTS apify_cost_changes (
        run_id TEXT PRIMARY KEY, changed REAL NOT NULL)''')


def spent_micro(conn, period: str) -> int:
    return conn.execute("""SELECT coalesce(sum(coalesce(c.cost,j.cap)),0)
        FROM apify_jobs j LEFT JOIN apify_costs c ON c.run_id=j.run_id
        WHERE j.period=?""", (period,)).fetchone()[0]


def unmetered_micro(conn, sampled_before: float) -> int:
    """Potential cost outside a known account snapshot, never a second read.

    Unknown jobs retain their entire cap. A newly learned or increased terminal
    cost also stays protected; refreshing the same metadata does not create a
    new monetary event. Legacy rows fall back to their last known check time.
    """
    return conn.execute('''SELECT coalesce(sum(coalesce(c.cost,j.cap)),0)
        FROM apify_jobs j LEFT JOIN apify_costs c ON c.run_id=j.run_id
        LEFT JOIN apify_cost_changes d ON d.run_id=c.run_id
        WHERE j.created>=? OR c.run_id IS NULL OR coalesce(d.changed,c.checked,?)>=?''',
        (sampled_before,sampled_before,sampled_before)).fetchone()[0]


def settle(run_id: str, data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    if 'id' in data and (not isinstance(data['id'],str) or data['id']!=run_id):
        return False
    cost = data.get("usageTotalUsd")
    if (data.get("status") not in TERMINAL or not re.fullmatch(r"[A-Za-z0-9]+", run_id)
            or isinstance(cost, bool) or not isinstance(cost, (int, float))
            or not math.isfinite(cost) or cost < 0):
        return False
    micros = int((Decimal(str(cost)) * 1_000_000).to_integral_value(rounding='ROUND_CEILING'))
    conn = connect()
    try:
        ensure_table(conn)
        conn.execute('BEGIN IMMEDIATE')
        old=conn.execute('SELECT cost,checked FROM apify_costs WHERE run_id=?',(run_id,)).fetchone()
        checked=time.time()
        if not old or micros>old[0]:
            conn.execute('''INSERT INTO apify_cost_changes VALUES (?,?) ON CONFLICT(run_id)
                DO UPDATE SET changed=max(apify_cost_changes.changed,excluded.changed)''',(run_id,checked))
        else:
            # Conservative one-time migration. A later unchanged read cannot
            # move an existing monetary timestamp to after the account meter.
            previous=old[1]
            if not isinstance(previous,(int,float)) or not math.isfinite(previous):
                previous=checked
            conn.execute('INSERT OR IGNORE INTO apify_cost_changes VALUES (?,?)',(run_id,previous))
        conn.execute("""INSERT INTO apify_costs VALUES (?,?,?,?) ON CONFLICT(run_id)
            DO UPDATE SET cost=max(apify_costs.cost,excluded.cost),status=excluded.status,checked=excluded.checked""",
                     (run_id, micros, data['status'], checked))
        conn.commit()
    finally:
        conn.close()
    return True


async def reconcile(client, limit: int = 8) -> int:
    """Meter old finished runs using GETs; never launches or retries an Actor."""
    conn = connect()
    try:
        ensure_table(conn)
        period = datetime.now(timezone.utc).strftime('%Y-%m')
        ids = [r[0] for r in conn.execute("""SELECT j.run_id FROM apify_jobs j
            LEFT JOIN apify_costs c ON c.run_id=j.run_id
            WHERE j.done=1 AND j.period=? AND j.run_id IS NOT NULL
              AND (c.run_id IS NULL OR c.checked<?)
            ORDER BY coalesce(c.checked,0),j.created LIMIT ?""",
            (period, time.time()-6*3600, max(0, min(limit, 100))))]
        conn.commit()
    finally:
        conn.close()
    count = 0
    for run_id in ids:
        if not re.fullmatch(r'[A-Za-z0-9]+', run_id):
            continue
        response = await client.get('https://api.apify.com/v2/actor-runs/' + run_id,
                                    headers={'Authorization': 'Bearer ' + Config.APIFY_TOKEN})
        if response.status_code == 200:
            count += int(settle(run_id, response.json().get('data', {})))
    return count
