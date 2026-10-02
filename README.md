# ResearchPulse — Faculty Research Intelligence

A final-year project for faculty research-profile lookup, scholarly metrics, publication analytics, source synchronization, metric history, and search history.

## Core features
- Scopus Search publication lookup by author name and optional affiliation.
- Official Scopus Author Profile resolution is entitlement-gated and is not enabled for the current API key.
- VFSTR Authors directory imported from the official CSE faculty page, with verified source links, designation/research-area filters, sorting, pagination, and access to the existing faculty profile.
- Official faculty photos from the same VFSTR directory are shown on profiles; missing source photos fall back to initials.
- April 2026 Google Scholar/Scopus metrics imported from the supplied VFSTR CSE faculty spreadsheet and stored as a dated history snapshot.
- Separate Scopus and Google Scholar metrics.
- Citations, h-index and Google Scholar i10-index.
- Publication list and citation counts.
- Sync-now workflow and historical metric snapshots.
- Live Google Scholar synchronization for linked profiles through the SerpAPI provider adapter.
- Profile editing for local research interests and photo URLs, with official directory values retained as fallbacks.
- Forward monthly Scholar/Scopus tracking chart from the current month through the next 12-month window; months without captured data remain unavailable.
- Change detection between syncs.
- Search history with individual delete and clear-all.
- Native Windows React + FastAPI deployment; Docker is optional.

## Authentication
The workspace is protected by an expiring signed session token. The local development credentials are configured in `backend/.env` through `AUTH_USERNAME` and `AUTH_PASSWORD`; do not commit real production credentials or the local `AUTH_SECRET`. After starting the app, open `http://127.0.0.1:5173` and sign in. Sessions last for `AUTH_SESSION_HOURS` and can be ended with `Sign out`.

Run `powershell -ExecutionPolicy Bypass -File backend/scripts/backup_database.ps1` to create a timestamped SQLite backup in `backend/backups`. Backend requests are logged with method, path, status, and duration; production deployments should forward these logs to a central monitor.

## Data-source policy
Scopus Search results are publication records; their `dc:creator` field is not treated as a verified faculty identity, and Search-derived citation values are not official author-profile metrics. Official author resolution requires the appropriate Elsevier API entitlement. Google Scholar does not expose an equivalent public author-data API, so this project uses a provider adapter for a linked public Scholar profile rather than claiming direct official API access.

The VFSTR CSE directory is imported from the official university page: https://vignan.ac.in/newvignan/departments/deptpeople.php?deptid=sch3_dept1&deptnm=CSE&school=sch3. The refresh reads the current faculty cards and preserves missing research interests as unavailable. It does not infer Scopus IDs or metrics; those fields remain unavailable until independently verified. The refresh endpoint only upserts source rows and does not clear existing entries if the source request fails.

The supplied “VFSTR DEEMED TO BE UNIVERSITY CSE Department Faculty H-index April 2026” spreadsheet is the source for the April 2026 Scholar/Scopus metric snapshot. It is fetched from its supplied Google Sheets export URL; embedded profile hyperlink targets are read from the workbook export. Spreadsheet rows are matched to the official roster only by unique normalized exact names or exact token-set order variants; fuzzy/ambiguous rows remain unmatched and are included in a persisted import report. The initial mapping imported 39 of 94 sheet rows; 7 were ambiguous, and 48 other rows remain unmatched, of which 42 have fuzzy suggestions for manual review and 6 have none. Spreadsheet status is preserved, blank metric cells remain unavailable rather than zero, and the snapshot is dated `2026-04`, not live. Supplied Scopus Author IDs are extracted from the spreadsheet URLs; the unauthorized Scopus Author Search endpoint is not called.

For live Google Scholar metrics, set `SERPAPI_API_KEY` in `backend/.env` and keep `SCHOLAR_ENABLED=true`, then restart the backend. Open a profile with a linked Google Scholar URL and click `Sync now`; the current Scholar values are stored as a separate live metric snapshot and are not used to overwrite the April 2026 historical dataset.

## Research value
The project can compare source metrics, visualize changes over time, and maintain an auditable history of when values were captured.
