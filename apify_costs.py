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


def spent_micro(conn, period: str) -> int:
    return conn.execute("""SELECT coalesce(sum(coalesce(c.cost,j.cap)),0)
        FROM apify_jobs j LEFT JOIN apify_costs c ON c.run_id=j.run_id
        WHERE j.period=?""", (period,)).fetchone()[0]


def settle(run_id: str, data: dict) -> bool:
    if not isinstance(data, dict):
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
        conn.execute("""INSERT INTO apify_costs VALUES (?,?,?,?) ON CONFLICT(run_id)
            DO UPDATE SET cost=max(apify_costs.cost,excluded.cost),status=excluded.status,checked=excluded.checked""",
                     (run_id, micros, data['status'], time.time()))
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
