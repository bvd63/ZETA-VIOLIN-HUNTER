"""SQLite online snapshots, isolated restore verification and public readiness."""
import asyncio
import gzip
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config import Config
from provider_state import read, write

log = logging.getLogger(__name__)


def readiness(last_cycle, now=None):
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo(Config.SEARCH_TIMEZONE))
    hours = [int(h.strip()) for h in Config.SEARCH_HOURS.split(',') if h.strip().isdigit()]
    slots = [local.replace(hour=h, minute=0, second=0, microsecond=0) - timedelta(days=day)
             for day in (0, 1) for h in hours]
    expected = max(slot for slot in slots if slot <= local)
    if last_cycle and last_cycle.tzinfo is None:
        last_cycle = last_cycle.replace(tzinfo=timezone.utc)
    healthy = bool(last_cycle and last_cycle <= now + timedelta(minutes=5) and
                   (last_cycle >= expected.astimezone(timezone.utc) - timedelta(minutes=5) or
                    local < expected + timedelta(minutes=90)))
    return {'status': 'ready' if healthy else 'search_stale'}


def verify_restore(archive):
    """Restore into an isolated directory. Never opens the live DB for writing."""
    with tempfile.TemporaryDirectory(prefix='zeta-restore-') as temp:
        restored = Path(temp) / 'restored.db'
        with gzip.open(archive, 'rb') as source, restored.open('wb') as dest:
            shutil.copyfileobj(source, dest)
        with sqlite3.connect(restored.as_uri() + '?mode=ro', uri=True) as conn:
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('SQLite restore failed integrity check')
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            counts = {name: conn.execute('SELECT count(*) FROM "' + name.replace('"','""') + '"').fetchone()[0]
                      for name in tables}
        return counts


def create_backup(db_path=None, directory=None, now=None, keep=10):
    """Consistent WAL-safe backup; validate before atomic publication/retention."""
    db_path = Path(db_path or Config.DB_PATH).resolve()
    if not db_path.is_file():
        raise FileNotFoundError('Database does not exist')
    directory = Path(directory or db_path.parent / 'backups')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    now = now or datetime.now(timezone.utc)
    target = directory / (now.strftime('zeta-%Y%m%dT%H%M%S%fZ') + '.db.gz')
    with tempfile.TemporaryDirectory(prefix='.snapshot-', dir=directory) as temp:
        snapshot = Path(temp) / 'snapshot.db'
        with sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True, timeout=30) as source:
            with sqlite3.connect(snapshot) as dest:
                source.backup(dest, pages=256, sleep=0.05)
        packed = Path(temp) / 'snapshot.db.gz'
        with snapshot.open('rb') as source, gzip.open(packed, 'wb') as dest:
            shutil.copyfileobj(source, dest)
        counts = verify_restore(packed)
        os.chmod(packed, 0o600)
        os.replace(packed, target)
    for old in sorted(directory.glob('zeta-*.db.gz'), reverse=True)[max(1, keep):]:
        old.unlink()
    return {'file': target.name, 'bytes': target.stat().st_size,
            'tables': len(counts), 'rows': sum(counts.values()), 'checked': now.timestamp()}


async def backup_tick():
    import time
    previous = read('operations:backup', {})
    if previous.get('checked', 0) > time.time() - 23 * 3600 and not previous.get('error'):
        return
    try:
        result = await asyncio.to_thread(create_backup)
        write('operations:backup', result)
        log.info('SQLite backup and isolated restore verified (%s tables, %s bytes)',
                 result['tables'], result['bytes'])
    except Exception as exc:
        previous.update(error=type(exc).__name__, failed_at=time.time())
        write('operations:backup', previous)
        log.warning('SQLite backup failed (%s)', type(exc).__name__)
