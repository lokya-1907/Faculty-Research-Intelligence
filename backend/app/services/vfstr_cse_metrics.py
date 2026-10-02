import csv
import hashlib
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from difflib import SequenceMatcher
from urllib.parse import parse_qs, urlparse
import httpx
from app.services import db
from app.services.vfstr_authors import OFFICIAL_CSE_URL, VFSTR_INSTITUTION


SPREADSHEET_URL='https://docs.google.com/spreadsheets/d/1vrMUVpWEywrjn26BJt-xYriscD1AeQfkjMXblvOl5WI/export?format=csv'
METRICS_DATE='2026-04'
METRICS_SOURCE='VFSTR CSE Faculty H-index dataset'
TITLE_PATTERN=re.compile(r'^(professor|prof|doctor|dr|mr|mrs|ms)\.?$',re.I)


class SpreadsheetImportError(RuntimeError):
    pass


def _tokens(value):
    tokens=re.findall(r'[a-z0-9]+',(value or '').casefold())
    while tokens and TITLE_PATTERN.match(tokens[0]):
        tokens.pop(0)
    return tokens


def _normalized_name(value):
    return ''.join(_tokens(value))


def _candidate_score(left,right):
    left_tokens=_tokens(left)
    right_tokens=_tokens(right)
    sequence=SequenceMatcher(None,''.join(left_tokens),''.join(right_tokens)).ratio()
    used=set()
    score=0.0
    for token in left_tokens:
        matches=[(1.0,index) for index,other in enumerate(right_tokens) if index not in used and token==other]
        matches += [(0.72,index) for index,other in enumerate(right_tokens) if index not in used and len(token)==1 and other.startswith(token)]
        matches += [(0.72,index) for index,other in enumerate(right_tokens) if index not in used and len(other)==1 and token.startswith(other)]
        if matches:
            weight,index=max(matches)
            score+=weight
            used.add(index)
    token_score=score/max(len(left_tokens),len(right_tokens),1)
    return max(sequence,token_score)


def _suggest_candidates(name,roster):
    ranked=sorted(((author['name'],_candidate_score(name,author['name'])) for author in roster),key=lambda item:item[1],reverse=True)
    if not ranked or ranked[0][1]<0.60:
        return []
    cutoff=max(0.60,ranked[0][1]-0.12)
    return [{'official_name':candidate,'similarity':round(score,3)} for candidate,score in ranked if score>=cutoff][:5]


def _read_sheet_rows(http_get=None):
    get=http_get or httpx.get
    try:
        response=get(SPREADSHEET_URL,timeout=45,follow_redirects=True,headers={'User-Agent':'ResearchPulse/1.0 (VFSTR CSE metrics import)'})
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise SpreadsheetImportError(f'Could not read the supplied VFSTR CSE metrics spreadsheet: {error}') from error
    lines=response.content.decode('utf-8-sig').splitlines()
    header_index=next((index for index,line in enumerate(lines) if 'Name of the Faculty' in line and 'Department' in line),None)
    if header_index is None:
        raise SpreadsheetImportError('The spreadsheet header row could not be identified; no records were imported.')
    rows=[]
    for row_number,row in enumerate(csv.DictReader(lines[header_index:]),start=header_index+2):
        name=(row.get('Name of the Faculty') or '').strip()
        if name:
            row['_source_row']=row_number
            rows.append(row)
    if not rows:
        raise SpreadsheetImportError('The spreadsheet contains no faculty rows; no records were imported.')
    try:
        xlsx_url=SPREADSHEET_URL.replace('format=csv','format=xlsx')
        workbook=get(xlsx_url,timeout=45,follow_redirects=True,headers={'User-Agent':'ResearchPulse/1.0 (VFSTR CSE metrics links)'})
        workbook.raise_for_status()
        archive=zipfile.ZipFile(io.BytesIO(workbook.content))
        sheet=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        relationships=ET.fromstring(archive.read('xl/worksheets/_rels/sheet1.xml.rels'))
        relation_ns='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
        targets={item.attrib['Id']:item.attrib.get('Target') for item in relationships if item.attrib.get('TargetMode')=='External'}
        links={}
        for element in sheet.iter():
            if not element.tag.endswith('}hyperlink'):
                continue
            reference=element.attrib.get('ref','')
            match=re.fullmatch(r'([A-Z]+)(\d+)',reference)
            relation_id=element.attrib.get(relation_ns+'id')
            if not match or not relation_id or relation_id not in targets:
                continue
            column,row_number=match.group(1),int(match.group(2))
            if column in {'J','K'}:
                links.setdefault(row_number,{})[column]=targets[relation_id]
        for row in rows:
            embedded=links.get(row['_source_row'],{})
            if embedded.get('J'):
                row['Google Scholar Profile URL']=embedded['J']
            if embedded.get('K'):
                row['Scopus Profile URL']=embedded['K']
    except (httpx.HTTPError,KeyError,ValueError,zipfile.BadZipFile,ET.ParseError):
        pass
    return rows


def _parse_integer(value):
    text=(value or '').strip().replace(',','')
    if not text or text.casefold() in {'-','n/a','na','not available'}:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _valid_http_url(value):
    value=(value or '').strip()
    parsed=urlparse(value)
    return value if parsed.scheme in {'http','https'} and parsed.netloc else None


def _scopus_author_id(profile_url):
    if not profile_url:
        return None
    query=parse_qs(urlparse(profile_url).query)
    found=query.get('authorId') or query.get('authorid')
    if found and re.fullmatch(r'\d+',found[0].strip()):
        return found[0].strip()
    match=re.search(r'(?i)authorId=(\d+)',profile_url)
    return match.group(1) if match else None


def _build_mapping(spreadsheet_rows,roster):
    exact=defaultdict(list)
    token_sets=defaultdict(list)
    for author in roster:
        exact[_normalized_name(author['name'])].append(author)
        token_sets[tuple(sorted(_tokens(author['name'])))].append(author)
    used=set()
    matched=[]
    unmatched=[]
    ambiguous=[]
    for row in spreadsheet_rows:
        sheet_name=row['Name of the Faculty'].strip()
        candidates=[author for author in exact.get(_normalized_name(sheet_name),[]) if author['id'] not in used]
        method='normalized_exact'
        if not candidates:
            candidates=[author for author in token_sets.get(tuple(sorted(_tokens(sheet_name))),[]) if author['id'] not in used]
            method='exact_token_order_variant'
        if len(candidates)==1:
            official=candidates[0]
            used.add(official['id'])
            matched.append({'spreadsheet_row':row['_source_row'],'spreadsheet_name':sheet_name,'official_name':official['name'],'vfstr_author_id':official['id'],'match_method':method,'row':row})
            continue
        if len(candidates)>1:
            ambiguous.append({'spreadsheet_row':row['_source_row'],'spreadsheet_name':sheet_name,'candidate_official_names':[candidate['name'] for candidate in candidates],'reason':'Multiple verified directory records share the normalized name.'})
            unmatched.append({'spreadsheet_row':row['_source_row'],'spreadsheet_name':sheet_name,'possible_candidates':[candidate['name'] for candidate in candidates]})
            continue
        suggestions=_suggest_candidates(sheet_name,[author for author in roster if author['id'] not in used])
        if len(suggestions)>1:
            ambiguous.append({'spreadsheet_row':row['_source_row'],'spreadsheet_name':sheet_name,'candidate_official_names':[candidate['official_name'] for candidate in suggestions],'reason':'Name variants are plausible but not an exact safe match; manual confirmation required.'})
        unmatched.append({'spreadsheet_row':row['_source_row'],'spreadsheet_name':sheet_name,'possible_candidates':[candidate['official_name'] for candidate in suggestions]})
    matched_ids={entry['vfstr_author_id'] for entry in matched}
    report_row_values=[{'spreadsheet_row':entry['spreadsheet_row'],'row':parse_spreadsheet_row(entry['row'],METRICS_DATE)} for entry in matched]
    report={
        'row_values':report_row_values,
        'unmatched_row_values':[{'spreadsheet_row':row['_source_row'],'spreadsheet_name':row['Name of the Faculty'].strip(),'row':parse_spreadsheet_row(row,METRICS_DATE)} for row in spreadsheet_rows if row['_source_row'] not in {entry['spreadsheet_row'] for entry in matched}],
        'source_url':SPREADSHEET_URL,
        'identity_source_url':OFFICIAL_CSE_URL,
        'metrics_date':METRICS_DATE,
        'snapshot_date':METRICS_DATE,
        'metrics_source':METRICS_SOURCE,
        'spreadsheet_record_count':len(spreadsheet_rows),
        'official_vfstr_cse_count':len(roster),
        'matched_count':len(matched),
        'unmatched_spreadsheet_count':len(unmatched),
        'unmatched_vfstr_count':sum(author['id'] not in matched_ids for author in roster),
        'ambiguous_count':len(ambiguous),
        'match_methods':{
            'normalized_exact':sum(item['match_method']=='normalized_exact' for item in matched),
            'exact_token_order_variant':sum(item['match_method']=='exact_token_order_variant' for item in matched),
        },
        'matched':[{k:v for k,v in entry.items() if k!='row'} for entry in matched],
        'unmatched_spreadsheet_records':unmatched,
        'unmatched_vfstr_faculty':[author['name'] for author in roster if author['id'] not in matched_ids],
        'ambiguous_matches':ambiguous,
    }
    return matched,report


def parse_spreadsheet_row(row, snapshot_date=METRICS_DATE):
    """Public, additive row parser: the same field mapping the importer applies."""
    gs_url=_valid_http_url(row.get('Google Scholar Profile URL'))
    scopus_url=_valid_http_url(row.get('Scopus Profile URL'))
    return {
        'spreadsheet_name': (row.get('Name of the Faculty') or '').strip(),
        'google_scholar_url': gs_url,
        'google_scholar_i10': _parse_integer(row.get('I10 Index (as per Google Scholar)')),
        'google_scholar_h_index': _parse_integer(row.get('h Index (as per Google Scholar)')),
        'google_scholar_citations': _parse_integer(row.get('Citations (as per Google Schoolar)')),
        'scopus_author_id': _scopus_author_id(scopus_url),
        'scopus_profile_url': scopus_url,
        'scopus_h_index': _parse_integer(row.get('h Index (as per Scopus)')),
        'scopus_citations': _parse_integer(row.get('Citations (as per Scopus)')),
        'professional_memberships': (row.get('Professional membership details') or '').strip() or None,
        'data_status': (row.get('Updated/not updated') or '').strip() or None,
        'metrics_date': snapshot_date,
    }


def import_april_2026_metrics(http_get=None):
    spreadsheet_rows=_read_sheet_rows(http_get)
    roster,total=db.vfstr_authors(department='CSE',limit=100,offset=0)
    if total>len(roster):
        for offset in range(len(roster),total,100):
            page,_=db.vfstr_authors(department='CSE',limit=100,offset=offset)
            roster.extend(page)
    matched,report=_build_mapping(spreadsheet_rows,roster)
    unmatched=report['unmatched_spreadsheet_records']
    ambiguous=report['ambiguous_matches']
    imported=0
    failed=[]
    for mapping in matched:
        row=mapping['row']
        author=next(item for item in roster if item['id']==mapping['vfstr_author_id'])
        gs_url=_valid_http_url(row.get('Google Scholar Profile URL'))
        scopus_url=_valid_http_url(row.get('Scopus Profile URL'))
        scopus_id=_scopus_author_id(scopus_url)
        status=(row.get('Updated/not updated') or '').strip() or None
        data={
            'google_scholar_url':gs_url,
            'google_scholar_i10':_parse_integer(row.get('I10 Index (as per Google Scholar)')),
            'google_scholar_h_index':_parse_integer(row.get('h Index (as per Google Scholar)')),
            'google_scholar_citations':_parse_integer(row.get('Citations (as per Google Schoolar)')),
            'scopus_author_id':scopus_id,
            'scopus_profile_url':scopus_url,
            'scopus_h_index':_parse_integer(row.get('h Index (as per Scopus)')),
            'scopus_citations':_parse_integer(row.get('Citations (as per Scopus)')),
            'professional_memberships':(row.get('Professional membership details') or '').strip() or None,
            'data_status':status,
            'metrics_date':METRICS_DATE,
        }
        try:
            faculty_id=author.get('faculty_id') or 'fac_vfstr_'+hashlib.sha256(author['id'].encode()).hexdigest()[:16]
            db.upsert_faculty({
                'id':faculty_id,'name':author['name'],'institution':author['institution'],'department':author.get('department') or '',
                'designation':author.get('designation') or '', 'scopus_author_id':scopus_id,'scopus_profile_url':scopus_url,
                'google_scholar_url':gs_url,
            })
            if not db.update_vfstr_research_data(author['id'],data):
                raise RuntimeError('Verified VFSTR directory row could not be updated.')
            db.link_vfstr_author_profile(author['id'],faculty_id)
            db.upsert_metric_snapshot(faculty_id,{
                'source':'vfstr_cse_dataset','snapshot_date':METRICS_DATE,'data_status':status,
                'google_scholar_citations':data['google_scholar_citations'],
                'google_scholar_h_index':data['google_scholar_h_index'],
                'google_scholar_i10_index':data['google_scholar_i10'],
                'scopus_citations':data['scopus_citations'],'scopus_h_index':data['scopus_h_index'],
                'status':status or 'status_not_provided',
            })
            imported+=1
        except Exception as error:
            failed.append({'spreadsheet_name':mapping['spreadsheet_name'],'official_name':author['name'],'error':str(error)})
    report['imported_count']=imported
    report['import_failures']=failed
    report['updated_status_count']=sum((entry['row'].get('Updated/not updated') or '').strip().casefold()=='updated' for entry in matched)
    report['not_updated_status_count']=sum((entry['row'].get('Updated/not updated') or '').strip().casefold()=='not updated' for entry in matched)
    report['matched_count']=len(matched)
    report['imported_count']=imported
    report['failed_import_count']=len(failed)
    report['unmatched_spreadsheet_count']=len(unmatched)
    report['ambiguous_count']=len(ambiguous)
    report['ambiguous_matches']=ambiguous
    report['unmatched_spreadsheet_records']=unmatched
    matched_ids={item['vfstr_author_id'] for item in matched}
    report['unmatched_vfstr_count']=len(roster)-len(matched_ids)
    report['unmatched_vfstr_faculty']=[author['name'] for author in roster if author['id'] not in matched_ids]
    report_id=db.save_vfstr_metric_import_report(SPREADSHEET_URL,METRICS_DATE,report)
    report['report_id']=report_id
    return report


def latest_import_report():
    return db.latest_vfstr_metric_import_report()