// Frontend smoke render: catches the class of bug that produced a blank page
// (a component referenced in JSX but never defined), for every route.
//
// Bundles src/main.jsx with the project's own bundler (rolldown, via Vite),
// then renders the real App through react-dom/server with a stubbed fetch.
// A ReferenceError / TypeError during render fails the process.
//
//   node scripts/smoke.mjs        # or: npm run smoke
import { readFile, writeFile, rm } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'rolldown';
import React from 'react';
import { renderToString } from 'react-dom/server';

const warnings = [];
const IGNORED = /Root> component|error boundaries/;
const originalWarn = console.warn;
console.warn = (...args) => { const text = args.join(' '); if (!IGNORED.test(text)) warnings.push(text); };
console.error = () => {};

const json = (payload, status = 200) => ({
  ok: status < 400,
  status,
  headers: new Map(),
  json: async () => payload,
  text: async () => JSON.stringify(payload),
  blob: async () => new Blob([JSON.stringify(payload)]),
});

globalThis.fetch = async url => {
  const path = String(url).replace(/^https?:\/\/[^/]+/, '');
  if (path.startsWith('/api/v1/health')) return json({ status: 'ok', scopus_configured: true, google_scholar_configured: true });
  if (path.startsWith('/api/v1/history')) return json([]);
  if (path.startsWith('/api/v1/authors/departments')) return json({ departments: ['CSE'], schools: [], designations: [], research_areas: [] });
  if (path.startsWith('/api/v1/authors/metrics/import-report')) return json({ report: { imported_count: 1 } });
  if (path.startsWith('/api/v1/authors')) return json({ authors: [], total: 0, page: 1, page_size: 24 });
  if (path.startsWith('/api/v1/exports')) return json({ datasets: [{ id: 'directory', filename: 'directory.csv', description: 'Directory' }], formats: ['csv'], recent_exports: [] });
  if (path.startsWith('/api/v1/review/queue')) return json({ available: true, counts: { total_unmatched: 0, pending: 0, linked: 0, without_suggestions: 0 }, rows: [], total: 0, pages: 1 });
  if (path.startsWith('/api/v1/analytics/ranking')) return json({ sort: 'scopus_citations', cohort_size: 0, entries: [], note: 'n' });
  if (path.startsWith('/api/v1/analytics/cohort')) return json({ cohort_size: 0, metrics: {}, note: 'n' });
  if (path.startsWith('/api/v1/sync/batch/status')) return json({ running: false, runs: [], latest: null });
  if (path.startsWith('/api/v1/sync/failures')) return json({ failing_sources: [], recent_errors: [], never_or_stale: [], circuit_breakers: [] });
  if (path.startsWith('/api/v1/maintenance/status')) return json({ database: { exists: true, integrity: 'ok' }, backups: [], schema: { current: 2 }, rate_limits: [] });
  return json({}, 404);
};

globalThis.localStorage = {
  store: new Map([['researchpulse_session', 'smoke-token']]),
  getItem(key) { return this.store.has(key) ? this.store.get(key) : null; },
  setItem(key, value) { this.store.set(key, String(value)); },
  removeItem(key) { this.store.delete(key); },
};

// The app calls createRoot(document.getElementById('root')) at module scope.
// react-dom/client accepts a container with nodeType 1, 9 or 11, so the stub
// carries nodeType=1 and no-ops the methods createRoot may touch. react-dom/server
// does the actual rendering.
const stubElement = {
  nodeType: 1,
  innerHTML: '',
  appendChild() {},
  removeChild() {},
  setAttribute() {},
  addEventListener() {},
  removeEventListener() {},
};
// react-dom/client resolves the owning document from container.ownerDocument and
// then sets a listener marker on it, so the stub needs a document of its own.
const stubDocument = {
  nodeType: 9,
  appendChild() {},
  removeChild() {},
  createElement: () => stubElement,
  addEventListener() {},
  removeEventListener() {},
};
stubElement.ownerDocument = stubDocument;
globalThis.document = Object.assign(stubDocument, {
  getElementById: () => stubElement,
  body: { appendChild() {}, removeChild() {} },
});

const entry = fileURLToPath(new URL('../src/main.jsx', import.meta.url));
const outfile = fileURLToPath(new URL('../src/__smoke_bundle.mjs', import.meta.url));

// The app reads import.meta.env.VITE_API_URL; define it for the render check.
const entrySource = await readFile(entry, 'utf8');
const patchedEntry = fileURLToPath(new URL('../src/__smoke_entry.jsx', import.meta.url));
await writeFile(patchedEntry, entrySource.replace('import.meta.env.VITE_API_URL', "'http://127.0.0.1:8000'"));

await build({
  input: patchedEntry,
  output: { file: outfile, format: 'esm' },
  // 'node' platform so bare specifiers (react, lucide-react) resolve from
  // node_modules; CSS is external because a render check does not need it.
  platform: 'node',
  resolve: { extensions: ['.js', '.jsx', '.mjs'] },
  // react must be the same instance this script renders with, so keep it external.
  external: source => source.endsWith('.css') || source === 'react' || source.startsWith('react-dom') || source === 'react/jsx-runtime',
});
await rm(patchedEntry, { force: true });

const routes = ['/', '/authors', '/exports', '/review', '/analytics', '/operations'];
let rendered = 0;
const failures = [];

// Register a tiny loader so the compiled bundle's external `.css` imports are
// accepted as empty modules by Node.
const { register } = await import('node:module');
register(
  'data:text/javascript,' + encodeURIComponent(`
    export async function load(url, context, nextLoad) {
      if (url.endsWith('.css')) {
        return { format: 'module', shortCircuit: true, source: 'export default {};' };
      }
      return nextLoad(url, context);
    }
  `),
  import.meta.url,
);

try {
  for (const route of routes) {
    globalThis.window = {
      location: { pathname: route, href: 'http://127.0.0.1:5173' + route },
      history: { pushState() {}, replaceState() {} },
      addEventListener() {}, removeEventListener() {},
    };
    try {
      const mod = await import(pathToFileURL(outfile).href + '?route=' + encodeURIComponent(route));
      const App = mod.App || mod.default;
      if (typeof App !== 'function') throw new Error('App export not found');
      const html = renderToString(React.createElement(App));
      if (!html || html.length < 50) throw new Error('rendered output was empty');
      rendered += 1;
    } catch (error) {
      failures.push({ route, error: String((error && error.message) || error) });
    }
  }
} finally {
  await rm(outfile, { force: true });
}

process.stdout.write(JSON.stringify({ ok: failures.length === 0, routes: rendered, failures, warnings: warnings.slice(0, 5) }) + '\n');
process.exit(failures.length === 0 ? 0 : 1);
