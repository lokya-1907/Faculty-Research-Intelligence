from typing import Optional, Literal
from pydantic import BaseModel, Field

class FacultySearchRequest(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    institution: str = Field(default='', max_length=200)
    department: str = Field(default='', max_length=200)

class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = 'bearer'
    expires_in: int
    username: str

class FacultyCandidate(BaseModel):
    id: str
    name: str
    affiliation: str = ''
    department: str = ''
    scopus_author_id: Optional[str] = None
    orcid: Optional[str] = None
    document_count: Optional[int] = None
    citation_count: Optional[int] = None
    h_index: Optional[int] = None
    source: str
    metrics_scope: Optional[str] = None
    i10_index: Optional[int] = None
    source_url: Optional[str] = None

class ScopusSearchPublication(BaseModel):
    id: str
    title: str
    record_creator: Optional[str] = None
    affiliation: Optional[str] = None
    year: Optional[int] = None
    journal: Optional[str] = None
    doi: Optional[str] = None
    citations: Optional[int] = None
    source: str = 'scopus_search'
    url: Optional[str] = None

class VFSTRAuthor(BaseModel):
    id: str
    faculty_id: Optional[str] = None
    name: str
    institution: str
    department: Optional[str] = None
    designation: Optional[str] = None
    school: Optional[str] = None
    campus: Optional[str] = None
    research_interests: Optional[list[str]] = None
    professional_memberships: Optional[str] = None
    photo_url: Optional[str] = None
    google_scholar_url: Optional[str] = None
    google_scholar_i10: Optional[int] = None
    google_scholar_h_index: Optional[int] = None
    google_scholar_citations: Optional[int] = None
    scopus_author_id: Optional[str] = None
    scopus_profile_url: Optional[str] = None
    scopus_h_index: Optional[int] = None
    scopus_citations: Optional[int] = None
    orcid: Optional[str] = None
    profile_url: Optional[str] = None
    publication_count: Optional[int] = None
    citation_count: Optional[int] = None
    h_index: Optional[int] = None
    i10_index: Optional[int] = None
    source: str
    source_url: str
    verified: bool
    data_status: Optional[str] = None
    metrics_date: Optional[str] = None
    last_updated: str

class VFSTRAuthorPage(BaseModel):
    authors: list[VFSTRAuthor]
    total: int
    page: int
    page_size: int
    last_updated: Optional[str] = None

class VFSTRDepartments(BaseModel):
    departments: list[str]
    schools: list[str]
    designations: list[str]
    research_areas: list[str]

class VFSTRAuthorRefreshResult(BaseModel):
    status: Literal['ok','partial','error']
    source_url: str
    records_found: int
    updated: int
    failed_records: list[dict[str,str]]
    unparsed_cards: int
    author_count: int
    last_updated: Optional[str] = None
    error_message: Optional[str] = None

class Metric(BaseModel):
    source: Literal['scopus_search','scopus','google_scholar','manual','vfstr_cse_dataset']
    citations: Optional[int] = None
    h_index: Optional[int] = None
    i10_index: Optional[int] = None
    publications: Optional[int] = None
    captured_at: str
    status: str = 'ok'
    google_scholar_citations: Optional[int] = None
    google_scholar_h_index: Optional[int] = None
    google_scholar_i10_index: Optional[int] = None
    scopus_citations: Optional[int] = None
    scopus_h_index: Optional[int] = None
    data_status: Optional[str] = None
    snapshot_date: Optional[str] = None
    snapshot_source: Optional[str] = None

class Publication(BaseModel):
    id: str
    title: str
    year: Optional[int] = None
    journal: Optional[str] = None
    doi: Optional[str] = None
    citations: Optional[int] = None
    source: str = 'scopus'
    url: Optional[str] = None

class FacultyProfile(BaseModel):
    id: str
    name: str
    institution: str
    department: str = ''
    designation: str = ''
    orcid: Optional[str] = None
    scopus_author_id: Optional[str] = None
    google_scholar_url: Optional[str] = None
    scopus_profile_url: Optional[str] = None
    metrics: list[Metric] = []
    publications: list[Publication] = []
    last_synced_at: Optional[str] = None
    source_status: dict[str, str] = {}
    research_interests: Optional[list[str]] = None
    professional_memberships: Optional[str] = None
    google_scholar_i10: Optional[int] = None
    google_scholar_h_index: Optional[int] = None
    google_scholar_citations: Optional[int] = None
    scopus_h_index: Optional[int] = None
    scopus_citations: Optional[int] = None
    metrics_date: Optional[str] = None
    metrics_data_status: Optional[str] = None
    vfstr_source_url: Optional[str] = None
    photo_url: Optional[str] = None

class SearchHistoryItem(BaseModel):
    id: int
    faculty_id: str
    faculty_name: str
    institution: str
    searched_at: str

class SyncResponse(BaseModel):
    faculty_id: str
    status: str
    message: str
    changed: list[str] = []
    synced_at: str
    statuses: dict[str, str] = {}


# --- Additive models for the optional extended routes (routes_extras.py) ---

class BatchSyncRequest(BaseModel):
    faculty_ids: list[str] = []
    only_with_scholar: bool = False
    only_never_synced: bool = False
    requested_by: Optional[str] = None

class ReviewApplyRequest(BaseModel):
    spreadsheet_row: int
    vfstr_author_id: str
    reviewed_by: Optional[str] = None
    force: bool = False

class ManualLinkRequest(BaseModel):
    spreadsheet_row: int
    vfstr_author_id: Optional[str] = None
    reviewed_by: Optional[str] = None
