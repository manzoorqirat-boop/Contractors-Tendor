import { ScrollX } from '../ScrollX';
import { Fragment, useEffect, useState } from 'react';
import { api } from '../api';
import { BriefView, readBase64, useAiStatus } from '../ai';
import { BoqCard, RiskCard } from '../Boq';
import { PriceAdvisor } from '../PriceAdvisor';
import { chain, dateSay, fmtDate, headline, inr, parseMoney, plural, relative } from '../format';
import { useLoad, useServerNow } from '../hooks';
import { BidProgress } from '../BidProgress';
import { AssigneeSelect, MyWork, TeamTab } from '../Team';
import { BiddersCard, CompetitorsCard } from '../Bidders';
import { PriceHistoryCard } from '../PriceHistory';
import { DocumentEvidence } from '../Clauses';
import { UseTemplate } from '../LetterTemplates';
import { CorrigendumForm } from '../Corrigenda';
import { useKeepActiveVisible } from '../Tabs';
import { Skeleton, useToast } from '../ui';
import { Only, ReadOnly, useSession } from '../session';
import { MoneyTab } from '../Money';
import { useConfirm } from '../confirm';
import type { AppConfig, BidAlert, Clause, BidDetail, BidItem, BidStats, BidSummary, MatchStatus, Stage, StatGroup, VaultDoc } from '../types';
import { tr } from '../i18n';

type WsTab = 'decide' | 'checklist' | 'price' | 'money' | 'letters' | 'result' | 'team';
const defaultTab = (s: Stage): WsTab => (s === 'preparing' ? 'checklist' : ['submitted', 'opened', 'won', 'lost'].includes(s) ? 'result' : 'decide');

const ACTIVE: Stage[] = ['interested', 'preparing', 'submitted', 'opened'];
const CHANGE_TEXT: Record<string, string> = { closing: tr('Closing date moved'), opening: tr('Bid opening date moved'), corrigendum: tr('Corrigendum'), prebid: tr('Pre-bid meeting date') };

const whenText = (a: BidAlert) => (a.urgency === 'overdue' ? 'overdue' : a.days_left === 0 ? 'today' : a.days_left === 1 ? 'tomorrow' : `in ${a.days_left} days`);
const num = parseMoney;

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
        <div><h1>{tr('Bids')}</h1><p className="sub">{data ? `${plural(activeCount, 'bid')} in progress` : tr('Loading…')}</p></div>
        <div className="head-actions">
        <Only role="bidder"><button className="btn" onClick={() => window.dispatchEvent(new CustomEvent('add-tender'))}>{tr('Add a tender')}</button></Only>
        <div className="seg" role="group" aria-label={tr('Bids view')}>
          <button type="button" className={mode === 'pipeline' ? 'on' : ''} aria-pressed={mode === 'pipeline'} onClick={() => setMode('pipeline')}>{tr('Pipeline')}</button>
          <button type="button" className={mode === 'results' ? 'on' : ''} aria-pressed={mode === 'results'} onClick={() => setMode('results')}>{tr('Results')}</button>
        </div>
        </div>
      </div>

      {mode === 'results' ? <Results /> : (
        <>
          <MyWork onOpen={onOpen} />
          {(alerts.data?.alerts.length ?? 0) > 0 && (
            <section className="card" style={{ marginBottom: 14 }}>
              <h2>{tr('Coming up')}</h2>
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

          <div className="chips-row" role="group" aria-label={tr('Show')}>
            <button type="button" className={`chip pick${view === 'active' ? ' on' : ''}`} onClick={() => setView('active')}>In progress{data && <span className="chip-n">{activeCount}</span>}</button>
            {data?.stages.map((s) => <button key={s.key} type="button" className={`chip pick${view === s.key ? ' on' : ''}`} onClick={() => setView(s.key)}>{tr(s.label)}<span className="chip-n">{data.counts[s.key]}</span></button>)}
            <button type="button" className={`chip pick${view === 'all' ? ' on' : ''}`} onClick={() => setView('all')}>{tr('All')}</button>
          </div>

          {list.error && <div className="alert bad">{list.error} <button className="link-btn" onClick={list.reload}>{tr('Try again')}</button></div>}
          {list.loading && !data && <Skeleton h={120} />}
          {data && shown.length === 0 && (
            <div className="card empty">{data.bids.length === 0 ? <>No bids yet. Open a tender on the Tenders page and press <strong>{tr('Start a bid')}</strong>.</> : tr('Nothing at this stage.')}</div>
          )}
          <div className="bid-list">
            {shown.map((b) => {
              const rel = relative(b.closing, config.now);
              const pct = b.items_total ? Math.round((100 * b.items_done) / b.items_total) : 0;
              return (
                <article key={b.id} className={`card bidcard urg-${ACTIVE.includes(b.stage) ? rel.tone || 'none' : 'none'}`} onClick={() => onOpen(b.id)}>
                  <div className="bidcard-top">
                    <h3 className="t-title"><button type="button" className="linkish" onClick={(e) => { e.stopPropagation(); onOpen(b.id); }}>{headline(b)}</button></h3>
                    <select className="stage-select" value={b.stage} aria-label={tr('Stage')} onClick={(e) => e.stopPropagation()} onChange={(e) => move(b, e.target.value as Stage)}>
                      {data!.stages.map((s) => <option key={s.key} value={s.key}>{tr(s.label)}</option>)}
                    </select>
                  </div>
                  <p className="muted small">{chain(b.organisation).join(' › ')} · <span className="mono">{b.tender_id}</span> · {b.portal_name}</p>
                  <div className="chips" style={{ marginTop: 6 }}>
                    {ACTIVE.includes(b.stage) && rel.text && <span className={`badge ${rel.tone === 'urgent' ? 'bad' : rel.tone === 'soon' ? 'warn' : 'plain'}`}>{rel.text}</span>}
                    <span className="badge plain">{b.value_text}</span>
                    {b.unseen > 0 && <span className="badge bad">{b.unseen} change{b.unseen === 1 ? '' : 's'} to check</span>}
                    {b.owner && <span className="badge plain">{b.owner}</span>}
                    {!b.has_doc && ACTIVE.includes(b.stage) && <span className="badge warn">{tr('Document not read')}</span>}
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
      <ScrollX label={tr('Bids')}>
        <table className="tbl"><thead><tr><th>{title.replace('By ', '')}</th><th className="num">{tr('Won')}</th><th className="num">{tr('Lost')}</th><th className="num">{tr('Win rate')}</th><th className="num">{tr('Value won')}</th></tr></thead>
          <tbody>{rows.map((r) => <tr key={r.label}><td>{r.label}</td><td className="num">{r.won}</td><td className="num">{r.lost}</td><td className="num">{Math.round((100 * r.won) / (r.won + r.lost))}%</td><td className="num">{r.won_value ? inr(r.won_value) : '-'}</td></tr>)}</tbody></table>
      </ScrollX>
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
        <div className="kpi"><div className="label">{tr('Win rate')}</div><div className="num">{data.win_rate === null ? '-' : `${data.win_rate}%`}</div><div className="note">{decided ? `${data.won} won of ${decided} decided` : 'Record results to see this'}</div></div>
        <div className="kpi"><div className="label">{tr('Bids submitted')}</div><div className="num">{data.submitted}</div><div className="note">{data.in_progress} still in progress</div></div>
        <div className="kpi"><div className="label">{tr('Value won')}</div><div className="num">{data.won_value ? inr(data.won_value) : '-'}</div><div className="note">{data.dropped} dropped before bidding</div></div>
        <div className="kpi"><div className="label">{tr('Your price vs the winner')}</div><div className="num">{data.avg_gap_to_l1 === null ? '-' : `${data.avg_gap_to_l1 > 0 ? '+' : ''}${data.avg_gap_to_l1}%`}</div><div className="note">{data.gap_samples ? `average over ${plural(data.gap_samples, 'lost bid')}` : 'Add the winning price on lost bids'}</div></div>
      </div>
      {decided === 0 && <div className="card empty">Mark a bid <strong>{tr('Won')}</strong> or <strong>{tr('Lost')}</strong> and add the prices on its page. Your record builds up here, and shows on similar tenders.</div>}
      <PriceHistoryCard />
      <CompetitorsCard />
      <GroupTable title={tr('By work type')} rows={data.by_work_type} />
      <GroupTable title={tr('By department')} rows={data.by_department} />
      <GroupTable title={tr('By size')} rows={data.by_size} />
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
      {dateSay(v, !!time) && <span className="date-say" aria-hidden>{dateSay(v, !!time)}</span>}
    </label>
  );
}

function MoneyField({ label, value, onSave }: { label: string; value: number | null; onSave: (v: number | null) => void }) {
  const [v, setV] = useState(value === null ? '' : String(value));
  useEffect(() => setV(value === null ? '' : String(value)), [value]);
  const parsed = num(v);
  const bad = v.trim() !== '' && parsed === null;  // never save (or clear) a price the app could not understand
  return (
    <label className="field-inline">{label}
      <input inputMode="decimal" value={v} placeholder="₹" aria-invalid={bad || undefined} aria-describedby={bad ? `money-err-${label}` : undefined} onChange={(e) => setV(e.target.value)} onBlur={() => { if (!bad && parsed !== value) onSave(parsed); }} />
      {bad ? <span className="field-err" id={`money-err-${label}`}>Not an amount. Try 12500000, 125 lakh or 1.25 crore.</span> : parsed ? <span className="date-say">{inr(parsed)}</span> : null}
    </label>
  );
}

const MATCH: Record<MatchStatus, [string, string]> = { ok: ['ok', tr('In vault')], expired: ['bad', tr('Expired')], expires_before_close: ['warn', tr('Expires before closing')], missing: ['warn', tr('Not in vault')], unknown: ['plain', ''] };

function Workspace({ id, config, onBack }: { id: number; config: AppConfig; onBack: () => void }) {
  const toast = useToast();
  const confirm = useConfirm();
  const { can } = useSession();
  const ai = useAiStatus();
  const nowIso = useServerNow(config.now);  // a hook: it must run on every render, before any early return
  const [bid, setBid] = useState<BidDetail | null>(null);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const [tabSel, setTabSel] = useState<WsTab | null>(null);
  const tabsRef = useKeepActiveVisible(tabSel);  // a hook, so it stays above the early returns below
  const vault = useLoad(() => api.vault(), []);
  const load = () => api.bid(id).then(setBid, (e: Error) => setError(e.message));
  useEffect(() => { load(); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function save(patch: Record<string, unknown>) {
    try { setBid(await api.patchBid(id, patch)); setSaved(true); setTimeout(() => setSaved(false), 2000); } catch (e) { toast((e as Error).message, 'bad'); }
  }
  async function remove() {
    if (!(await confirm({ title: tr('Remove this bid?'), body: 'Its checklist, notes, drafts and results are deleted and this cannot be undone. The tender itself stays in the list.', confirmLabel: tr('Remove bid'), danger: true }))) return;
    try { await api.deleteBid(id); onBack(); } catch (e) { toast((e as Error).message, 'bad'); }
  }
  async function seen() { await api.changesSeen(id).catch(() => undefined); load(); }
  async function copyId() { try { await navigator.clipboard.writeText(bid!.tender_id); toast(tr('Tender ID copied')); } catch { toast(tr('Could not copy'), 'bad'); } }

  if (error) return <><button className="link-btn" onClick={onBack}>{tr('← All bids')}</button><div className="alert bad">{error}</div></>;
  if (!bid) return <><Skeleton h={28} style={{ marginBottom: 12 }} /><Skeleton h={160} /></>;

  const rel = relative(bid.closing, nowIso);
  const open = ACTIVE.includes(bid.stage);
  const unseen = bid.changes.filter((c) => !c.seen);
  const tab = tabSel ?? defaultTab(bid.stage);
  const docsOpen = bid.items.filter((i) => i.kind === 'document' && !i.done).length;
  const docProblems = bid.items.filter((i) => i.kind === 'document' && !i.done && i.match && ['expired', 'expires_before_close', 'missing'].includes(i.match.status)).length;
  const changeStage = async (s: string) => {
    if (s === 'submitted' && bid.stage !== 'submitted' && docsOpen > 0 && !(await confirm({ title: tr('Mark as submitted anyway?'), body: `${plural(docsOpen, 'document')} on the checklist ${docsOpen === 1 ? 'is' : 'are'} not ticked yet.`, confirmLabel: tr('Mark as submitted') }))) return;
    save({ stage: s });
  };
  const stages: { key: Stage; label: string }[] = [['interested', tr('Interested')], ['preparing', tr('Preparing')], ['submitted', tr('Submitted')], ['opened', tr('Bid opened')], ['won', tr('Won')], ['lost', tr('Lost')], ['dropped', tr('Dropped')]].map(([key, label]) => ({ key: key as Stage, label }));

  return (
    <>
      <button className="link-btn" onClick={onBack}>{tr('← All bids')}</button>
      <div className="page-head" style={{ marginTop: 6 }}>
        <div style={{ minWidth: 0 }}>
          <h1 style={{ fontSize: 18 }}>{headline(bid)}</h1>
          <p className="sub">{chain(bid.organisation).join(' › ')} · <span className="mono">{bid.tender_id}</span> · {bid.portal_name}</p>
        </div>
        <div className="head-actions">
          {saved && <span className="muted small" role="status">{tr('Saved ✓')}</span>}
          <button className="btn" onClick={copyId}>{tr('Copy Tender ID')}</button>
          {bid.manual && <button className="btn" onClick={() => window.dispatchEvent(new CustomEvent('add-tender', { detail: { editKey: bid.key } }))}>Edit tender details</button>}
          {bid.search_url && <a className="btn" href={bid.search_url} target="_blank" rel="noopener noreferrer">{tr('Find on portal')}</a>}
        </div>
      </div>

      {unseen.length > 0 && (
        <div className="alert bad">
          <strong>{tr('Changed on the portal since you started')}</strong>
          <ul style={{ margin: '6px 0 6px 18px', padding: 0 }}>
            {unseen.map((c) => (
              <li key={c.id}>{CHANGE_TEXT[c.what] ?? c.what}: {c.what === 'closing' || c.what === 'opening' ? <>{fmtDate(c.old, true)} → <strong>{fmtDate(c.new, true)}</strong></> : c.what === 'prebid' ? fmtDate(c.new, true) : c.new}</li>
            ))}
          </ul>
          <button className="btn small" onClick={seen}>{tr('I have checked these')}</button>
        </div>
      )}

      <ReadOnly>
      <BidProgress bid={bid} aiOn={!!ai?.configured} tab={tab} onGo={setTabSel} onChange={(patch) => setBid({ ...bid, ...patch })} />
      <NextStep bid={bid} />

      <section className="card" style={{ marginBottom: 14 }}>
        <div className="ws-grid">
          <label className="field-inline">{tr('Stage')}<select value={bid.stage} onChange={(e) => changeStage(e.target.value)}>{stages.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}</select>
          </label>
          <OwnerField value={bid.owner ?? ''} onSave={(v) => save({ owner: v })} />
          <div className="field-inline">{tr('Value')}<span className="static">{bid.value_text}</span></div>
          <div className="field-inline">{tr('EMD')}<span className="static">{bid.emd ? inr(bid.emd, false) : '-'}</span></div>
        </div>
        {open && rel.text && <div className={`when-banner ${rel.tone}`} style={{ marginTop: 12 }}>{rel.text}</div>}
        <h2 className="h3-like" style={{ margin: '14px 0 6px' }}>{tr('Dates')}</h2>
        <div className="ws-grid">
          <DateField label={tr('Bid closes')} value={bid.closing} time onSave={(v) => save({ closing: v })} />
          <DateField label={tr('Bids open')} value={bid.opening} time onSave={(v) => save({ opening: v })} />
          <DateField label={tr('Pre-bid meeting')} value={bid.prebid_date} time onSave={(v) => save({ prebid_date: v })} />
          <DateField label={tr('Last date for queries')} value={bid.query_deadline} onSave={(v) => save({ query_deadline: v })} />
          <DateField label={tr('EMD due')} value={bid.emd_due} onSave={(v) => save({ emd_due: v })} />
          {bid.manual && <DateField label={tr('Last checked on the portal')} value={bid.portal_checked ?? null} onSave={(v) => save({ portal_checked: v })} />}
        </div>
        {bid.manual
          ? <p className="muted small" style={{ marginTop: 8 }}>This tender was added by hand, so the app cannot watch it for corrigenda or moved dates. Look at the portal yourself and record the day here: the bid reminds you in the last days before closing.{' '}
              <button className="link-btn" onClick={() => save({ portal_checked: new Date().toISOString().slice(0, 10) })}>{tr('Checked today')}</button></p>
          : <p className="muted small" style={{ marginTop: 8 }}>Each run looks at this tender again and records new corrigenda and moved dates. The daily email repeats the ones coming up.</p>}
        <Only role="bidder"><CorrigendumForm bidId={bid.id} manual={!!bid.manual} onRecorded={(b) => setBid({ ...bid, ...b })} /></Only>
        {bid.history.length > 0 && <div className="alert warn" style={{ marginTop: 10, marginBottom: 0 }}>{bid.history.map((h) => <div key={h}>{h}</div>)}</div>}
      </section>

      <div className="ws-tabs" role="tablist" aria-label="Bid sections" ref={tabsRef} onKeyDown={(e) => {
        if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
        const order: WsTab[] = can('bidder') ? ['decide', 'checklist', 'price', 'money', 'letters', 'result', 'team'] : ['decide', 'checklist', 'price', 'letters', 'result', 'team'];
        const next = order[(order.indexOf(tab) + (e.key === 'ArrowRight' ? 1 : order.length - 1)) % order.length];
        setTabSel(next); e.preventDefault();
        setTimeout(() => (e.currentTarget.querySelector('[aria-selected="true"]') as HTMLElement | null)?.focus(), 0);
      }}>
        {([['decide', tr('Decide')], ['checklist', tr('Checklist')], ['price', tr('Price')], ...(can('bidder') ? [['money', tr('Money')]] : []), ['letters', tr('Letters')], ['result', tr('Result & notes')], ['team', tr('Team')]] as [WsTab, string][]).map(([k, l]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} tabIndex={tab === k ? 0 : -1} className={tab === k ? 'on' : ''} onClick={() => setTabSel(k)}>
            {l}{k === 'checklist' && docProblems > 0 && <span className="count-pill alert-pill" title={tr('Required papers not ready')}>{docProblems}</span>}{k === 'team' && (bid.comment_count ?? 0) > 0 && <span className="count-pill" title={tr('Comments')}>{bid.comment_count}</span>}{k === 'team' && bid.signoff?.state === 'requested' && <span className="count-pill alert-pill" title={tr('Waiting for approval')}>!</span>}
          </button>
        ))}
      </div>

      {tab === 'checklist' && <Checklist bid={bid} vault={vault.data?.documents ?? []} aiOn={!!ai?.configured} onChange={load} />}
      {tab === 'price' && <><BoqCard bidId={bid.id} /><RiskCard bidId={bid.id} aiOn={!!ai?.configured} hasSource={!!bid.has_source} /><PriceAdvisor bidId={bid.id} onUse={(p) => save({ my_value: p })} /></>}
      {tab === 'money' && can('bidder') && <MoneyTab bidId={bid.id} />}
      {tab === 'decide' && (
        <>
          <EligibilityCard bid={bid} aiOn={!!ai?.configured} onChange={(patch) => setBid({ ...bid, ...patch })} />
          {bid.brief && <section className="card" style={{ marginBottom: 14 }}><h2>{tr('Bid / no-bid brief')}</h2><BriefView b={bid.brief} /></section>}
          {!ai?.configured && !bid.eligibility && !bid.brief && <div className="card empty">Eligibility checks and bid / no-bid briefs need the AI assistant. Until it is on, use the checklist to record the conditions yourself.</div>}
        </>
      )}
      {tab === 'team' && <TeamTab bid={bid} config={config} onChange={(patch) => setBid({ ...bid, ...patch })} />}
      {tab === 'letters' && <>
        <UseTemplate bidId={bid.id} tenderId={bid.tender_id ?? ''} />
        <Drafts bid={bid} aiOn={!!ai?.configured} onChange={(patch) => setBid({ ...bid, ...patch })} />
        {!ai?.configured && !bid.drafts && <div className="card empty">AI-written drafts need the AI assistant. The templates above work without it.</div>}
      </>}

      {tab === 'result' && (['submitted', 'opened', 'won', 'lost'] as Stage[]).includes(bid.stage) && (
        <section id="sec-result" className="card" style={{ marginBottom: 14 }}>
          <h2>{tr('Result')}</h2>
          <p className="sub">Recording prices and the outcome builds your record, which shows on similar tenders.</p>
          <div className="ws-grid">
            <MoneyField label={tr('Your quoted price (₹)')} value={bid.my_value} onSave={(v) => save({ my_value: v })} />
            <MoneyField label={tr('Winning (L1) price (₹)')} value={bid.l1_value} onSave={(v) => save({ l1_value: v })} />
            <TextField label={tr('Winner')} value={bid.l1_bidder ?? ''} max={120} onSave={(v) => save({ l1_bidder: v })} />
          </div>
          {bid.my_value && bid.l1_value ? <p className="muted small" style={{ marginTop: 8 }}>Your price was {Math.abs(((bid.my_value - bid.l1_value) / bid.l1_value) * 100).toFixed(1)}% {bid.my_value >= bid.l1_value ? 'above' : 'below'} the winning price.</p> : null}
          <label className="field" style={{ marginTop: 10 }}>{tr('What happened')}<textarea key={`r-${bid.result_note ?? ''}`} defaultValue={bid.result_note ?? ''} maxLength={1000} onBlur={(e) => e.target.value !== (bid.result_note ?? '') && save({ result_note: e.target.value })} /></label>
          <div className="d-actions">
            {bid.stage !== 'won' && <button className="btn" onClick={() => save({ stage: 'won' })}>{tr('Mark won')}</button>}
            {bid.stage !== 'lost' && <button className="btn" onClick={() => save({ stage: 'lost' })}>{tr('Mark lost')}</button>}
          </div>
        </section>
      )}

      {tab === 'result' && (['submitted', 'opened', 'won', 'lost'] as Stage[]).includes(bid.stage) && <BiddersCard bid={bid} aiOn={!!ai?.configured} onChange={(patch) => setBid({ ...bid, ...patch })} />}
      {tab === 'result' && <section id="sec-notes" className="card" style={{ marginBottom: 14 }}>
        <label className="field">{tr('Notes')}<textarea key={`n-${bid.notes ?? ''}`} defaultValue={bid.notes ?? ''} maxLength={4000} placeholder="Site visit, rates, who to call…" onBlur={(e) => e.target.value !== (bid.notes ?? '') && save({ notes: e.target.value })} /></label>
        {bid.changes.length > 0 && (
          <details><summary className="muted small">Change history ({bid.changes.length})</summary>
            <ul style={{ margin: '6px 0 0 18px', padding: 0 }} className="small">{bid.changes.map((c) => <li key={c.id}>{fmtDate(c.at, true)} · {CHANGE_TEXT[c.what] ?? c.what}: {c.what === 'closing' || c.what === 'opening' ? `${fmtDate(c.old, true)} → ${fmtDate(c.new, true)}` : c.new}</li>)}</ul>
          </details>
        )}
      </section>}
      {tab === 'result' && !(['submitted', 'opened', 'won', 'lost'] as Stage[]).includes(bid.stage) && <p className="muted small">The result form appears once the bid is Submitted.</p>}
      <button className="link-btn" style={{ color: 'var(--bad)' }} onClick={remove}>{tr('Remove this bid')}</button>
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
  return <div className="alert next-step"><strong>{tr('Next:')}</strong> {text}</div>;
}

function OwnerField({ value, onSave }: { value: string; onSave: (v: string) => void }) {
  return <label className="field-inline">{tr('Owner')}<AssigneeSelect value={value || null} label="Owner of this bid" className="" onChange={onSave} /></label>;
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
  const [found, setFound] = useState<Clause[] | null>(null);
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
  async function assign(i: BidItem, owner: string) {
    setItems((xs) => xs.map((x) => (x.id === i.id ? { ...x, owner: owner || null } : x)));
    try { await api.patchItem(bid.id, i.id, { owner }); } catch (e) { toast((e as Error).message, 'bad'); onChange(); }
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
  async function findClauses() {
    setBusy(true);
    try {
      const r = await api.findClauses(bid.id, file ? '' : paste, file ? await readBase64(file) : '');
      setFound(r.clauses);
      toast(r.clauses.length ? `Found ${plural(r.clauses.length, 'condition')}` : (r.note || 'No conditions found in that text'));
      onChange();
    } catch (e) { toast((e as Error).message, 'bad'); } finally { setBusy(false); }
  }
  async function readDoc() {
    setBusy(true);
    try {
      const r = await api.bidDocument(bid.id, bid.key, file ? '' : paste, file ? await readBase64(file) : '');
      toast(`Document read. Added ${plural(r.added, 'item')}`); setShowDoc(false); setFile(null); setPaste(''); onChange();
    } catch (e) { toast((e as Error).message, 'bad'); } finally { setBusy(false); }
  }

  const groups: [BidItem['kind'], string][] = [['document', tr('Documents to submit')], ['condition', tr('Conditions to meet')], ['task', tr('Steps')]];
  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <div className="card-head">
        <div><h2>{tr('Checklist')}</h2><p className="sub">{done} of {items.length} done{problems > 0 && <> · <span style={{ color: 'var(--warn)' }}>{plural(problems, 'paper')} need attention</span></>}</p></div>
        <div className="head-actions">
          {!bid.manual && <button className="btn small primary" onClick={fetchPortal} disabled={fetching}>{fetching ? tr('Fetching…') : tr('Fetch from portal')}</button>}
          <button className="btn small" onClick={fromDoc}>{tr('Add standard steps')}</button>
          <Only role="bidder"><a className="btn small" href={api.packUrl(bid.id)} download title="One zip of the Vault papers this checklist needs, in order, with an index of what is missing">{tr('Download bid pack')}</a></Only>
          <button className="btn small" onClick={() => setShowDoc((v) => !v)} aria-expanded={showDoc}>{aiOn ? (bid.doc ? tr('Read another document') : tr('Read tender document')) : tr('Find conditions in the tender text')}</button>
        </div>
      </div>
      <div className="progress" style={{ marginBottom: 12 }} aria-hidden><span style={{ width: `${pct}%` }} /></div>
      {note && <div className="alert warn">{note}</div>}
      {bid.portal_facts && bid.portal_facts.length > 0 && (
        <details open style={{ marginBottom: 10 }}><summary className="muted small">{tr('From the portal\'s tender page')}</summary>
          <dl className="facts">{bid.portal_facts.map((f) => <Fragment key={f.label}><dt>{f.label}</dt><dd>{f.value}</dd></Fragment>)}</dl>
        </details>
      )}
      {!bid.doc && <p className="muted small">Fetch from portal reads the fees, class and dates, and the tender notice when the portal gives it without a login. Otherwise: the document list and conditions come from the tender document. {aiOn ? 'Press Read tender document and upload the NIT.' : 'Turn on the AI assistant to read it, or add items by hand.'}</p>}
      {showDoc && (
        <div className="ai-doc">
          <label className="field">{tr('Upload the tender PDF (up to 10 MB)')}<input type="file" accept="application/pdf,.pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
          <label className="field">{tr('Or paste the text')}<textarea value={paste} onChange={(e) => setPaste(e.target.value)} disabled={!!file} spellCheck={false} /></label>
          <div className="head-actions">
            {aiOn && <button className="btn primary" onClick={readDoc} disabled={busy || (!file && !paste.trim())}>{busy ? tr('Reading document…') : tr('Read and add to checklist')}</button>}
            <button className={aiOn ? 'btn' : 'btn primary'} onClick={findClauses} disabled={busy || (!file && !paste.trim())} title="Fixed rules, no AI: each condition is quoted from the text">{tr('Find conditions (no AI)')}</button>
          </div>
        </div>
      )}
      <DocumentEvidence doc={bid.doc ?? null} found={found ?? bid.clauses?.clauses ?? null} />
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
                          <option value="">{i.match && i.match.status !== 'unknown' && i.match.status !== 'missing' ? tr('Automatic') : tr('Pick from vault')}</option>
                          {docs.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                        </select>
                      )}
                      <AssigneeSelect value={i.owner} label={`Who is responsible for: ${i.text.slice(0, 60)}`} onChange={(v) => assign(i, v)} />
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
        <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label={tr('Kind')}><option value="task">{tr('Step')}</option><option value="document">{tr('Document')}</option><option value="condition">{tr('Condition')}</option></select>
        <button className="btn" disabled={!text.trim()}>{tr('Add')}</button>
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
        <div><h2>{tr('Eligibility check')}</h2><p className="sub">Compares the tender's conditions with your company profile, your facts and the vault.</p></div>
        {aiOn && <button className="btn small" onClick={run} disabled={busy || !bid.doc}>{busy ? tr('Checking…') : e ? tr('Check again') : tr('Check eligibility')}</button>}
      </div>
      {!bid.doc && <p className="muted small">Read the tender document first: the conditions come from it.</p>}
      {error && <div className="alert warn">{error}</div>}
      {e && (
        <>
          <div className="brief-head"><span className={`badge ${tone}`}>{e.verdict}</span><strong>{e.summary}</strong></div>
          <ul className="elig">
            {e.checks.map((c, i) => (
              <li key={i} className={c.status}><span className="elig-icon" aria-label={c.status}>{icon[c.status]}</span>
                <div><div>{c.requirement}</div>{c.evidence && <div className="muted small">{c.evidence}</div>}{c.action && <div className="small"><strong>{tr('To do:')}</strong> {c.action}</div>}</div></li>
            ))}
          </ul>
          <p className="muted small">AI can be wrong. The portal's own document is the final word. {e.checked && `Checked ${fmtDate(e.checked, true)}.`} Add your turnover and registration facts in the Vault for a better answer.</p>
        </>
      )}
    </section>
  );
}

// ---- drafts ------------------------------------------------------------------------------------------------------

const DRAFTS: [string, string][] = [['cover_letter', tr('Covering letter')], ['declaration', tr('Declaration / undertaking')], ['prebid_query', tr('Pre-bid query letter')], ['custom', tr('Something else')]];

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
  async function copy() { try { await navigator.clipboard.writeText(text); toast(tr('Copied')); } catch { toast(tr('Could not copy'), 'bad'); } }
  function download() {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url; a.download = `${bid.tender_id}-${kind}.txt`; a.click();
    URL.revokeObjectURL(url);
  }
  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <h2>{tr('Draft letters')}</h2>
      <p className="sub">Written from your company profile and facts. Anything unknown is left in [brackets] for you to fill in. Check it against the tender's own format before signing.</p>
      <div className="seg" role="group" aria-label="Kind of draft" style={{ marginBottom: 10, flexWrap: 'wrap' }}>
        {DRAFTS.map(([k, l]) => <button key={k} type="button" className={kind === k ? 'on' : ''} aria-pressed={kind === k} onClick={() => setKind(k)}>{l}</button>)}
      </div>
      <label className="field">{kind === 'custom' ? tr('What should it say?') : tr('Anything to include? (optional)')}
        <textarea value={ask} onChange={(e) => setAsk(e.target.value)} maxLength={800} placeholder={kind === 'prebid_query' ? 'e.g. Ask whether the 5% retention can be replaced by a bank guarantee' : kind === 'custom' ? 'e.g. A request to extend bid validity by 30 days' : ''} /></label>
      {aiOn && <button className="btn primary" onClick={make} disabled={busy || (kind === 'custom' && !ask.trim())}>{busy ? tr('Writing…') : text ? tr('Write again') : tr('Write draft')}</button>}
      {error && <div className="alert warn" style={{ marginTop: 10 }}>{error}</div>}
      {text && (
        <>
          <textarea className="draft-box" value={text} onChange={(e) => setText(e.target.value)} spellCheck />
          <div className="d-actions"><button className="btn" onClick={copy}>{tr('Copy')}</button><button className="btn" onClick={download}>{tr('Download .txt')}</button></div>
        </>
      )}
    </section>
  );
}
