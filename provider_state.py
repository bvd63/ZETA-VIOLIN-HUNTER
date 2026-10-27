"""Small shared state for additional providers; reserve BEFORE any billable call.

Rolling 31-day caps are conservative even if an account renews on a different
day from the calendar month. These are bot reservations, never account balances.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import time
from database import connect


@contextmanager
def store():
    conn = connect()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS provider_state (key TEXT PRIMARY KEY,value TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS provider_reservations (provider TEXT,created REAL,units INTEGER)")
        conn.execute("CREATE INDEX IF NOT EXISTS provider_reservations_period ON provider_reservations(provider,created)")
        conn.commit()
        yield conn
    finally:
        conn.close()


def read(key: str, default=None):
    with store() as conn:
        row = conn.execute("SELECT value FROM provider_state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default


def write(key: str, value) -> None:
    with store() as conn:
        conn.execute("INSERT OR REPLACE INTO provider_state VALUES (?,?)", (key, json.dumps(value)))
        conn.commit()


def reserve(provider: str, units: int, rolling_limit: int, daily_limit: int, now: float = None) -> bool:
    now = time.time() if now is None else now
    day_start = datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if units <= 0:
        return False
    with store() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rolling = conn.execute("SELECT coalesce(sum(units),0) FROM provider_reservations WHERE provider=? AND created>?",
                               (provider, now - 31 * 86400)).fetchone()[0]
        daily = conn.execute("SELECT coalesce(sum(units),0) FROM provider_reservations WHERE provider=? AND created>=?",
                             (provider, day_start)).fetchone()[0]
        if rolling + units > rolling_limit or daily + units > daily_limit:
            conn.rollback()
            return False
        conn.execute("INSERT INTO provider_reservations VALUES (?,?,?)", (provider, now, units))
        conn.commit()
        return True


def used(provider: str, now: float = None) -> int:
    now = time.time() if now is None else now
    with store() as conn:
        return conn.execute("SELECT coalesce(sum(units),0) FROM provider_reservations WHERE provider=? AND created>?",
                            (provider, now - 31 * 86400)).fetchone()[0]


def reserve_work(provider: str, units: int, rolling_limit: int, daily_limit: int,
                 scheduled_rolling: int, *, auxiliary: bool = False,
                 counters: tuple = (), now: float = None) -> bool:
    """Atomically reserve actual cost, its lane, and optional page counters.

    Aggregate rolling cost never increases. Extra work gets only the surplus
    above the complete 31-day scheduled plan and a separate daily/page lane.
    Untagged historical charges conservatively consume that surplus; no reset,
    refund or reclassification can erase a potentially billed request.
    """
    now = time.time() if now is None else now
    day = datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if units <= 0:
        return False
    with store() as conn:
        conn.execute('BEGIN IMMEDIATE')

        def total(name: str, since: float) -> int:
            return conn.execute('SELECT coalesce(sum(units),0) FROM provider_reservations WHERE provider=? AND created>?',
                                (name, since)).fetchone()[0]

        rolling = total(provider, now - 31 * 86400)
        core_key, aux_key = provider + ':core', provider + ':aux'
        surplus = max(0, rolling_limit - scheduled_rolling)
        if auxiliary:
            lane_used = max(0, rolling - total(core_key, now - 31 * 86400))
            allowed = lane_used + units <= surplus and total(aux_key, day - .001) + units <= daily_limit
        else:
            # Existing untagged calls still count, while tagged extra work has
            # its own daily allowance and cannot consume an evening slot.
            daily = total(provider, day - .001) - total(aux_key, day - .001)
            allowed = daily + units <= daily_limit
        if not allowed or rolling + units > rolling_limit:
            conn.rollback()
            return False
        rows = [(provider, now, units), (aux_key if auxiliary else core_key, now, units)]
        for name, count, rolling_cap, day_cap in counters:
            key = name + ':aux' if auxiliary else name
            if (total(key, now - 31 * 86400) + count > rolling_cap
                    or total(key, day - .001) + count > day_cap):
                conn.rollback()
                return False
            rows.append((key, now, count))
        conn.executemany('INSERT INTO provider_reservations VALUES (?,?,?)', rows)
        conn.commit()
        return True


def claim_window(key: str, seconds: int, now: float = None) -> bool:
    """Atomic persistent run guard, including overlapping/restarted workers."""
    now = time.time() if now is None else now
    with store() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT value FROM provider_state WHERE key=?", (key,)).fetchone()
        if row and now - float(json.loads(row[0])) < seconds:
            conn.rollback()
            return False
        conn.execute("INSERT OR REPLACE INTO provider_state VALUES (?,?)", (key, json.dumps(now)))
        conn.commit()
        return True
