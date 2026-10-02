import re, httpx
from app.core.config import settings
from app.services import http_client

class ScholarError(RuntimeError): pass

def fetch(profile_url):
    if not profile_url: raise ScholarError('Google Scholar profile URL is not linked.')
    m=re.search(r'[?&]user=([^&]+)',profile_url)
    if not m: raise ScholarError('Google Scholar profile URL must contain a user= identifier.')
    if not settings.serpapi_api_key: raise ScholarError('SERPAPI_API_KEY is not configured for Google Scholar retrieval.')
    # Transient failures are retried with backoff by the shared client; the error
    # text for a terminal failure is unchanged.
    try:
        data=http_client.get_json(settings.scholar_provider_url,params={'engine':'google_scholar_author','author_id':m.group(1),'api_key':settings.serpapi_api_key},timeout=25,provider='google_scholar',breaker=False)
    except http_client.CircuitOpen as e:
        raise ScholarError(f'Google Scholar provider temporarily unavailable: {e}') from e
    except http_client.RetryExhausted as e:
        cause=e.cause
        response=getattr(cause,'response',None)
        if response is None and hasattr(cause,'status_code'):
            response=cause
        if response is not None:
            raise ScholarError(f'Google Scholar provider returned HTTP {response.status_code}.') from cause
        raise ScholarError(f'Google Scholar provider network error: {cause}') from cause
    author=data.get('author',{}); cited=data.get('cited_by',{}).get('table',[])
    metrics={}
    for item in cited:
        if 'citations' in item: metrics['citations']=item['citations'].get('all')
        if 'h_index' in item: metrics['h_index']=item['h_index'].get('all')
        if 'i10_index' in item: metrics['i10_index']=item['i10_index'].get('all')
    pubs=[]
    for a in data.get('articles',[])[:50]:
        pubs.append({'id':'scholar:'+str(a.get('result_id') or abs(hash(a.get('title','')))),'title':a.get('title',''),'year':int(a['year']) if str(a.get('year','')).isdigit() else None,'journal':a.get('publication'),'citations':a.get('cited_by',0),'source':'google_scholar','url':a.get('link')})
    return {'name':author.get('name',''),'affiliation':author.get('affiliations',''),'metrics':metrics,'publications':pubs,'source_url':profile_url}
