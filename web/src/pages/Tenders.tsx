import { useEffect, useState } from 'react';
import { api } from '../api';
import { chain, fmtDate, headline, relative } from '../format';
import { useDebounced, useLoad } from '../hooks';
import type { AppConfig, Filters, Tender } from '../types';

interface Props { config: AppConfig; filters: Filters; setFilters: (f: Filters) => void }

const SORTS: [string, string][] = [
  ['closing', 'Closing soonest'], ['value', 'Highest value'], ['published', 'Newest published'], ['first_seen', 'Newly found'],
];
const VALUES: [string, string][] = [
  ['', 'Any value'], ['100000', '1 lakh +'], ['1000000', '10 lakh +'], ['5000000', '50 lakh +'], ['10000000', '1 crore +'], ['100000000', '10 crore +'],
];
const PAGE_SIZE = 25;

export function TendersPage({ config, filters, setFilters }: Props) {
  const [qInput, setQInput] = useState(filters.q);
  const debounced = useDebounced(qInput);
  useEffect(() => { setQInput(filters.q); }, [filters.q]);
  useEffect(() => { if (debounced !== filters.q) setFilters({ ...filters, q: debounced, page: 1 }); /* eslint-disable-next-line */ }, [debounced]);

  const { data, error, loading } = useLoad(() => api.tenders(filters, PAGE_SIZE), [JSON.stringify(filters)]);
  const set = (patch: Partial<Filters>) => setFilters({ ...filters, page: 1, ...patch });
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Tenders</h1>
          <p className="sub">{data ? `${data.total.toLocaleString('en-IN')} civil tender${data.total === 1 ? '' : 's'}` : 'Loading…'}</p>
        </div>
        <a className="btn" href={api.exportUrl(filters)} download>Download as spreadsheet</a>
      </div>

      <div className="card">
        <div className="filters">
          <input type="text" placeholder="Search work, department, location or Tender ID" aria-label="Search" value={qInput} onChange={(e) => setQInput(e.target.value)} maxLength={100} />
          <select aria-label="Status" value={filters.status} onChange={(e) => set({ status: e.target.value })}>
            <option value="open">Open</option><option value="closing_soon">Closing soon</option><option value="closed">Closed</option><option value="all">All</option>
          </select>
          <select aria-label="Portal" value={filters.portal} onChange={(e) => set({ portal: e.target.value })}>
            <option value="">All portals</option>
            {config.portals.map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}
          </select>
          <select aria-label="Category" value={filters.category} onChange={(e) => set({ category: e.target.value })}>
            <option value="">All categories</option>
            {(data?.categories ?? []).map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <select aria-label="Minimum value" value={filters.min_value} onChange={(e) => set({ min_value: e.target.value })}>
            {VALUES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <select aria-label="Sort" value={filters.sort} onChange={(e) => set({ sort: e.target.value })}>
            {SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </div>

        {error && <div className="alert bad">{error}</div>}
        {!error && data && data.items.length === 0 && <div className="empty">No tenders match these filters.</div>}
        <div style={{ opacity: loading ? 0.6 : 1 }} aria-busy={loading}>
          {data?.items.map((t) => <TenderRow key={t.key} t={t} now={config.now} />)}
        </div>

        {data && data.total > PAGE_SIZE && (
          <div className="pager">
            <span className="sub">Page {filters.page} of {pages}</span>
            <span style={{ display: 'flex', gap: 8 }}>
              <button className="btn small" disabled={filters.page <= 1} onClick={() => setFilters({ ...filters, page: filters.page - 1 })}>Previous</button>
              <button className="btn small" disabled={filters.page >= pages} onClick={() => setFilters({ ...filters, page: filters.page + 1 })}>Next</button>
            </span>
          </div>
        )}
      </div>
    </>
  );
}

function TenderRow({ t, now }: { t: Tender; now: string }) {
  const [copied, setCopied] = useState(false);
  const rel = relative(t.closing, now);
  const org = chain(t.organisation);

  async function copy() {
    try {
      await navigator.clipboard.writeText(t.tender_id);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard blocked: the ID is still on screen to select */ }
  }

  return (
    <article className="tender">
      <div>
        <div className="work">{headline(t)}</div>
        <div className="org">{org.join(' › ')}</div>
        <div className="chips">
          <span className="badge accent">{t.portal_name}</span>
          {t.category && <span className="badge plain">{t.category}</span>}
          {t.location && <span className="badge plain">{t.location}</span>}
          <span className="mono">{t.tender_id}</span>
          <button className="btn small" onClick={copy} aria-label={`Copy Tender ID ${t.tender_id}`}>{copied ? 'Copied' : 'Copy ID'}</button>
          <a className="btn small" href={t.search_url} target="_blank" rel="noopener noreferrer">Find on portal</a>
        </div>
      </div>
      <div className="side">
        <div className="value">{t.value === null ? 'Value not stated' : t.value_text}</div>
        <div className="closes">Closes {fmtDate(t.closing, true)}</div>
        {rel.text && <div className={`when ${rel.tone}`}>{rel.text}</div>}
        {t.emd !== null && t.emd > 0 && <div className="closes">EMD {t.emd.toLocaleString('en-IN')}</div>}
      </div>
    </article>
  );
}
