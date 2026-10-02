"""Additive batch sync with progress tracking and an explicit failure surface.

Reuses service.sync unchanged, but never swallows errors: every outcome is
recorded in sync_runs / sync_log so a failed background refresh is visible.
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from app.services import db, extras_db, service

logger = logging.getLogger('researchpulse.batch')

MAX_BATCH = 500
STALE_AFTER_HOURS = 24
_run_lock = threading.Lock()
_active = {'running': False, 'run_id': None}


def _target_ids(faculty_ids=None, only_with_scholar=False, only_never_synced=False):
    if faculty_ids:
        return [fid for fid in dict.fromkeys(faculty_ids) if fid][:MAX_BATCH]
    if only_with_scholar or only_never_synced:
        rows, _ = db.vfstr_authors(limit=10000, offset=0)
        ids = []
        for row in rows:
            if only_with_scholar and not row.get('google_scholar_url'):
                continue
            faculty_id = row.get('faculty_id')
            if not faculty_id or not db.get_faculty(faculty_id):
                continue
            if only_never_synced and db.latest_metrics(faculty_id):
                continue
            ids.append(faculty_id)
        return ids[:MAX_BATCH]
    return db.all_faculty_ids()[:MAX_BATCH]


def sync_many(faculty_ids=None, only_with_scholar=False, only_never_synced=False,
              actor=None, kind='batch_sync'):
    """Synchronise many profiles, recording one sync_run for the whole batch."""
    targets = _target_ids(faculty_ids, only_with_scholar, only_never_synced)
    run_id = extras_db.start_sync_run(kind, requested=len(targets), message='Batch synchronization started.')
    started = time.perf_counter()
    results = []
    succeeded = failed = 0
    for faculty_id in targets:
        faculty = db.get_faculty(faculty_id)
        name = faculty['name'] if faculty else faculty_id
        try:
            outcome = service.sync(faculty_id)
            status = outcome.get('status', 'ok')
            if status == 'ok':
                succeeded += 1
            else:
                failed += 1
            results.append({
                'faculty_id': faculty_id, 'name': name, 'status': status,
                'changed': outcome.get('changed', []),
                'message': outcome.get('message'),
                'statuses': outcome.get('statuses', {}),
            })
        except Exception as error:  # recorded, never swallowed
            failed += 1
            logger.exception('batch sync failed for faculty_id=%s', faculty_id)
            db.log_sync(faculty_id, 'batch_sync', 'error', str(error))
            results.append({
                'faculty_id': faculty_id, 'name': name, 'status': 'error',
                'changed': [], 'message': str(error), 'statuses': {},
            })

    duration = round(time.perf_counter() - started, 2)
    status = 'ok' if failed == 0 else ('partial' if succeeded else 'error')
    summary = {
        'requested': len(targets),
        'succeeded': succeeded,
        'failed': failed,
        'skipped': 0,
        'duration_seconds': duration,
        'results': results,
    }
    extras_db.finish_sync_run(
        run_id, status, succeeded=succeeded, failed=failed, detail=summary,
        message=f'Batch synchronization finished in {duration}s: {succeeded} succeeded, {failed} failed.',
    )
    extras_db.log_audit('batch_sync', 'sync_run', str(run_id), actor, summary)
    return {'run_id': run_id, 'status': status, **summary}


def sync_many_background(faculty_ids=None, only_with_scholar=False,
                         only_never_synced=False, actor=None):
    """Kick off a batch in a daemon thread so HTTP callers are not blocked."""
    with _run_lock:
        if _active['running']:
            raise RuntimeError('A batch synchronization is already running.')
        _active['running'] = True
        _active['run_id'] = None

    def worker():
        try:
            outcome = sync_many(faculty_ids, only_with_scholar, only_never_synced, actor)
            _active['run_id'] = outcome['run_id']
        except Exception:
            logger.exception('background batch sync crashed')
        finally:
            _active['running'] = False

    thread = threading.Thread(target=worker, name='researchpulse-batch-sync', daemon=True)
    thread.start()
    return {
        'started': True,
        'requested': len(_target_ids(faculty_ids, only_with_scholar, only_never_synced)),
    }


def batch_status():
    runs = extras_db.sync_runs(limit=10)
    return {
        'running': _active['running'],
        'run_id': _active['run_id'],
        'runs': runs,
        'latest': runs[0] if runs else None,
    }


def sync_failures():
    """Everything the periodic job would otherwise hide."""
    summary = extras_db.sync_failure_summary()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=STALE_AFTER_HOURS)).isoformat(timespec='seconds')
    with db.conn() as connection:
        stale = connection.execute(
            '''SELECT f.id, f.name, MAX(m.captured_at) last_capture
               FROM faculty f LEFT JOIN metrics m ON m.faculty_id=f.id
               GROUP BY f.id
               HAVING last_capture IS NULL OR last_capture < ?
               ORDER BY last_capture IS NOT NULL, lower(f.name) LIMIT 50''',
            (cutoff,),
        ).fetchall()
    summary['never_or_stale'] = [dict(row) for row in stale]
    summary['stale_after_hours'] = STALE_AFTER_HOURS
    summary['stale_cutoff'] = cutoff
    return summary
