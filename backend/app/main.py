from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import logging
import os
import time
from pathlib import Path
from app.core.config import settings
from app.models.schemas import FacultySearchRequest, FacultyCandidate, FacultyProfile, SearchHistoryItem, SyncResponse, ScopusSearchPublication, VFSTRAuthor, VFSTRAuthorPage, VFSTRDepartments, VFSTRAuthorRefreshResult, LoginRequest, LoginResponse
from app.services import auth, db, service, vfstr_authors, vfstr_cse_metrics
from app.services import extras_db, http_client
from app.services import maintenance
from app import routes_extras

logger=logging.getLogger('researchpulse')
_log_handlers=[logging.StreamHandler()]
_log_file=os.getenv('LOG_FILE','').strip() or str(Path(settings.database_path).parent.parent/'logs'/'researchpulse.log')
try:
    Path(_log_file).parent.mkdir(parents=True,exist_ok=True)
    from logging.handlers import RotatingFileHandler as _RFH
    _log_handlers.append(_RFH(_log_file,maxBytes=2_000_000,backupCount=5,encoding='utf-8'))
except OSError:
    logger.warning('File logging unavailable at %s; continuing with console logging only.',_log_file)
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s',handlers=_log_handlers)

@asynccontextmanager
async def periodic_sync():
    while True:
        await asyncio.sleep(max(1, settings.sync_interval_hours) * 3600)
        if not settings.scopus_api_key:
            continue
        try:
            outcome=batch.sync_many(actor='scheduler',kind='scheduled_sync')
            if outcome['failed']:
                logger.warning('Scheduled synchronization finished with %s failure(s) of %s.',outcome['failed'],outcome['requested'])
            else:
                logger.info('Scheduled synchronization completed for %s profile(s).',outcome['succeeded'])
        except Exception:
            logger.exception('Scheduled synchronization failed.')
            try:
                extras_db.start_sync_run('scheduled_sync',0,message='Scheduled synchronization crashed.')
            except Exception:
                pass

@asynccontextmanager
async def lifespan(app):
    db.init_db()
    task=asyncio.create_task(periodic_sync())
    backup_task=asyncio.create_task(maintenance.periodic_backup())
    try:
        yield
    finally:
        for pending in (task,backup_task):
            pending.cancel()
        for pending in (task,backup_task):
            try: await pending
            except asyncio.CancelledError: pass

db.init_db()
extras_db.init_extras_db()
from app.services import batch
app=FastAPI(title=settings.app_name,version=settings.version,description='Faculty research profiles, citation metrics, publication history and source synchronization.')
app.add_middleware(CORSMiddleware,allow_origins=[x.strip() for x in settings.cors_origins.split(',')],allow_credentials=True,allow_methods=['*'],allow_headers=['*'])
app.include_router(routes_extras.router,prefix='/api/v1')

@app.middleware('http')
async def require_auth(request, call_next):
    started=time.perf_counter()
    if request.method == 'OPTIONS':
        return await call_next(request)
    if settings.auth_required and request.url.path.startswith('/api/v1/') and request.url.path not in {'/api/v1/health','/api/v1/auth/login'}:
        header=request.headers.get('Authorization','')
        token=header[7:] if header.lower().startswith('bearer ') else ''
        if not auth.verify_token(token):
            origin=request.headers.get('origin','')
            headers={'Access-Control-Allow-Credentials':'true'}
            if origin in [value.strip() for value in settings.cors_origins.split(',')]:
                headers['Access-Control-Allow-Origin']=origin
                headers['Vary']='Origin'
            response=JSONResponse(status_code=401,content={'detail':'Authentication required.'},headers=headers)
            logger.info('%s %s %s %.1fms',request.method,request.url.path,response.status_code,(time.perf_counter()-started)*1000)
            return response
    response=await call_next(request)
    response.headers.setdefault('X-Content-Type-Options','nosniff')
    response.headers.setdefault('X-Frame-Options','DENY')
    response.headers.setdefault('Referrer-Policy','same-origin')
    logger.info('%s %s %s %.1fms',request.method,request.url.path,response.status_code,(time.perf_counter()-started)*1000)
    return response

@app.get('/api/v1/health')
def health():
    failures=extras_db.sync_failure_summary()
    return {'status':'ok','version':settings.version,'scopus_configured':bool(settings.scopus_api_key) and settings.scopus_enabled,'google_scholar_configured':bool(settings.serpapi_api_key) and settings.scholar_enabled,'university':settings.university_name,
            'last_sync_success':failures['last_success']['finished_at'] if failures['last_success'] else None,
            'last_sync_failure':failures['last_failure']['finished_at'] if failures['last_failure'] else None,
            'failing_sources':[row['source'] for row in failures['failing_sources'] if row['failures']],
            'circuit_breakers_open':[state['provider'] for state in failures['circuit_breakers'] if state['open']]}

@app.post('/api/v1/auth/login',response_model=LoginResponse)
def login(request: LoginRequest):
    token=auth.authenticate(request.username,request.password)
    if not token:
        raise HTTPException(401,'Invalid username or password.')
    return {'access_token':token,'token_type':'bearer','expires_in':settings.auth_session_hours*3600,'username':request.username}

@app.post('/api/v1/faculty/search',response_model=list[ScopusSearchPublication])
def search(req: FacultySearchRequest):
    results,err=service.publication_search(req.name,req.institution)
    if err: raise HTTPException(503,err)
    if not results: return []
    return results

@app.post('/api/v1/faculty/select')
def select(candidate: FacultyCandidate, institution: str=''):
    raise HTTPException(403,'Faculty profiles cannot be created from Scopus Search publication records or client-supplied Author IDs. Official Scopus Author Search requires the appropriate Elsevier API entitlement.')

@app.get('/api/v1/faculty/{fid}/monthly-history')
def monthly_metrics_history(fid:str):
    if not db.get_faculty(fid): raise HTTPException(404,'Faculty not found.')
    months=db.monthly_metric_history(fid)
    return {'faculty_id':fid,'start_month':months[0]['month'],'end_month':months[-1]['month'],'periods':len(months),'months':months}

@app.get('/api/v1/faculty/{fid}',response_model=FacultyProfile)
def get_profile(fid:str):
    p=service.profile(fid)
    if not p: raise HTTPException(404,'Faculty not found.')
    return p

@app.patch('/api/v1/faculty/{fid}')
def update_profile(fid:str, payload: dict):
    f=db.get_faculty(fid)
    if not f: raise HTTPException(404,'Faculty not found.')
    allowed={k:payload.get(k) for k in ['department','designation','orcid','google_scholar_url'] if k in payload}
    interests=payload.get('research_interests') if 'research_interests' in payload else None
    photo_url=payload.get('photo_url') if 'photo_url' in payload else None
    if interests is not None and (not isinstance(interests,list) or any(not isinstance(item,str) or not item.strip() for item in interests)):
        raise HTTPException(400,'Research interests must be a list of non-empty strings.')
    if photo_url and (not isinstance(photo_url,str) or not photo_url.startswith(('http://','https://'))):
        raise HTTPException(400,'Photo URL must be an http or https URL.')
    if 'google_scholar_url' in allowed and allowed['google_scholar_url']:
        import re
        if not re.search(r'https?://scholar\.google\.com/citations\?user=', allowed['google_scholar_url']):
            raise HTTPException(400,'Google Scholar URL must be a public profile URL containing ?user=...')
    with db.conn() as c:
        fields=[]; vals=[]
        for k,v in allowed.items(): fields.append(f'{k}=?'); vals.append(v)
        if fields:
            fields.append('updated_at=?'); vals.append(db.now()); vals.append(fid)
            c.execute('UPDATE faculty SET '+','.join(fields)+' WHERE id=?',vals)
    if 'research_interests' in payload or 'photo_url' in payload:
        db.update_faculty_profile(fid,interests,photo_url)
    return service.profile(fid)

@app.post('/api/v1/faculty/{fid}/sync',response_model=SyncResponse)
def sync(fid:str):
    try: return service.sync(fid)
    except ValueError as e: raise HTTPException(404,str(e))

@app.get('/api/v1/faculty/{fid}/history')
def metrics_history(fid:str): return db.metric_history(fid)

@app.get('/api/v1/history',response_model=list[SearchHistoryItem])
def search_history(): return db.history()

@app.delete('/api/v1/history')
def clear_history(): db.clear_history(); return {'status':'ok'}

@app.delete('/api/v1/history/{item_id}')
def delete_history(item_id:int): db.delete_history_item(item_id); return {'status':'ok'}

@app.get('/api/v1/config')
def config(): return {'university':settings.university_name,'sync_interval_hours':settings.sync_interval_hours,'scopus_live':bool(settings.scopus_api_key) and settings.scopus_enabled,'scholar_live':bool(settings.serpapi_api_key) and settings.scholar_enabled}

@app.get('/api/v1/authors',response_model=VFSTRAuthorPage)
def list_vfstr_authors(search: str='',department: str='',school: str='',designation: str='',research_area: str='',has_google_scholar: bool|None=None,has_scopus: bool|None=None,data_status: str='',sort: str='name_az',page: int=Query(1,ge=1),page_size: int=Query(24,ge=1,le=100)):
    return vfstr_authors.list_authors(search,department,school,designation,research_area,has_google_scholar,has_scopus,data_status,sort,page,page_size)

@app.get('/api/v1/authors/departments',response_model=VFSTRDepartments)
def vfstr_author_departments():
    return vfstr_authors.departments()

@app.post('/api/v1/authors/refresh',response_model=VFSTRAuthorRefreshResult)
def refresh_vfstr_authors():
    provider=vfstr_authors.OfficialVFSTRCSERosterProvider()
    result=vfstr_authors.refresh_from_provider(provider)
    _,author_count=db.vfstr_authors(limit=1)
    return {
        **result,
        'source_url':provider.source_url,
        'author_count':author_count,
        'last_updated':db.vfstr_authors_last_updated(),
    }

@app.post('/api/v1/authors/metrics/import')
def import_vfstr_metrics_snapshot():
    try:
        return vfstr_cse_metrics.import_april_2026_metrics()
    except vfstr_cse_metrics.SpreadsheetImportError as error:
        raise HTTPException(503,str(error))

@app.get('/api/v1/authors/metrics/import-report')
def get_vfstr_metrics_import_report():
    report=vfstr_cse_metrics.latest_import_report()
    if not report:
        raise HTTPException(404,'No VFSTR CSE metrics import report is available.')
    return report

@app.get('/api/v1/authors/{author_id}/profile',response_model=FacultyProfile)
def open_vfstr_author_profile(author_id: str):
    faculty_id=vfstr_authors.open_profile(author_id)
    if not faculty_id:
        raise HTTPException(404,'Verified VFSTR author not found.')
    return service.profile(faculty_id)

@app.get('/api/v1/authors/{author_id}',response_model=VFSTRAuthor)
def get_vfstr_author(author_id: str):
    author=vfstr_authors.get_author(author_id)
    if not author:
        raise HTTPException(404,'Verified VFSTR author not found.')
    return author
