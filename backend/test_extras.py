"""Tests for the additive feature set (exports, analytics, batch sync, review,
resilient HTTP). These do not touch or replace the existing test suite.
"""
import json
import sqlite3

import httpx
import pytest

from app.services import analytics, batch, extras_db, http_client, review


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, 'database_path', str(tmp_path / 'extras.db'))
    monkeypatch.setattr(config.settings, 'auth_required', False)
    from app.services import db
    db.init_db()
    extras_db.init_extras_db()
    from app.main import app
    from fastapi.testclient import TestClient
    return TestClient(app)


def _seed_directory(faculty_id='fac_vfstr_test', author_id='vfstr_test'):
    from app.services import db
    db.upsert_faculty({
        'id': faculty_id, 'name': 'Dr. Test Author', 'institution': 'Vignan University',
        'department': 'CSE', 'designation': 'Professor (CSE)',
    })
    db.upsert_vfstr_author({
        'id': author_id, 'faculty_id': faculty_id, 'name': 'Dr. Test Author',
        'institution': 'Vignan University', 'department': 'CSE',
        'designation': 'Professor (CSE)', 'source': 'Official VFSTR CSE faculty directory',
        'source_url': 'https://vignan.ac.in/official', 'verified': True,
    })
    return faculty_id, author_id


# ------------------------------------------------------------------ exports

def test_directory_export_csv_and_xlsx(client):
    _seed_directory()
    csv_response = client.get('/api/v1/exports/directory?format=csv')
    assert csv_response.status_code == 200
    assert 'text/csv' in csv_response.headers['content-type']
    assert 'attachment; filename="directory-' in csv_response.headers['content-disposition']
    body = csv_response.content.decode('utf-8-sig')
    assert 'Name,Designation,Department' in body.splitlines()[0]
    assert 'Dr. Test Author' in body

    xlsx = client.get('/api/v1/exports/directory?format=xlsx')
    assert xlsx.status_code == 200
    assert xlsx.content[:2] == b'PK'  # valid zip container


def test_export_catalogue_and_unknown_dataset(client):
    catalogue = client.get('/api/v1/exports')
    assert catalogue.status_code == 200
    assert {item['id'] for item in catalogue.json()['datasets']} == {'directory', 'metrics', 'publications'}
    assert client.get('/api/v1/exports/nonsense').status_code == 404


def test_export_log_records_rows(client):
    _seed_directory()
    client.get('/api/v1/exports/directory?format=csv')
    assert extras_db.recent_exports()[0]['dataset'] == 'directory'


# ------------------------------------------------------------------ analytics

def test_ranking_percentile_and_missing_exclusion(client):
    _seed_directory()
    from app.services import db
    db.upsert_faculty({'id': 'fac_b', 'name': 'Dr. Second Author', 'institution': 'Vignan University'})
    db.upsert_vfstr_author({
        'id': 'vfstr_b', 'faculty_id': 'fac_b', 'name': 'Dr. Second Author',
        'institution': 'Vignan University', 'source': 'Official VFSTR CSE faculty directory',
        'source_url': 'https://vignan.ac.in/official', 'verified': True,
    })
    # Metric columns are written by the import path, which is what this exercises.
    db.update_vfstr_research_data('vfstr_b', {'scopus_citations': 50, 'metrics_date': '2026-04'})
    db.update_vfstr_research_data('vfstr_test', {'scopus_citations': 10, 'metrics_date': '2026-04'})
    result = client.get('/api/v1/analytics/ranking?sort=scopus_citations').json()
    assert [entry['name'] for entry in result['entries']] == ['Dr. Second Author', 'Dr. Test Author']
    assert result['entries'][0]['percentile'] == 100.0
    assert result['entries'][1]['percentile'] == 50.0
    assert 'not directly comparable' in result['note']


def test_ranking_rejects_unknown_sort(client):
    assert client.get('/api/v1/analytics/ranking?sort=bogus').status_code == 400


def test_compare_reports_delta_and_missing(client):
    _seed_directory()
    result = client.get('/api/v1/analytics/compare?faculty_ids=fac_vfstr_test,missing_id').json()
    assert len(result['subjects']) == 1
    assert result['missing_faculty_ids'] == ['missing_id']


def test_dedupe_groups_same_work_across_sources(client):
    faculty_id, _ = _seed_directory()
    from app.services import db
    db.replace_publications(faculty_id, [
        {'id': 'scopus:1', 'title': 'A Study of Things', 'year': 2020, 'citations': 5, 'source': 'scopus', 'doi': '10.1/x'},
        {'id': 'scholar:1', 'title': 'A study of things', 'year': 2020, 'citations': 7, 'source': 'google_scholar', 'doi': '10.1/X'},
    ])
    result = client.get(f'/api/v1/analytics/dedupe/{faculty_id}').json()
    assert result['total_publications'] == 2
    assert result['unique_works'] == 1
    assert result['duplicate_works'] == 1


def test_cohort_counts_missing_separately_from_zero(client):
    _seed_directory()
    summary = client.get('/api/v1/analytics/cohort').json()
    assert summary['metrics']['scopus_citations']['missing'] == 1
    assert summary['metrics']['scopus_citations']['median'] is None


# ---------------------------------------------------------------- batch sync

def test_batch_sync_records_run_and_failures(client, monkeypatch):
    faculty_id, _ = _seed_directory()
    monkeypatch.setattr(batch.service, 'sync', lambda fid: {'status': 'ok', 'changed': [], 'message': 'done', 'statuses': {}})
    result = client.post('/api/v1/sync/batch?background=false', json={'faculty_ids': [faculty_id]}).json()
    assert result['status'] == 'ok'
    assert result['succeeded'] == 1
    run = client.get(f"/api/v1/sync/batch/{result['run_id']}").json()
    assert run['status'] == 'ok'
    assert run['detail']['requested'] == 1


def test_batch_sync_records_exception_instead_of_swallowing(client, monkeypatch):
    faculty_id, _ = _seed_directory()

    def boom(fid):
        raise RuntimeError('provider exploded')

    monkeypatch.setattr(batch.service, 'sync', boom)
    result = client.post('/api/v1/sync/batch?background=false', json={'faculty_ids': [faculty_id]}).json()
    assert result['status'] == 'error'
    assert result['failed'] == 1
    assert result['results'][0]['message'] == 'provider exploded'
    failures = client.get('/api/v1/sync/failures').json()
    assert failures['last_failure'] is not None
    assert any(entry['source'] == 'batch_sync' for entry in failures['recent_errors'])


# ------------------------------------------------------------------- review

def test_review_queue_empty_without_report(client):
    result = client.get('/api/v1/review/queue').json()
    assert result['available'] is False
    assert result['rows'] == []


def test_review_apply_requires_suggestion_or_force(client):
    faculty_id, author_id = _seed_directory()
    from app.services import db
    db.save_vfstr_metric_import_report('https://example.test/sheet', '2026-04', {
        'snapshot_date': '2026-04',
        'matched': [],
        'ambiguous_matches': [],
        'unmatched_spreadsheet_records': [{
            'spreadsheet_row': 9,
            'spreadsheet_name': 'Prof. Someone Else',
            'possible_candidates': ['Dr. Other Person'],
        }],
        'unmatched_row_values': [{
            'spreadsheet_row': 9,
            'row': {'scopus_citations': 11, 'scopus_h_index': 3, 'google_scholar_citations': 20,
                    'google_scholar_h_index': 5, 'google_scholar_i10': 2, 'data_status': 'Updated'},
        }],
    })
    guard = client.post('/api/v1/review/apply', json={'spreadsheet_row': 9, 'vfstr_author_id': author_id})
    assert guard.status_code == 400
    assert 'not one of the suggested candidates' in guard.json()['detail']

    forced = client.post('/api/v1/review/apply',
                         json={'spreadsheet_row': 9, 'vfstr_author_id': author_id, 'force': True})
    assert forced.status_code == 200
    assert forced.json()['official_name'] == 'Dr. Test Author'

    queue = client.get('/api/v1/review/queue').json()
    assert queue['counts'] == {'total_unmatched': 1, 'pending': 0, 'linked': 1,
                               'with_suggestions': 1, 'without_suggestions': 0, 'ambiguous': 0}

    undone = client.post('/api/v1/review/undo', json={'spreadsheet_row': 9})
    assert undone.status_code == 200
    assert extras_db.manual_links() == []


def test_review_apply_rejects_row_outside_queue(client):
    _seed_directory()
    from app.services import db
    db.save_vfstr_metric_import_report('https://example.test/sheet', '2026-04',
                                       {'snapshot_date': '2026-04', 'unmatched_spreadsheet_records': [],
                                        'matched': [], 'ambiguous_matches': []})
    response = client.post('/api/v1/review/apply', json={'spreadsheet_row': 1, 'vfstr_author_id': 'vfstr_test'})
    assert response.status_code == 400


# ---------------------------------------------------------------- http client

class _FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError('boom', request=None, response=None)

    def json(self):
        return self._payload


class _FakeTransport:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        status = self.statuses[min(self.calls - 1, len(self.statuses) - 1)]
        if isinstance(status, Exception):
            raise status
        return _FakeResponse(status, {'ok': True})


def test_http_client_retries_transient_then_succeeds():
    transport = _FakeTransport([503, 429, 200])
    payload = http_client.get_json('https://example.test', provider='unit-retry',
                                   transport=transport, backoff=0.001, max_backoff=0.002)
    assert payload == {'ok': True}
    assert transport.calls == 3


def test_http_client_gives_up_and_reports_attempts():
    transport = _FakeTransport([503, 503, 503, 503])
    with pytest.raises(http_client.RetryExhausted) as error:
        http_client.get_json('https://example.test', provider='unit-fail',
                             transport=transport, attempts=3, backoff=0.001, max_backoff=0.002)
    assert error.value.attempts == 3
    assert transport.calls == 3


def test_http_client_opens_circuit_after_repeated_failures():
    provider = 'unit-breaker'
    transport = _FakeTransport([500] * 20)
    for _ in range(http_client.BREAKER_THRESHOLD):
        with pytest.raises(http_client.RetryExhausted):
            http_client.get_json('https://example.test', provider=provider,
                                 transport=transport, attempts=1, backoff=0.001)
    with pytest.raises(http_client.CircuitOpen):
        http_client.get_json('https://example.test', provider=provider,
                             transport=transport, attempts=1, backoff=0.001)
    states = {state['provider']: state for state in http_client.breaker_states()}
    assert states[provider]['open'] is True
    assert states[provider]['retry_after_seconds'] > 0


# -------------------------------------------------------------- maintenance

def test_backup_and_status_endpoints(client):
    _seed_directory()
    backup = client.post('/api/v1/maintenance/backup')
    assert backup.status_code == 200
    assert backup.json()['backup'].endswith('.db')
    status = client.get('/api/v1/maintenance/status').json()
    assert status['database']['integrity'] == 'ok'
    assert status['database']['faculty'] >= 1


def test_audit_log_records_manual_link(client):
    faculty_id, author_id = _seed_directory()
    from app.services import db
    db.save_vfstr_metric_import_report('https://example.test/sheet', '2026-04', {
        'snapshot_date': '2026-04', 'matched': [], 'ambiguous_matches': [],
        'unmatched_spreadsheet_records': [{
            'spreadsheet_row': 3, 'spreadsheet_name': 'Dr. Test Author',
            'possible_candidates': ['Dr. Test Author'],
        }],
        'unmatched_row_values': [{'spreadsheet_row': 3, 'row': {'scopus_citations': 4}}],
    })
    client.post('/api/v1/review/apply', json={'spreadsheet_row': 3, 'vfstr_author_id': author_id,
                                              'reviewed_by': 'tester'})
    actions = [entry['action'] for entry in client.get('/api/v1/audit').json()['entries']]
    assert 'manual_link' in actions
