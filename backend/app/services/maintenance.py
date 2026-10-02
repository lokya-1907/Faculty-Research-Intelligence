"""Scheduled maintenance: periodic SQLite backups with retention.

Started from main.py's lifespan as a background task. Uses the online backup
API (extras_db.backup_database) so an active reader/writer cannot corrupt it.
"""
import asyncio
import logging

from app.core.config import settings
from app.services import extras_db

logger = logging.getLogger('researchpulse.maintenance')

DEFAULT_INTERVAL_HOURS = 24
DEFAULT_RETENTION = 14


def interval_hours():
    import os
    try:
        return max(1, int(os.getenv('BACKUP_INTERVAL_HOURS', str(DEFAULT_INTERVAL_HOURS))))
    except ValueError:
        return DEFAULT_INTERVAL_HOURS


def retention():
    import os
    try:
        return max(1, int(os.getenv('BACKUP_RETENTION', str(DEFAULT_RETENTION))))
    except ValueError:
        return DEFAULT_RETENTION


def run_backup_once():
    try:
        path = extras_db.backup_database()
        pruned = extras_db.prune_backups(keep=retention())
        logger.info('Database backup written to %s (pruned %s old file(s)).', path, len(pruned))
        return {'status': 'ok', 'backup': path, 'pruned': pruned}
    except Exception as error:
        logger.exception('Scheduled database backup failed.')
        extras_db.log_audit('backup_failed', 'database', None, 'scheduler', {'error': str(error)})
        return {'status': 'error', 'error': str(error)}


async def periodic_backup():
    """Back up on a fixed interval; failures are logged, never raised."""
    while True:
        await asyncio.sleep(interval_hours() * 3600)
        run_backup_once()
