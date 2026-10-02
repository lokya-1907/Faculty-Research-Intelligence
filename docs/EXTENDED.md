# Extended workspace — what was added

Everything here is **additive**. No existing endpoint, table, component, style or
behaviour was changed or removed. The original suite still passes untouched
(29 tests); the extended suites add 38 more (**67 total**, **81% coverage**).

Run the app exactly as before:

    powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1
    powershell -ExecutionPolicy Bypass -File .\start_windows.ps1

Four new sections appear in the sidebar under **EXTENDED**:
**Exports · Import Review · Analytics · Operations**.

---

## 1. Resilience — failures are no longer invisible

**Problem:** the 6-hourly background sync swallowed every exception
(`except Exception: pass`), logging was console-only, and the only retry logic in
the codebase was one 429 branch.

**Added**
- `app/services/http_client.py` — shared GET helper with **bounded retry, exponential
  backoff, full jitter**, `Retry-After` support, and a **per-provider circuit breaker**
  (5 consecutive failures → 120s cooldown). `RetryExhausted` and `CircuitOpen` are
  distinct, catchable errors.
- **`scopus.py` and `scholar.py` now use it.** Every terminal response is classified
  into exactly the same `ScopusError` / `ScholarError` message as before — the
  status-code mapping was factored into `scopus._raise_for_status()` and is unchanged
  in behaviour. Verified live: a Scopus Search returned 109 publications and a full
  Scholar sync returned `ok` through the new path.
- `app/services/maintenance.py` — rotating **file logging**
  (`backend/logs/researchpulse.log`, 2 MB × 5) and **periodic database backups**.
- The scheduled sync now runs through the batch runner, so every outcome is recorded
  and failures are logged with a stack trace instead of disappearing.
- `/api/v1/health` also reports `last_sync_success`, `last_sync_failure`,
  `failing_sources` and `circuit_breakers_open`.
- `GET /api/v1/sync/failures` returns last success/failure, failures per source, the
  20 most recent error messages, open circuit breakers, and profiles with no capture
  in the last 24 hours.

## 2. Batch sync with progress

**Added** `app/services/batch.py` and endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/sync/batch` | Sync many profiles (`?background=true` default) |
| `GET /api/v1/sync/batch/status` | Running state + the 10 most recent runs |
| `GET /api/v1/sync/batch/{run_id}` | Full per-profile detail for one run |

Targets can be an explicit `faculty_ids` list, `only_with_scholar`, or
`only_never_synced`. Each run is persisted in a new `sync_runs` table with
requested / succeeded / failed counts and duration. A second concurrent batch is
refused with **409** while one is running.

## 3. Exports

`GET /api/v1/exports` lists the datasets; each downloads as **CSV or XLSX**:

- `directory` — all verified authors, honouring the same filters as `/authors`
- `metrics` — every captured metric snapshot
- `publications` — every synchronised publication

The XLSX writer is dependency-free (`extras_db.to_xlsx`), so nothing new is installed.
Every export is recorded in `export_log`, and each response carries
`X-Total-Rows` / `X-Exported-Rows`. Pass `page` and `page_size` to window large
datasets; the default exports everything.

## 4. Unmatched-row review

The April 2026 import leaves **55 of 94 spreadsheet rows unmatched** (49 with
suggestions, 6 with none, 7 flagged ambiguous). There was no way to act on them.

- `GET /api/v1/review/queue` — the queue with the importer's own suggestions and
  current link state. Paginates with `page` / `page_size` (`0` = every row, the default).
- `POST /api/v1/review/apply` — link a row to a verified author and apply exactly the
  metric fields the automatic import would have written.
- `POST /api/v1/review/undo` — remove a link.

**Safety:** if the chosen author is not one of the suggested candidates the request is
refused unless `"force": true` is sent, so a mis-click cannot attach metrics to the
wrong person. Every action is written to a new `audit_log`.

To support this, `vfstr_cse_metrics` now also **persists the parsed row values** in the
import report (`row_values`, `unmatched_row_values`) and exposes
`parse_spreadsheet_row()` — the same field mapping the importer uses. If your stored
report predates this, re-run `POST /api/v1/authors/metrics/import` once.

## 5. Analytics

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/analytics/ranking` | Ranked cohort with percentiles, sortable, filterable |
| `GET /api/v1/analytics/cohort` | Median / average / min / max / coverage per metric |
| `GET /api/v1/analytics/compare` | Side-by-side for up to 6 profiles, with deltas |
| `GET /api/v1/analytics/source-comparison/{id}` | Per-source view for one profile |
| `GET /api/v1/analytics/dedupe/{id}` | Groups the same work across sources by DOI, then title |

Missing values are counted as missing and **never treated as zero**. Every analytics
response carries an explicit comparability note: Google Scholar comes through a capped
provider adapter (`scholar.py` returns at most 50 articles) and Scopus values are
Search-derived, so the two sources must not be read as equivalent.

## 6. Operability

- **Dockerfile** + **docker-compose.yml** + `.dockerignore` — multi-stage build
  (Python backend + Vite frontend), healthcheck, named volumes for data/backups/logs.
  Native Windows remains the default path.
- **CI** (`.github/workflows/ci.yml`) — ruff + pytest **with a coverage gate** on the
  backend, and `npm run smoke` + production build on the frontend.
- **`pyproject.toml`** — ruff, pytest and coverage configuration. The lint rule set is
  deliberately correctness-only (`E9, F63, F7, F82`) so the existing code passes
  unchanged. The coverage floor is **55%** against a measured **81%**.
- **Migrations** — `backend/scripts/migrate.py` takes a backup, applies the idempotent
  schema upgrades, prints the applied migration list and verifies integrity. A
  `schema_migrations` table now records the database's version explicitly
  (currently **2 of 2**) instead of inferring it.
- **Scheduled backups** — `backend/scripts/backup_scheduled.ps1` for Task Scheduler,
  plus the in-process scheduler and `POST /api/v1/maintenance/backup`.
  `GET /api/v1/maintenance/status` reports size, row counts, integrity result, backups,
  schema version and current rate-limit usage.
- **Rate limiting** (`app/services/rate_limit.py`) — sliding window on the endpoints that
  spend real upstream quota: batch sync (4 / 5 min), metrics import (3 / 15 min),
  directory refresh (6 / 15 min), backup (4 / 10 min), export (30 / 60 s). A blocked
  call gets **429** with `Retry-After`.

## 7. Frontend safety net

`npm run smoke` (`frontend/scripts/smoke.mjs`) bundles `src/main.jsx` with the project's
own bundler, renders the real `App` through `react-dom/server` for **every route**
(`/`, `/authors`, `/exports`, `/review`, `/analytics`, `/operations`) with a stubbed
`fetch`, and fails on any render error. This is the check that would have caught the
blank profile page caused by a component being used but never defined. It runs in CI
and is also exercised from `backend/test_extras_routes.py`.

`src/main.jsx` gained one line — `export {App};` — so the smoke script can import the
component directly. It is inert in the browser build.

## 8. Accessibility

The extended panels use `role="status"` / `aria-live="polite"` regions so async loads
and outcomes are announced, `role="table"`/`row`/`cell` on the ranking grid, a visible
focus ring on the section heading, and focus is moved to the heading when the panel
changes. The chart keeps its `aria-pressed` legend chips and `role="status"` tooltip.

## New environment variables (all optional)

    LOG_FILE=backend/logs/researchpulse.log
    BACKUP_INTERVAL_HOURS=24
    BACKUP_RETENTION=14

## New tables

`schema_migrations`, `sync_runs`, `manual_metric_links`, `audit_log`, `export_log` —
created by `extras_db.init_extras_db()`. No existing table was altered.

## Fixed along the way

- **`db.upsert_vfstr_author()` silently dropped metric columns.** It never listed
  `scopus_citations`, `scopus_h_index`, `google_scholar_citations`,
  `google_scholar_h_index`, `google_scholar_i10`, `data_status`, `metrics_date` or
  `professional_memberships` in its INSERT, so any caller passing metrics lost them with
  no error (the importer only worked because it called `update_vfstr_research_data()`
  separately). Now persisted, with `COALESCE` on conflict so a metric-less re-upsert
  cannot wipe existing values.

## Not done

Password hashing and the credential handling you excluded. The `auth_users` table
remains unused, and the keys in `backend/.env` are still plaintext — rotate them before
this leaves your machine.
