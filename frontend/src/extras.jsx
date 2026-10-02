/* Additive workspace panels: exports, unmatched-row review, cohort analytics and
   batch synchronisation. New routes only; no existing page is modified. */
import React, { useEffect, useMemo, useState } from 'react';
import { AlertCircle, BarChart3, CheckCircle2, Database, Download, GitCompare, Link2, RefreshCw, Trash2, X } from 'lucide-react';
import './extras.css';

const EXTRA_SECTIONS = [
  { id: 'exports', label: 'Exports', icon: Download, blurb: 'Download the directory, metrics and publications as CSV or Excel.' },
  { id: 'review', label: 'Import Review', icon: Link2, blurb: 'Link the spreadsheet rows the automatic import could not match safely.' },
  { id: 'analytics', label: 'Analytics', icon: BarChart3, blurb: 'Cohort ranking, percentiles and side-by-side comparison.' },
  { id: 'operations', label: 'Operations', icon: Database, blurb: 'Batch synchronization, failure history and database health.' },
];

const METRIC_LABELS = {
  google_scholar_citations: 'Scholar citations',
  scopus_citations: 'Scopus citations',
  google_scholar_h_index: 'Scholar h-index',
  scopus_h_index: 'Scopus h-index',
  google_scholar_i10: 'Scholar i10-index',
};

function download(filename, blob) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function ExtrasNavButtons({ page, navigate }) {
  return <>
    {EXTRA_SECTIONS.map(section => (
      <button key={section.id} className={page === section.id ? 'active' : ''} onClick={() => navigate(section.id)}>
        <section.icon size={18} />{section.label}
      </button>
    ))}
  </>;
}

export function ExtrasMobileNavButtons({ page, navigate }) {
  return <>
    {EXTRA_SECTIONS.map(section => (
      <button key={section.id} className={page === section.id ? 'active' : ''} onClick={() => navigate(section.id)}>
        <section.icon size={16} />{section.label}
      </button>
    ))}
  </>;
}

export function ExtrasPage({ page, navigate, apiFetch, openProfile }) {
  const headingRef = React.useRef(null);
  // Move focus to the section heading when the section changes so keyboard and
  // screen-reader users are not left at the top of the previous panel.
  React.useEffect(() => {
    if (headingRef.current) headingRef.current.focus();
  }, [page]);
  return <section className="authors-page" aria-labelledby="extras-heading">
    <div className="authors-heading">
      <div>
        <span className="eyebrow">EXTENDED WORKSPACE</span>
        <h2 id="extras-heading" tabIndex={-1} ref={headingRef}>{EXTRA_SECTIONS.find(item => item.id === page)?.label || 'Extras'}</h2>
        <p>{EXTRA_SECTIONS.find(item => item.id === page)?.blurb}</p>
      </div>
      <div className="authors-page-actions">
        <button className="secondary" onClick={() => navigate('search')}><X size={15} />Back to workspace</button>
      </div>
    </div>
    {page === 'exports' && <ExportPanel apiFetch={apiFetch} />}
    {page === 'review' && <ReviewPanel apiFetch={apiFetch} />}
    {page === 'analytics' && <AnalyticsPanel apiFetch={apiFetch} openProfile={openProfile} />}
    {page === 'operations' && <OperationsPanel apiFetch={apiFetch} />}
  </section>;
}

/* ------------------------------------------------------------------ exports */

function ExportPanel({ apiFetch }) {
  const [catalogue, setCatalogue] = useState(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    apiFetch('/api/v1/exports')
      .then(response => response.ok ? response.json() : null)
      .then(result => { if (active && result) setCatalogue(result); })
      .catch(() => {})
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const run = async (dataset, format) => {
    setBusy(dataset + format);
    setError('');
    setMessage('');
    try {
      const response = await apiFetch(`/api/v1/exports/${dataset}?format=${format}`);
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `Export failed with HTTP ${response.status}.`);
      }
      const blob = await response.blob();
      download(`${dataset}.${format}`, blob);
      setMessage(`${dataset}.${format} downloaded (${Math.max(1, Math.round(blob.size / 1024))} KB).`);
    } catch (exportError) {
      setError(exportError.message);
    } finally {
      setBusy('');
    }
  };

  const datasets = catalogue?.datasets || [];
  return <div className="extras-grid">
    <div className="sr-status" role="status" aria-live="polite">
      {loading ? 'Loading export catalogue...' : error ? `Export error: ${error}` : message}
    </div>
    {datasets.map(dataset => (
      <article className="extras-card" key={dataset.id}>
        <span className="label">{dataset.id.toUpperCase()}</span>
        <h3>{dataset.description}</h3>
        <p>Downloads every matching row, not just the current page.</p>
        <div className="extras-actions">
          <button className="secondary" disabled={busy !== ''} onClick={() => run(dataset.id, 'csv')}>
            <Download size={14} />{busy === dataset.id + 'csv' ? 'Preparing...' : 'CSV'}
          </button>
          <button className="secondary" disabled={busy !== ''} onClick={() => run(dataset.id, 'xlsx')}>
            <Download size={14} />{busy === dataset.id + 'xlsx' ? 'Preparing...' : 'Excel'}
          </button>
        </div>
      </article>
    ))}
    <article className="extras-card">
      <span className="label">RECENT EXPORTS</span>
      {error && <div className="notice error"><AlertCircle size={15} />{error}</div>}
      {message && <div className="notice" style={{ background: '#edf9f1', color: '#2e7a4b' }}><CheckCircle2 size={15} />{message}</div>}
      {catalogue?.recent_exports?.length
        ? <table className="extras-table"><thead><tr><th>Dataset</th><th>Rows</th><th>When</th></tr></thead><tbody>
            {catalogue.recent_exports.slice(0, 8).map(entry => (
              <tr key={entry.id}><td>{entry.dataset}</td><td>{entry.row_count}</td><td>{new Date(entry.created_at).toLocaleString()}</td></tr>
            ))}
          </tbody></table>
        : <p>No exports recorded yet.</p>}
    </article>
  </div>;
}

/* ------------------------------------------------------------------- review */

function ReviewPanel({ apiFetch }) {
  const [queue, setQueue] = useState(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [onlyPending, setOnlyPending] = useState(true);

  const load = async () => {
    try {
      const response = await apiFetch('/api/v1/review/queue');
      if (!response.ok) throw new Error('Could not load the review queue.');
      setQueue(await response.json());
    } catch (loadError) {
      setError(loadError.message);
    }
  };
  useEffect(() => { load(); }, []);

  const link = async (row, authorName) => {
    setBusy(String(row.spreadsheet_row));
    setError('');
    setMessage('');
    try {
      const search = await apiFetch('/api/v1/authors?page_size=1&search=' + encodeURIComponent(authorName));
      const found = search.ok ? await search.json() : { authors: [] };
      const author = found.authors?.[0];
      if (!author) throw new Error(`No verified author matched "${authorName}".`);
      const response = await apiFetch('/api/v1/review/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ spreadsheet_row: row.spreadsheet_row, vfstr_author_id: author.id }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'The row could not be linked.');
      setMessage(`Row ${row.spreadsheet_row} ("${row.spreadsheet_name}") linked to ${result.official_name}.`);
      await load();
    } catch (linkError) {
      setError(linkError.message);
    } finally {
      setBusy('');
    }
  };

  const undo = async row => {
    setBusy(String(row.spreadsheet_row));
    setError('');
    setMessage('');
    try {
      const response = await apiFetch('/api/v1/review/undo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ spreadsheet_row: row.spreadsheet_row }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'The link could not be removed.');
      setMessage(`Link for row ${row.spreadsheet_row} removed.`);
      await load();
    } catch (undoError) {
      setError(undoError.message);
    } finally {
      setBusy('');
    }
  };

  if (!queue) return <article className="extras-card" role="status" aria-live="polite"><p>Loading the review queue...</p></article>;
  if (!queue.available) return <article className="extras-card"><span className="label">REVIEW QUEUE</span><h3>Nothing to review yet</h3><p>{queue.message}</p></article>;

  const counts = queue.counts || {};
  const rows = (queue.rows || []).filter(row => !onlyPending || row.status !== 'linked');
  return <>
    <div className="sr-status" role="status" aria-live="polite">
      {error ? `Review error: ${error}` : message ? message : `${counts.pending} of ${counts.total_unmatched} unmatched rows still pending.`}
    </div>
    <div className="extras-stats">
      <div className="extras-stat"><span>Unmatched rows</span><strong>{counts.total_unmatched}</strong><small>from the {queue.snapshot_date} import</small></div>
      <div className="extras-stat"><span>Still pending</span><strong>{counts.pending}</strong><small>awaiting a decision</small></div>
      <div className="extras-stat"><span>Linked</span><strong>{counts.linked}</strong><small>confirmed by an operator</small></div>
      <div className="extras-stat"><span>No suggestion</span><strong>{counts.without_suggestions}</strong><small>need manual lookup</small></div>
    </div>
    <article className="extras-card">
      <span className="label">UNMATCHED SPREADSHEET ROWS</span>
      <h3>Confirm a candidate to apply the row</h3>
      <p>Suggestions are the ones the importer computed. Applying a row writes the same metric fields the automatic import would have written, and records who confirmed it.</p>
      <div className="extras-actions">
        <button className="secondary" onClick={() => setOnlyPending(value => !value)}>{onlyPending ? 'Show linked rows too' : 'Show pending only'}</button>
        <button className="secondary" onClick={load}><RefreshCw size={14} />Reload</button>
      </div>
      {error && <div className="notice error"><AlertCircle size={15} />{error}</div>}
      {message && <div className="notice" style={{ background: '#edf9f1', color: '#2e7a4b' }}><CheckCircle2 size={15} />{message}</div>}
      <div className="review-list">
        {rows.length === 0 && <div className="review-empty">Nothing left to review.</div>}
        {rows.map(row => (
          <div className={'review-row ' + (row.status === 'linked' ? 'is-linked' : '')} key={row.spreadsheet_row}>
            <div className="review-name">
              <strong>{row.spreadsheet_name}</strong>
              <small>Row {row.spreadsheet_row}{row.status === 'linked' ? ' · linked to ' + row.linked_official_name : ''}</small>
              {row.ambiguous && <span className="review-flag"><AlertCircle size={11} />{row.ambiguity_reason || 'Ambiguous name'}</span>}
            </div>
            <div className="review-candidates">
              {row.suggestions.length === 0 && <small style={{ color: '#98a1b0' }}>No candidate suggested — search the directory manually.</small>}
              {row.suggestions.map(candidate => (
                <button className="review-candidate" key={candidate} disabled={busy !== '' || row.status === 'linked'} onClick={() => link(row, candidate)}>
                  {busy === String(row.spreadsheet_row) ? 'Linking...' : candidate}
                </button>
              ))}
            </div>
            <div className="review-state">
              <span className={'status-pill ' + (row.status === 'linked' ? 'updated' : '')}>{row.status}</span>
              {row.status === 'linked' && <button className="secondary" style={{ height: 32, padding: '0 10px', fontSize: 10 }} disabled={busy !== ''} onClick={() => undo(row)}><Trash2 size={13} />Undo</button>}
            </div>
          </div>
        ))}
      </div>
    </article>
  </>;
}

/* ---------------------------------------------------------------- analytics */

function AnalyticsPanel({ apiFetch, openProfile }) {
  const [sort, setSort] = useState('scopus_citations');
  const [department, setDepartment] = useState('');
  const [ranking, setRanking] = useState(null);
  const [cohort, setCohort] = useState(null);
  const [compareIds, setCompareIds] = useState('');
  const [comparison, setComparison] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setBusy(true);
    setError('');
    try {
      const query = new URLSearchParams({ sort, limit: '25' });
      if (department) query.set('department', department);
      const [rankingResponse, cohortResponse] = await Promise.all([
        apiFetch('/api/v1/analytics/ranking?' + query),
        apiFetch('/api/v1/analytics/cohort' + (department ? '?department=' + encodeURIComponent(department) : '')),
      ]);
      if (!rankingResponse.ok) throw new Error((await rankingResponse.json()).detail || 'Ranking failed.');
      setRanking(await rankingResponse.json());
      setCohort(cohortResponse.ok ? await cohortResponse.json() : null);
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => { load(); }, [sort, department]);

  const runCompare = async () => {
    const ids = compareIds.split(',').map(value => value.trim()).filter(Boolean);
    if (!ids.length) { setError('Enter at least one faculty id, or open profiles and copy their ids.'); return; }
    setBusy(true);
    setError('');
    try {
      const response = await apiFetch('/api/v1/analytics/compare?faculty_ids=' + encodeURIComponent(ids.join(',')));
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Comparison failed.');
      setComparison(result);
    } catch (compareError) {
      setError(compareError.message);
    } finally {
      setBusy(false);
    }
  };

  return <>
    <div className="sr-status" role="status" aria-live="polite">
      {error ? `Analytics error: ${error}` : ranking ? `Ranking by ${ranking.sort_label}: ${ranking.returned} of ${ranking.cohort_size} authors with a reported value.` : 'Loading cohort ranking...'}
    </div>
    <article className="extras-card">
      <span className="label">COHORT RANKING</span>
      <h3>{ranking ? `${ranking.cohort_size} authors with a reported value` : 'Loading...'}</h3>
      <div className="extras-actions">
        <select value={sort} onChange={event => setSort(event.target.value)} style={{ height: 38, border: '1px solid #dce1eb', borderRadius: 8, padding: '0 10px', font: 'inherit', fontSize: 11 }}>
          {Object.entries(METRIC_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>
        <input value={department} onChange={event => setDepartment(event.target.value)} placeholder="Department filter (optional)" style={{ height: 38, border: '1px solid #dce1eb', borderRadius: 8, padding: '0 11px', font: 'inherit', fontSize: 11, minWidth: 220 }} />
        <button className="secondary" onClick={load} disabled={busy}><RefreshCw size={14} />Refresh</button>
      </div>
      {error && <div className="notice error"><AlertCircle size={15} />{error}</div>}
      {ranking && <div className="rank-table" role="table" aria-label={`Cohort ranking by ${ranking.sort_label}`}>
        <div className="rank-row head" role="row"><span role="columnheader">#</span><span role="columnheader">Name</span><span role="columnheader" className="rank-department">Department</span><span role="columnheader" className="rank-value">Value</span><span role="columnheader" className="rank-percentile">Percentile</span></div>
        {ranking.entries.map(entry => (
          <div className={'rank-row ' + (entry.value === null ? 'is-missing' : '')} key={entry.faculty_id || entry.name} role="row">
            <span className="rank-position" role="cell">{entry.rank}</span>
            <span className="rank-name" role="cell" title={entry.name}>{entry.name}</span>
            <span className="rank-department" role="cell" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{entry.department || '—'}</span>
            <span className="rank-value" role="cell">{entry.value === null ? 'Not reported' : entry.value}</span>
            <span className="rank-percentile" role="cell">{entry.percentile === null ? '—' : entry.percentile + '%'}</span>
          </div>
        ))}
      </div>}
      {ranking && <p className="extras-note">{ranking.note}</p>}
    </article>

    {cohort && <article className="extras-card">
      <span className="label">COHORT DISTRIBUTION</span>
      <h3>Median, spread and coverage per metric</h3>
      <table className="extras-table">
        <thead><tr><th>Metric</th><th>Reported</th><th>Missing</th><th>Median</th><th>Average</th><th>Min</th><th>Max</th></tr></thead>
        <tbody>
          {Object.entries(cohort.metrics).map(([key, stats]) => (
            <tr key={key}>
              <td>{METRIC_LABELS[key] || key}</td>
              <td>{stats.reported}</td>
              <td>{stats.missing}</td>
              <td>{stats.median ?? '—'}</td>
              <td>{stats.average ?? '—'}</td>
              <td>{stats.min ?? '—'}</td>
              <td>{stats.max ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="extras-note">Missing values are counted separately and never treated as zero.</p>
    </article>}

    <article className="extras-card">
      <span className="label">SIDE-BY-SIDE COMPARISON</span>
      <h3>Compare up to six profiles</h3>
      <div className="compare-picker">
        <label>Faculty ids (comma separated)
          <input value={compareIds} onChange={event => setCompareIds(event.target.value)} placeholder="fac_vfstr_..., fac_vfstr_..." />
        </label>
        <button className="primary" onClick={runCompare} disabled={busy}><GitCompare size={15} />Compare</button>
      </div>
      {comparison && comparison.subjects.length > 0 && <div className="compare-grid">
        <div className="compare-cell head">Metric</div>
        {comparison.subjects.map(subject => <div className="compare-cell head" key={subject.faculty_id}>{subject.name}<small style={{ fontWeight: 400 }}>{subject.designation || '—'}</small></div>)}
        {Object.keys(METRIC_LABELS).map(key => (
          <React.Fragment key={key}>
            <div className="compare-cell"><span>{METRIC_LABELS[key]}</span><small>value · percentile</small></div>
            {comparison.subjects.map(subject => {
              const cell = subject.metrics[key] || {};
              return <div className="compare-cell" key={subject.faculty_id + key}>
                <strong>{cell.value === null || cell.value === undefined ? 'Not reported' : cell.value}</strong>
                <small>{cell.percentile === null || cell.percentile === undefined ? '—' : cell.percentile + ' percentile'}</small>
              </div>;
            })}
          </React.Fragment>
        ))}
      </div>}
      {comparison && comparison.missing_faculty_ids.length > 0 && <p className="extras-note">Not found: {comparison.missing_faculty_ids.join(', ')}</p>}
      {comparison && <p className="extras-note">{comparison.note}</p>}
      <p className="extras-note">Open a profile to copy its id, or pick from the ranking above.</p>
      {ranking && <div className="extras-actions">
        {ranking.entries.slice(0, 6).map(entry => (
          <button className="secondary" key={entry.author_id} onClick={() => setCompareIds(current => current ? current + ', ' + entry.faculty_id : entry.faculty_id)}>
            + {entry.name}
          </button>
        ))}
      </div>}
    </article>
  </>;
}

/* --------------------------------------------------------------- operations */

function OperationsPanel({ apiFetch }) {
  const [status, setStatus] = useState(null);
  const [failures, setFailures] = useState(null);
  const [maintenance, setMaintenance] = useState(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  const load = async () => {
    try {
      const [statusResponse, failuresResponse, maintenanceResponse] = await Promise.all([
        apiFetch('/api/v1/sync/batch/status'),
        apiFetch('/api/v1/sync/failures'),
        apiFetch('/api/v1/maintenance/status'),
      ]);
      if (statusResponse.ok) setStatus(await statusResponse.json());
      if (failuresResponse.ok) setFailures(await failuresResponse.json());
      if (maintenanceResponse.ok) setMaintenance(await maintenanceResponse.json());
    } catch (loadError) {
      setError(loadError.message);
    }
  };
  useEffect(() => { load(); const timer = setInterval(load, 15000); return () => clearInterval(timer); }, []);

  const startBatch = async payload => {
    setBusy('batch');
    setError('');
    setMessage('');
    try {
      const response = await apiFetch('/api/v1/sync/batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'The batch could not be started.');
      setMessage(`Batch started for ${result.requested} profile(s). Progress appears below.`);
      await load();
    } catch (batchError) {
      setError(batchError.message);
    } finally {
      setBusy('');
    }
  };

  const backup = async () => {
    setBusy('backup');
    setError('');
    setMessage('');
    try {
      const response = await apiFetch('/api/v1/maintenance/backup', { method: 'POST' });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Backup failed.');
      setMessage(`Backup written: ${result.backup}`);
      await load();
    } catch (backupError) {
      setError(backupError.message);
    } finally {
      setBusy('');
    }
  };

  const runs = status?.runs || [];
  return <>
    <div className="sr-status" role="status" aria-live="polite">
      {error ? `Operations error: ${error}` : message ? message : status?.running ? 'A batch synchronization is running.' : 'No batch synchronization is running.'}
    </div>
    <article className="extras-card">
      <span className="label">BATCH SYNCHRONIZATION</span>
      <h3>{status?.running ? 'A batch is running' : 'Refresh many profiles at once'}</h3>
      <p>Runs in the background. Every profile's outcome is recorded, including failures, so nothing is silently skipped.</p>
      <div className="extras-actions">
        <button className="primary" disabled={busy !== '' || status?.running} onClick={() => startBatch({ only_never_synced: true })}><RefreshCw size={15} />Sync never-synced profiles</button>
        <button className="secondary" disabled={busy !== '' || status?.running} onClick={() => startBatch({ only_with_scholar: true })}><RefreshCw size={15} />Sync linked Scholar profiles</button>
        <button className="secondary" disabled={busy !== '' || status?.running} onClick={() => startBatch({})}><RefreshCw size={15} />Sync all</button>
        <button className="secondary" disabled={busy !== ''} onClick={backup}><Database size={15} />{busy === 'backup' ? 'Backing up...' : 'Back up database now'}</button>
      </div>
      {error && <div className="notice error"><AlertCircle size={15} />{error}</div>}
      {message && <div className="notice" style={{ background: '#edf9f1', color: '#2e7a4b' }}><CheckCircle2 size={15} />{message}</div>}
      {runs.length > 0 && <table className="extras-table">
        <thead><tr><th>Run</th><th>Kind</th><th>Status</th><th>OK</th><th>Failed</th><th>Finished</th></tr></thead>
        <tbody>
          {runs.map(run => (
            <tr key={run.id} className={run.status === 'error' ? 'is-error' : ''}>
              <td>{run.id}</td>
              <td>{run.kind}</td>
              <td><span className={'extras-pill ' + (run.status === 'ok' ? 'ok' : run.status === 'partial' ? 'warn' : 'bad')}>{run.status}</span></td>
              <td>{run.succeeded}</td>
              <td>{run.failed}</td>
              <td>{run.finished_at ? new Date(run.finished_at).toLocaleString() : 'running'}</td>
            </tr>
          ))}
        </tbody>
      </table>}
    </article>

    {failures && <article className="extras-card">
      <span className="label">SYNC FAILURES</span>
      <h3>What the background job could not do</h3>
      <p>Previously these were swallowed. They are now recorded per source.</p>
      {failures.failing_sources?.length > 0
        ? <table className="extras-table"><thead><tr><th>Source</th><th>Failures</th><th>Last attempt</th></tr></thead><tbody>
            {failures.failing_sources.map(source => (
              <tr key={source.source}><td>{source.source}</td><td>{source.failures}</td><td>{source.last_at ? new Date(source.last_at).toLocaleString() : '—'}</td></tr>
            ))}
          </tbody></table>
        : <p>No recorded failures.</p>}
      {failures.recent_errors?.length > 0 && <table className="extras-table">
        <thead><tr><th>Source</th><th>Message</th><th>When</th></tr></thead>
        <tbody>
          {failures.recent_errors.slice(0, 8).map((entry, index) => (
            <tr key={index}><td>{entry.source}</td><td>{entry.message}</td><td>{entry.synced_at ? new Date(entry.synced_at).toLocaleString() : '—'}</td></tr>
          ))}
        </tbody>
      </table>}
      <p className="extras-note">{failures.never_or_stale?.length || 0} profiles have no capture in the last {failures.stale_after_hours} hours.</p>
      {failures.circuit_breakers?.length > 0 && <table className="extras-table">
        <thead><tr><th>Provider</th><th>Consecutive failures</th><th>Suspended</th></tr></thead>
        <tbody>
          {failures.circuit_breakers.map(state => (
            <tr key={state.provider}><td>{state.provider}</td><td>{state.consecutive_failures}</td><td>{state.open ? `yes, ${state.retry_after_seconds}s left` : 'no'}</td></tr>
          ))}
        </tbody>
      </table>}
    </article>}

    {maintenance && <article className="extras-card">
      <span className="label">DATABASE HEALTH</span>
      <h3>{maintenance.database.exists ? 'SQLite database reachable' : 'Database file missing'}</h3>
      <table className="extras-table">
        <tbody>
          <tr><td>Integrity check</td><td>{maintenance.database.integrity}</td></tr>
          <tr><td>Size</td><td>{maintenance.database.bytes ? Math.round(maintenance.database.bytes / 1024) + ' KB' : '—'}</td></tr>
          <tr><td>Faculty rows</td><td>{maintenance.database.faculty}</td></tr>
          <tr><td>Metric rows</td><td>{maintenance.database.metrics}</td></tr>
          <tr><td>Publication rows</td><td>{maintenance.database.publications}</td></tr>
          <tr><td>Directory rows</td><td>{maintenance.database.vfstr_authors}</td></tr>
        </tbody>
      </table>
      {maintenance.backups?.length > 0 && <table className="extras-table">
        <thead><tr><th>Backup</th><th>Size</th><th>Modified</th></tr></thead>
        <tbody>
          {maintenance.backups.slice(0, 6).map(file => (
            <tr key={file.name}><td>{file.name}</td><td>{Math.round(file.bytes / 1024)} KB</td><td>{new Date(file.modified).toLocaleString()}</td></tr>
          ))}
        </tbody>
      </table>}
      <p className="extras-note">Automatic backups run every 24 hours and keep the 14 most recent files.</p>
    </article>}
  </>;
}
