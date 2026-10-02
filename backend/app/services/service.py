from datetime import datetime, timezone
import hashlib
import json
from app.services import db, scopus, scholar


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def publication_search(name, institution):
    if not scopus.settings.scopus_enabled or not scopus.settings.scopus_api_key:
        return [], 'Scopus is not configured. Add SCOPUS_API_KEY to backend/.env.'
    try:
        entries = scopus.search_publications(name, institution)
        publications = []
        for entry in entries:
            identifier = str(entry.get('dc:identifier') or entry.get('eid') or hashlib.sha1(str(entry).encode()).hexdigest())
            cover = entry.get('prism:coverDate') or entry.get('prism:publicationDate') or ''
            affiliations = entry.get('affiliation') or []
            if isinstance(affiliations, dict):
                affiliations = [affiliations]
            publications.append({
                'id': 'scopus:' + identifier,
                'title': entry.get('dc:title') or '',
                'record_creator': entry.get('dc:creator'),
                'affiliation': next((item.get('affilname') for item in affiliations if isinstance(item, dict) and item.get('affilname')), None),
                'year': int(str(cover)[:4]) if str(cover)[:4].isdigit() else None,
                'journal': entry.get('prism:publicationName'),
                'doi': entry.get('prism:doi'),
                'citations': int(entry.get('citedby-count', 0) or 0),
                'source': 'scopus_search',
                'url': entry.get('prism:url'),
            })
        return publications, None
    except scopus.ScopusError as e:
        return [], str(e)


def _publication_rows(entries):
    pubs = []
    for e in entries:
        identifier = str(e.get('dc:identifier') or e.get('eid') or hashlib.sha1(str(e).encode()).hexdigest())
        cover = e.get('prism:coverDate') or ''
        pubs.append({
            'id': 'scopus:' + identifier,
            'title': e.get('dc:title',''),
            'year': int(str(cover)[:4]) if str(cover)[:4].isdigit() else None,
            'journal': e.get('prism:publicationName'),
            'doi': e.get('prism:doi'),
            'citations': int(e.get('citedby-count',0) or 0),
            'source': 'scopus',
            'url': e.get('prism:url'),
        })
    return pubs


def _computed_metrics(pubs):
    citations = sorted((int(p.get('citations') or 0) for p in pubs), reverse=True)
    h = sum(1 for i, c in enumerate(citations, 1) if c >= i)
    i10 = sum(1 for c in citations if c >= 10)
    return {
        'citations': sum(citations),
        'h_index': h,
        'i10_index': i10,
        'publications': len(pubs),
    }


def sync(fid):
    f = db.get_faculty(fid)
    if not f:
        raise ValueError('Faculty not found.')
    changed=[]; messages=[]; statuses={}

    if scopus.settings.scopus_api_key and scopus.settings.scopus_enabled:
        if not f['scopus_author_id']:
            message = 'Scopus Search publication records cannot be assigned to an author profile. A verified Scopus Author ID is required; official author lookup requires the appropriate Elsevier API entitlement.'
            statuses['scopus_search'] = 'author_id_required'
            messages.append(message)
            db.log_sync(fid, 'scopus_search', 'error', message)
        else:
            try:
                entries = scopus.documents(author_id=f['scopus_author_id'])
                pubs = _publication_rows(entries)
                db.replace_publications(fid, pubs)
                metrics = _computed_metrics(pubs)
                old = db.previous_metric(fid, 'scopus_search')
                m = {'source':'scopus_search', **metrics, 'captured_at':now(), 'status':'ok'}
                db.add_metric(fid, m)
                if old:
                    prev=old[0]
                    for key,label in [('citations','citations'),('h_index','h-index'),('i10_index','i10-index'),('publications','publications')]:
                        if m.get(key) is not None and prev[key] is not None and m[key] != prev[key]:
                            changed.append(f'{label}: {prev[key]} → {m[key]}')
                statuses['scopus_search']='connected'
                db.log_sync(fid,'scopus_search','ok','Scopus Author ID publication synchronization completed.')
            except scopus.ScopusError as e:
                statuses['scopus_search']='error'; messages.append(str(e)); db.log_sync(fid,'scopus_search','error',str(e))
    else:
        statuses['scopus_search']='not_configured'

    if f['google_scholar_url']:
        if not scholar.settings.scholar_enabled:
            message='Google Scholar synchronization is disabled. Set SCHOLAR_ENABLED=true in backend/.env.'
            statuses['google_scholar']='disabled'; messages.append(message); db.log_sync(fid,'google_scholar','error',message)
        elif not scholar.settings.serpapi_api_key:
            message='Google Scholar synchronization requires SERPAPI_API_KEY in backend/.env.'
            statuses['google_scholar']='not_configured'; messages.append(message); db.log_sync(fid,'google_scholar','error',message)
        else:
            try:
                s=scholar.fetch(f['google_scholar_url'])
                old=db.previous_metric(fid,'google_scholar')
                m={'source':'google_scholar',**s['metrics'],'publications':len(s['publications']),'captured_at':now()}
                db.add_metric(fid,m)
                for key,label in [('citations','citations'),('h_index','h-index'),('i10_index','i10-index')]:
                    if old and m.get(key) is not None and old[0][key] is not None and m[key]!=old[0][key]:
                        changed.append(f'Google Scholar {label}: {old[0][key]} → {m[key]}')
                statuses['google_scholar']='connected'; db.log_sync(fid,'google_scholar','ok','Google Scholar synchronization completed.')
            except scholar.ScholarError as e:
                statuses['google_scholar']='error'; messages.append(str(e)); db.log_sync(fid,'google_scholar','error',str(e))
    else:
        statuses['google_scholar']='profile_not_configured'

    return {'faculty_id':fid,'status':'ok' if not messages else 'partial','message':'Sync completed.' + (' '+ ' '.join(messages) if messages else ''),'changed':changed,'synced_at':now(),'statuses':statuses}


def profile(fid):
    f=db.get_faculty(fid)
    if not f: return None
    metrics=db.latest_metrics(fid)
    sync_metrics=[metric for metric in metrics if metric['source']!='vfstr_cse_dataset']
    directory=db.vfstr_author_by_faculty_id(fid)
    return {
        'id':f['id'],'name':f['name'],'institution':f['institution'],'department':f['department'],
        'designation':f['designation'],'orcid':f['orcid'],'scopus_author_id':f['scopus_author_id'],
        'scopus_profile_url':f['scopus_profile_url'],'google_scholar_url':f['google_scholar_url'],'metrics':metrics,'publications':db.publications(fid),
        'last_synced_at':max([m['captured_at'] for m in sync_metrics],default=None),
        'source_status':{m['source']:'connected' if m['status']=='ok' else m['status'] for m in metrics},
        'research_interests':json.loads(f['research_interests']) if f['research_interests'] else (directory['research_interests'] if directory else None),
        'professional_memberships':directory['professional_memberships'] if directory else None,
        'google_scholar_i10':directory['google_scholar_i10'] if directory else None,
        'google_scholar_h_index':directory['google_scholar_h_index'] if directory else None,
        'google_scholar_citations':directory['google_scholar_citations'] if directory else None,
        'scopus_h_index':directory['scopus_h_index'] if directory else None,
        'scopus_citations':directory['scopus_citations'] if directory else None,
        'metrics_date':directory['metrics_date'] if directory else None,
        'metrics_data_status':directory['data_status'] if directory else None,
        'vfstr_source_url':directory['source_url'] if directory else None,
        'photo_url':f['photo_url'] or (directory['photo_url'] if directory else None),
    }
