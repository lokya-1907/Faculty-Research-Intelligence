"""Second additive test module: route-layer validation, rate limiting, schema
versioning, background batch threading, maintenance scheduling and the frontend
smoke check. Nothing here modifies or replaces the existing suites.
"""
import asyncio
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import pytest

from app.services import batch, extras_db, maintenance, rate_limit


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, 'database_path', str(tmp_path / 'routes.db'))
    monkeypatch.setattr(config.settings, 'auth_required', False)
    from app.services import db
    db.init_db()
    extras_db.init_extras_db()
    rate_limit.reset()
    from app.main import app
    from fastapi.testclient import TestClient
    return TestClient(app)


def _seed(faculty_id='fac_r', author_id='vfstr_r'):
    from app.services import db
    db.upsert_faculty({'id': faculty_id, 'name': 'Dr. Route Author',
                       'institution': 'Vignan University', 'department': 'CSE'})
    db.upsert_vfstr_author({'id': author_id, 'faculty_id': faculty_id, 'name': 'Dr. Route Author',
                            'institution': 'Vignan University', 'department': 'CSE',
                            'source': 'Official VFSTR CSE faculty directory',
                            'source_url': 'https://vignan.ac.in/official', 'verified': True})
    return faculty_id, author_id


# ------------------------------------------------------------- route layer

def test_export_rejects_unknown_format(client):
    _seed()
    assert client.get('/api/v1/exports/directory?format=pdf').status_code == 422
    assert client.get('/api/v1/exports/nope?format=csv').status_code == 404


def test_export_filename_and_row_headers(client):
    _seed()
    response = client.get('/api/v1/exports/directory?format=csv')
    assert response.status_code == 200
    disposition = response.headers['content-disposition']
    assert re.search(r'filename="directory-\d{8}-\d{4}\.csv"', disposition)
    assert response.headers['x-total-rows'] == '1'
    assert response.headers['x-exported-rows'] == '1'


def test_export_pagination_windows_rows(client):
    from app.services import db
    for index in range(5):
        db.upsert_faculty({'id': f'fac_p{index}', 'name': f'Dr. Page {index}',
                           'institution': 'Vignan University'})
        db.upsert_vfstr_author({'id': f'vfstr_p{index}', 'faculty_id': f'fac_p{index}',
                                'name': f'Dr. Page {index}', 'institution': 'Vignan University',
                                'source': 'Official VFSTR CSE faculty directory',
                                'source_url': 'https://vignan.ac.in/official', 'verified': True})
    page = client.get('/api/v1/exports/directory?format=csv&page=2&page_size=2')
    assert page.headers['x-total-rows'] == '5'
    assert page.headers['x-exported-rows'] == '2'
    body = page.content.decode('utf-8-sig').splitlines()
    assert len(body) == 3  # header + 2 rows


def test_review_queue_paginates_and_reports_totals(client):
    from app.services import db
    rows = [{'spreadsheet_row': index, 'spreadsheet_name': f'Name {index}',
             'possible_candidates': []} for index in range(1, 8)]
    db.save_vfstr_metric_import_report('https://example.test/sheet', '2026-04', {
        'snapshot_date': '2026-04', 'matched': [], 'ambiguous_matches': [],
        'unmatched_spreadsheet_records': rows,
    })
    first = client.get('/api/v1/review/queue?page=1&page_size=3').json()
    assert first['total'] == 7 and first['pages'] == 3 and len(first['rows']) == 3
    third = client.get('/api/v1/review/queue?page=3&page_size=3').json()
    assert len(third['rows']) == 1
    everything = client.get('/api/v1/review/queue').json()
    assert len(everything['rows']) == 7 and everything['page_size'] == 0


def test_review_queue_rejects_bad_page_bounds(client):
    assert client.get('/api/v1/review/queue?page=0').status_code == 422
    assert client.get('/api/v1/review/queue?page_size=9999').status_code == 422


def test_compare_caps_subject_count(client):
    _seed()
    ids = ','.join(f'fac_{index}' for index in range(7))
    response = client.get(f'/api/v1/analytics/compare?faculty_ids={ids}')
    assert response.status_code == 400
    assert 'At most 6' in response.json()['detail']


def test_analytics_unknown_faculty_returns_404(client):
    assert client.get('/api/v1/analytics/source-comparison/missing').status_code == 404
    assert client.get('/api/v1/analytics/dedupe/missing').status_code == 404


def test_batch_run_404_for_unknown_id(client):
    assert client.get('/api/v1/sync/batch/999999').status_code == 404


# ------------------------------------------------------------ rate limiting

def test_batch_sync_is_rate_limited(client, monkeypatch):
    faculty_id, _ = _seed()
    monkeypatch.setattr(batch.service, 'sync',
                        lambda fid: {'status': 'ok', 'changed': [], 'message': 'done', 'statuses': {}})
    limit = rate_limit.batch_sync_limit.max_calls
    for _ in range(limit):
        assert client.post('/api/v1/sync/batch?background=false',
                           json={'faculty_ids': [faculty_id]}).status_code == 200
    blocked = client.post('/api/v1/sync/batch?background=false', json={'faculty_ids': [faculty_id]})
    assert blocked.status_code == 429
    assert 'Retry-After' in blocked.headers
    assert 'Rate limit reached' in blocked.json()['detail']


def test_backup_is_rate_limited(client):
    _seed()
    for _ in range(rate_limit.backup_limit.max_calls):
        assert client.post('/api/v1/maintenance/backup').status_code == 200
    assert client.post('/api/v1/maintenance/backup').status_code == 429


def test_rate_limit_reports_state(client):
    rate_limit.export_limit.check('unit')
    status = client.get('/api/v1/maintenance/status').json()
    names = {entry['name'] for entry in status['rate_limits']}
    assert 'export' in names


# --------------------------------------------------------- schema versioning

def test_schema_version_reports_applied_and_pending(client):
    version = extras_db.schema_version()
    assert version['current'] == version['latest_known']
    assert version['pending'] == []
    assert {entry['version'] for entry in version['applied']} == {1, 2}


def test_schema_version_is_idempotent(client):
    before = extras_db.schema_version()['current']
    extras_db.init_extras_db()
    extras_db.init_extras_db()
    after = extras_db.schema_version()
    assert after['current'] == before
    assert len(after['applied']) == len({entry['version'] for entry in after['applied']})


def test_maintenance_status_exposes_schema(client):
    status = client.get('/api/v1/maintenance/status').json()
    assert status['schema']['current'] >= 1


# ------------------------------------------------------- background batches

def test_background_batch_runs_and_records_run(client, monkeypatch):
    faculty_id, _ = _seed()
    monkeypatch.setattr(batch.service, 'sync',
                        lambda fid: {'status': 'ok', 'changed': [], 'message': 'done', 'statuses': {}})
    started = client.post('/api/v1/sync/batch', json={'faculty_ids': [faculty_id]})
    assert started.status_code == 200
    assert started.json()['started'] is True
    for _ in range(50):
        status = client.get('/api/v1/sync/batch/status').json()
        if not status['running']:
            break
        time.sleep(0.05)
    assert status['running'] is False
    assert status['runs'][0]['status'] == 'ok'
    assert status['runs'][0]['succeeded'] == 1


def test_second_concurrent_batch_is_rejected(client, monkeypatch):
    faculty_id, _ = _seed()
    release = threading.Event()

    def slow_sync(fid):
        release.wait(timeout=5)
        return {'status': 'ok', 'changed': [], 'message': 'done', 'statuses': {}}

    monkeypatch.setattr(batch.service, 'sync', slow_sync)
    first = client.post('/api/v1/sync/batch', json={'faculty_ids': [faculty_id]})
    assert first.status_code == 200
    second = client.post('/api/v1/sync/batch', json={'faculty_ids': [faculty_id]})
    assert second.status_code == 409
    assert 'already running' in second.json()['detail']
    release.set()
    for _ in range(50):
        if not client.get('/api/v1/sync/batch/status').json()['running']:
            break
        time.sleep(0.05)


# ------------------------------------------------------------ maintenance

def test_periodic_backup_runs_then_cancels_cleanly(tmp_path, monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, 'database_path', str(tmp_path / 'm.db'))
    from app.services import db
    db.init_db()
    extras_db.init_extras_db()
    monkeypatch.setattr(maintenance, 'interval_hours', lambda: 0.0001)
    calls = {'n': 0}

    def counted():
        calls['n'] += 1
        return {'status': 'ok', 'backup': 'x', 'pruned': []}

    monkeypatch.setattr(maintenance, 'run_backup_once', counted)

    async def scenario():
        task = asyncio.create_task(maintenance.periodic_backup())
        # Poll instead of a fixed sleep: under load the loop may not have
        # scheduled the first tick yet when a fixed sleep expires.
        for _ in range(100):
            if calls['n']:
                break
            await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return task

    task = asyncio.run(scenario())
    assert calls['n'] >= 1
    assert task.cancelled()


def test_run_backup_once_reports_failure_without_raising(monkeypatch):
    def boom():
        raise RuntimeError('disk full')

    monkeypatch.setattr(extras_db, 'backup_database', boom)
    result = maintenance.run_backup_once()
    assert result['status'] == 'error'
    assert 'disk full' in result['error']


def test_backup_failure_is_audited(tmp_path, monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, 'database_path', str(tmp_path / 'a.db'))
    from app.services import db
    db.init_db()
    extras_db.init_extras_db()
    monkeypatch.setattr(extras_db, 'backup_database',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))
    maintenance.run_backup_once()
    actions = [entry['action'] for entry in extras_db.audit_entries()]
    assert 'backup_failed' in actions


# ------------------------------------------------------------- frontend smoke

REPO = Path(__file__).resolve().parents[1]
FRONTEND = REPO / 'frontend'
NODE_CANDIDATES = [
    os.getenv('NODE_BINARY', ''),
    r'C:\Program Files\nodejs\node.exe',
    'node',
]


def _node_binary():
    for candidate in NODE_CANDIDATES:
        if not candidate:
            continue
        if candidate == 'node':
            return candidate
        if Path(candidate).exists():
            return candidate
    return None


@pytest.mark.skipif(_node_binary() is None, reason='node is not available')
def test_frontend_renders_every_route_without_a_reference_error():
    """Renders App with react-dom/server for each route.

    This is the check that would have caught the blank profile page caused by a
    component being used but never defined.
    """
    if not (FRONTEND / 'node_modules').exists():
        pytest.skip('frontend dependencies are not installed')
    script = FRONTEND / 'scripts' / 'smoke.mjs'
    if not script.exists():
        pytest.skip('frontend smoke script is missing')
    result = subprocess.run(
        [_node_binary(), str(script)],
        cwd=str(FRONTEND), capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f'smoke render failed:\n{result.stdout}\n{result.stderr}'
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload['ok'] is True, payload
    assert payload['routes'] >= 4
