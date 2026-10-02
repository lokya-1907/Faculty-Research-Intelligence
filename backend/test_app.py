import os, tempfile
from datetime import datetime, timezone
import pytest
import io
import zipfile
os.environ['DATABASE_PATH']=os.path.join(tempfile.gettempdir(),'researchpulse_test.db')
os.environ['AUTH_REQUIRED']='false'
from fastapi.testclient import TestClient
from app.main import app
from app.services import auth, db, scopus, service, scholar, vfstr_authors, vfstr_cse_metrics


@pytest.fixture
def directory_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db.settings,'database_path',str(tmp_path/'directory.db'))
    db.init_db()
    return db


def verified_directory_test_record(name='Test Author',department='Computer Science',school='Engineering',**overrides):
    record={
        'name':name,
        'institution':"Vignan's Foundation for Science, Technology and Research University",
        'department':department,
        'school':school,
        'scopus_author_id':None,
        'orcid':None,
        'profile_url':None,
        'publication_count':None,
        'citation_count':None,
        'h_index':None,
        'i10_index':None,
        'source':'VFSTR verified roster test fixture',
        'source_url':'https://example.org/test-roster',
        'verified':True,
    }
    record.update(overrides)
    return record

def test_auth_token_round_trip(monkeypatch):
    monkeypatch.setattr(auth.settings,'auth_username','test-user')
    monkeypatch.setattr(auth.settings,'auth_password','test-password')
    monkeypatch.setattr(auth.settings,'auth_secret','test-secret')
    monkeypatch.setattr(auth.settings,'auth_session_hours',1)
    token=auth.authenticate('test-user','test-password')
    assert token
    assert auth.verify_token(token)['sub']=='test-user'
    assert auth.authenticate('test-user','wrong-password') is None

def test_scholar_provider_parses_current_author_metrics(monkeypatch):
    class Response:
        is_success=True
        status_code=200
        def json(self):
            return {'author':{'name':'Test Scholar'},'cited_by':{'table':[{'citations':{'all':27}},{'h_index':{'all':2}},{'i10_index':{'all':1}}]},'articles':[]}
    monkeypatch.setattr(scholar.settings,'serpapi_api_key','test-key')
    monkeypatch.setattr(scholar.httpx,'get',lambda *args,**kwargs:Response())
    result=scholar.fetch('https://scholar.google.com/citations?user=test')
    assert result['metrics']=={'citations':27,'h_index':2,'i10_index':1}

def test_profile_edits_and_monthly_history_are_exposed(tmp_path, monkeypatch):
    monkeypatch.setattr(db.settings,'database_path',str(tmp_path/'profile-edits.db'))
    db.init_db()
    db.upsert_faculty({'id':'editable-faculty','name':'Editable Faculty','institution':'VFSTR'})
    db.upsert_metric_snapshot('editable-faculty',{'source':'vfstr_cse_dataset','snapshot_date':'2026-04','google_scholar_citations':48,'scopus_citations':36,'data_status':'Updated'})
    with TestClient(app) as client:
        update=client.patch('/api/v1/faculty/editable-faculty',json={'research_interests':['Networks','Data Mining'],'photo_url':'https://example.org/faculty.png'})
        monthly=client.get('/api/v1/faculty/editable-faculty/monthly-history')
    assert update.status_code==200
    assert update.json()['research_interests']==['Networks','Data Mining']
    assert update.json()['photo_url']=='https://example.org/faculty.png'
    assert monthly.status_code==200
    current_month=datetime.now(timezone.utc).strftime('%Y-%m')
    assert monthly.json()['start_month']==current_month
    assert monthly.json()['periods']==12
    assert len(monthly.json()['months'])==12
    assert monthly.json()['months'][0]['month']==current_month

def test_health():
    db.init_db()
    with TestClient(app) as c:
        r=c.get('/api/v1/health'); assert r.status_code==200; assert r.json()['status']=='ok'

def test_history_empty():
    db.init_db(); db.clear_history()
    with TestClient(app) as c:
        assert c.get('/api/v1/history').status_code==200

def test_author_query_normalizes_surname_first_names():
    assert scopus._author_query('Kumar, Raj') == 'AUTHOR-NAME("Raj, Kumar")'
    assert scopus._author_query('Peram, S.R.') == 'AUTHOR-NAME("S.R., Peram")'
    assert scopus._author_query('Raj Kumar') == 'AUTHOR-NAME("Kumar, Raj")'


def test_scopus_search_returns_publications_not_author_identities(monkeypatch):
    publication={
        'dc:creator':'Other Researcher',
        'dc:title':'A Scopus-indexed publication',
        'dc:identifier':'SCOPUS_ID:123456789',
        'citedby-count':'4',
        'affiliation':[{'affilname':'Example University'}],
    }
    monkeypatch.setattr(scopus, '_search_all_documents', lambda query, count: ([publication], 1))

    results=scopus.search_publications('Sundari, P Neela')

    assert results == [publication]
    assert 'dc:creator' in results[0]
    assert 'scopus_author_id' not in results[0]

def test_search_endpoint_keeps_affiliation_optional(monkeypatch):
    captured={}

    def fake_search(name, institution):
        captured['name']=name
        captured['institution']=institution
        return [], None

    monkeypatch.setattr(service, 'publication_search', fake_search)
    with TestClient(app) as c:
        response=c.post('/api/v1/faculty/search',json={'name':'Sundari, P Neela'})

    assert response.status_code==200
    assert captured=={'name':'Sundari, P Neela','institution':''}

def test_scopus_search_retries_after_100_record_service_limit(monkeypatch):
    page_sizes=[]

    def fake_request(path, params):
        page_sizes.append(params['count'])
        if params['count']==100:
            raise scopus.ScopusError('Scopus API returned HTTP 400: Exceeds the maximum number allowed for the service level')
        return {'search-results': {'entry': [{'dc:title': 'Research paper'}], 'opensearch:totalResults': '1'}}

    monkeypatch.setattr(scopus, 'request', fake_request)
    entries, total = scopus._search_documents('AUTHOR-NAME("Kumar, Raj")', 100)

    assert page_sizes == [100, 25]
    assert len(entries) == 1
    assert total == 1


def test_search_all_documents_fetches_later_pages_and_deduplicates(monkeypatch):
    starts=[]

    def fake_request(path, params):
        starts.append(params['start'])
        first=params['start']
        last=min(first+params['count'],30)
        entries=[{'eid':f'doc-{index}'} for index in range(first,last)]
        return {'search-results': {
            'entry': entries,
            'opensearch:totalResults': '30',
        }}

    monkeypatch.setattr(scopus, 'request', fake_request)

    entries, total=scopus._search_all_documents('AUTHOR-NAME("Sundari")')

    assert starts == [0,25]
    assert len(entries) == 30
    assert total == 30


def test_empty_result_placeholder_produces_no_publications(monkeypatch):
    def fake_request(path, params):
        return {'search-results': {
            'entry': [{'@_fa': 'true', 'error': 'Result set was empty'}],
            'opensearch:totalResults': '0',
        }}

    monkeypatch.setattr(scopus, 'request', fake_request)

    assert scopus.search_publications('Dr. Raj Kumar', 'Vignan University') == []


def test_publication_creator_is_not_promoted_to_candidate(monkeypatch):
    publication = {
        'dc:creator': 'Raj Kumar',
        'dc:title': 'Verified research publication',
        'dc:identifier': 'SCOPUS_ID:123456789',
        'citedby-count': '12',
        'affiliation': [{'affilname': 'Vignan University'}],
    }

    monkeypatch.setattr(scopus, '_search_all_documents', lambda query, count: ([publication], 1))

    results = scopus.search_publications('Raj Kumar', 'Vignan University')

    assert len(results) == 1
    assert results[0]['dc:creator'] == 'Raj Kumar'
    assert 'scopus_author_id' not in results[0]


def test_client_supplied_author_id_cannot_create_faculty_profile():
    candidate={
        'id':'client-supplied-profile',
        'name':'Unverified person',
        'affiliation':'Example University',
        'source':'scopus',
        'scopus_author_id':'fabricated-id',
    }

    with TestClient(app) as c:
        response=c.post('/api/v1/faculty/select',json=candidate)

    assert response.status_code==403
    assert 'client-supplied Author IDs' in response.json()['detail']


def test_author_profile_authorization_error_explains_entitlement(monkeypatch):
    response=scopus.httpx.Response(401,json={'service-error':{'status':{'statusCode':'AUTHORIZATION_ERROR','statusText':'Not entitled'}}})
    monkeypatch.setattr(scopus.settings, 'scopus_api_key', 'configured-test-key')
    monkeypatch.setattr(scopus, 'headers', lambda: {'Accept':'application/json'})
    monkeypatch.setattr(scopus.httpx, 'get', lambda *args, **kwargs: response)

    try:
        scopus.request('/search/author', {'query':'AUTHLASTNAME(Sundari)'})
        assert False, 'Expected Scopus authorization error.'
    except scopus.ScopusError as error:
        assert 'Official Scopus Author Profile lookup' in str(error)
        assert 'Elsevier API entitlement' in str(error)


def test_sync_without_verified_author_id_does_not_assign_publications(monkeypatch):
    faculty={'id':'faculty-test','name':'Sundari P. Neela','institution':'Example University','scopus_author_id':None,'google_scholar_url':None}
    monkeypatch.setattr(db, 'get_faculty', lambda fid: faculty)
    monkeypatch.setattr(db, 'log_sync', lambda *args: None)
    monkeypatch.setattr(scopus.settings, 'scopus_api_key', 'configured-test-key')
    monkeypatch.setattr(scopus.settings, 'scopus_enabled', True)
    monkeypatch.setattr(scopus, 'documents', lambda **kwargs: (_ for _ in ()).throw(AssertionError('Must not search publications by name for a profile.')))

    result=service.sync('faculty-test')

    assert result['status'] == 'partial'
    assert result['statuses']['scopus_search'] == 'author_id_required'
    assert 'verified Scopus Author ID is required' in result['message']


def test_publication_retrieval_uses_bounded_page_size(monkeypatch):
    page_sizes=[]

    def fake_request(path, params):
        page_sizes.append(params['count'])
        return {'search-results': {'entry': [], 'opensearch:totalResults': '0'}}

    monkeypatch.setattr(scopus, 'request', fake_request)

    assert scopus.documents(author_id='12345') == []
    assert page_sizes == [25]


def test_vfstr_author_listing_empty_and_department_facets(directory_db):
    with TestClient(app) as c:
        listing=c.get('/api/v1/authors')
        departments=c.get('/api/v1/authors/departments')

    assert listing.status_code==200
    assert listing.json()['total']==0
    assert listing.json()['authors']==[]
    assert departments.status_code==200
    assert departments.json()=={'departments':[],'schools':[],'designations':[],'research_areas':[]}


def test_vfstr_author_listing_search_department_sort_and_missing_metrics(directory_db):
    first=vfstr_authors.upsert_verified_author(verified_directory_test_record('Zeta Author',department='Computer Science',designation='Professor (CSE)',research_interests=['Machine Learning'],citation_count=8,publication_count=3,h_index=2))
    second=vfstr_authors.upsert_verified_author(verified_directory_test_record('Alpha Author',department='Electrical Engineering',school='Technology',citation_count=20,publication_count=7,h_index=4))

    with TestClient(app) as c:
        listing=c.get('/api/v1/authors?department=Computer+Science&sort=citations')
        searched=c.get('/api/v1/authors?search=Electrical&sort=name_az')
        by_designation=c.get('/api/v1/authors?designation=Professor+(CSE)')
        by_research_area=c.get('/api/v1/authors?research_area=machine+learning')
        facets=c.get('/api/v1/authors/departments')
        detail=c.get('/api/v1/authors/'+second)

    assert listing.status_code==200
    assert listing.json()['total']==1
    assert listing.json()['authors'][0]['id']==first
    assert listing.json()['authors'][0]['i10_index'] is None
    assert searched.status_code==200
    assert [author['name'] for author in searched.json()['authors']]==['Alpha Author']
    assert by_designation.status_code==200 and [author['name'] for author in by_designation.json()['authors']]==['Zeta Author']
    assert by_research_area.status_code==200 and [author['name'] for author in by_research_area.json()['authors']]==['Zeta Author']
    assert 'Professor (CSE)' in facets.json()['designations']
    assert 'Machine Learning' in facets.json()['research_areas']
    assert detail.status_code==200
    assert detail.json()['source']=='VFSTR verified roster test fixture'
    assert detail.json()['citation_count']==20


def test_vfstr_author_department_filter_and_duplicate_upsert_preserves_metrics(directory_db):
    original=verified_directory_test_record('Duplicate Author',department='Computer Science',citation_count=19,h_index=3)
    author_id=vfstr_authors.upsert_verified_author(original)
    repeated={**original,'department':'','citation_count':None,'h_index':None,'source_url':'https://example.org/updated-roster'}
    assert vfstr_authors.upsert_verified_author(repeated)==author_id

    with TestClient(app) as c:
        match=c.get('/api/v1/authors?department=Computer+Science')
        other=c.get('/api/v1/authors?department=Physics')

    assert match.status_code==200
    assert match.json()['total']==1
    assert match.json()['authors'][0]['citation_count']==19
    assert match.json()['authors'][0]['h_index']==3
    assert other.status_code==200
    assert other.json()['total']==0


def test_vfstr_author_detail_missing_returns_404(directory_db):
    with TestClient(app) as c:
        response=c.get('/api/v1/authors/not-a-directory-record')
    assert response.status_code==404


def test_unverified_or_non_vfstr_author_is_rejected(directory_db):
    unverified=verified_directory_test_record('Unverified',verified=False)
    non_vfstr=verified_directory_test_record('Other Institution',institution='Other University')

    with pytest.raises(vfstr_authors.DirectorySourceError):
        vfstr_authors.upsert_verified_author(unverified)
    with pytest.raises(vfstr_authors.DirectorySourceError):
        vfstr_authors.upsert_verified_author(non_vfstr)
    assert vfstr_authors.list_authors()['total']==0


def test_directory_provider_failure_preserves_existing_records(directory_db):
    vfstr_authors.upsert_verified_author(verified_directory_test_record('Existing Verified Author',citation_count=6))

    class FailingProvider:
        def fetch_authors(self):
            raise RuntimeError('Source temporarily unavailable')

    result=vfstr_authors.refresh_from_provider(FailingProvider())

    assert result['status']=='error'
    assert result['updated']==0
    assert vfstr_authors.list_authors()['total']==1
    assert vfstr_authors.list_authors()['authors'][0]['citation_count']==6


def test_official_directory_refresh_reports_import_count(directory_db):
    class TestProvider:
        unparsed_cards=0
        def fetch_authors(self):
            return [verified_directory_test_record('Refresh Test Author',research_interests=['Networks'])]

    result=vfstr_authors.refresh_from_provider(TestProvider())

    assert result['status']=='ok'
    assert result['records_found']==1
    assert result['updated']==1
    assert result['failed_records']==[]
    assert vfstr_authors.list_authors()['total']==1


def test_official_directory_refresh_route_reports_source_and_total(directory_db, monkeypatch):
    class TestProvider:
        source_url='https://example.org/test-roster'
        unparsed_cards=0
        def fetch_authors(self):
            return [verified_directory_test_record('Refresh Route Test Author')]

    monkeypatch.setattr(vfstr_authors,'OfficialVFSTRCSERosterProvider',TestProvider)

    with TestClient(app) as c:
        response=c.post('/api/v1/authors/refresh')

    assert response.status_code==200
    assert response.json()['status']=='ok'
    assert response.json()['updated']==1
    assert response.json()['author_count']==1
    assert response.json()['source_url']==TestProvider.source_url


def test_official_vfstr_provider_parses_published_card_fields():
    html='''<div class="faculty-card"><div class="img-11"><img class="faculty-img" src="../../Facultyprofiles/uploads/163/profilepic163.png"></div><p class="aboutus-div-56">dr . k.v. krishna kishore</p><div class="faculty-details"><p class="faculty-branch">PROFESSOR (CSE)</p><p class="faculty-interest">Machine Learning<br>Neural Networks<br></p></div></div>'''

    class Response:
        text=html
        def raise_for_status(self):
            return None

    provider=vfstr_authors.OfficialVFSTRCSERosterProvider(http_get=lambda *args,**kwargs:Response())
    records=provider.fetch_authors()

    assert len(records)==1
    assert records[0]['name']=='Dr. K.V. Krishna Kishore'
    assert records[0]['designation']=='Professor (CSE)'
    assert records[0]['department']=='CSE'
    assert records[0]['institution']=="Vignan's Foundation for Science, Technology and Research"
    assert records[0]['campus']=='Vadlamudi'
    assert records[0]['research_interests']==['Machine Learning','Neural Networks']
    assert records[0]['photo_url']=='https://vignan.ac.in/Facultyprofiles/uploads/163/profilepic163.png'
    assert records[0]['verified'] is True
    assert records[0]['source_url']==vfstr_authors.OFFICIAL_CSE_URL


def test_verified_directory_author_opens_existing_profile_with_metrics_unavailable(directory_db):
    author_id=vfstr_authors.upsert_verified_author(verified_directory_test_record(
        'Official Faculty',designation='Assistant Professor (CSE)',campus='Vadlamudi',
        research_interests=['Data Mining'],
    ))

    with TestClient(app) as c:
        response=c.get('/api/v1/authors/'+author_id+'/profile')
        author=c.get('/api/v1/authors/'+author_id)

    assert response.status_code==200
    profile=response.json()
    assert profile['name']=='Official Faculty'
    assert profile['scopus_author_id'] is None
    assert profile['publications']==[]
    assert profile['metrics']==[]
    assert author.status_code==200
    assert author.json()['faculty_id']==profile['id']
    assert author.json()['research_interests']==['Data Mining']



def test_april_spreadsheet_import_preserves_official_identity_and_nullable_metrics(directory_db):
    official=vfstr_authors.upsert_verified_author(verified_directory_test_record('Test Author',department='CSE',designation='Professor (CSE)'))
    header=['Sl.No','Name of the Faculty','Department','I10 Index (as per Google Scholar)','Professional membership details','h Index (as per Google Scholar)','Citations (as per Google Schoolar)','h Index (as per Scopus)','Citations (as per Scopus)','Google Scholar Profile URL','Scopus Profile URL','Updated/not updated']
    lines=[
        'VFSTR DEEMED TO BE UNIVERSITY',
        'CSE Department Faculty H-index April 2026',
        ','.join(header),
        '1,Prof. Dr. Test Author,CSE,0,ACM,2,,1,12,https://scholar.google.com/citations?user=test,https://www.scopus.com/authid/detail.uri?authorId=36086160000,Not Updated',
    ]
    csv_bytes='\n'.join(lines).encode('utf-8')

    class Response:
        content=csv_bytes
        def raise_for_status(self):
            return None

    report=vfstr_cse_metrics.import_april_2026_metrics(http_get=lambda *args,**kwargs:Response())
    directory=directory_db.vfstr_author(official)
    faculty=directory_db.get_faculty(directory['faculty_id'])
    snapshots=directory_db.metric_history(directory['faculty_id'])

    assert report['matched_count']==1
    assert report['imported_count']==1
    assert report['not_updated_status_count']==1
    assert directory['name']=='Test Author'
    assert directory['google_scholar_i10']==0
    assert directory['google_scholar_citations'] is None
    assert directory['scopus_author_id']=='36086160000'
    assert directory['data_status']=='Not Updated'
    assert directory['metrics_date']=='2026-04'
    assert faculty['name']=='Test Author'
    assert faculty['scopus_author_id']=='36086160000'
    assert snapshots[0]['snapshot_date']=='2026-04'
    assert snapshots[0]['source']=='vfstr_cse_dataset'
    assert snapshots[0]['data_status']=='Not Updated'
    assert report['snapshot_date']=='2026-04'
    saved_report=db.latest_vfstr_metric_import_report()
    assert saved_report['report']['matched_count']==1
    assert saved_report['report']['unmatched_vfstr_count']==0

    repeated=vfstr_cse_metrics.import_april_2026_metrics(http_get=lambda *args,**kwargs:Response())
    assert repeated['imported_count']==1
    assert len(directory_db.metric_history(directory['faculty_id']))==1


def test_vfstr_april_source_and_status_filters(directory_db):
    first=vfstr_authors.upsert_verified_author(verified_directory_test_record('Scholar And Scopus',google_scholar_url='https://scholar.google.com/citations?user=abc',scopus_author_id='123456',data_status='Updated'))
    second=vfstr_authors.upsert_verified_author(verified_directory_test_record('Scholar Only',google_scholar_url='https://scholar.google.com/citations?user=def',data_status='Not Updated'))
    db.update_vfstr_research_data(first,{'google_scholar_url':'https://scholar.google.com/citations?user=abc','scopus_author_id':'123456','data_status':'Updated','metrics_date':'2026-04'})
    db.update_vfstr_research_data(second,{'google_scholar_url':'https://scholar.google.com/citations?user=def','data_status':'Not Updated','metrics_date':'2026-04'})

    with TestClient(app) as c:
        scholar=c.get('/api/v1/authors?has_google_scholar=true')
        scopus=c.get('/api/v1/authors?has_scopus=true')
        not_updated=c.get('/api/v1/authors?data_status=Not+Updated')

    assert scholar.status_code==200 and scholar.json()['total']==2
    assert scopus.status_code==200 and scopus.json()['total']==1
    assert not_updated.status_code==200 and [x['name'] for x in not_updated.json()['authors']]==['Scholar Only']


def test_spreadsheet_reader_preserves_embedded_profile_hyperlinks():
    headers=['Sl.No','Name of the Faculty','Department','I10 Index (as per Google Scholar)','Professional membership details','h Index (as per Google Scholar)','Citations (as per Google Schoolar)','h Index (as per Scopus)','Citations (as per Scopus)','Google Scholar Profile URL','Scopus Profile URL','Updated/not updated']
    csv_content='\n'.join(['Title','Subtitle',','.join(headers),'1,Test Author,CSE,,,,,,,Scholar display,Scopus display,Updated']).encode()
    workbook_buffer=io.BytesIO()
    with zipfile.ZipFile(workbook_buffer,'w') as workbook:
        workbook.writestr('xl/worksheets/sheet1.xml','''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><hyperlinks><hyperlink ref="J4" r:id="rId1"/><hyperlink ref="K4" r:id="rId2"/></hyperlinks></worksheet>''')
        workbook.writestr('xl/worksheets/_rels/sheet1.xml.rels','''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="https://scholar.google.com/citations?user=verified" TargetMode="External"/><Relationship Id="rId2" Target="https://www.scopus.com/authid/detail.uri?authorId=123456" TargetMode="External"/></Relationships>''')

    class Response:
        def __init__(self,content): self.content=content
        def raise_for_status(self): return None

    def fake_get(url,**kwargs):
        return Response(workbook_buffer.getvalue() if url.endswith('format=xlsx') else csv_content)

    rows=vfstr_cse_metrics._read_sheet_rows(fake_get)

    assert rows[0]['Google Scholar Profile URL']=='https://scholar.google.com/citations?user=verified'
    assert rows[0]['Scopus Profile URL']=='https://www.scopus.com/authid/detail.uri?authorId=123456'
