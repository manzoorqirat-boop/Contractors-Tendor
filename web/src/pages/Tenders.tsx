import { useEffect, useState } from 'react';
import { api } from '../api';
import type { Nav } from '../App';
import { FilterBar } from '../FilterBar';
import { StarButton, TenderDrawer } from '../TenderDrawer';
import { chain, fmtDate, headline, relative } from '../format';
import { useLoad } from '../hooks';
import { Skeleton, useToast } from '../ui';
import type { AppConfig, Tender } from '../types';

const SORTS: [string, string][] = [
  ['closing', 'Closing soonest'], ['closing_desc', 'Closing latest'], ['value', 'Highest value'], ['value_asc', 'Lowest value'], ['published', 'Newest published'], ['first_seen', 'Newly found'],
];
const PAGE_SIZE = 25;

export function TendersPage({ config, nav }: { config: AppConfig; nav: Nav }) {
  const { filters, display } = nav;
  const toast = useToast();
  const { data, error, loading, reload } = useLoad(() => api.tenders(filters, display.sort, display.page, PAGE_SIZE), [JSON.stringify(filters), display.sort, display.page]);
  const [items, setItems] = useState<Tender[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => { if (data) setItems(data.items); }, [data]);

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  const layout = display.layout;
  const setStar = (key: string, starred: boolean) => setItems((xs) => xs.map((t) => (t.key === key ? { ...t, starred } : t)));
  const sortBy = (s: string) => nav.setDisplay({ sort: s, page: 1 });

  async function copyId(id: string) {
    try { await navigator.clipboard.writeText(id); toast('Tender ID copied'); } catch { toast('Could not copy', 'bad'); }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Tenders</h1>
          <p className="sub">{data ? `${data.total.toLocaleString('en-IN')} civil tender${data.total === 1 ? '' : 's'}` : 'Loading…'}</p>
        </div>
        <div className="head-actions">
          <a className="btn" href={api.exportUrl(filters, display.sort)} download>Download spreadsheet</a>
        </div>
      </div>

      <FilterBar filters={filters} display={display} config={config} facets={nav.facets} onChange={nav.setFilters} onApplyView={(f, d) => nav.go('tenders', f, d)} />

      <section className="card">
        <div className="list-tools">
          <label className="field-inline">Sort
            <select value={display.sort} onChange={(e) => sortBy(e.target.value)} aria-label="Sort">{SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
          </label>
          <div className="seg" role="group" aria-label="Layout">
            <button type="button" className={layout === 'cards' ? 'on' : ''} aria-pressed={layout === 'cards'} onClick={() => nav.setDisplay({ layout: 'cards' })}>Cards</button>
            <button type="button" className={layout === 'table' ? 'on' : ''} aria-pressed={layout === 'table'} onClick={() => nav.setDisplay({ layout: 'table' })}>Table</button>
          </div>
        </div>

        {error && <div className="alert bad">{error} <button className="link-btn" onClick={reload}>Try again</button></div>}
        {!data && !error && Array.from({ length: 5 }, (_, i) => <div key={i} className="tender"><div><Skeleton h={16} w="80%" style={{ marginBottom: 8 }} /><Skeleton h={12} w="55%" /></div><Skeleton h={30} w={110} /></div>)}
        {data && items.length === 0 && <div className="empty">No tenders match these filters.</div>}

        <div style={{ opacity: loading && data ? 0.55 : 1 }} aria-busy={loading}>
          {layout === 'cards' && items.map((t) => <Card key={t.key} t={t} now={config.now} onOpen={() => setSelected(t.key)} onStar={(s) => setStar(t.key, s)} onCopy={() => copyId(t.tender_id)} />)}
          {layout === 'table' && items.length > 0 && (
            <div className="scroll-x">
              <table className="tbl">
                <thead><tr><th aria-label="Star" /><th>Work</th><th>Department</th><th>Portal</th><th className="num">Value</th><th>Closes</th></tr></thead>
                <tbody>
                  {items.map((t) => {
                    const rel = relative(t.closing, config.now);
                    return (
                      <tr key={t.key} className="click" tabIndex={0} onClick={() => setSelected(t.key)} onKeyDown={(e) => { if (e.key === 'Enter') setSelected(t.key); }}>
                        <td><StarButton t={t} onChange={(s) => setStar(t.key, s)} /></td>
                        <td style={{ minWidth: 240 }}><div className="strong">{headline(t).slice(0, 140)}{headline(t).length > 140 ? '…' : ''}</div><div className="mono muted">{t.tender_id}</div></td>
                        <td className="muted small">{chain(t.organisation)[0]}</td>
                        <td className="nowrap">{t.portal_name.split(' (')[0]}</td>
                        <td className="num nowrap">{t.value === null ? <span className="muted">Not stated</span> : t.value_text.split(' (')[0]}</td>
                        <td className="nowrap">{fmtDate(t.closing)}{rel.text && <div className={`when ${rel.tone} small`}>{rel.text}</div>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {data && data.total > PAGE_SIZE && (
          <div className="pager">
            <span className="muted">Page {display.page} of {pages}</span>
            <span style={{ display: 'flex', gap: 8 }}>
              <button className="btn small" disabled={display.page <= 1} onClick={() => nav.setDisplay({ page: display.page - 1 })}>Previous</button>
              <button className="btn small" disabled={display.page >= pages} onClick={() => nav.setDisplay({ page: display.page + 1 })}>Next</button>
            </span>
          </div>
        )}
      </section>

      {selected && <TenderDrawer tenderKey={selected} now={config.now} onClose={() => setSelected(null)} onStar={setStar} />}
    </>
  );
}

function Card({ t, now, onOpen, onStar, onCopy }: { t: Tender; now: string; onOpen: () => void; onStar: (s: boolean) => void; onCopy: () => void }) {
  const rel = relative(t.closing, now);
  return (
    <article className="tender" onClick={onOpen}>
      <div>
        <div className="t-title">
          <StarButton t={t} onChange={onStar} />
          <button className="work linkish" onClick={onOpen}>{headline(t)}</button>
        </div>
        <div className="org">{chain(t.organisation).join(' › ')}</div>
        <div className="chips">
          <span className="badge accent">{t.portal_name.split(' (')[0]}</span>
          {t.category && <span className="badge plain">{t.category}</span>}
          {t.location && <span className="badge plain">{t.location}</span>}
          <span className="mono">{t.tender_id}</span>
          <button className="btn small" onClick={(e) => { e.stopPropagation(); onCopy(); }} aria-label={`Copy Tender ID ${t.tender_id}`}>Copy ID</button>
        </div>
      </div>
      <div className="side">
        <div className="value">{t.value === null ? 'Value not stated' : t.value_text}</div>
        <div className="closes">Closes {fmtDate(t.closing, true)}</div>
        {rel.text && <div className={`when ${rel.tone}`}>{rel.text}</div>}
      </div>
    </article>
  );
}
