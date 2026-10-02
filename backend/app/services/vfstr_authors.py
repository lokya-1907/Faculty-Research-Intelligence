import hashlib
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
import httpx
from app.services import db


SORT_OPTIONS = {'name_az','name_za','google_scholar_citations','scopus_citations','google_scholar_h_index','scopus_h_index','google_scholar_i10_index','publications','citations','h_index'}
OFFICIAL_CSE_URL = 'https://vignan.ac.in/newvignan/departments/deptpeople.php?deptid=sch3_dept1&deptnm=CSE&school=sch3'
OFFICIAL_SOURCE_NAME = 'Official VFSTR CSE faculty directory'
VFSTR_INSTITUTION = "Vignan's Foundation for Science, Technology and Research"


class DirectorySourceError(RuntimeError):
    pass


class _FacultyCardParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.records=[]
        self.unparsed_cards=0
        self._div_depth=0
        self._card_depth=None
        self._card=None
        self._capture=None

    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag=='div':
            self._div_depth+=1
            classes=(attrs.get('class') or '').split()
            if 'faculty-card' in classes:
                self._card_depth=self._div_depth
                self._card={'name':[],'designation':[],'interests':[],'photo_url':None}
        if self._card is not None and tag=='img' and not self._card.get('photo_url'):
            self._card['photo_url']=attrs.get('src')
        elif self._card is not None and tag=='p':
            classes=(attrs.get('class') or '').split()
            self._capture={
                'aboutus-div-56':'name',
                'faculty-branch':'designation',
                'faculty-interest':'interests',
            }.get(next((item for item in classes if item in {'aboutus-div-56','faculty-branch','faculty-interest'}),''))
        elif self._card is not None and tag=='br' and self._capture=='interests':
            self._card['interests'].append('\n')

    def handle_data(self, data):
        if self._card is not None and self._capture:
            self._card[self._capture].append(data)

    def handle_endtag(self, tag):
        if tag=='p':
            self._capture=None
        if tag=='div':
            if self._card is not None and self._div_depth==self._card_depth:
                self._finish_card()
            self._div_depth=max(0,self._div_depth-1)

    def _finish_card(self):
        text=lambda field: re.sub(r'\s+',' ',''.join(self._card[field])).strip()
        name=text('name')
        if name:
            self.records.append({
                'raw_name':name,
                'designation':text('designation'),
                'research_interests':[value.strip() for value in re.split(r'[\n;]+',''.join(self._card['interests'])) if value.strip()],
                'photo_url':self._card.get('photo_url'),
            })
        else:
            self.unparsed_cards+=1
        self._card=None
        self._card_depth=None
        self._capture=None


class OfficialVFSTRCSERosterProvider:
    source_url=OFFICIAL_CSE_URL

    def __init__(self, http_get=None):
        self.http_get=http_get or httpx.get
        self.unparsed_cards=0

    @staticmethod
    def _display_name(raw_name):
        cleaned=re.sub(r'\s+',' ',raw_name).strip()
        match=re.match(r'(?i)^(dr|mr|mrs|ms)\s*\.\s*(.*)$',cleaned)
        if match:
            prefix=match.group(1).capitalize()+'.'
            cleaned=match.group(2).lstrip(' .')
        else:
            prefix=''
        display=cleaned.title()
        return (prefix+' '+display).strip()

    @staticmethod
    def _display_designation(value):
        if not value:
            return None
        designation=re.sub(r'\s+',' ',value).strip().title()
        return re.sub(r'\bCse\b','CSE',designation)

    def fetch_authors(self):
        try:
            response=self.http_get(self.source_url,timeout=45,follow_redirects=True,headers={'User-Agent':'ResearchPulse/1.0 (VFSTR directory import)'})
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise DirectorySourceError(f'Official VFSTR CSE directory request failed: {error}') from error
        parser=_FacultyCardParser()
        parser.feed(response.text)
        self.unparsed_cards=parser.unparsed_cards
        if not parser.records:
            raise DirectorySourceError('Official VFSTR CSE directory returned no parseable faculty cards; existing records were left unchanged.')
        return [{
            'name':self._display_name(record['raw_name']),
            'institution':VFSTR_INSTITUTION,
            'department':'CSE',
            'designation':self._display_designation(record['designation']),
            'campus':'Vadlamudi',
            'research_interests':record['research_interests'] or None,
            'photo_url':urljoin(self.source_url,record.get('photo_url') or '') or None,
            'source':OFFICIAL_SOURCE_NAME,
            'source_url':self.source_url,
            'verified':True,
        } for record in parser.records]


def _is_vfstr_affiliation(value):
    normalized=re.sub(r'[^a-z0-9]+',' ',(value or '').lower()).strip()
    return 'vfstr' in normalized or all(token in normalized for token in ('vignan', 'foundation', 'technology', 'research'))


def _validate_source_record(record):
    name=(record.get('name') or '').strip()
    institution=(record.get('institution') or '').strip()
    source=(record.get('source') or '').strip()
    source_url=(record.get('source_url') or '').strip()
    parsed=urlparse(source_url)
    if not name or not institution:
        raise DirectorySourceError('Directory records require a name and institution.')
    if not _is_vfstr_affiliation(institution):
        raise DirectorySourceError('Directory records must have a verifiable VFSTR institution association.')
    if not record.get('verified') or not source or parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise DirectorySourceError('Directory records require a verified flag, named source, and source URL.')
    if record.get('faculty_id') and not db.get_faculty(record['faculty_id']):
        raise DirectorySourceError('Linked ResearchPulse faculty profile does not exist.')


def upsert_verified_author(record):
    """Persist an author only when an upstream provider supplies verifiable provenance."""
    _validate_source_record(record)
    scopus_id=(record.get('scopus_author_id') or '').strip()
    stable_key=scopus_id or '|'.join((record['name'].strip().casefold(),record['institution'].strip().casefold(),record['source_url'].strip()))
    author_id='vfstr_'+hashlib.sha256(stable_key.encode()).hexdigest()[:20]
    values={**record,'id':author_id,'name':record['name'].strip(),'institution':record['institution'].strip()}
    for field in ('faculty_id','department','designation','school','campus','scopus_author_id','orcid','profile_url'):
        value=values.get(field)
        values[field]=value.strip() or None if isinstance(value,str) else value
    return db.upsert_vfstr_author(values)


def open_profile(author_id):
    author=db.vfstr_author(author_id)
    if not author:
        return None
    faculty_id=author.get('faculty_id')
    if not faculty_id or not db.get_faculty(faculty_id):
        faculty_id='fac_vfstr_'+hashlib.sha256(author['id'].encode()).hexdigest()[:16]
        db.upsert_faculty({
            'id':faculty_id,
            'name':author['name'],
            'institution':author['institution'],
            'department':author.get('department') or '',
            'designation':author.get('designation') or '',
            'orcid':author.get('orcid'),
            'scopus_author_id':author.get('scopus_author_id'),
        })
        db.link_vfstr_author_profile(author['id'],faculty_id)
    return faculty_id


def list_authors(search='', department='', school='', designation='', research_area='', has_google_scholar=None, has_scopus=None, data_status='', sort='name_az', page=1, page_size=24):
    if sort not in SORT_OPTIONS:
        sort='name_az'
    page=max(1,page)
    page_size=min(max(1,page_size),100)
    rows,total=db.vfstr_authors(search=search,department=department,school=school,designation=designation,research_area=research_area,has_google_scholar=has_google_scholar,has_scopus=has_scopus,data_status=data_status,sort=sort,limit=page_size,offset=(page-1)*page_size)
    return {
        'authors':rows,
        'total':total,
        'page':page,
        'page_size':page_size,
        'last_updated':db.vfstr_authors_last_updated(),
    }


def get_author(author_id):
    return db.vfstr_author(author_id)


def departments():
    return db.vfstr_author_departments()


def refresh_from_provider(provider):
    """Provider hook. A failure never clears or overwrites stored directory rows."""
    try:
        records=provider.fetch_authors()
    except Exception as error:
        return {
            'status':'error','records_found':0,'updated':0,'failed_records':[],
            'unparsed_cards':getattr(provider,'unparsed_cards',0),'error_message':str(error),
        }
    updated=0
    failures=[]
    for record in records:
        try:
            upsert_verified_author(record)
            updated+=1
        except Exception as error:
            failures.append({'name':record.get('name') or 'Name unavailable','error':str(error)})
    return {
        'status':'partial' if failures else 'ok',
        'records_found':len(records),
        'updated':updated,
        'failed_records':failures,
        'unparsed_cards':getattr(provider,'unparsed_cards',0),
        'error_message':None,
    }
