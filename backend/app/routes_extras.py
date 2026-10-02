"""Additive API routes.

Mounted by main.py under /api/v1 with the same auth middleware, so every route
here is protected exactly like the existing ones. No existing route is changed.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.models.schemas import (
    BatchSyncRequest,
    ManualLinkRequest,
    ReviewApplyRequest,
)
from app.services import analytics, batch, db, extras_db, rate_limit, review

logger = logging.getLogger('researchpulse.extras')

router = APIRouter()

EXPORTS = {
    'directory': ('directory.csv', 'Verified VFSTR faculty directory'),
    'metrics': ('metrics.csv', 'Captured metric snapshots'),
    'publications': ('publications.csv', 'Synchronised publications'),
}


# --------------------------------------------------------------------- exports

def _dataset(dataset, **filters):
    if dataset == 'directory':
        return extras_db.DIRECTORY_COLUMNS, extras_db.directory_rows(**filters)
    if dataset == 'metrics':
        return extras_db.METRICS_COLUMNS, extras_db.metrics_rows()
    if dataset == 'publications':
        return extras_db.PUBLICATION_COLUMNS, extras_db.publication_rows()
    raise HTTPException(404, f'Unknown dataset "{dataset}".')


@router.get('/exports/{dataset}')
def export_dataset(
    dataset: str,
    page: int = Query(0, ge=0, description='0 exports every row; 1+ paginates.'),
    page_size: int = Query(0, ge=0, le=5000),
    format: str = Query('csv', pattern='^(csv|xlsx)$'),
    search: str = '',
    department: str = '',
    school: str = '',
    designation: str = '',
    research_area: str = '',
    data_status: str = '',
    sort: str = 'name_az',
):
    columns, rows = _dataset(
        dataset, search=search, department=department, school=school,
        designation=designation, research_area=research_area,
        data_status=data_status, sort=sort,
    ) if dataset == 'directory' else _dataset(dataset)
    total = len(rows)
    if page_size:
        start = max(0, (max(1, page) - 1) * page_size)
        rows = rows[start:start + page_size]
    extras_db.log_export(dataset, len(rows))
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')
    if format == 'xlsx':
        payload = extras_db.to_xlsx(columns, rows, sheet_name=dataset.title()[:28])
        return Response(
            content=payload,
            media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={
                'Content-Disposition': f'attachment; filename="{dataset}-{stamp}.xlsx"',
                'X-Total-Rows': str(total),
                'X-Exported-Rows': str(len(rows)),
            },
        )
    payload = extras_db.to_csv(columns, rows)
    return Response(
        content=payload.encode('utf-8-sig'),
        media_type='text/csv; charset=utf-8',
        headers={
            'Content-Disposition': f'attachment; filename="{dataset}-{stamp}.csv"',
            'X-Total-Rows': str(total),
            'X-Exported-Rows': str(len(rows)),
        },
    )


@router.get('/exports')
def export_catalogue():
    return {
        'datasets': [
            {'id': key, 'filename': value[0], 'description': value[1]}
            for key, value in EXPORTS.items()
        ],
        'formats': ['csv', 'xlsx'],
        'recent_exports': extras_db.recent_exports(),
    }


# ------------------------------------------------------------------ batch sync

@router.post('/sync/batch')
def start_batch_sync(request: BatchSyncRequest, background: bool = True):
    rate_limit.batch_sync_limit.check(request.requested_by or 'anonymous')
    try:
        if background:
            return batch.sync_many_background(
                request.faculty_ids or None,
                only_with_scholar=request.only_with_scholar,
                only_never_synced=request.only_never_synced,
                actor=request.requested_by,
            )
        return batch.sync_many(
            request.faculty_ids or None,
            only_with_scholar=request.only_with_scholar,
            only_never_synced=request.only_never_synced,
            actor=request.requested_by,
        )
    except RuntimeError as error:
        raise HTTPException(409, str(error))


@router.get('/sync/batch/status')
def batch_sync_status(): return batch.batch_status()


@router.get('/sync/batch/{run_id}')
def batch_sync_run(run_id: int):
    record = extras_db.sync_run(run_id)
    if not record:
        raise HTTPException(404, 'Synchronisation run not found.')
    return record


@router.get('/sync/failures')
def sync_failures(): return batch.sync_failures()


# ------------------------------------------------------------------- analytics

@router.get('/analytics/cohort')
def cohort(department: str = '', school: str = '', designation: str = ''):
    rows, _ = db.vfstr_authors(limit=10000, offset=0)
    return analytics.cohort_summary(rows, department, school, designation)


@router.get('/analytics/ranking')
def ranking(
    sort: str = 'google_scholar_citations',
    limit: int = Query(25, ge=1, le=500),
    department: str = '',
    school: str = '',
    designation: str = '',
    include_missing: bool = False,
):
    if sort not in analytics.COHORT_SORTS:
        raise HTTPException(400, f'Unknown sort "{sort}". Allowed: {", ".join(sorted(analytics.COHORT_SORTS))}.')
    return analytics.ranking(sort, limit, department, school, designation, include_missing)


@router.get('/analytics/compare')
def compare(faculty_ids: str = Query(..., description='Comma-separated faculty ids, at most 6.')):
    ids = [value.strip() for value in faculty_ids.split(',') if value.strip()]
    if not ids:
        raise HTTPException(400, 'At least one faculty id is required.')
    if len(ids) > 6:
        raise HTTPException(400, 'At most 6 faculty profiles can be compared at once.')
    return analytics.compare(ids)


@router.get('/analytics/source-comparison/{faculty_id}')
def source_comparison(faculty_id: str):
    try:
        return analytics.source_comparison(faculty_id)
    except ValueError as error:
        raise HTTPException(404, str(error))


@router.get('/analytics/dedupe/{faculty_id}')
def dedupe(faculty_id: str):
    if not db.get_faculty(faculty_id):
        raise HTTPException(404, 'Faculty not found.')
    return analytics.dedupe_publications(faculty_id)


# --------------------------------------------------------------- review queue

@router.get('/review/queue')
def review_queue(
    page: int = Query(1, ge=1),
    page_size: int = Query(0, ge=0, le=500, description='0 returns every row.'),
):
    return review.review_queue(page=page, page_size=page_size)


@router.post('/review/apply')
def review_apply(request: ReviewApplyRequest):
    try:
        return review.apply_link(request.spreadsheet_row, request.vfstr_author_id,
                                 reviewed_by=request.reviewed_by, force=request.force)
    except ValueError as error:
        raise HTTPException(400, str(error))


@router.post('/review/undo')
def review_undo(request: ManualLinkRequest):
    try:
        return review.undo_link(request.spreadsheet_row, request.vfstr_author_id, request.reviewed_by)
    except ValueError as error:
        raise HTTPException(404, str(error))


@router.get('/review/links')
def review_links(status: str = ''):
    return {'links': extras_db.manual_links(status or None)}


@router.get('/audit')
def audit(limit: int = Query(100, ge=1, le=500)):
    return {'entries': extras_db.audit_entries(limit)}


# ---------------------------------------------------------------- maintenance

@router.post('/maintenance/backup')
def maintenance_backup():
    rate_limit.backup_limit.check('anonymous')
    try:
        path = extras_db.backup_database()
    except FileNotFoundError as error:
        raise HTTPException(404, str(error))
    return {'backup': path, 'pruned': extras_db.prune_backups()}


@router.get('/maintenance/status')
def maintenance_status():
    return {
        'database': extras_db.database_stats(),
        'backups': _backup_listing(),
        'circuit_breakers': extras_db.sync_failure_summary()['circuit_breakers'],
        'schema': extras_db.schema_version(),
        'rate_limits': rate_limit.all_states(),
    }


def _backup_listing():
    from pathlib import Path
    from app.core.config import settings
    directory = Path(settings.database_path).parent.parent / 'backups'
    if not directory.exists():
        return []
    files = sorted(directory.glob('research_intelligence-*.db'),
                   key=lambda item: item.stat().st_mtime, reverse=True)
    return [
        {'name': item.name, 'bytes': item.stat().st_size,
         'modified': datetime.fromtimestamp(item.stat().st_mtime, timezone.utc).isoformat(timespec='seconds')}
        for item in files[:20]
    ]
