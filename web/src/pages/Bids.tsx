import { Fragment, useEffect, useState } from 'react';
import { api } from '../api';
import { BriefView, readBase64, useAiStatus } from '../ai';
import { BoqCard, RiskCard } from '../Boq';
import { PriceAdvisor } from '../PriceAdvisor';
import { chain, fmtDate, headline, inr, plural, relative } from '../format';
import { useLoad } from '../hooks';
import { Skeleton, useToast } from '../ui';
import { Only, ReadOnly } from '../session';
import type { AppConfig, BidAlert, BidDetail, BidItem, BidStats, BidSummary, MatchStatus, Stage, StatGroup, VaultDoc } from '../types';

type WsTab = 'decide' | 'checklist' | 'price' | 'letters' | 'result';
const defaultTab = (s: Stage): WsTab => (s === 'preparing' ? 'checklist' : ['submitted', 'opened', 'won', 'lost'].includes(s) ? 'result' : 'decide');

const ACTIVE: Stage[] = ['interested', 'preparing', 'submitted', 'opened'];
const CHANGE_TEXT: Record<string, string> = { closing: 'Closing date moved', opening: 'Bid opening date moved', corrigendum: 'Corrigendum', prebid: 'Pre-bid meeting date' };

const whenText = (a: BidAlert) => (a.urgency === 'overdue' ? 'overdue' : a.days_left === 0 ? 'today' : a.days_left === 1 ? 'tomorrow' : `in ${a.days_left} days`);
const num = (v: string): number | null => { const n = Number(v.replace(/[,\s₹]/g, '')); return v.trim() === '' || Number.isNaN(n) ? null : n; };

// Remembered while the app is open, so coming back from a bid returns to the same list.
let lastMode: 'pipeline' | 'results' = 'pipeline';
let lastView: 'active' | 'all' | Stage = 'active';

export function BidsPage({ config, bidId, onOpen }: { config: AppConfig; bidId: number | null; onOpen: (id: number | null) => void }) {
  return bidId ? <Workspace key={bidId} id={bidId} config={config} onBack={() => onOpen(null)} /> : <Pipeline config={config} onOpen={onOpen} />;
}

// ---- pipeline ----------------------------------------------------------------------------------------------------

function Pipeline({ config, onOpen }: { config: AppConfig; onOpen: (id: number) => void }) {
  const toast = useToast();
  const [mode, setModeState] = useState<'pipeline' | 'results'>(lastMode);
  const [view, setViewState] = useState<'active' | 'all' | Stage>(lastView);
  const setMode = (m: 'pipeline' | 'results') => { lastMode = m; setModeState(m); };
  const setView = (v: 'active' | 'all' | Stage) => { lastView = v; setViewState(v); };
  const list = useLoad(() => api.bids(), []);
  const alerts = useLoad(() => api.bidAlerts(), []);
  const data = list.data;

  const shown = (data?.bids ?? []).filter((b) => (view === 'all' ? true : view === 'active' ? ACTIVE.includes(b.stage) : b.stage === view));
  const activeCount = data ? ACTIVE.reduce((n, s) => n + data.counts[s], 0) : 0;

  async function move(b: BidSummary, stage: Stage) {
    try { await api.patchBid(b.id, { stage }); list.reload(); alerts.reload(); } catch (e) { toast((e as Error).message, 'bad'); }
  }

  return (
    <>
      <div className="page-head">
        <div><h1>Bids</h1><p className="sub">{data ? `${plural(activeCount, 'bid')} in progress` : 'Loading…'}</p></div>
        <div className="head-actions">
        <Only role="bidder"><button className="btn" onClick={() => window.dispatchEvent(new CustomEvent('add-tender'))}>Add a tender</button></Only>
        <div className="seg" role="group" aria-label="Bids view">
          <button type="button" className={mode === 'pipeline' ? 'on' : ''} aria-pressed={mode === 'pipeline'} onClick={() => setMode('pipeline')}>Pipeline</button>
          <button type="button" className={mode === 'results' ? 'on' : ''} aria-pressed={mode === 'results'} onClick={() => setMode('results')}>Results</button>
        </div>
        </div>
      </div>

      {mode === 'results' ? <Results /> : (
        <>
          {(alerts.data?.alerts.length ?? 0) > 0 && (
            <section className="card" style={{ marginBottom: 14 }}>
              <h2>Coming up</h2>
              <ul className="nextup">
                {alerts.data!.alerts.slice(0, 8).map((a, i) => (
                  <li key={i} className={a.urgency}>
                    <button type="button" className="linkish" disabled={!a.bid_id} onClick={() => a.bid_id && onOpen(a.bid_id)}>
                      <strong>{a.label}</strong>{a.title ? <span className="muted"> · {a.title}</span> : a.extra ? <span className="muted"> · {a.extra}</span> : null}
                    </button>
                    <span className={`badge ${a.urgency === 'overdue' ? 'bad' : a.urgency === 'later' ? 'plain' : 'warn'}`}>{fmtDate(a.date)} · {whenText(a)}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <div className="chips-row" role="group" aria-label="Show">
            <button type="button" className={`chip pick${view === 'active' ? ' on' : ''}`} onClick={() => setView('active')}>In progress{data && <span className="chip-n">{activeCount}</span>}</button>
            {data?.stages.map((s) => <button key={s.key} type="button" className={`chip pick${view === s.key ? ' on' : ''}`} onClick={() => setView(s.key)}>{s.label}<span className="chip-n">{data.counts[s.key]}</span></button>)}
            <button type="button" className={`chip pick${view === 'all' ? ' on' : ''}`} onClick={() => setView('all')}>All</button>
          </div>

          {list.error && <div className="alert bad">{list.error} <button className="link-btn" onClick={list.reload}>Try again</button></div>}
          {list.loading && !data && <Skeleton h={120} />}
          {data && shown.length === 0 && (
            <div className="card empty">{data.bids.length === 0 ? <>No bids yet. Open a tender on the Tenders page and press <strong>Start a bid</strong>.</> : 'Nothing at this stage.'}</div>
          )}
          <div className="bid-list">
            {shown.map((b) => {
              const rel = relative(b.closing, config.now);
              const pct = b.items_total ? Math.round((100 * b.items_done) / b.items_total) : 0;
              return (
                <article key={b.id} className={`card bidcard urg-${ACTIVE.includes(b.stage) ? rel.tone || 'none' : 'none'}`} onClick={() => onOpen(b.id)}>
                  <div className="bidcard-top">
                    <h3 className="t-title"><button type="button" className="linkish" onClick={(e) => { e.stopPropagation(); onOpen(b.id); }}>{headline(b)}</button></h3>
                    <select className="stage-select" value={b.stage} aria-label="Stage" onClick={(e) => e.stopPropagation()} onChange={(e) => move(b, e.target.value as Stage)}>
                      {data!.stages.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
                    </select>
                  </div>
                  <p className="muted small">{chain(b.organisation).join(' › ')} · <span className="mono">{b.tender_id}</span> · {b.portal_name}</p>
                  <div className="chips" style={{ marginTop: 6 }}>
                    {ACTIVE.includes(b.stage) && rel.text && <span className={`badge ${rel.tone === 'urgent' ? 'bad' : rel.tone === 'soon' ? 'warn' : 'plain'}`}>{rel.text}</span>}
                    <span className="badge plain">{b.value_text}</span>
                    {b.unseen > 0 && <span className="badge bad">{b.unseen} change{b.unseen === 1 ? '' : 's'} to check</span>}
                    {b.owner && <span className="badge plain">{b.owner}</span>}
                    {!b.has_doc && ACTIVE.includes(b.stage) && <span className="badge warn">Document not read</span>}
                  </div>
                  {b.items_total > 0 && (
                    <div className="progress-row"><div className="progress" aria-hidden><span style={{ width: `${pct}%` }} /></div><span className="muted small">{b.items_done}/{b.items_total} done</span></div>
                  )}
                </article>
              );
            })}
          </div>
        </>
      )}
    </>
  );
}

// ---- results -----------------------------------------------------------------------------------------------------

function GroupTable({ title, rows }: { title: string; rows: StatGroup[] }) {
  if (!rows.length) return null;
  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <h2>{title}</h2>
      <div className="scroll-x">
        <table className="tbl"><thead><tr><th>{title.replace('By ', '')}</th><th className="num">Won</th><th className="num">Lost</th><th className="num">Win rate</th><th className="num">Value won</th></tr></thead>
          <tbody>{rows.map((r) => <tr key={r.label}><td>{r.label}</td><td className="num">{r.won}</td><td className="num">{r.lost}</td><td className="num">{Math.round((100 * r.won) / (r.won + r.lost))}%</td><td className="num">{r.won_value ? inr(r.won_value) : '-'}</td></tr>)}</tbody></table>
      </div>
    </section>
  );
}

function Results() {
  const { data, error, loading } = useLoad<BidStats>(() => api.bidStats(), []);
  if (error) return <div className="alert bad">{error}</div>;
  if (loading || !data) return <Skeleton h={120} />;
  const decided = data.won + data.lost;
  return (
    <>
      <div className="grid kpis">
        <div className="kpi"><div className="label">Win rate</div><div className="num">{data.win_rate === null ? '-' : `${data.win_rate}%`}</div><div className="note">{decided ? `${data.won} won of ${decided} decided` : 'Record results to see this'}</div></div>
        <div className="kpi"><div className="label">Bids submitted</div><div className="num">{data.submitted}</div><div className="note">{data.in_progress} still in progress</div></div>
        <div className="kpi"><div className="label">Value won</div><div className="num">{data.won_value ? inr(data.won_value) : '-'}</div><div className="note">{data.dropped} dropped before bidding</div></div>
        <div className="kpi"><div className="label">Your price vs the winner</div><div className="num">{data.avg_gap_to_l1 === null ? '-' : `${data.avg_gap_to_l1 > 0 ? '+' : ''}${data.avg_gap_to_l1}%`}</div><div className="note">{data.gap_samples ? `average over ${plural(data.gap_samples, 'lost bid')}` : 'Add the winning price on lost bids'}</div></div>
      </div>
      {decided === 0 && <div className="card empty">Mark a bid <strong>Won</strong> or <strong>Lost</strong> and add the prices on its page. Your record builds up here, and shows on similar tenders.</div>}
      <GroupTable title="By work type" rows={data.by_work_type} />
      <GroupTable title="By department" rows={data.by_department} />
      <GroupTable title="By size" rows={data.by_size} />
    </>
  );
}

// ---- one bid -----------------------------------------------------------------------------------------------------

function DateField({ label, value, time, onSave }: { label: string; value: string | null; time?: boolean; onSave: (v: string | null) => void }) {
  const shown = value ? value.slice(0, time ? 16 : 10) : '';
  const [v, setV] = useState(shown);
  useEffect(() => setV(shown), [shown]);
  const commit = (x: string) => { const out = x ? (time ? `${x}:00` : x) : null; if ((out ?? '') !== (value ?? '') && (out || value)) onSave(out); };
  return (
    <label className="field-inline">{label}
      <input type={time ? 'datetime-local' : 'date'} value={v} onChange={(e) => setV(e.target.value)} onBlur={() => commit(v)} onKeyDown={(e) => { if (e.key === 'Enter') commit(v); }} />
    </label>
  );
}

function MoneyField({ label, value, onSave }: { label: string; value: number | null; onSave: (v: number | null) => void }) {
  const [v, setV] = useState(value === null ? '' : String(value));
  useEffect(() => setV(value === null ? '' : String(value)), [value]);
  return <label className="field-inline">{label}<input inputMode="decimal" value={v} placeholder="₹" onChange={(e) => setV(e.target.value)} onBlur={() => { const n = num(v); if (n !== value) onSave(n); }} /></label>;
}

const MATCH: Record<MatchStatus, [string, string]> = { ok: ['ok', 'In vault'], expired: ['bad', 'Expired'], expires_before_close: ['warn', 'Expires before closing'], missing: ['warn', 'Not in vault'], unknown: ['plain', ''] };

function Workspace({ id, config, onBack }: { id: number; config: AppConfig; onBack: () => void }) {
  const toast = useToast();
  const ai = useAiStatus();
  const [bid, setBid] = useState<BidDetail | null>(null);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const [tabSel, setTabSel] = useState<WsTab | null>(null);
  const vault = useLoad(() => api.vault(), []);
  const load = () => api.bid(id).then(setBid, (e: Error) => setError(e.message));
  useEffect(() => { load(); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function save(patch: Record<string, unknown>) {
    try { setBid(await api.patchBid(id, patch)); setSaved(true); setTimeout(() => setSaved(false), 2000); } catch (e) { toast((e as Error).message, 'bad'); }
  }
  async function remove() {
    if (!window.confirm('Remove this bid and its checklist? The tender itself stays in the list.')) return;
    try { await api.deleteBid(id); onBack(); } catch (e) { toast((e as Error).message, 'bad'); }
  }
  async function seen() { await api.changesSeen(id).catch(() => undefined); load(); }
  async function copyId() { try { await navigator.clipboard.writeText(bid!.tender_id); toast('Tender ID copied'); } catch { toast('Could not copy', 'bad'); } }

  if (error) return <><button className="link-btn" onClick={onBack}>← All bids</button><div className="alert bad">{error}</div></>;
  if (!bid) return <><Skeleton h={28} style={{ marginBottom: 12 }} /><Skeleton h={160} /></>;

  const rel = relative(bid.closing, config.now);
  const open = ACTIVE.includes(bid.stage);
  const unseen = bid.changes.filter((c) => !c.seen);
  const tab = tabSel ?? defaultTab(bid.stage);
  const docsOpen = bid.items.filter((i) => i.kind === 'document' && !i.done).length;
  const docProblems = bid.items.filter((i) => i.kind === 'document' && !i.done && i.match && ['expired', 'expires_before_close', 'missing'].includes(i.match.status)).length;
  const changeStage = (s: string) => {
    if (s === 'submitted' && bid.stage !== 'submitted' && docsOpen > 0 && !window.confirm(`${plural(docsOpen, 'document')} on the checklist ${docsOpen === 1 ? 'is' : 'are'} not ticked yet. Mark this bid as submitted anyway?`)) return;
    save({ stage: s });
  };
  const stages: { key: Stage; label: string }[] = [['interested', 'Interested'], ['preparing', 'Preparing'], ['submitted', 'Submitted'], ['opened', 'Bid opened'], ['won', 'Won'], ['lost', 'Lost'], ['dropped', 'Dropped']].map(([key, label]) => ({ key: key as Stage, label }));

  return (
    <>
      <button className="link-btn" onClick={onBack}>← All bids</button>
      <div className="page-head" style={{ marginTop: 6 }}>
        <div style={{ minWidth: 0 }}>
          <h1 style={{ fontSize: 18 }}>{headline(bid)}</h1>
          <p className="sub">{chain(bid.organisation).join(' › ')} · <span className="mono">{bid.tender_id}</span> · {bid.portal_name}</p>
        </div>
        <div className="head-actions">
          {saved && <span className="muted small" role="status">Saved ✓</span>}
          <button className="btn" onClick={copyId}>Copy Tender ID</button>
          {bid.manual && <button className="btn" onClick={() => window.dispatchEvent(new CustomEvent('add-tender', { detail: { editKey: bid.key } }))}>Edit tender details</button>}
          {bid.search_url && <a className="btn" href={bid.search_url} target="_blank" rel="noopener noreferrer">Find on portal</a>}
        </div>
      </div>

      {unseen.length > 0 && (
        <div className="alert bad">
          <strong>Changed on the portal since you started</strong>
          <ul style={{ margin: '6px 0 6px 18px', padding: 0 }}>
            {unseen.map((c) => (
              <li key={c.id}>{CHANGE_TEXT[c.what] ?? c.what}: {c.what === 'closing' || c.what === 'opening' ? <>{fmtDate(c.old, true)} → <strong>{fmtDate(c.new, true)}</strong></> : c.what === 'prebid' ? fmtDate(c.new, true) : c.new}</li>
            ))}
          </ul>
          <button className="btn small" onClick={seen}>I have checked these</button>
        </div>
      )}

      <ReadOnly>
      <NextStep bid={bid} />

      <section className="card" style={{ marginBottom: 14 }}>
        <div className="ws-grid">
          <label className="field-inline">Stage
            <select value={bid.stage} onChange={(e) => changeStage(e.target.value)}>{stages.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}</select>
          </label>
          <OwnerField value={bid.owner ?? ''} onSave={(v) => save({ owner: v })} />
          <div className="field-inline">Value<span className="static">{bid.value_text}</span></div>
          <div className="field-inline">EMD<span className="static">{bid.emd ? inr(bid.emd, false) : '-'}</span></div>
        </div>
        {open && rel.text && <div className={`when-banner ${rel.tone}`} style={{ marginTop: 12 }}>{rel.text}</div>}
        <h3 style={{ margin: '14px 0 6px' }}>Dates</h3>
        <div className="ws-grid">
          <DateField label="Bid closes" value={bid.closing} time onSave={(v) => save({ closing: v })} />
          <DateField label="Bids open" value={bid.opening} time onSave={(v) => save({ opening: v })} />
          <DateField label="Pre-bid meeting" value={bid.prebid_date} time onSave={(v) => save({ prebid_date: v })} />
          <DateField label="Last date for queries" value={bid.query_deadline} onSave={(v) => save({ query_deadline: v })} />
          <DateField label="EMD due" value={bid.emd_due} onSave={(v) => save({ emd_due: v })} />
          {bid.manual && <DateField label="Last checked on the portal" value={bid.portal_checked ?? null} onSave={(v) => save({ portal_checked: v })} />}
        </div>
        {bid.manual
          ? <p className="muted small" style={{ marginTop: 8 }}>This tender was added by hand, so the app cannot watch it for corrigenda or moved dates. Look at the portal yourself and record the day here: the bid reminds you in the last days before closing.{' '}
              <button className="link-btn" onClick={() => save({ portal_checked: new Date().toISOString().slice(0, 10) })}>Checked today</button></p>
          : <p className="muted small" style={{ marginTop: 8 }}>Each run looks at this tender again and records new corrigenda and moved dates. The daily email repeats the ones coming up.</p>}
        {bid.history.length > 0 && <div className="alert warn" style={{ marginTop: 10, marginBottom: 0 }}>{bid.history.map((h) => <div key={h}>{h}</div>)}</div>}
      </section>

      <div className="ws-tabs" role="tablist" aria-label="Bid sections" onKeyDown={(e) => {
        if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
        const order: WsTab[] = ['decide', 'checklist', 'price', 'letters', 'result'];
        const next = order[(order.indexOf(tab) + (e.key === 'ArrowRight' ? 1 : order.length - 1)) % order.length];
        setTabSel(next); e.preventDefault();
        setTimeout(() => (e.currentTarget.querySelector('[aria-selected="true"]') as HTMLElement | null)?.focus(), 0);
      }}>
        {([['decide', 'Decide'], ['checklist', 'Checklist'], ['price', 'Price'], ['letters', 'Letters'], ['result', 'Result & notes']] as [WsTab, string][]).map(([k, l]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} tabIndex={tab === k ? 0 : -1} className={tab === k ? 'on' : ''} onClick={() => setTabSel(k)}>
            {l}{k === 'checklist' && docProblems > 0 && <span className="count-pill alert-pill" title="Required papers not ready">{docProblems}</span>}
          </button>
        ))}
      </div>

      {tab === 'checklist' && <Checklist bid={bid} vault={vault.data?.documents ?? []} aiOn={!!ai?.configured} onChange={load} />}
      {tab === 'price' && <><BoqCard bidId={bid.id} /><RiskCard bidId={bid.id} aiOn={!!ai?.configured} hasSource={!!bid.has_source} /><PriceAdvisor bidId={bid.id} onUse={(p) => save({ my_value: p })} /></>}
      {tab === 'decide' && (
        <>
          <EligibilityCard bid={bid} aiOn={!!ai?.configured} onChange={(patch) => setBid({ ...bid, ...patch })} />
          {bid.brief && <section className="card" style={{ marginBottom: 14 }}><h2>Bid / no-bid brief</h2><BriefView b={bid.brief} /></section>}
          {!ai?.configured && !bid.eligibility && !bid.brief && <div className="card empty">Eligibility checks and bid / no-bid briefs need the AI assistant. Until it is on, use the checklist to record the conditions yourself.</div>}
        </>
      )}
      {tab === 'letters' && <>
        <Drafts bid={bid} aiOn={!!ai?.configured} onChange={(patch) => setBid({ ...bid, ...patch })} />
        {!ai?.configured && !bid.drafts && <div className="card empty">Draft letters need the AI assistant.</div>}
      </>}

      {tab === 'result' && (['submitted', 'opened', 'won', 'lost'] as Stage[]).includes(bid.stage) && (
        <section id="sec-result" className="card" style={{ marginBottom: 14 }}>
          <h2>Result</h2>
          <p className="sub">Recording prices and the outcome builds your record, which shows on similar tenders.</p>
          <div className="ws-grid">
            <MoneyField label="Your quoted price (₹)" value={bid.my_value} onSave={(v) => save({ my_value: v })} />
            <MoneyField label="Winning (L1) price (₹)" value={bid.l1_value} onSave={(v) => save({ l1_value: v })} />
            <TextField label="Winner" value={bid.l1_bidder ?? ''} max={120} onSave={(v) => save({ l1_bidder: v })} />
          </div>
          {bid.my_value && bid.l1_value ? <p className="muted small" style={{ marginTop: 8 }}>Your price was {Math.abs(((bid.my_value - bid.l1_value) / bid.l1_value) * 100).toFixed(1)}% {bid.my_value >= bid.l1_value ? 'above' : 'below'} the winning price.</p> : null}
          <label className="field" style={{ marginTop: 10 }}>What happened<textarea key={`r-${bid.result_note ?? ''}`} defaultValue={bid.result_note ?? ''} maxLength={1000} onBlur={(e) => e.target.value !== (bid.result_note ?? '') && save({ result_note: e.target.value })} /></label>
          <div className="d-actions">
            {bid.stage !== 'won' && <button className="btn" onClick={() => save({ stage: 'won' })}>Mark won</button>}
            {bid.stage !== 'lost' && <button className="btn" onClick={() => save({ stage: 'lost' })}>Mark lost</button>}
          </div>
        </section>
      )}

      {tab === 'result' && <section id="sec-notes" className="card" style={{ marginBottom: 14 }}>
        <label className="field">Notes<textarea key={`n-${bid.notes ?? ''}`} defaultValue={bid.notes ?? ''} maxLength={4000} placeholder="Site visit, rates, who to call…" onBlur={(e) => e.target.value !== (bid.notes ?? '') && save({ notes: e.target.value })} /></label>
        {bid.changes.length > 0 && (
          <details><summary className="muted small">Change history ({bid.changes.length})</summary>
            <ul style={{ margin: '6px 0 0 18px', padding: 0 }} className="small">{bid.changes.map((c) => <li key={c.id}>{fmtDate(c.at, true)} · {CHANGE_TEXT[c.what] ?? c.what}: {c.what === 'closing' || c.what === 'opening' ? `${fmtDate(c.old, true)} → ${fmtDate(c.new, true)}` : c.new}</li>)}</ul>
          </details>
        )}
      </section>}
      {tab === 'result' && !(['submitted', 'opened', 'won', 'lost'] as Stage[]).includes(bid.stage) && <p className="muted small">The result form appears once the bid is Submitted.</p>}
      <button className="link-btn" style={{ color: 'var(--bad)' }} onClick={remove}>Remove this bid</button>
      </ReadOnly>
    </>
  );
}

function NextStep({ bid }: { bid: BidDetail }) {
  const docsOpen = bid.items.filter((i) => i.kind === 'document' && !i.done).length;
  const problems = bid.items.filter((i) => i.kind === 'document' && !i.done && i.match && ['expired', 'expires_before_close', 'missing'].includes(i.match.status)).length;
  let text = '';
  if (bid.stage === 'interested') text = !bid.doc ? 'Read the tender document in the checklist, then run the eligibility check to decide whether to bid.' : !bid.eligibility ? 'Run the eligibility check, then move the bid to Preparing, or to Dropped if you will not bid.' : 'Decide: move the bid to Preparing, or to Dropped if you will not bid.';
  else if (bid.stage === 'preparing') text = problems > 0 ? `${plural(problems, 'required paper')} not ready in the vault. Fix them in the checklist before closing.` : docsOpen > 0 ? `${plural(docsOpen, 'document')} still to gather before you submit.` : 'Checklist is complete. File on the portal, then mark the bid Submitted.';
  else if (bid.stage === 'submitted') text = 'Record your quoted price in Result below, then wait for the bid opening.';
  else if (bid.stage === 'opened') text = 'Add the winning price and mark the bid Won or Lost.';
  if (!text) return null;
  return <div className="alert next-step"><strong>Next:</strong> {text}</div>;
}

function OwnerField({ value, onSave }: { value: string; onSave: (v: string) => void }) {
  return <TextField label="Owner" value={value} max={60} placeholder="Who is preparing it" onSave={onSave} />;
}

function TextField({ label, value, max, placeholder, onSave }: { label: string; value: string; max: number; placeholder?: string; onSave: (v: string) => void }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return <label className="field-inline">{label}<input value={v} maxLength={max} placeholder={placeholder} onChange={(e) => setV(e.target.value)} onBlur={() => v.trim() !== value && onSave(v.trim())} /></label>;
}

// ---- checklist ---------------------------------------------------------------------------------------------------

function Checklist({ bid, vault, aiOn, onChange }: { bid: BidDetail; vault: VaultDoc[]; aiOn: boolean; onChange: () => void }) {
  const toast = useToast();
  const [items, setItems] = useState<BidItem[]>(bid.items);
  const [text, setText] = useState('');
  const [kind, setKind] = useState('task');
  const [showDoc, setShowDoc] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [paste, setPaste] = useState('');
  const [busy, setBusy] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [note, setNote] = useState('');
  useEffect(() => setItems(bid.items), [bid.items]);

  const done = items.filter((i) => i.done).length;
  const pct = items.length ? Math.round((100 * done) / items.length) : 0;
  const problems = items.filter((i) => i.kind === 'document' && !i.done && i.match && ['expired', 'expires_before_close', 'missing'].includes(i.match.status)).length;

  async function toggle(i: BidItem) {
    setItems((xs) => xs.map((x) => (x.id === i.id ? { ...x, done: !x.done } : x)));
    try { await api.patchItem(bid.id, i.id, { done: !i.done }); } catch (e) { toast((e as Error).message, 'bad'); onChange(); }
  }
  async function link(i: BidItem, v: string) {
    try { await api.patchItem(bid.id, i.id, { vault_id: v ? Number(v) : null }); onChange(); } catch (e) { toast((e as Error).message, 'bad'); }
  }
  async function remove(i: BidItem) {
    setItems((xs) => xs.filter((x) => x.id !== i.id));
    try { await api.deleteItem(bid.id, i.id); } catch (e) { toast((e as Error).message, 'bad'); onChange(); }
  }
  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    try { await api.addItem(bid.id, text.trim(), kind); setText(''); onChange(); } catch (err) { toast((err as Error).message, 'bad'); }
  }
  async function fromDoc() {
    try { const r = await api.generateItems(bid.id); toast(r.added ? `Added ${plural(r.added, 'item')}` : 'Nothing new to add'); onChange(); } catch (e) { toast((e as Error).message, 'bad'); }
  }
  async function fetchPortal() {
    setFetching(true); setNote('');
    try {
      const r = await api.fetchPortal(bid.id);
      const more = [r.boq, r.risks].filter((x) => x && x.state !== 'kept').map((x) => x?.message).filter(Boolean);
      setNote(`${r.message}${r.added ? ` Added ${plural(r.added, 'item')}.` : ''}${r.corrigenda.length ? ` Corrigenda on the portal: ${r.corrigenda.join('; ')}.` : ''}${more.length ? ` ${more.join(' ')}` : ''}${r.boq?.state === 'read' ? ' See the Price tab.' : ''}`);
      onChange();
    } catch (e) { setNote((e as Error).message); } finally { setFetching(false); }
  }
  async function readDoc() {
    setBusy(true);
    try {
      const r = await api.bidDocument(bid.id, bid.key, file ? '' : paste, file ? await readBase64(file) : '');
      toast(`Document read. Added ${plural(r.added, 'item')}`); setShowDoc(false); setFile(null); setPaste(''); onChange();
    } catch (e) { toast((e as Error).message, 'bad'); } finally { setBusy(false); }
  }

  const groups: [BidItem['kind'], string][] = [['document', 'Documents to submit'], ['condition', 'Conditions to meet'], ['task', 'Steps']];
  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <div className="card-head">
        <div><h2>Checklist</h2><p className="sub">{done} of {items.length} done{problems > 0 && <> · <span style={{ color: 'var(--warn)' }}>{plural(problems, 'paper')} need attention</span></>}</p></div>
        <div className="head-actions">
          {!bid.manual && <button className="btn small primary" onClick={fetchPortal} disabled={fetching}>{fetching ? 'Fetching…' : 'Fetch from portal'}</button>}
          <button className="btn small" onClick={fromDoc}>Add standard steps</button>
          {aiOn && <button className="btn small" onClick={() => setShowDoc((v) => !v)} aria-expanded={showDoc}>{bid.doc ? 'Read another document' : 'Read tender document'}</button>}
        </div>
      </div>
      <div className="progress" style={{ marginBottom: 12 }} aria-hidden><span style={{ width: `${pct}%` }} /></div>
      {note && <div className="alert warn">{note}</div>}
      {bid.portal_facts && bid.portal_facts.length > 0 && (
        <details open style={{ marginBottom: 10 }}><summary className="muted small">From the portal's tender page</summary>
          <dl className="facts">{bid.portal_facts.map((f) => <Fragment key={f.label}><dt>{f.label}</dt><dd>{f.value}</dd></Fragment>)}</dl>
        </details>
      )}
      {!bid.doc && <p className="muted small">Fetch from portal reads the fees, class and dates, and the tender notice when the portal gives it without a login. Otherwise: the document list and conditions come from the tender document. {aiOn ? 'Press Read tender document and upload the NIT.' : 'Turn on the AI assistant to read it, or add items by hand.'}</p>}
      {showDoc && (
        <div className="ai-doc">
          <label className="field">Upload the tender PDF (up to 10 MB)<input type="file" accept="application/pdf,.pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
          <label className="field">Or paste the text<textarea value={paste} onChange={(e) => setPaste(e.target.value)} disabled={!!file} spellCheck={false} /></label>
          <button className="btn primary" onClick={readDoc} disabled={busy || (!file && !paste.trim())}>{busy ? 'Reading document…' : 'Read and add to checklist'}</button>
        </div>
      )}
      {groups.map(([k, title]) => {
        const rows = items.filter((i) => i.kind === k);
        if (!rows.length) return null;
        return (
          <Fragment key={k}>
            <h3 style={{ margin: '14px 0 4px' }}>{title}</h3>
            <ul className="checklist">
              {rows.map((i) => {
                const [tone, label] = i.match ? MATCH[i.match.status] : ['plain', ''];
                const docs = vault;
                return (
                  <li key={i.id} className={i.done ? 'done' : ''}>
                    <label className="check"><input type="checkbox" checked={i.done} onChange={() => toggle(i)} /><span>{i.text}</span></label>
                    <div className="check-tools">
                      {i.match && label && <span className={`badge ${tone}`} title={i.match.name}>{i.match.status === 'ok' ? `In vault: ${i.match.name}` : label}</span>}
                      {i.kind === 'document' && docs.length > 0 && (
                        <select className="mini-select" value={i.vault_id ?? ''} aria-label="Paper from the vault" onChange={(e) => link(i, e.target.value)}>
                          <option value="">{i.match && i.match.status !== 'unknown' && i.match.status !== 'missing' ? 'Automatic' : 'Pick from vault'}</option>
                          {docs.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                        </select>
                      )}
                      <button type="button" className="link-btn" aria-label="Remove item" onClick={() => remove(i)}>✕</button>
                    </div>
                  </li>
                );
              })}
            </ul>
          </Fragment>
        );
      })}
      <form className="add-item" onSubmit={add}>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Add an item" maxLength={300} aria-label="New checklist item" />
        <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind"><option value="task">Step</option><option value="document">Document</option><option value="condition">Condition</option></select>
        <button className="btn" disabled={!text.trim()}>Add</button>
      </form>
    </section>
  );
}

// ---- eligibility -------------------------------------------------------------------------------------------------

function EligibilityCard({ bid, aiOn, onChange }: { bid: BidDetail; aiOn: boolean; onChange: (p: Partial<BidDetail>) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const e = bid.eligibility;
  async function run() {
    setBusy(true); setError('');
    try { onChange({ eligibility: await api.eligibility(bid.id) }); } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  if (!aiOn && !e) return null;
  const tone = e?.verdict === 'Eligible' || e?.verdict === 'Likely eligible' ? 'ok' : e?.verdict === 'Gaps' ? 'bad' : 'warn';
  const icon = { met: '✓', gap: '✗', unclear: '?' } as const;
  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <div className="card-head">
        <div><h2>Eligibility check</h2><p className="sub">Compares the tender's conditions with your company profile, your facts and the vault.</p></div>
        {aiOn && <button className="btn small" onClick={run} disabled={busy || !bid.doc}>{busy ? 'Checking…' : e ? 'Check again' : 'Check eligibility'}</button>}
      </div>
      {!bid.doc && <p className="muted small">Read the tender document first: the conditions come from it.</p>}
      {error && <div className="alert warn">{error}</div>}
      {e && (
        <>
          <div className="brief-head"><span className={`badge ${tone}`}>{e.verdict}</span><strong>{e.summary}</strong></div>
          <ul className="elig">
            {e.checks.map((c, i) => (
              <li key={i} className={c.status}><span className="elig-icon" aria-label={c.status}>{icon[c.status]}</span>
                <div><div>{c.requirement}</div>{c.evidence && <div className="muted small">{c.evidence}</div>}{c.action && <div className="small"><strong>To do:</strong> {c.action}</div>}</div></li>
            ))}
          </ul>
          <p className="muted small">AI can be wrong. The portal's own document is the final word. {e.checked && `Checked ${fmtDate(e.checked, true)}.`} Add your turnover and registration facts in the Vault for a better answer.</p>
        </>
      )}
    </section>
  );
}

// ---- drafts ------------------------------------------------------------------------------------------------------

const DRAFTS: [string, string][] = [['cover_letter', 'Covering letter'], ['declaration', 'Declaration / undertaking'], ['prebid_query', 'Pre-bid query letter'], ['custom', 'Something else']];

function Drafts({ bid, aiOn, onChange }: { bid: BidDetail; aiOn: boolean; onChange: (p: Partial<BidDetail>) => void }) {
  const toast = useToast();
  const [kind, setKind] = useState('cover_letter');
  const [ask, setAsk] = useState('');
  const [text, setText] = useState(bid.drafts?.cover_letter ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { setText(kind === 'custom' ? '' : bid.drafts?.[kind] ?? ''); setError(''); }, [kind]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!aiOn && !bid.drafts) return null;

  async function make() {
    setBusy(true); setError('');
    try {
      const r = await api.draft(bid.id, kind, ask, !!text);
      setText(r.text);
      if (kind !== 'custom') onChange({ drafts: { ...(bid.drafts ?? {}), [kind]: r.text } });
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function copy() { try { await navigator.clipboard.writeText(text); toast('Copied'); } catch { toast('Could not copy', 'bad'); } }
  function download() {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url; a.download = `${bid.tender_id}-${kind}.txt`; a.click();
    URL.revokeObjectURL(url);
  }
  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <h2>Draft letters</h2>
      <p className="sub">Written from your company profile and facts. Anything unknown is left in [brackets] for you to fill in. Check it against the tender's own format before signing.</p>
      <div className="seg" role="group" aria-label="Kind of draft" style={{ marginBottom: 10, flexWrap: 'wrap' }}>
        {DRAFTS.map(([k, l]) => <button key={k} type="button" className={kind === k ? 'on' : ''} aria-pressed={kind === k} onClick={() => setKind(k)}>{l}</button>)}
      </div>
      <label className="field">{kind === 'custom' ? 'What should it say?' : 'Anything to include? (optional)'}
        <textarea value={ask} onChange={(e) => setAsk(e.target.value)} maxLength={800} placeholder={kind === 'prebid_query' ? 'e.g. Ask whether the 5% retention can be replaced by a bank guarantee' : kind === 'custom' ? 'e.g. A request to extend bid validity by 30 days' : ''} /></label>
      {aiOn && <button className="btn primary" onClick={make} disabled={busy || (kind === 'custom' && !ask.trim())}>{busy ? 'Writing…' : text ? 'Write again' : 'Write draft'}</button>}
      {error && <div className="alert warn" style={{ marginTop: 10 }}>{error}</div>}
      {text && (
        <>
          <textarea className="draft-box" value={text} onChange={(e) => setText(e.target.value)} spellCheck />
          <div className="d-actions"><button className="btn" onClick={copy}>Copy</button><button className="btn" onClick={download}>Download .txt</button></div>
        </>
      )}
    </section>
  );
}
