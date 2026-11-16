import { ScrollX } from '../ScrollX';
import { useEffect, useState } from 'react';
import { api } from '../api';
import type { Nav } from '../App';
import { FitBadge } from '../ai';
import { FilterBar } from '../FilterBar';
import { EMPTY } from '../filters';
import { StarButton, TenderDrawer } from '../TenderDrawer';
import { chain, fmtDate, headline, relative } from '../format';
import { useLoad } from '../hooks';
import { Skeleton, useToast } from '../ui';
import { Only } from '../session';
import { useConfirm } from '../confirm';
import type { AppConfig, Tender } from '../types';

const SORTS: [string, string][] = [
  ['closing', 'Closing soonest'], ['closing_desc', 'Closing latest'], ['value', 'Highest value'], ['value_asc', 'Lowest value'], ['published', 'Newest published'], ['first_seen', 'Newly found'], ['fit', 'Best fit'],
];
const PAGE_SIZE = 25;

export function TendersPage({ config, nav }: { config: AppConfig; nav: Nav }) {
  const { filters, display } = nav;
  const toast = useToast();
  const confirm = useConfirm();
  const { data, error, loading, reload } = useLoad(() => api.tenders(filters, display.sort, display.page, PAGE_SIZE), [JSON.stringify(filters), display.sort, display.page]);
  const [items, setItems] = useState<Tender[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  // Tenders ticked for a bulk action. Kept across pages; at most MAX_PICK so the download address stays short.
  const [picked, setPicked] = useState<string[]>([]);
  const [bulkBusy, setBulkBusy] = useState(false);
  const MAX_PICK = 100;
  const isPicked = (k: string) => picked.includes(k);
  const togglePick = (k: string) => {
    if (!isPicked(k) && picked.length >= MAX_PICK) { toast(`You can select up to ${MAX_PICK} tenders at a time.`, 'bad'); return; }
    setPicked((p) => (p.includes(k) ? p.filter((x) => x !== k) : [...p, k]));
  };
  const pageKeys = items.map((t) => t.key);
  const allOnPage = pageKeys.length > 0 && pageKeys.every(isPicked);
  const togglePage = () => setPicked((p) => (allOnPage ? p.filter((k) => !pageKeys.includes(k)) : [...new Set([...p, ...pageKeys])].slice(0, MAX_PICK)));
  async function bulkStar(starred: boolean) {
    setBulkBusy(true);
    try { const r = await api.starBulk(picked, starred); setItems((xs) => xs.map((t) => (picked.includes(t.key) ? { ...t, starred } : t))); toast(`${starred ? 'Starred' : 'Removed the star from'} ${r.changed} tender${r.changed === 1 ? '' : 's'}`); }
    catch (e) { toast((e as Error).message, 'bad'); } finally { setBulkBusy(false); }
  }
  async function bulkBids() {
    if (picked.length > 5 && !(await confirm({ title: `Start ${picked.length} bids?`, body: 'Each tender gets its own bid with the standard checklist. Tenders that already have a bid are left alone.', confirmLabel: 'Start bids' }))) return;
    setBulkBusy(true);
    try {
      const r = await api.createBidsBulk(picked);
      toast(`Started ${r.created.length} bid${r.created.length === 1 ? '' : 's'}${r.existing.length ? `, ${r.existing.length} already had one` : ''}.`);
      setPicked([]); reload();
    } catch (e) { toast((e as Error).message, 'bad'); } finally { setBulkBusy(false); }
  }
  useEffect(() => { if (data) setItems(data.items); }, [data]);
  useEffect(() => { window.addEventListener('tenders-changed', reload); return () => window.removeEventListener('tenders-changed', reload); }, [reload]);

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  const layout = display.layout;
  const showFit = items.some((t) => t.fit || t.ai_civil === 0);
  const setStar = (key: string, starred: boolean) => setItems((xs) => xs.map((t) => (t.key === key ? { ...t, starred } : t)));
  const sortBy = (s: string) => nav.setDisplay({ sort: s, page: 1 });

  async function startBid(key: string) {
    try {
      const r = await api.createBid(key);
      if (r.created) toast('Added to your bids');
      window.dispatchEvent(new CustomEvent('open-bid', { detail: r.id }));
    } catch (e) { toast((e as Error).message, 'bad'); }
  }

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
          <Only role="bidder"><button className="btn" onClick={() => window.dispatchEvent(new CustomEvent('add-tender'))}>Add a tender</button></Only>
          <a className="btn" href={api.exportUrl(filters, display.sort, 'xlsx')} download>Download Excel</a>
          <a className="btn" href={api.exportUrl(filters, display.sort)} download>CSV</a>
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
        {data && items.length === 0 && <div className="empty"><h2>No tenders match these filters</h2><p className="sub">Widen the closing window or remove a filter to see more.</p><button className="btn primary" onClick={() => nav.setFilters(EMPTY)}>Clear all filters</button></div>}

        <div style={{ opacity: loading && data ? 0.55 : 1 }} aria-busy={loading}>
          {layout === 'cards' && items.map((t) => <Card key={t.key} picked={isPicked(t.key)} onPick={() => togglePick(t.key)} t={t} now={config.now} onOpen={() => setSelected(t.key)} onStar={(s) => setStar(t.key, s)} onCopy={() => copyId(t.tender_id)} onStart={() => startBid(t.key)} />)}
          {layout === 'table' && items.length > 0 && (
            <ScrollX label="Tenders">
              <table className="tbl">
                <thead><tr><th><input type="checkbox" checked={allOnPage} onChange={togglePage} aria-label="Select all tenders on this page" /></th><th><span className="sr-only">Star</span></th><th>Work</th>{showFit && <th>Fit</th>}<th>Department</th><th>Portal</th><th className="num">Value</th><th>Closes</th></tr></thead>
                <tbody>
                  {items.map((t) => {
                    const rel = relative(t.closing, config.now);
                    return (
                      <tr key={t.key} className="click" tabIndex={0} onClick={() => setSelected(t.key)} onKeyDown={(e) => { if (e.key === 'Enter') setSelected(t.key); }}>
                        <td onClick={(e) => e.stopPropagation()}><input type="checkbox" checked={isPicked(t.key)} onChange={() => togglePick(t.key)} aria-label={`Select ${t.tender_id}`} /></td>
                        <td><StarButton t={t} onChange={(s) => setStar(t.key, s)} /></td>
                        <td style={{ minWidth: 240 }}><div className="strong">{headline(t).slice(0, 140)}{headline(t).length > 140 ? '…' : ''}</div><div className="mono muted">{t.tender_id}</div></td>
                        {showFit && <td className="nowrap"><FitBadge fit={t.fit} reason={t.fit_reason} notCivil={t.ai_civil} /></td>}
                        <td className="muted small">{chain(t.organisation)[0]}</td>
                        <td className="nowrap">{t.portal_name.split(' (')[0]}</td>
                        <td className="num nowrap">{t.value === null ? <span className="muted">Not stated</span> : t.value_text.split(' (')[0]}</td>
                        <td className="nowrap">{fmtDate(t.closing)}{rel.text && <div className={`when ${rel.tone} small`}>{rel.text}</div>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </ScrollX>
          )}
        </div>

        {data && data.total > PAGE_SIZE && (
          <div className="pager">
            <span className="muted">Showing {((display.page - 1) * PAGE_SIZE + 1).toLocaleString('en-IN')}–{Math.min(display.page * PAGE_SIZE, data.total).toLocaleString('en-IN')} of {data.total.toLocaleString('en-IN')}</span>
            <span style={{ display: 'flex', gap: 8 }}>
              <button className="btn small" disabled={display.page <= 1} onClick={() => nav.setDisplay({ page: display.page - 1 })}>Previous</button>
              <button className="btn small" disabled={display.page >= pages} onClick={() => nav.setDisplay({ page: display.page + 1 })}>Next</button>
            </span>
          </div>
        )}
      </section>

      {picked.length > 0 && (
        <div className="bulkbar" role="region" aria-label="Actions for the selected tenders">
          <strong>{picked.length} selected</strong>
          {layout === 'cards' && !allOnPage && <button type="button" className="link-btn" onClick={togglePage}>Select this page</button>}
          <span className="bulk-actions">
            <Only role="bidder">
              <button type="button" className="btn small" disabled={bulkBusy} onClick={() => bulkStar(true)}>Star</button>
              <button type="button" className="btn small" disabled={bulkBusy} onClick={() => bulkStar(false)}>Unstar</button>
              <button type="button" className="btn small primary" disabled={bulkBusy} onClick={bulkBids}>Start bids</button>
            </Only>
            <a className="btn small" href={api.exportUrl(filters, display.sort, 'xlsx', picked)} download>Excel</a>
            <a className="btn small" href={api.exportUrl(filters, display.sort, 'csv', picked)} download>CSV</a>
            <button type="button" className="btn small ghost" onClick={() => setPicked([])}>Clear</button>
          </span>
        </div>
      )}

      {selected && <TenderDrawer tenderKey={selected} now={config.now} onClose={() => setSelected(null)} onStar={setStar} />}
    </>
  );
}

function Card({ t, now, picked, onPick, onOpen, onStar, onCopy, onStart }: { t: Tender; now: string; picked: boolean; onPick: () => void; onOpen: () => void; onStar: (s: boolean) => void; onCopy: () => void; onStart: () => void }) {
  const rel = relative(t.closing, now);
  return (
    <article className={`tender urg-${rel.tone || 'none'}`} onClick={onOpen}>
      <div>
        <div className="t-title">
          <input type="checkbox" className="pick" checked={picked} onClick={(e) => e.stopPropagation()} onChange={onPick} aria-label={`Select ${t.tender_id}`} />
          <StarButton t={t} onChange={onStar} />
          <button className="work linkish" onClick={onOpen}>{headline(t)}</button>
        </div>
        <div className="org">{chain(t.organisation).join(' › ')}</div>
        <div className="chips">
          <span className="badge accent">{t.portal_name.split(' (')[0]}</span>
          {t.work_type && <span className="badge plain">{t.work_type}</span>}
          <FitBadge fit={t.fit} reason={t.fit_reason} notCivil={t.ai_civil} />
          {!t.work_type && t.category && <span className="badge plain">{t.category}</span>}
          {t.location && <span className="badge plain">{t.location}</span>}
          <span className="mono">{t.tender_id}</span>
          <button className="btn small ghost" onClick={(e) => { e.stopPropagation(); onCopy(); }} aria-label={`Copy Tender ID ${t.tender_id}`}>Copy ID</button>
          <Only role="bidder"><button className="btn small primary" onClick={(e) => { e.stopPropagation(); onStart(); }}>Start bid</button></Only>
        </div>
      </div>
      <div className="side">
        <div className="value">{t.value === null ? 'Value not stated' : t.value_text}</div>
        <div className="closes">Closes {fmtDate(t.closing, true)}</div>
        {rel.text && <div className={`when pill ${rel.tone}`}>{rel.text}</div>}
      </div>
    </article>
  );
}
