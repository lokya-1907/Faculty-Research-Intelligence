"""Additive analytics: cohort comparison, ranking, cross-source dedup and an
explicit source-comparability note. Reads existing tables only.
"""
from app.services import db

# Google Scholar's provider adapter caps the retrieved article list, so its
# publication count is a sample rather than the full record. Scopus Search is
# likewise computed from the retrieved publications, not the official profile.
COMPARABILITY_NOTE = (
    'Google Scholar and Scopus values are not directly comparable: Google Scholar '
    'is retrieved through a provider adapter that returns a capped article sample, '
    'and Scopus values are computed from Scopus Search publications rather than the '
    'official Scopus Author Profile. Compare within a source, not across sources.'
)

COHORT_SORTS = {
    'google_scholar_citations': 'Google Scholar citations',
    'scopus_citations': 'Scopus citations',
    'google_scholar_h_index': 'Google Scholar h-index',
    'scopus_h_index': 'Scopus h-index',
    'google_scholar_i10_index': 'Google Scholar i10-index',
    'name_az': 'Name',
}


def _metric(row, key):
    value = row.get(key)
    return value if isinstance(value, (int, float)) else None


def _percentile(values, value):
    """Percent of peers at or below this value."""
    present = [item for item in values if item is not None]
    if not present or value is None:
        return None
    return round(100.0 * sum(1 for item in present if item <= value) / len(present), 1)


def _median(values):
    present = sorted(item for item in values if item is not None)
    if not present:
        return None
    middle = len(present) // 2
    if len(present) % 2:
        return float(present[middle])
    return (present[middle - 1] + present[middle]) / 2.0


def _average(values):
    present = [item for item in values if item is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 2)


def _cohort(rows, department='', school='', designation=''):
    filtered = [
        row for row in rows
        if (not department or row.get('department') == department)
        and (not school or row.get('school') == school)
        and (not designation or row.get('designation') == designation)
    ]
    return filtered or rows


def cohort_summary(rows, department='', school='', designation=''):
    cohort = _cohort(rows, department, school, designation)
    summary = {
        'cohort_size': len(cohort),
        'filters': {'department': department, 'school': school, 'designation': designation},
        'metrics': {},
        'note': COMPARABILITY_NOTE,
    }
    for key in ('google_scholar_citations', 'scopus_citations',
                'google_scholar_h_index', 'scopus_h_index', 'google_scholar_i10'):
        values = [_metric(row, key) for row in cohort]
        summary['metrics'][key] = {
            'reported': sum(1 for value in values if value is not None),
            'missing': sum(1 for value in values if value is None),
            'median': _median(values),
            'average': _average(values),
            'max': max((value for value in values if value is not None), default=None),
            'min': min((value for value in values if value is not None), default=None),
        }
    return summary


def ranking(sort='google_scholar_citations', limit=50, department='', school='',
            designation='', include_missing=False):
    rows = db.vfstr_authors(department=department, school=school, designation=designation,
                            sort=sort, limit=10000, offset=0)[0]
    if sort not in COHORT_SORTS:
        sort = 'google_scholar_citations'
    cohort = _cohort(rows, department, school, designation)
    values = [_metric(row, sort) for row in cohort]
    if not include_missing:
        cohort = [row for row in cohort if _metric(row, sort) is not None]
    ordered = sorted(
        cohort,
        key=lambda row: (-(row.get(sort) or -1), str(row.get('name') or '').lower()),
    )
    entries = []
    for index, row in enumerate(ordered[:max(1, limit)], start=1):
        value = _metric(row, sort)
        entries.append({
            'rank': index,
            'name': row.get('name'),
            'designation': row.get('designation'),
            'department': row.get('department'),
            'value': value,
            'percentile': _percentile(values, value),
            'google_scholar_citations': row.get('google_scholar_citations'),
            'scopus_citations': row.get('scopus_citations'),
            'google_scholar_h_index': row.get('google_scholar_h_index'),
            'scopus_h_index': row.get('scopus_h_index'),
            'data_status': row.get('data_status'),
            'author_id': row.get('id'),
        })
    return {
        'sort': sort,
        'sort_label': COHORT_SORTS.get(sort, sort),
        'cohort_size': len(cohort),
        'returned': len(entries),
        'entries': entries,
        'note': COMPARABILITY_NOTE,
    }


def _profile_row(faculty_id):
    faculty = db.get_faculty(faculty_id)
    if not faculty:
        return None
    directory = db.vfstr_author_by_faculty_id(faculty_id) or {}
    record = dict(faculty)
    record.update({
        'google_scholar_citations': directory.get('google_scholar_citations'),
        'google_scholar_h_index': directory.get('google_scholar_h_index'),
        'google_scholar_i10': directory.get('google_scholar_i10'),
        'scopus_citations': directory.get('scopus_citations'),
        'scopus_h_index': directory.get('scopus_h_index'),
        'data_status': directory.get('data_status'),
    })
    return record


def compare(faculty_ids):
    """Side-by-side comparison with each faculty's percentile inside the cohort."""
    ids = [fid for fid in dict.fromkeys(faculty_ids) if fid][:6]
    if not ids:
        raise ValueError('At least one faculty id is required.')
    all_rows = db.vfstr_authors(limit=10000, offset=0)[0]
    metric_keys = ('google_scholar_citations', 'scopus_citations',
                   'google_scholar_h_index', 'scopus_h_index', 'google_scholar_i10')
    cohort_values = {key: [_metric(row, key) for row in all_rows] for key in metric_keys}

    subjects = []
    missing = []
    for fid in ids:
        row = _profile_row(fid)
        if not row:
            missing.append(fid)
            continue
        metrics = {}
        for key in metric_keys:
            value = _metric(row, key)
            metrics[key] = {
                'value': value,
                'percentile': _percentile(cohort_values[key], value),
            }
        subjects.append({
            'faculty_id': fid,
            'name': row.get('name'),
            'designation': row.get('designation'),
            'department': row.get('department'),
            'metrics': metrics,
            'publications': len(db.publications(fid, limit=500)),
            'last_synced_at': row.get('last_synced_at'),
        })

    deltas = {}
    if len(subjects) == 2:
        left, right = subjects
        for key in metric_keys:
            a = left['metrics'][key]['value']
            b = right['metrics'][key]['value']
            deltas[key] = None if a is None or b is None else b - a

    return {
        'subjects': subjects,
        'missing_faculty_ids': missing,
        'deltas': deltas,
        'cohort_size': len(all_rows),
        'note': COMPARABILITY_NOTE,
    }


def source_comparison(faculty_id):
    """Per-source view for one faculty, plus the comparability caveat."""
    faculty = db.get_faculty(faculty_id)
    if not faculty:
        raise ValueError('Faculty not found.')
    directory = db.vfstr_author_by_faculty_id(faculty_id) or {}
    latest = {metric['source']: metric for metric in db.latest_metrics(faculty_id)}
    scholar = latest.get('google_scholar', {})
    scopus = latest.get('scopus_search', {})
    return {
        'faculty_id': faculty_id,
        'name': faculty['name'],
        'google_scholar': {
            'citations': scholar.get('citations') if scholar else directory.get('google_scholar_citations'),
            'h_index': scholar.get('h_index') if scholar else directory.get('google_scholar_h_index'),
            'i10_index': scholar.get('i10_index') if scholar else directory.get('google_scholar_i10'),
            'publications': scholar.get('publications'),
            'captured_at': scholar.get('captured_at'),
            'source': 'live sync' if scholar else ('April 2026 snapshot' if directory else None),
        },
        'scopus': {
            'citations': scopus.get('citations') if scopus else directory.get('scopus_citations'),
            'h_index': scopus.get('h_index') if scopus else directory.get('scopus_h_index'),
            'i10_index': scopus.get('i10_index') if scopus else None,
            'publications': scopus.get('publications'),
            'captured_at': scopus.get('captured_at'),
            'source': 'live sync' if scopus else ('April 2026 snapshot' if directory else None),
        },
        'note': COMPARABILITY_NOTE,
    }


def _normalize_title(value):
    import re
    return re.sub(r'[^a-z0-9]+', '', str(value or '').casefold())


def dedupe_publications(faculty_id):
    """Group the same work appearing under both sources by DOI, then by title."""
    rows = db.publications(faculty_id, limit=500)
    groups = {}
    order = []
    for row in rows:
        doi = (row.get('doi') or '').strip().casefold()
        key = ('doi', doi) if doi else ('title', _normalize_title(row.get('title')))
        if key not in groups:
            groups[key] = {'key': key[1], 'matched_by': key[0], 'title': row.get('title'),
                           'year': row.get('year'), 'sources': [], 'citations': {}}
            order.append(key)
        group = groups[key]
        group['sources'].append(row.get('source'))
        group['citations'][row.get('source')] = row.get('citations')
        if not group.get('year') and row.get('year'):
            group['year'] = row['year']
        if not group.get('title') and row.get('title'):
            group['title'] = row['title']
    merged = []
    duplicates = 0
    for key in order:
        group = groups[key]
        group['sources'] = sorted({source for source in group['sources'] if source})
        group['duplicate'] = len(group['sources']) > 1
        if group['duplicate']:
            duplicates += 1
        merged.append(group)
    return {
        'faculty_id': faculty_id,
        'total_publications': len(rows),
        'unique_works': len(merged),
        'duplicate_works': duplicates,
        'works': merged,
    }
