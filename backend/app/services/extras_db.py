"""Additive database layer.

Creates only new tables (sync_runs, manual_metric_links, audit_log, export_log)
and reads existing ones. It never alters or drops anything created by db.py, so
the existing application behaviour is untouched.
"""
import csv
import io
import json
import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.services import db

logger = logging.getLogger('researchpulse.extras')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _backend_root():
    """Directory that holds the backend package (and therefore data/, logs/, backups/)."""
    return Path(__file__).resolve().parents[2]


def init_extras_db():
    with db.conn() as connection:
        connection.executescript('''
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          applied_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sync_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          kind TEXT NOT NULL,
          status TEXT NOT NULL,
          started_at TEXT NOT NULL,
          finished_at TEXT,
          requested INTEGER DEFAULT 0,
          succeeded INTEGER DEFAULT 0,
          failed INTEGER DEFAULT 0,
          skipped INTEGER DEFAULT 0,
          detail_json TEXT,
          message TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sync_runs_started ON sync_runs(started_at DESC);

        CREATE TABLE IF NOT EXISTS manual_metric_links (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          spreadsheet_row INTEGER,
          spreadsheet_name TEXT NOT NULL,
          vfstr_author_id TEXT NOT NULL,
          official_name TEXT,
          snapshot_date TEXT,
          metrics_json TEXT,
          status TEXT NOT NULL DEFAULT 'linked',
          reviewed_by TEXT,
          created_at TEXT NOT NULL,
          UNIQUE(spreadsheet_row, vfstr_author_id)
        );

        CREATE TABLE IF NOT EXISTS audit_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          action TEXT NOT NULL,
          entity TEXT,
          entity_id TEXT,
          actor TEXT,
          detail TEXT,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);

        CREATE TABLE IF NOT EXISTS export_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          dataset TEXT NOT NULL,
          row_count INTEGER DEFAULT 0,
          actor TEXT,
          created_at TEXT NOT NULL
        );
        ''')
    record_migration(1, 'additive_extended_tables')
    record_migration(2, 'upsert_vfstr_author_persists_metric_columns')


# Ordered, append-only migration log. Each entry is a schema change that has
# already been applied idempotently above; recording it makes the database's
# current version explicit instead of inferred.
MIGRATIONS = [
    (1, 'additive_extended_tables'),
    (2, 'upsert_vfstr_author_persists_metric_columns'),
]


def record_migration(version, name):
    with db.conn() as connection:
        connection.execute(
            'INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) VALUES(?,?,?)',
            (version, name, now()),
        )


def schema_version():
    with db.conn() as connection:
        row = connection.execute('SELECT MAX(version) value FROM schema_migrations').fetchone()
        applied = connection.execute(
            'SELECT version,name,applied_at FROM schema_migrations ORDER BY version'
        ).fetchall()
    return {
        'current': (row['value'] if row and row['value'] is not None else 0),
        'latest_known': MIGRATIONS[-1][0] if MIGRATIONS else 0,
        'applied': [dict(entry) for entry in applied],
        'pending': [
            {'version': version, 'name': name}
            for version, name in MIGRATIONS
            if version not in {entry['version'] for entry in applied}
        ],
    }


# --------------------------------------------------------------------------- sync runs

def start_sync_run(kind, requested=0, message=None):
    with db.conn() as connection:
        cursor = connection.execute(
            'INSERT INTO sync_runs(kind,status,started_at,requested,message) VALUES(?,?,?,?,?)',
            (kind, 'running', now(), requested, message),
        )
        return cursor.lastrowid


def finish_sync_run(run_id, status, succeeded=0, failed=0, skipped=0, detail=None, message=None):
    with db.conn() as connection:
        connection.execute(
            'UPDATE sync_runs SET status=?,finished_at=?,succeeded=?,failed=?,skipped=?,detail_json=?,message=? WHERE id=?',
            (status, now(), succeeded, failed, skipped,
             json.dumps(detail, ensure_ascii=False) if detail is not None else None, message, run_id),
        )


def _run_record(row):
    record = dict(row)
    raw = record.pop('detail_json', None)
    try:
        record['detail'] = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        record['detail'] = None
    return record


def sync_runs(limit=20):
    with db.conn() as connection:
        rows = connection.execute(
            'SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?', (limit,)
        ).fetchall()
    return [_run_record(row) for row in rows]


def sync_run(run_id):
    with db.conn() as connection:
        row = connection.execute('SELECT * FROM sync_runs WHERE id=?', (run_id,)).fetchone()
    return _run_record(row) if row else None


def sync_failure_summary():
    """Surface silent background failures: last success, last failure, open breakers."""
    from app.services import http_client
    with db.conn() as connection:
        last_success = connection.execute(
            "SELECT * FROM sync_runs WHERE status IN ('ok','partial') ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_failure = connection.execute(
            "SELECT * FROM sync_runs WHERE status IN ('error','failed') ORDER BY id DESC LIMIT 1"
        ).fetchone()
        failing_sources = connection.execute(
            """SELECT source, COUNT(*) failures, MAX(synced_at) last_at
               FROM sync_log WHERE status='error'
               GROUP BY source ORDER BY failures DESC LIMIT 10"""
        ).fetchall()
        recent_errors = connection.execute(
            """SELECT faculty_id, source, message, synced_at FROM sync_log
               WHERE status='error' ORDER BY id DESC LIMIT 20"""
        ).fetchall()
    return {
        'last_success': _run_record(last_success) if last_success else None,
        'last_failure': _run_record(last_failure) if last_failure else None,
        'failing_sources': [dict(row) for row in failing_sources],
        'recent_errors': [dict(row) for row in recent_errors],
        'circuit_breakers': http_client.breaker_states(),
    }


# --------------------------------------------------------------------------- manual links

def upsert_manual_link(spreadsheet_row, spreadsheet_name, vfstr_author_id,
                       official_name=None, snapshot_date=None, metrics=None,
                       status='linked', reviewed_by=None):
    with db.conn() as connection:
        connection.execute(
            '''INSERT INTO manual_metric_links(
                 spreadsheet_row,spreadsheet_name,vfstr_author_id,official_name,
                 snapshot_date,metrics_json,status,reviewed_by,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(spreadsheet_row,vfstr_author_id) DO UPDATE SET
                 spreadsheet_name=excluded.spreadsheet_name,
                 official_name=excluded.official_name,
                 snapshot_date=excluded.snapshot_date,
                 metrics_json=excluded.metrics_json,
                 status=excluded.status,
                 reviewed_by=excluded.reviewed_by''',
            (spreadsheet_row, spreadsheet_name, vfstr_author_id, official_name,
             snapshot_date, json.dumps(metrics, ensure_ascii=False) if metrics else None,
             status, reviewed_by, now()),
        )
    log_audit('manual_link', 'vfstr_author', vfstr_author_id,
              reviewed_by, {'spreadsheet_row': spreadsheet_row, 'spreadsheet_name': spreadsheet_name, 'status': status})


def manual_links(status=None):
    with db.conn() as connection:
        if status:
            rows = connection.execute(
                'SELECT * FROM manual_metric_links WHERE status=? ORDER BY spreadsheet_row', (status,)
            ).fetchall()
        else:
            rows = connection.execute(
                'SELECT * FROM manual_metric_links ORDER BY spreadsheet_row'
            ).fetchall()
    records = []
    for row in rows:
        record = dict(row)
        raw = record.pop('metrics_json', None)
        try:
            record['metrics'] = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            record['metrics'] = None
        records.append(record)
    return records


def delete_manual_link(link_id):
    with db.conn() as connection:
        cursor = connection.execute('DELETE FROM manual_metric_links WHERE id=?', (link_id,))
        return cursor.rowcount > 0


def log_audit(action, entity=None, entity_id=None, actor=None, detail=None):
    with db.conn() as connection:
        connection.execute(
            'INSERT INTO audit_log(action,entity,entity_id,actor,detail,created_at) VALUES(?,?,?,?,?,?)',
            (action, entity, entity_id, actor,
             json.dumps(detail, ensure_ascii=False) if detail is not None else None, now()),
        )


def audit_entries(limit=100):
    with db.conn() as connection:
        rows = connection.execute(
            'SELECT * FROM audit_log ORDER BY id DESC LIMIT ?', (limit,)
        ).fetchall()
    records = []
    for row in rows:
        record = dict(row)
        raw = record.get('detail')
        try:
            record['detail'] = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            pass
        records.append(record)
    return records


# --------------------------------------------------------------------------- datasets

def directory_rows(search='', department='', school='', designation='',
                   research_area='', has_google_scholar=None, has_scopus=None,
                   data_status='', sort='name_az'):
    """Every verified directory author, unpaginated, for export."""
    authors, total = db.vfstr_authors(
        search=search, department=department, school=school, designation=designation,
        research_area=research_area, has_google_scholar=has_google_scholar,
        has_scopus=has_scopus, data_status=data_status, sort=sort,
        limit=10000, offset=0,
    )
    if len(authors) < total:
        authors, _ = db.vfstr_authors(
            search=search, department=department, school=school, designation=designation,
            research_area=research_area, has_google_scholar=has_google_scholar,
            has_scopus=has_scopus, data_status=data_status, sort=sort,
            limit=total, offset=0,
        )
    return authors


DIRECTORY_COLUMNS = [
    ('name', 'Name'), ('designation', 'Designation'), ('department', 'Department'),
    ('school', 'School'), ('campus', 'Campus'), ('institution', 'Institution'),
    ('google_scholar_citations', 'Google Scholar citations'),
    ('google_scholar_h_index', 'Google Scholar h-index'),
    ('google_scholar_i10', 'Google Scholar i10-index'),
    ('scopus_citations', 'Scopus citations'), ('scopus_h_index', 'Scopus h-index'),
    ('scopus_author_id', 'Scopus author ID'), ('google_scholar_url', 'Google Scholar URL'),
    ('scopus_profile_url', 'Scopus profile URL'), ('data_status', 'Metrics status'),
    ('metrics_date', 'Metrics date'), ('source_url', 'Source'),
]

METRICS_COLUMNS = [
    ('name', 'Name'), ('designation', 'Designation'), ('department', 'Department'),
    ('snapshot_date', 'Snapshot date'), ('source', 'Source'),
    ('citations', 'Citations'), ('h_index', 'h-index'), ('i10_index', 'i10-index'),
    ('publications', 'Publications'), ('status', 'Status'), ('captured_at', 'Captured at'),
]


def metrics_rows(limit=2000):
    with db.conn() as connection:
        rows = connection.execute(
            '''SELECT f.name, f.designation, f.department, m.snapshot_date, m.source,
                      m.citations, m.h_index, m.i10_index, m.publications, m.status, m.captured_at
               FROM metrics m JOIN faculty f ON f.id=m.faculty_id
               ORDER BY lower(f.name), m.captured_at DESC LIMIT ?''', (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def publication_rows(limit=5000):
    with db.conn() as connection:
        rows = connection.execute(
            '''SELECT f.name faculty_name, p.title, p.year, p.journal, p.doi,
                      p.citations, p.source, p.url
               FROM publications p JOIN faculty f ON f.id=p.faculty_id
               ORDER BY lower(f.name), p.year DESC, p.citations DESC LIMIT ?''', (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


PUBLICATION_COLUMNS = [
    ('faculty_name', 'Faculty'), ('title', 'Title'), ('year', 'Year'),
    ('journal', 'Journal'), ('doi', 'DOI'), ('citations', 'Citations'),
    ('source', 'Source'), ('url', 'URL'),
]


def to_csv(columns, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\n')
    writer.writerow([label for _, label in columns])
    for row in rows:
        writer.writerow([row.get(key) if row.get(key) is not None else '' for key, _ in columns])
    return buffer.getvalue()


def to_xlsx(columns, rows, sheet_name='Export'):
    """Minimal XLSX writer with no third-party dependency."""
    import zipfile
    from xml.sax.saxutils import escape

    def column_name(index):
        name = ''
        index += 1
        while index:
            index, remainder = divmod(index - 1, 26)
            name = chr(65 + remainder) + name
        return name

    def cell(reference, value):
        if value is None or value == '':
            return f'<c r="{reference}"/>'
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f'<c r="{reference}"><v>{value}</v></c>'
        return f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'

    header = ''.join(
        cell(f'{column_name(index)}1', label) for index, (_, label) in enumerate(columns)
    )
    body = []
    for row_index, row in enumerate(rows, start=2):
        body.append('<row r="%d">%s</row>' % (row_index, ''.join(
            cell(f'{column_name(index)}{row_index}',
                 row.get(key) if row.get(key) is not None else '')
            for index, (key, _) in enumerate(columns)
        )))
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData><row r="1">{header}</row>{"".join(body)}</sheetData></worksheet>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{escape(sheet_name)}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', content_types)
        archive.writestr('_rels/.rels', root_rels)
        archive.writestr('xl/workbook.xml', workbook)
        archive.writestr('xl/_rels/workbook.xml.rels', workbook_rels)
        archive.writestr('xl/worksheets/sheet1.xml', sheet)
    return buffer.getvalue()


def log_export(dataset, row_count, actor=None):
    with db.conn() as connection:
        connection.execute(
            'INSERT INTO export_log(dataset,row_count,actor,created_at) VALUES(?,?,?,?)',
            (dataset, row_count, actor, now()),
        )


def recent_exports(limit=20):
    with db.conn() as connection:
        return [dict(row) for row in connection.execute(
            'SELECT * FROM export_log ORDER BY id DESC LIMIT ?', (limit,)
        ).fetchall()]


# --------------------------------------------------------------------------- backup

def backup_database(destination=None):
    """SQLite-consistent backup using the online backup API."""
    source = Path(settings.database_path)
    if not source.is_absolute():
        source = _backend_root() / source
    if not source.exists():
        raise FileNotFoundError(f'Database not found at {source}')
    if destination is None:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        # settings.database_path is documented as relative to the backend
        # directory, so resolve the backups folder next to it rather than
        # against the process working directory.
        directory = _backend_root() / 'backups'
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f'research_intelligence-{stamp}.db'
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(source)) as origin, sqlite3.connect(str(destination)) as target:
        origin.backup(target)
    try:
        log_audit('backup', 'database', str(destination), None, {'bytes': destination.stat().st_size})
    except sqlite3.Error:
        # The audit table may not exist yet (fresh database, or a run before
        # init_extras_db). The backup itself has already succeeded.
        logger.warning('Could not record the backup in the audit log; the backup file was still written.')
    return str(destination)


def prune_backups(keep=14):
    directory = _backend_root() / 'backups'
    if not directory.exists():
        return []
    files = sorted(directory.glob('research_intelligence-*.db'), key=lambda item: item.stat().st_mtime, reverse=True)
    removed = []
    for stale in files[keep:]:
        try:
            stale.unlink()
            removed.append(str(stale))
        except OSError:
            pass
    return removed


def database_stats():
    path = Path(settings.database_path)
    stats = {'path': str(path), 'exists': path.exists()}
    if path.exists():
        stats['bytes'] = path.stat().st_size
    with db.conn() as connection:
        for table in ('faculty', 'metrics', 'publications', 'vfstr_authors', 'sync_log'):
            try:
                stats[table] = connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            except sqlite3.Error:
                stats[table] = None
        try:
            stats['integrity'] = connection.execute('PRAGMA integrity_check').fetchone()[0]
        except sqlite3.Error:
            stats['integrity'] = 'unknown'
    return stats
