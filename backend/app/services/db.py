import json, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from app.core.config import settings


def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')

def conn():
    c=sqlite3.connect(settings.database_path)
    c.row_factory=sqlite3.Row
    return c

def init_db():
    with conn() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS faculty (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, institution TEXT NOT NULL,
          department TEXT DEFAULT '', designation TEXT DEFAULT '', orcid TEXT,
                    scopus_author_id TEXT, scopus_profile_url TEXT, google_scholar_url TEXT, research_interests TEXT, photo_url TEXT, created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS metrics (
          id INTEGER PRIMARY KEY AUTOINCREMENT, faculty_id TEXT NOT NULL, source TEXT NOT NULL,
          citations INTEGER, h_index INTEGER, i10_index INTEGER, publications INTEGER,
          google_scholar_citations INTEGER, google_scholar_h_index INTEGER, google_scholar_i10_index INTEGER,
          scopus_citations INTEGER, scopus_h_index INTEGER, data_status TEXT,
          snapshot_date TEXT, snapshot_source TEXT,
          captured_at TEXT NOT NULL, status TEXT DEFAULT 'ok',
          FOREIGN KEY(faculty_id) REFERENCES faculty(id)
        );
        CREATE TABLE IF NOT EXISTS publications (
          id TEXT PRIMARY KEY, faculty_id TEXT NOT NULL, title TEXT NOT NULL, year INTEGER,
          journal TEXT, doi TEXT, citations INTEGER, source TEXT NOT NULL, url TEXT,
          updated_at TEXT NOT NULL, FOREIGN KEY(faculty_id) REFERENCES faculty(id)
        );
        CREATE TABLE IF NOT EXISTS search_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT, faculty_id TEXT NOT NULL,
          searched_at TEXT NOT NULL, FOREIGN KEY(faculty_id) REFERENCES faculty(id)
        );
        CREATE TABLE IF NOT EXISTS sync_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT, faculty_id TEXT NOT NULL, source TEXT NOT NULL,
          status TEXT NOT NULL, message TEXT, synced_at TEXT NOT NULL,
          FOREIGN KEY(faculty_id) REFERENCES faculty(id)
        );
        CREATE TABLE IF NOT EXISTS vfstr_authors (
          id TEXT PRIMARY KEY,
          faculty_id TEXT UNIQUE,
          name TEXT NOT NULL,
          institution TEXT NOT NULL,
          department TEXT,
          designation TEXT,
          school TEXT,
          campus TEXT,
          research_interests TEXT,
          professional_memberships TEXT,
          photo_url TEXT,
          google_scholar_url TEXT,
          google_scholar_i10 INTEGER,
          google_scholar_h_index INTEGER,
          google_scholar_citations INTEGER,
          scopus_author_id TEXT UNIQUE,
          scopus_profile_url TEXT,
          scopus_h_index INTEGER,
          scopus_citations INTEGER,
          orcid TEXT,
          profile_url TEXT,
          publication_count INTEGER,
          citation_count INTEGER,
          h_index INTEGER,
          i10_index INTEGER,
          source TEXT NOT NULL,
          source_url TEXT NOT NULL,
          verified INTEGER NOT NULL DEFAULT 0 CHECK(verified IN (0,1)),
          data_status TEXT,
          metrics_date TEXT,
          last_updated TEXT NOT NULL,
          FOREIGN KEY(faculty_id) REFERENCES faculty(id)
        );
                CREATE TABLE IF NOT EXISTS vfstr_metric_import_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_url TEXT NOT NULL,
                    snapshot_date TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                );
        CREATE INDEX IF NOT EXISTS idx_metrics_faculty ON metrics(faculty_id, captured_at);
        CREATE INDEX IF NOT EXISTS idx_history_time ON search_history(searched_at DESC);
        CREATE INDEX IF NOT EXISTS idx_vfstr_authors_department ON vfstr_authors(department);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_vfstr_authors_identity ON vfstr_authors(lower(name), lower(institution));
        ''')
        columns={row['name'] for row in db.execute('PRAGMA table_info(vfstr_authors)')}
        directory_columns={
            'designation':'TEXT','campus':'TEXT','research_interests':'TEXT','professional_memberships':'TEXT',
            'photo_url':'TEXT','google_scholar_url':'TEXT','google_scholar_i10':'INTEGER','google_scholar_h_index':'INTEGER','google_scholar_citations':'INTEGER',
            'scopus_profile_url':'TEXT','scopus_h_index':'INTEGER','scopus_citations':'INTEGER','data_status':'TEXT','metrics_date':'TEXT',
        }
        for column,definition in directory_columns.items():
            if column not in columns:
                db.execute(f'ALTER TABLE vfstr_authors ADD COLUMN {column} {definition}')
        faculty_columns={row['name'] for row in db.execute('PRAGMA table_info(faculty)')}
        if 'scopus_profile_url' not in faculty_columns:
            db.execute('ALTER TABLE faculty ADD COLUMN scopus_profile_url TEXT')
        if 'research_interests' not in faculty_columns:
            db.execute('ALTER TABLE faculty ADD COLUMN research_interests TEXT')
        if 'photo_url' not in faculty_columns:
            db.execute('ALTER TABLE faculty ADD COLUMN photo_url TEXT')
        metric_columns={row['name'] for row in db.execute('PRAGMA table_info(metrics)')}
        metric_additions={
            'google_scholar_citations':'INTEGER','google_scholar_h_index':'INTEGER','google_scholar_i10_index':'INTEGER',
            'scopus_citations':'INTEGER','scopus_h_index':'INTEGER','data_status':'TEXT','snapshot_date':'TEXT','snapshot_source':'TEXT',
        }
        for column,definition in metric_additions.items():
            if column not in metric_columns:
                db.execute(f'ALTER TABLE metrics ADD COLUMN {column} {definition}')


def upsert_vfstr_author(data):
    timestamp=now()
    with conn() as db:
        existing=None
        if data.get('scopus_author_id'):
            existing=db.execute('SELECT id FROM vfstr_authors WHERE scopus_author_id=?',(data['scopus_author_id'],)).fetchone()
        if not existing:
            existing=db.execute('SELECT id FROM vfstr_authors WHERE lower(name)=lower(?) AND lower(institution)=lower(?)',(data['name'],data['institution'])).fetchone()
        author_id=existing['id'] if existing else data['id']
        db.execute('''INSERT INTO vfstr_authors(
            id,faculty_id,name,institution,department,designation,school,campus,research_interests,photo_url,scopus_author_id,orcid,profile_url,
            publication_count,citation_count,h_index,i10_index,source,source_url,verified,last_updated,
            google_scholar_url,google_scholar_i10,google_scholar_h_index,google_scholar_citations,
            scopus_h_index,scopus_citations,professional_memberships,data_status,metrics_date
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            faculty_id=COALESCE(excluded.faculty_id,vfstr_authors.faculty_id),
            department=COALESCE(excluded.department,vfstr_authors.department),
            designation=COALESCE(excluded.designation,vfstr_authors.designation),
            school=COALESCE(excluded.school,vfstr_authors.school),
            campus=COALESCE(excluded.campus,vfstr_authors.campus),
            research_interests=COALESCE(excluded.research_interests,vfstr_authors.research_interests),
            photo_url=COALESCE(excluded.photo_url,vfstr_authors.photo_url),
            scopus_author_id=COALESCE(excluded.scopus_author_id,vfstr_authors.scopus_author_id),
            orcid=COALESCE(excluded.orcid,vfstr_authors.orcid),
            profile_url=COALESCE(excluded.profile_url,vfstr_authors.profile_url),
            publication_count=COALESCE(excluded.publication_count,vfstr_authors.publication_count),
            citation_count=COALESCE(excluded.citation_count,vfstr_authors.citation_count),
            h_index=COALESCE(excluded.h_index,vfstr_authors.h_index),
            i10_index=COALESCE(excluded.i10_index,vfstr_authors.i10_index),
            google_scholar_url=COALESCE(excluded.google_scholar_url,vfstr_authors.google_scholar_url),
            google_scholar_i10=COALESCE(excluded.google_scholar_i10,vfstr_authors.google_scholar_i10),
            google_scholar_h_index=COALESCE(excluded.google_scholar_h_index,vfstr_authors.google_scholar_h_index),
            google_scholar_citations=COALESCE(excluded.google_scholar_citations,vfstr_authors.google_scholar_citations),
            scopus_h_index=COALESCE(excluded.scopus_h_index,vfstr_authors.scopus_h_index),
            scopus_citations=COALESCE(excluded.scopus_citations,vfstr_authors.scopus_citations),
            professional_memberships=COALESCE(excluded.professional_memberships,vfstr_authors.professional_memberships),
            data_status=COALESCE(excluded.data_status,vfstr_authors.data_status),
            metrics_date=COALESCE(excluded.metrics_date,vfstr_authors.metrics_date),
            source=excluded.source,source_url=excluded.source_url,verified=excluded.verified,last_updated=excluded.last_updated''',(
            author_id,data.get('faculty_id'),data['name'],data['institution'],data.get('department'),data.get('designation'),
            data.get('school'),data.get('campus'),json.dumps(data.get('research_interests')) if data.get('research_interests') else None,data.get('photo_url'),
            data.get('scopus_author_id'),data.get('orcid'),data.get('profile_url'),data.get('publication_count'),
            data.get('citation_count'),data.get('h_index'),data.get('i10_index'),data['source'],data['source_url'],
            int(bool(data['verified'])),timestamp,
            data.get('google_scholar_url'),data.get('google_scholar_i10'),data.get('google_scholar_h_index'),data.get('google_scholar_citations'),
            data.get('scopus_h_index'),data.get('scopus_citations'),data.get('professional_memberships'),data.get('data_status'),data.get('metrics_date')))
    return author_id


def update_vfstr_research_data(author_id,data):
    with conn() as db:
        cursor=db.execute('''UPDATE vfstr_authors SET
            google_scholar_url=COALESCE(?,google_scholar_url),google_scholar_i10=?,google_scholar_h_index=?,google_scholar_citations=?,
            scopus_author_id=COALESCE(?,scopus_author_id),scopus_profile_url=COALESCE(?,scopus_profile_url),scopus_h_index=?,scopus_citations=?,
            professional_memberships=COALESCE(?,professional_memberships),data_status=?,metrics_date=?,last_updated=?
            WHERE id=? AND verified=1''',(
            data.get('google_scholar_url'),data.get('google_scholar_i10'),data.get('google_scholar_h_index'),data.get('google_scholar_citations'),
            data.get('scopus_author_id'),data.get('scopus_profile_url'),data.get('scopus_h_index'),data.get('scopus_citations'),
            data.get('professional_memberships'),data.get('data_status'),data.get('metrics_date'),data.get('last_updated',now()),author_id))
        return cursor.rowcount==1


def vfstr_author_by_faculty_id(faculty_id):
    with conn() as db:
        row=db.execute('SELECT * FROM vfstr_authors WHERE faculty_id=? AND '+_VFSTR_AUTHOR_FILTER,(faculty_id,)).fetchone()
        return _vfstr_author_record(row) if row else None


def upsert_metric_snapshot(faculty_id,snapshot):
    captured_at=snapshot['snapshot_date']+'-01T00:00:00+00:00'
    values=(snapshot.get('citations'),snapshot.get('h_index'),snapshot.get('i10_index'),snapshot.get('publications'),
        snapshot.get('google_scholar_citations'),snapshot.get('google_scholar_h_index'),snapshot.get('google_scholar_i10_index'),
        snapshot.get('scopus_citations'),snapshot.get('scopus_h_index'),snapshot.get('data_status'),snapshot['snapshot_date'],
        snapshot['source'],captured_at,snapshot.get('status','ok'))
    with conn() as db:
        existing=db.execute('SELECT id FROM metrics WHERE faculty_id=? AND source=? AND snapshot_date=?',(faculty_id,snapshot['source'],snapshot['snapshot_date'])).fetchone()
        if existing:
            db.execute('''UPDATE metrics SET citations=?,h_index=?,i10_index=?,publications=?,google_scholar_citations=?,google_scholar_h_index=?,google_scholar_i10_index=?,scopus_citations=?,scopus_h_index=?,data_status=?,snapshot_date=?,snapshot_source=?,captured_at=?,status=? WHERE id=?''',(*values,existing['id']))
        else:
            db.execute('''INSERT INTO metrics(faculty_id,source,citations,h_index,i10_index,publications,google_scholar_citations,google_scholar_h_index,google_scholar_i10_index,scopus_citations,scopus_h_index,data_status,snapshot_date,snapshot_source,captured_at,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(faculty_id,snapshot['source'],*values))


def save_vfstr_metric_import_report(source_url,snapshot_date,report):
    with conn() as db:
        cursor=db.execute('INSERT INTO vfstr_metric_import_reports(source_url,snapshot_date,report_json,imported_at) VALUES(?,?,?,?)',(source_url,snapshot_date,json.dumps(report,ensure_ascii=False),now()))
        return cursor.lastrowid


def latest_vfstr_metric_import_report():
    with conn() as db:
        row=db.execute('SELECT * FROM vfstr_metric_import_reports ORDER BY id DESC LIMIT 1').fetchone()
        if not row:
            return None
        report=dict(row)
        report['report']=json.loads(report.pop('report_json'))
        return report


_VFSTR_AUTHOR_FILTER = "verified=1 AND source_url<>'' AND (lower(institution) LIKE '%vignan%' OR lower(institution) LIKE '%vfstr%')"


def _vfstr_author_record(row):
    record=dict(row)
    try:
        record['research_interests']=json.loads(record['research_interests']) if record.get('research_interests') else None
    except (TypeError,ValueError):
        record['research_interests']=None
    return record


def vfstr_authors(search='', department='', school='', designation='', research_area='', has_google_scholar=None, has_scopus=None, data_status='', sort='name_az', limit=24, offset=0):
    clauses=[_VFSTR_AUTHOR_FILTER]
    values=[]
    if search.strip():
        clauses.append('(lower(name) LIKE ? OR lower(department) LIKE ? OR lower(school) LIKE ? OR lower(designation) LIKE ? OR lower(research_interests) LIKE ?)')
        term='%'+search.strip().lower()+'%'
        values.extend([term,term,term,term,term])
    if department.strip():
        clauses.append('department=?')
        values.append(department.strip())
    if school.strip():
        clauses.append('school=?')
        values.append(school.strip())
    if designation.strip():
        clauses.append('designation=?')
        values.append(designation.strip())
    if research_area.strip():
        clauses.append('lower(research_interests) LIKE ?')
        values.append('%'+research_area.strip().lower()+'%')
    if has_google_scholar is not None:
        clauses.append('(google_scholar_url IS NOT NULL AND google_scholar_url<>\'\')' if has_google_scholar else '(google_scholar_url IS NULL OR google_scholar_url=\'\')')
    if has_scopus is not None:
        clauses.append('(scopus_profile_url IS NOT NULL AND scopus_profile_url<>\'\' OR scopus_author_id IS NOT NULL AND scopus_author_id<>\'\')' if has_scopus else '(scopus_profile_url IS NULL OR scopus_profile_url=\'\') AND (scopus_author_id IS NULL OR scopus_author_id=\'\')')
    if data_status.strip():
        clauses.append('data_status=?')
        values.append(data_status.strip())
    order_by={
        'name_az':'lower(name) ASC',
        'name_za':'lower(name) DESC',
        'google_scholar_citations':'CASE WHEN google_scholar_citations IS NULL THEN 1 ELSE 0 END,google_scholar_citations DESC,lower(name) ASC',
        'scopus_citations':'CASE WHEN scopus_citations IS NULL THEN 1 ELSE 0 END,scopus_citations DESC,lower(name) ASC',
        'google_scholar_h_index':'CASE WHEN google_scholar_h_index IS NULL THEN 1 ELSE 0 END,google_scholar_h_index DESC,lower(name) ASC',
        'scopus_h_index':'CASE WHEN scopus_h_index IS NULL THEN 1 ELSE 0 END,scopus_h_index DESC,lower(name) ASC',
        'google_scholar_i10_index':'CASE WHEN google_scholar_i10 IS NULL THEN 1 ELSE 0 END,google_scholar_i10 DESC,lower(name) ASC',
        'citations':'CASE WHEN citation_count IS NULL THEN 1 ELSE 0 END,citation_count DESC,lower(name) ASC',
        'h_index':'CASE WHEN h_index IS NULL THEN 1 ELSE 0 END,h_index DESC,lower(name) ASC',
        'publications':'CASE WHEN publication_count IS NULL THEN 1 ELSE 0 END,publication_count DESC,lower(name) ASC',
    }.get(sort,'lower(name) ASC')
    where=' AND '.join(clauses)
    with conn() as db:
        total=db.execute('SELECT COUNT(*) FROM vfstr_authors WHERE '+where,values).fetchone()[0]
        rows=db.execute('SELECT * FROM vfstr_authors WHERE '+where+' ORDER BY '+order_by+' LIMIT ? OFFSET ?',(*values,limit,offset)).fetchall()
    return [_vfstr_author_record(row) for row in rows],total


def vfstr_author(author_id):
    with conn() as db:
        row=db.execute('SELECT * FROM vfstr_authors WHERE id=? AND '+_VFSTR_AUTHOR_FILTER,(author_id,)).fetchone()
        return _vfstr_author_record(row) if row else None


def link_vfstr_author_profile(author_id, faculty_id):
    with conn() as db:
        db.execute('UPDATE vfstr_authors SET faculty_id=? WHERE id=? AND '+_VFSTR_AUTHOR_FILTER,(faculty_id,author_id))


def vfstr_author_departments():
    with conn() as db:
        rows=db.execute('SELECT department,school,designation,research_interests FROM vfstr_authors WHERE '+_VFSTR_AUTHOR_FILTER+' ORDER BY lower(department),lower(school)').fetchall()
    records=[_vfstr_author_record(row) for row in rows]
    return {
        'departments':sorted({row['department'] for row in records if row.get('department')}),
        'schools':sorted({row['school'] for row in records if row.get('school')}),
        'designations':sorted({row['designation'] for row in records if row.get('designation')}),
        'research_areas':sorted({interest for row in records for interest in (row.get('research_interests') or [])}),
    }


def vfstr_authors_last_updated():
    with conn() as db:
        row=db.execute('SELECT MAX(last_updated) value FROM vfstr_authors WHERE '+_VFSTR_AUTHOR_FILTER).fetchone()
        return row['value']

def upsert_faculty(data):
    t=now()
    with conn() as db:
        db.execute('''INSERT INTO faculty(id,name,institution,department,designation,orcid,scopus_author_id,scopus_profile_url,google_scholar_url,research_interests,photo_url,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,institution=excluded.institution,department=excluded.department,designation=excluded.designation,orcid=COALESCE(excluded.orcid,faculty.orcid),scopus_author_id=COALESCE(excluded.scopus_author_id,faculty.scopus_author_id),scopus_profile_url=COALESCE(excluded.scopus_profile_url,faculty.scopus_profile_url),google_scholar_url=COALESCE(excluded.google_scholar_url,faculty.google_scholar_url),research_interests=COALESCE(excluded.research_interests,faculty.research_interests),photo_url=COALESCE(excluded.photo_url,faculty.photo_url),updated_at=excluded.updated_at''',
        (data['id'],data['name'],data.get('institution',''),data.get('department',''),data.get('designation',''),data.get('orcid'),data.get('scopus_author_id'),data.get('scopus_profile_url'),data.get('google_scholar_url'),json.dumps(data.get('research_interests')) if data.get('research_interests') else None,data.get('photo_url'),t,t))

def update_faculty_profile(fid, research_interests=None, photo_url=None):
    with conn() as db:
        existing=db.execute('SELECT research_interests,photo_url FROM faculty WHERE id=?',(fid,)).fetchone()
        if not existing: return False
        interests=json.dumps(research_interests) if research_interests is not None else existing['research_interests']
        image=photo_url if photo_url is not None else existing['photo_url']
        db.execute('UPDATE faculty SET research_interests=?,photo_url=?,updated_at=? WHERE id=?',(interests,image,now(),fid))
        return True

def monthly_metric_history(fid, start_month=None, periods=12):
    from datetime import datetime
    start=datetime.now(timezone.utc).replace(day=1) if start_month is None else datetime.strptime(start_month,'%Y-%m').replace(tzinfo=timezone.utc)
    months=[]
    cursor=start
    for _ in range(periods):
        months.append(cursor.strftime('%Y-%m'))
        cursor=(cursor.replace(day=28)+__import__('datetime').timedelta(days=4)).replace(day=1)
    rows=metric_history(fid,limit=250)
    result=[]
    for month in months:
        matching=[row for row in rows if (row.get('snapshot_date') or row.get('captured_at','')[:7])==month]
        snapshot=next((row for row in matching if row.get('source')=='vfstr_cse_dataset'),{})
        live_scholar=next((row for row in matching if row.get('source')=='google_scholar'),{})
        live_scopus=next((row for row in matching if row.get('source')=='scopus_search'),{})
        scholar_metrics={
            'citations':live_scholar.get('citations') if live_scholar else snapshot.get('google_scholar_citations'),
            'h_index':live_scholar.get('h_index') if live_scholar else snapshot.get('google_scholar_h_index'),
            'i10_index':live_scholar.get('i10_index') if live_scholar else snapshot.get('google_scholar_i10_index'),
        }
        scopus_metrics={
            'citations':live_scopus.get('citations') if live_scopus else snapshot.get('scopus_citations'),
            'h_index':live_scopus.get('h_index') if live_scopus else snapshot.get('scopus_h_index'),
            'i10_index':live_scopus.get('i10_index') if live_scopus else None,
        }
        result.append({'month':month,'label':datetime.strptime(month,'%Y-%m').strftime('%b %Y'),'google_scholar':scholar_metrics['citations'],'scopus':scopus_metrics['citations'],'scholar_metrics':scholar_metrics,'scopus_metrics':scopus_metrics,'available':any(value is not None for value in (*scholar_metrics.values(),*scopus_metrics.values()))})
    return result

def get_faculty(fid):
    with conn() as db: return db.execute('SELECT * FROM faculty WHERE id=?',(fid,)).fetchone()

def all_faculty_ids():
    with conn() as db: return [r['id'] for r in db.execute('SELECT id FROM faculty ORDER BY updated_at DESC').fetchall()]

def add_metric(fid,m):
    with conn() as db: db.execute('INSERT INTO metrics(faculty_id,source,citations,h_index,i10_index,publications,captured_at,status) VALUES(?,?,?,?,?,?,?,?)',(fid,m['source'],m.get('citations'),m.get('h_index'),m.get('i10_index'),m.get('publications'),m.get('captured_at',now()),m.get('status','ok')))

def latest_metrics(fid):
    with conn() as db:
        rows=db.execute('''SELECT m.* FROM metrics m JOIN (SELECT source,MAX(id) id FROM metrics WHERE faculty_id=? GROUP BY source) x ON x.id=m.id ORDER BY m.source''',(fid,)).fetchall()
        return [dict(r) for r in rows]

def metric_history(fid,limit=50):
    with conn() as db: return [dict(r) for r in db.execute('SELECT * FROM metrics WHERE faculty_id=? ORDER BY captured_at DESC LIMIT ?',(fid,limit)).fetchall()]

def replace_publications(fid, pubs):
    with conn() as db:
        db.execute('DELETE FROM publications WHERE faculty_id=?',(fid,))
        for p in pubs:
            db.execute('INSERT OR REPLACE INTO publications(id,faculty_id,title,year,journal,doi,citations,source,url,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(p['id'],fid,p['title'],p.get('year'),p.get('journal'),p.get('doi'),p.get('citations'),p.get('source','scopus'),p.get('url'),now()))

def publications(fid,limit=100):
    with conn() as db: return [dict(r) for r in db.execute('SELECT * FROM publications WHERE faculty_id=? ORDER BY year DESC, citations DESC LIMIT ?',(fid,limit)).fetchall()]

def add_history(fid):
    with conn() as db: db.execute('INSERT INTO search_history(faculty_id,searched_at) VALUES(?,?)',(fid,now()))

def history(limit=100):
    with conn() as db: return [dict(r) for r in db.execute('SELECT h.id,h.faculty_id,f.name faculty_name,f.institution,h.searched_at FROM search_history h JOIN faculty f ON f.id=h.faculty_id ORDER BY h.searched_at DESC LIMIT ?',(limit,)).fetchall()]

def clear_history():
    with conn() as db: db.execute('DELETE FROM search_history')

def delete_history_item(i):
    with conn() as db: db.execute('DELETE FROM search_history WHERE id=?',(i,))

def log_sync(fid,source,status,message):
    with conn() as db: db.execute('INSERT INTO sync_log(faculty_id,source,status,message,synced_at) VALUES(?,?,?,?,?)',(fid,source,status,message,now()))

def previous_metric(fid,source):
    with conn() as db: return db.execute('SELECT * FROM metrics WHERE faculty_id=? AND source=? ORDER BY id DESC LIMIT 2',(fid,source)).fetchall()
