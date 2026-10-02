"""Additive review workflow for unmatched spreadsheet rows.

The April 2026 import leaves rows unmatched when a name is not a safe exact
match. This service turns those rows into a reviewable queue and lets an
operator link a row to a verified directory author, applying the same metric
fields the automatic import would have applied. It never invents a match.
"""
import hashlib
import logging

from app.services import db, extras_db, vfstr_cse_metrics

logger = logging.getLogger('researchpulse.review')

METRIC_FIELDS = (
    ('google_scholar_i10', 'I10 Index (as per Google Scholar)'),
    ('google_scholar_h_index', 'h Index (as per Google Scholar)'),
    ('google_scholar_citations', 'Citations (as per Google Schoolar)'),
    ('scopus_h_index', 'h Index (as per Scopus)'),
    ('scopus_citations', 'Citations (as per Scopus)'),
)


def _latest_report():
    stored = db.latest_vfstr_metric_import_report()
    if not stored:
        return None
    report = stored.get('report') or {}
    report.setdefault('snapshot_date', stored.get('snapshot_date'))
    report.setdefault('source_url', stored.get('source_url'))
    report.setdefault('report_id', stored.get('id'))
    report.setdefault('imported_at', stored.get('imported_at'))
    return report


def review_queue(refresh=False, page=1, page_size=0):
    """Unmatched spreadsheet rows with suggestions, annotated with link state.

    `page_size=0` (the default) returns every row, preserving the original
    response shape. Pass a page_size to paginate.
    """
    report = _latest_report()
    if not report:
        return {
            'available': False,
            'message': 'No VFSTR metrics import report is available. Run the April 2026 metrics import first.',
            'rows': [],
            'counts': {},
            'total': 0,
            'page': 1,
            'page_size': 0,
            'pages': 1,
        }
    links = {entry['spreadsheet_row']: entry for entry in extras_db.manual_links()}
    ambiguous_by_row = {}
    for entry in report.get('ambiguous_matches', []) or []:
        ambiguous_by_row.setdefault(entry.get('spreadsheet_row'), []).append(entry)

    rows = []
    for entry in report.get('unmatched_spreadsheet_records', []) or []:
        row_number = entry.get('spreadsheet_row')
        link = links.get(row_number)
        suggestions = [
            item if isinstance(item, str) else item.get('official_name')
            for item in (entry.get('possible_candidates') or [])
        ]
        suggestions = [name for name in suggestions if name]
        rows.append({
            'spreadsheet_row': row_number,
            'spreadsheet_name': entry.get('spreadsheet_name'),
            'suggestions': suggestions,
            'ambiguous': row_number in ambiguous_by_row,
            'ambiguity_reason': (ambiguous_by_row.get(row_number) or [{}])[0].get('reason'),
            'status': link['status'] if link else 'pending',
            'linked_author_id': link['vfstr_author_id'] if link else None,
            'linked_official_name': link['official_name'] if link else None,
            'linked_at': link['created_at'] if link else None,
        })

    linked = sum(1 for row in rows if row['status'] == 'linked')
    total = len(rows)
    size = max(0, int(page_size or 0))
    current_page = max(1, int(page or 1))
    if size:
        start = (current_page - 1) * size
        rows = rows[start:start + size]
    return {
        'available': True,
        'snapshot_date': report.get('snapshot_date'),
        'source_url': report.get('source_url'),
        'imported_at': report.get('imported_at'),
        'rows': rows,
        'total': total,
        'page': current_page,
        'page_size': size,
        'pages': (max(1, -(-total // size)) if size else 1),
        'counts': {
            'total_unmatched': total,
            'pending': len(rows) - linked,
            'linked': linked,
            'with_suggestions': sum(1 for row in rows if row['suggestions']),
            'without_suggestions': sum(1 for row in rows if not row['suggestions']),
            'ambiguous': sum(1 for row in rows if row['ambiguous']),
        },
    }


def _suggest_for(name, exclude_author_ids=None):
    rows, total = db.vfstr_authors(limit=10000, offset=0)
    roster = rows
    if total > len(roster):
        roster, _ = db.vfstr_authors(limit=total, offset=0)
    exclude = set(exclude_author_ids or [])
    pool = [author for author in roster if author['id'] not in exclude]
    return vfstr_cse_metrics._suggest_candidates(name, pool)


def apply_link(spreadsheet_row, vfstr_author_id, reviewed_by=None, force=False):
    """Link one spreadsheet row to a verified author and apply its metrics.

    `force` is required to override the suggestion guard, so a careless click
    cannot silently attach the wrong metrics to a person.
    """
    report = _latest_report()
    if not report:
        raise ValueError('No VFSTR metrics import report is available.')

    entry = next(
        (item for item in report.get('unmatched_spreadsheet_records', []) or []
         if item.get('spreadsheet_row') == spreadsheet_row),
        None,
    )
    if not entry:
        already = next(
            (item for item in report.get('matched', []) or []
             if item.get('spreadsheet_row') == spreadsheet_row),
            None,
        )
        if already:
            raise ValueError('That spreadsheet row was already matched automatically by the import.')
        raise ValueError(f'Spreadsheet row {spreadsheet_row} is not part of the review queue.')

    author = db.vfstr_author(vfstr_author_id)
    if not author:
        raise ValueError('Verified VFSTR author not found.')

    suggestions = [
        item if isinstance(item, str) else item.get('official_name')
        for item in (entry.get('possible_candidates') or [])
    ]
    suggestions = [name for name in suggestions if name]
    if suggestions and author['name'] not in suggestions and not force:
        raise ValueError(
            f'"{author["name"]}" is not one of the suggested candidates for '
            f'"{entry.get("spreadsheet_name")}". Resend with force=true to confirm this is intentional.'
        )

    spreadsheet_name = entry.get('spreadsheet_name')
    metrics = _metrics_from_report(report, spreadsheet_row)
    if metrics is None:
        raise ValueError(
            'The stored import report does not include the metric values for this row; '
            're-run the April 2026 metrics import so the row can be applied safely.'
        )

    snapshot_date = report.get('snapshot_date') or vfstr_cse_metrics.METRICS_DATE
    faculty_id = author.get('faculty_id') or 'fac_vfstr_' + hashlib.sha256(author['id'].encode()).hexdigest()[:16]
    db.upsert_faculty({
        'id': faculty_id,
        'name': author['name'],
        'institution': author['institution'],
        'department': author.get('department') or '',
        'designation': author.get('designation') or '',
        'scopus_author_id': metrics.get('scopus_author_id'),
        'scopus_profile_url': metrics.get('scopus_profile_url'),
        'google_scholar_url': metrics.get('google_scholar_url'),
    })
    data = {
        'google_scholar_url': metrics.get('google_scholar_url'),
        'google_scholar_i10': metrics.get('google_scholar_i10'),
        'google_scholar_h_index': metrics.get('google_scholar_h_index'),
        'google_scholar_citations': metrics.get('google_scholar_citations'),
        'scopus_author_id': metrics.get('scopus_author_id'),
        'scopus_profile_url': metrics.get('scopus_profile_url'),
        'scopus_h_index': metrics.get('scopus_h_index'),
        'scopus_citations': metrics.get('scopus_citations'),
        'professional_memberships': metrics.get('professional_memberships'),
        'data_status': metrics.get('data_status'),
        'metrics_date': snapshot_date,
    }
    if not db.update_vfstr_research_data(author['id'], data):
        raise ValueError('The verified VFSTR directory row could not be updated.')

    db.link_vfstr_author_profile(author['id'], faculty_id)
    db.upsert_metric_snapshot(faculty_id, {
        'source': 'vfstr_cse_dataset',
        'snapshot_date': snapshot_date,
        'data_status': metrics.get('data_status'),
        'google_scholar_citations': metrics.get('google_scholar_citations'),
        'google_scholar_h_index': metrics.get('google_scholar_h_index'),
        'google_scholar_i10_index': metrics.get('google_scholar_i10'),
        'scopus_citations': metrics.get('scopus_citations'),
        'scopus_h_index': metrics.get('scopus_h_index'),
        'status': metrics.get('data_status') or 'status_not_provided',
    })
    extras_db.upsert_manual_link(
        spreadsheet_row, spreadsheet_name, author['id'],
        official_name=author['name'], snapshot_date=snapshot_date,
        metrics={key: value for key, value in metrics.items() if value is not None},
        status='linked', reviewed_by=reviewed_by,
    )
    return {
        'spreadsheet_row': spreadsheet_row,
        'spreadsheet_name': spreadsheet_name,
        'vfstr_author_id': author['id'],
        'official_name': author['name'],
        'faculty_id': faculty_id,
        'snapshot_date': snapshot_date,
        'applied_metrics': {key: value for key, value in metrics.items() if value is not None},
    }


def _metrics_from_report(report, spreadsheet_row):
    """Metric values captured in the import report, when the importer stored them."""
    for collection in ('row_values', 'unmatched_row_values', 'rows'):
        for item in report.get(collection, []) or []:
            if item.get('spreadsheet_row') == spreadsheet_row or item.get('_source_row') == spreadsheet_row:
                return item.get('row') or item.get('values') or item
    return None


def apply_link_from_row_values(spreadsheet_row, vfstr_author_id, row, reviewed_by=None, force=False):
    """Link using row values supplied by the caller (the importer's own parser)."""
    report = _latest_report()
    if not report:
        raise ValueError('No VFSTR metrics import report is available.')
    parsed = vfstr_cse_metrics.parse_spreadsheet_row(row, report.get('snapshot_date') or vfstr_cse_metrics.METRICS_DATE)
    stashed = dict(report)
    stashed.setdefault('row_values', []).append({'spreadsheet_row': spreadsheet_row, 'row': parsed})
    return _apply(parsed, spreadsheet_row, vfstr_author_id, report, reviewed_by, force)


def _apply(parsed, spreadsheet_row, vfstr_author_id, report, reviewed_by, force):
    author = db.vfstr_author(vfstr_author_id)
    if not author:
        raise ValueError('Verified VFSTR author not found.')
    snapshot_date = report.get('snapshot_date') or vfstr_cse_metrics.METRICS_DATE
    faculty_id = author.get('faculty_id') or 'fac_vfstr_' + hashlib.sha256(author['id'].encode()).hexdigest()[:16]
    db.upsert_faculty({
        'id': faculty_id, 'name': author['name'], 'institution': author['institution'],
        'department': author.get('department') or '', 'designation': author.get('designation') or '',
        'scopus_author_id': parsed.get('scopus_author_id'),
        'scopus_profile_url': parsed.get('scopus_profile_url'),
        'google_scholar_url': parsed.get('google_scholar_url'),
    })
    data = {
        'google_scholar_url': parsed.get('google_scholar_url'),
        'google_scholar_i10': parsed.get('google_scholar_i10'),
        'google_scholar_h_index': parsed.get('google_scholar_h_index'),
        'google_scholar_citations': parsed.get('google_scholar_citations'),
        'scopus_author_id': parsed.get('scopus_author_id'),
        'scopus_profile_url': parsed.get('scopus_profile_url'),
        'scopus_h_index': parsed.get('scopus_h_index'),
        'scopus_citations': parsed.get('scopus_citations'),
        'professional_memberships': parsed.get('professional_memberships'),
        'data_status': parsed.get('data_status'),
        'metrics_date': snapshot_date,
    }
    if not db.update_vfstr_research_data(author['id'], data):
        raise ValueError('The verified VFSTR directory row could not be updated.')
    db.link_vfstr_author_profile(author['id'], faculty_id)
    db.upsert_metric_snapshot(faculty_id, {
        'source': 'vfstr_cse_dataset', 'snapshot_date': snapshot_date,
        'data_status': parsed.get('data_status'),
        'google_scholar_citations': parsed.get('google_scholar_citations'),
        'google_scholar_h_index': parsed.get('google_scholar_h_index'),
        'google_scholar_i10_index': parsed.get('google_scholar_i10'),
        'scopus_citations': parsed.get('scopus_citations'),
        'scopus_h_index': parsed.get('scopus_h_index'),
        'status': parsed.get('data_status') or 'status_not_provided',
    })
    extras_db.upsert_manual_link(
        spreadsheet_row, parsed.get('spreadsheet_name') or '', author['id'],
        official_name=author['name'], snapshot_date=snapshot_date,
        metrics={key: value for key, value in parsed.items() if value is not None},
        status='linked', reviewed_by=reviewed_by,
    )
    return {
        'spreadsheet_row': spreadsheet_row,
        'vfstr_author_id': author['id'],
        'official_name': author['name'],
        'faculty_id': faculty_id,
        'snapshot_date': snapshot_date,
    }


def undo_link(spreadsheet_row, vfstr_author_id=None, actor=None):
    """Remove a manual link record (does not delete the applied metrics)."""
    links = extras_db.manual_links()
    target = next(
        (entry for entry in links
         if entry['spreadsheet_row'] == spreadsheet_row
         and (vfstr_author_id is None or entry['vfstr_author_id'] == vfstr_author_id)),
        None,
    )
    if not target:
        raise ValueError('No manual link exists for that spreadsheet row.')
    extras_db.delete_manual_link(target['id'])
    extras_db.log_audit('manual_link_removed', 'vfstr_author', target['vfstr_author_id'], actor,
                        {'spreadsheet_row': spreadsheet_row})
    return {'removed': True, 'spreadsheet_row': spreadsheet_row, 'vfstr_author_id': target['vfstr_author_id']}
