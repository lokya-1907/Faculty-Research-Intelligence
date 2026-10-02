import hashlib
import re
import httpx
from app.core.config import settings
from app.services import http_client

BASE = 'https://api.elsevier.com/content'
MAX_SEARCH_RESULTS = 5000

class ScopusError(RuntimeError):
    pass


def headers():
    h = {'Accept': 'application/json'}
    if settings.scopus_api_key:
        h['X-ELS-APIKey'] = settings.scopus_api_key
    if settings.scopus_inst_token:
        h['X-ELS-Insttoken'] = settings.scopus_inst_token
    return h


def request(path, params):
    if not settings.scopus_api_key:
        raise ScopusError('SCOPUS_API_KEY is not configured.')
    # Transient failures (429/5xx/network) are retried with backoff by the shared
    # client, which also opens a per-provider circuit breaker after repeated
    # failures. Status-code handling below is unchanged: any terminal response is
    # still classified into exactly the same ScopusError messages as before.
    try:
        data = http_client.get_json(BASE + path, params=params, headers=headers(), timeout=25,
                                    provider='scopus', breaker=False)
    except http_client.CircuitOpen as e:
        raise ScopusError(f'Scopus API temporarily unavailable: {e}') from e
    except http_client.RetryExhausted as e:
        cause = e.cause
        response = getattr(cause, 'response', None)
        if response is None and hasattr(cause, 'status_code'):
            response = cause
        if response is not None:
            return _raise_for_status(response)
        raise ScopusError(f'Scopus network error: {cause}') from cause
    return data


def _raise_for_status(r):
    """Preserve the original status-code -> error-message mapping."""
    if r.status_code in (401, 403):
        try:
            status = r.json().get('service-error', {}).get('status', {})
            code = status.get('statusCode')
            detail = status.get('statusText')
        except Exception:
            code = None
            detail = None
        if code == 'AUTHORIZATION_ERROR':
            raise ScopusError('Official Scopus Author Profile lookup requires the appropriate Elsevier API entitlement. Scopus Search publication results remain available.')
    if r.status_code == 401:
        raise ScopusError('Scopus API key was rejected (401).')
    if r.status_code == 403:
        raise ScopusError('Scopus API access is not permitted for this key (403).')
    if r.status_code == 429:
        raise ScopusError('Scopus API quota/rate limit reached (429).')
    if not r.is_success:
        try:
            detail = r.json().get('service-error', {}).get('status', {}).get('statusText')
        except Exception:
            detail = None
        raise ScopusError(f'Scopus API returned HTTP {r.status_code}' + (f': {detail}' if detail else '.'))
    return r.json()


def _clean_name(name: str):
    # Remove common honorifics before building the Scopus AUTHOR-NAME query.
    cleaned = re.sub(r'(?i)\b(?:dr|prof|mr|mrs|ms|sir)\.?\s*', ' ', name)
    return ' '.join(cleaned.split())


def _author_query(name: str):
    cleaned = _clean_name(name)
    if ',' in cleaned:
        surname, given_names = (part.strip() for part in cleaned.split(',', 1))
        if surname and given_names:
            return f'AUTHOR-NAME("{given_names}, {surname}")'
    parts = cleaned.split()
    if not parts:
        raise ScopusError('Please enter a faculty name.')
    if len(parts) == 1:
        return f'AUTHLASTNAME({parts[0]})'
    # Scopus Search supports AUTHOR-NAME and treats comma-separated values as
    # last name + first name for a specific author.
    return f'AUTHOR-NAME("{parts[-1]}, {" ".join(parts[:-1])}")'


def _affil_query(institution: str):
    if not institution.strip():
        return ''
    safe = institution.strip().replace('"', '')
    return f' AND AFFIL("{safe}")'


def _is_service_level_limit(error: ScopusError):
    return 'exceeds the maximum number allowed for the service level' in str(error).casefold()


def _search_documents(query: str, count: int = 25, start: int = 0):
    page_sizes = [count]
    page_sizes.extend(size for size in (25, 10, 5, 1) if size < count)
    last_error = None
    for page_size in page_sizes:
        try:
            data = request('/search/scopus', {
                'query': query,
                'count': page_size,
                'start': start,
                'sort': '-citedby-count',
                'view': 'STANDARD',
            })
            break
        except ScopusError as error:
            if not _is_service_level_limit(error) or page_size == page_sizes[-1]:
                raise
            last_error = error
    else:
        raise last_error
    sr = data.get('search-results', {})
    entries = sr.get('entry', []) or []
    entries = [entry for entry in entries if isinstance(entry, dict) and 'error' not in entry]
    return entries, int(sr.get('opensearch:totalResults', 0) or 0)


def _search_all_documents(query: str, count: int = 25):
    entries, total = _search_documents(query, count, 0)
    if not entries:
        return [], total

    page_size = min(count, 25)
    result_limit = min(total, MAX_SEARCH_RESULTS)
    collected = list(entries)
    start = len(entries)
    while start < result_limit:
        page, page_total = _search_documents(query, page_size, start)
        total = max(total, page_total)
        if not page:
            break
        collected.extend(page)
        start += len(page)
        if len(page) < page_size:
            break

    unique = {}
    for entry in collected:
        key = entry.get('eid') or entry.get('dc:identifier')
        if key:
            unique[key] = entry
        else:
            unique[str(entry)] = entry
    return list(unique.values()), total


def search_publications(name, institution=''):
    """Return Scopus Search records without treating their creators as identities."""
    query = _author_query(name) + _affil_query(institution)
    entries, _ = _search_all_documents(query, 25)
    return entries


def documents(author_id=None, name=None, institution=''):
    if author_id:
        query = f'AU-ID({author_id})'
    elif name:
        query = _author_query(name) + _affil_query(institution)
    else:
        raise ScopusError('A Scopus author ID or faculty name is required for publication retrieval.')
    entries, _ = _search_documents(query, 25)
    return entries
