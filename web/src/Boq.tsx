import { Fragment, useState } from 'react';
import { api } from './api';
import { readBase64 } from './ai';
import { inr, parseMoney, plural } from './format';
import { useLoad } from './hooks';
import { ErrorNote, useToast } from './ui';
import { useConfirm } from './confirm';
import { useSession } from './session';
import type { BoqCandidate, BoqLine, BoqStatus, CatalogueItem, Simulation } from './types';

const STATUS: Record<BoqStatus, [string, string]> = {
  AUTO_MATCHED: ['ok', 'Matched'],
  NEEDS_REVIEW: ['warn', 'Check'],
  NEW_NON_SOR_ITEM: ['plain', 'Not in catalogue'],
};
const KINDS: { key: string; label: string; hint: string }[] = [
  { key: 'items', label: 'Items and rates', hint: 'Columns: erp_item_code, item_name, primary_uom, standard_base_rate. Optional: detailed_specification, category, sor_code, is_composite (yes/no), gst_pct.' },
  { key: 'activities', label: 'Composite activity rates', hint: 'Columns: activity_code, description, uom, material_cost, labor_cost, machinery_cost, overhead_cost, wastage_pct, erp_item_code.' },
  { key: 'sor', label: 'Schedule of rates (DSR / SOR)', hint: 'Columns: source, sor_code, description, uom, rate. Optional: state.' },
  { key: 'locations', label: 'Location cost index', hint: 'Columns: state, cost_index (1.00 = base), hill_carriage_pct. Optional: district, royalty_per_cum, note.' },
];
const num = parseMoney;
const pct = (v: number | null | undefined) => (v === null || v === undefined ? '-' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`);

// ---- BOQ card ------------------------------------------------------------------------------------------------------

export function BoqCard({ bidId }: { bidId: number }) {
  const toast = useToast();
  const confirm = useConfirm();
  const [review, setReview] = useState(false);
  const view = useLoad(() => api.bidBoq(bidId, review), [bidId, review]);
  const other = useLoad(() => api.bidBoq(bidId, !review), [bidId, review]); // the same BOQ priced the other way, for the side-by-side totals
  const items = useLoad(() => api.boqCatalogue(), [bidId]);
  const [accepting, setAccepting] = useState(false);
  const reloadAll = () => { view.reload(); other.reload(); };
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState<number | null>(null);
  const [filter, setFilter] = useState<'all' | 'check' | 'none'>('all');

  const d = view.data;
  const boq = d?.boq ?? null;
  const noCatalogue = !!d && (d.catalogue.item_master ?? 0) === 0;

  async function upload() {
    if (!file) return;
    setBusy(true);
    try {
      const r = await api.uploadBoq(bidId, file.name, await readBase64(file));
      toast(`BOQ read. ${plural(r.boq?.lines.length ?? 0, 'item')} found`);
      setFile(null); reloadAll();
    } catch (e) { toast((e as Error).message, 'bad'); } finally { setBusy(false); }
  }
  async function choose(line: BoqLine, code: string | null) {
    setPicking(null);
    try { await api.confirmBoqLine(bidId, line.line_no, code); reloadAll(); } catch (e) { toast((e as Error).message, 'bad'); }
  }

  const lines = (boq?.lines ?? []).filter((l) => filter === 'all' || (filter === 'check' ? l.status === 'NEEDS_REVIEW' : l.status === 'NEW_NON_SOR_ITEM'));
  const pr = d?.pricing ?? null;
  const confirmedPr = review ? other.data?.pricing ?? null : pr;
  const withCheckPr = review ? pr : other.data?.pricing ?? null;
  const suggested = (boq?.lines ?? []).filter((l) => l.status === 'NEEDS_REVIEW' && l.item_code);
  async function acceptAll() {
    if (!(await confirm({ title: `Confirm ${plural(suggested.length, 'suggested match', 'suggested matches')}?`, body: 'Each line takes the item the app suggested. You can still change any of them afterwards, and every confirmation teaches the app for next time.', confirmLabel: 'Confirm all' }))) return;
    setAccepting(true);
    try { for (const l of suggested) await api.confirmBoqLine(bidId, l.line_no, l.item_code); toast(`Confirmed ${plural(suggested.length, 'line')}`); }
    catch (e) { toast((e as Error).message, 'bad'); } finally { setAccepting(false); reloadAll(); }
  }

  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <div className="card-head">
        <div>
          <h2>Bill of quantities (BOQ)</h2>
          <p className="sub">Upload the tender's BOQ (.xlsx, .xlsm, .xls, .csv or a PDF with a table). Each line is matched to your item catalogue; confirm the matches and the cost is worked out from your own rates.</p>
        </div>
        <div className="head-actions">
          <a className="btn small" href="#/vault">Item catalogue{d ? ` (${d.catalogue.item_master ?? 0})` : ''} in Vault</a>
        </div>
      </div>

      <ErrorNote error={view.error} onRetry={view.reload} />
      {noCatalogue && <div className="alert warn">Your item catalogue is empty, so nothing can be matched yet. Load your item list once in the <a href="#/vault">Vault</a>, then come back here.</div>}

      <div className="ai-doc">
        <label className="field" style={{ margin: 0 }}>BOQ file (.xlsx, .xlsm, .xls, .csv or .pdf, up to 10 MB)
          <input type="file" accept=".xlsx,.xlsm,.xls,.csv,.pdf,application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        <div><button className="btn primary" onClick={upload} disabled={busy || !file}>{busy ? 'Reading and matching…' : boq ? 'Replace BOQ and match again' : 'Read and match'}</button></div>
        {d && !d.ai && <p className="muted small" style={{ margin: 0 }}>The AI assistant is off, so lines are matched by rules only and none is marked Matched until you confirm it.</p>}
      </div>

      {view.loading && !d && <p className="muted small">Loading…</p>}
      {boq && pr && (
        <>
          <h3 style={{ margin: '16px 0 6px' }}>{boq.source_name}</h3>
          <div className="row-badges" style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
            <span className="badge ok">{boq.counts.AUTO_MATCHED ?? 0} matched</span>
            <span className="badge warn">{boq.counts.NEEDS_REVIEW ?? 0} to check</span>
            <span className="badge plain">{boq.counts.NEW_NON_SOR_ITEM ?? 0} not in catalogue</span>
            <span className="badge accent">{boq.rate_type === 'percentage_rate' ? 'Quote % above or below' : boq.rate_type === 'item_rate' ? 'Item rates' : 'Rate type unclear'}</span>
          </div>
          {boq.warnings.length > 0 && <div className="alert warn">{boq.warnings.slice(0, 6).map((w) => <div key={w}>{w}</div>)}{boq.warnings.length > 6 && <div>and {boq.warnings.length - 6} more</div>}</div>}
          <dl className="facts">
            <dt>Buyer's estimate</dt><dd>{inr(pr.estimate_total, false)}</dd>
            <dt>Priced from your catalogue</dt><dd>{pr.priced_lines} of {pr.total_lines} lines, {pr.coverage_pct.toFixed(0)}% of the value</dd>
            <dt>Your cost, confirmed lines only</dt><dd>{confirmedPr ? <>{inr(confirmedPr.projected_cost, false)} <span className="muted small">({confirmedPr.break_even_pct === null ? '-' : pct(confirmedPr.break_even_pct)} against the estimate)</span></> : '-'}</dd>
            <dt>If lines marked Check are right</dt><dd>{withCheckPr ? <>{inr(withCheckPr.projected_cost, false)} <span className="muted small">({withCheckPr.break_even_pct === null ? '-' : pct(withCheckPr.break_even_pct)}). A guess until you confirm them.</span></> : '-'}</dd>
          </dl>
          <p className="muted small">{pr.assumption} Material-only rates and lump sums are never counted as a full cost unless you enter your own figure for the line.</p>
          <div className="head-actions" style={{ margin: '8px 0' }}>
            {suggested.length > 0 && <button className="btn small primary" onClick={acceptAll} disabled={accepting}>{accepting ? 'Confirming…' : `Confirm all ${suggested.length} suggested matches`}</button>}
            <label className="muted small"><input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} /> Show lines marked "Check" with a cost in the table</label>
            <select value={filter} onChange={(e) => setFilter(e.target.value as 'all' | 'check' | 'none')} aria-label="Show lines">
              <option value="all">All lines</option><option value="check">Lines to check</option><option value="none">Not in catalogue</option>
            </select>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table className="tbl">
              <thead><tr><th>Sr</th><th>Description</th><th>Unit</th><th className="num">Qty</th><th className="num">Estimate rate</th><th>Matched item</th><th className="num">Your cost</th></tr></thead>
              <tbody>
                {lines.map((l, i) => (
                  <Fragment key={l.line_no}>
                    {l.section && (i === 0 || lines[i - 1].section !== l.section) && <tr><td colSpan={7} className="strong muted">{l.section}</td></tr>}
                    <tr>
                      <td className="nowrap">{l.sr}</td>
                      <td>{l.description}</td>
                      <td className="nowrap">{l.uom ?? l.uom_raw ?? '-'}</td>
                      <td className="num">{l.qty === null ? '-' : l.qty.toLocaleString('en-IN')}</td>
                      <td className="num">{l.est_rate === null ? '-' : l.est_rate.toLocaleString('en-IN')}</td>
                      <td style={{ minWidth: 230 }}>
                        <MatchCell line={l} items={items.data?.items ?? []} picking={picking === l.line_no} onPick={() => setPicking(l.line_no)} onCancel={() => setPicking(null)} onChoose={(c) => choose(l, c)} />
                      </td>
                      <td className="num nowrap">
                        {l.cost?.complete && l.cost.cost_amount !== null
                          ? <>{inr(l.cost.cost_amount, false)}<div className="muted small" title={l.cost.note}>{l.manual_cost !== null ? 'your figure' : pct(l.cost.variance_pct)}</div></>
                          : <span className="muted" title={l.cost?.note ?? ''}>-</span>}
                        <CostEntry line={l} bidId={bidId} onSaved={reloadAll} />
                      </td>
                    </tr>
                  </Fragment>
                ))}
                {lines.length === 0 && <tr><td colSpan={7} className="muted">No lines to show.</td></tr>}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

/** Lets a bidder enter what a line will cost in total: the way to price a lump sum or an item that is not in the catalogue. */
function CostEntry({ line, bidId, onSaved }: { line: BoqLine; bidId: number; onSaved: () => void }) {
  const toast = useToast();
  const { can } = useSession();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [saving, setSaving] = useState(false);
  const priced = line.cost?.complete && line.manual_cost === null;
  if (!can('bidder') || (priced && !line.lump_sum)) return null;
  async function save(cost: number | null) {
    setSaving(true);
    try { await api.setBoqLineCost(bidId, line.line_no, cost); setOpen(false); onSaved(); }
    catch (e) { toast((e as Error).message, 'bad'); } finally { setSaving(false); }
  }
  if (!open) {
    return (
      <div>
        <button type="button" className="btn small" onClick={() => { setText(line.manual_cost !== null ? String(line.manual_cost) : ''); setOpen(true); }}>
          {line.manual_cost !== null ? 'Change cost' : line.lump_sum ? 'Enter lump-sum cost' : 'Enter cost'}
        </button>
      </div>
    );
  }
  const value = parseMoney(text);
  return (
    <form onSubmit={(e) => { e.preventDefault(); if (value !== null && value > 0) void save(value); }} style={{ display: 'grid', gap: 4, marginTop: 4 }}>
      <label className="muted small">Your total cost for this line (₹, or 4.5 lakh)
        <input autoFocus value={text} onChange={(e) => setText(e.target.value)} inputMode="decimal" aria-invalid={text !== '' && (value === null || value <= 0)} />
      </label>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <button className="btn small primary" disabled={saving || value === null || value <= 0}>Save</button>
        {line.manual_cost !== null && <button type="button" className="btn small" disabled={saving} onClick={() => void save(null)}>Remove</button>}
        <button type="button" className="btn small" onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </form>
  );
}

function MatchCell({ line, items, picking, onPick, onCancel, onChoose }: {
  line: BoqLine; items: CatalogueItem[]; picking: boolean; onPick: () => void; onCancel: () => void; onChoose: (code: string | null) => void;
}) {
  const [tone, label] = STATUS[line.status];
  if (picking) {
    const sug = line.candidates.filter((c: BoqCandidate) => !c.conflicts.length);
    const sugCodes = new Set(sug.map((c) => c.code));
    const same = items.filter((i) => (!line.uom || i.uom === line.uom) && !sugCodes.has(i.code));
    return (
      <div>
        <select autoFocus defaultValue="" onChange={(e) => onChoose(e.target.value === '__none__' ? null : e.target.value)} aria-label="Choose the catalogue item for this line">
          <option value="" disabled>Choose an item…</option>
          {sug.length > 0 && <optgroup label="Suggested">{sug.map((c) => <option key={c.code} value={c.code}>{c.name} ({c.code})</option>)}</optgroup>}
          {same.length > 0 && <optgroup label={line.uom ? `All items per ${line.uom}` : 'All items'}>{same.map((i) => <option key={i.code} value={i.code}>{i.name} ({i.code})</option>)}</optgroup>}
          <option value="__none__">None of these: not in my catalogue</option>
        </select>
        <button className="btn small" style={{ marginLeft: 6 }} onClick={onCancel}>Cancel</button>
      </div>
    );
  }
  return (
    <div>
      <span className={`badge ${tone}`}>{label}</span>{' '}
      {line.item_code ? <span>{line.item_name} <span className="muted small">({line.item_code})</span></span> : <span className="muted small">{line.reason}</span>}
      {line.item_code && line.reason && line.status !== 'AUTO_MATCHED' && <div className="muted small">{line.reason}</div>}
      <div style={{ marginTop: 4 }}>
        {line.status === 'NEEDS_REVIEW' && line.item_code && <button className="btn small primary" style={{ marginRight: 6 }} onClick={() => onChoose(line.item_code)}>Confirm</button>}
        {line.status !== 'AUTO_MATCHED' || line.tier !== 'confirmed' ? <button className="btn small" onClick={onPick}>{line.item_code ? 'Change' : 'Choose item'}</button> : <button className="btn small" onClick={onPick}>Change</button>}
      </div>
    </div>
  );
}

export function CataloguePanel({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const [kind, setKind] = useState('items');
  const [text, setText] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<{ added: number; updated: number; errors: string[] } | null>(null);
  const hint = KINDS.find((k) => k.key === kind)?.hint ?? '';

  async function run() {
    setBusy(true);
    try {
      const body = file ? await file.text() : text;
      const r = await api.importCatalogue(kind, body);
      setRes(r.result);
      toast(`Added ${r.result.added}, updated ${r.result.updated}${r.result.errors.length ? `, ${plural(r.result.errors.length, 'row')} skipped` : ''}`);
      if (r.result.added + r.result.updated > 0) { setFile(null); setText(''); onDone(); }
    } catch (e) { toast((e as Error).message, 'bad'); } finally { setBusy(false); }
  }

  return (
    <div className="ai-doc" style={{ marginBottom: 12 }}>
      <label className="field" style={{ margin: 0 }}>What are you loading?
        <select value={kind} onChange={(e) => { setKind(e.target.value); setRes(null); }}>{KINDS.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}</select>
      </label>
      <p className="muted small" style={{ margin: 0 }}>{hint} Export it from your ERP as CSV. Loading the same code again updates it.</p>
      <label className="field" style={{ margin: 0 }}>CSV file<input type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
      <label className="field" style={{ margin: 0 }}>Or paste the CSV text<textarea value={text} onChange={(e) => setText(e.target.value)} disabled={!!file} spellCheck={false} rows={4} /></label>
      <div><button className="btn primary" onClick={run} disabled={busy || (!file && !text.trim())}>{busy ? 'Loading…' : 'Load'}</button></div>
      {res && res.errors.length > 0 && <div className="alert warn" style={{ margin: 0 }}>{res.errors.slice(0, 10).map((e) => <div key={e}>{e}</div>)}{res.errors.length > 10 && <div>and {res.errors.length - 10} more</div>}</div>}
    </div>
  );
}

// ---- Risk and margin card ------------------------------------------------------------------------------------------

const PRESENT: Record<string, [string, string]> = { yes: ['warn', 'In this tender'], unclear: ['plain', 'Unclear'], no_mention: ['plain', 'Not mentioned'] };

export function RiskCard({ bidId, aiOn, hasSource = false }: { bidId: number; aiOn: boolean; hasSource?: boolean }) {
  const toast = useToast();
  const risks = useLoad(() => api.bidRisks(bidId), [bidId]);
  const [file, setFile] = useState<File | null>(null);
  const [paste, setPaste] = useState('');
  const [busy, setBusy] = useState(false);
  const [l1, setL1] = useState('');
  const [quote, setQuote] = useState('');
  const [review, setReview] = useState(false);
  const [expo, setExpo] = useState<Record<string, string>>({});
  const [sim, setSim] = useState<Simulation | null>(null);
  const [running, setRunning] = useState(false);

  const f = risks.data?.findings ?? null;
  const labels = Object.fromEntries((risks.data?.tags ?? []).map((t) => [t.tag, t.label]));

  async function read() {
    setBusy(true);
    try {
      await api.readRisks(bidId, file ? '' : paste, file ? await readBase64(file) : '');
      toast('Tender read for risks'); setFile(null); setPaste(''); setSim(null); risks.reload();
    } catch (e) { toast((e as Error).message, 'bad'); } finally { setBusy(false); }
  }
  async function run() {
    setRunning(true);
    try {
      const exposure: Record<string, number> = {};
      for (const [k, v] of Object.entries(expo)) { const n = num(v); if (n !== null) exposure[k] = Math.max(0, Math.min(100, n)) / 100; }
      setSim(await api.simulate(bidId, { l1_price: num(l1), quoted_pct: num(quote), exposure, include_review: review }));
    } catch (e) { toast((e as Error).message, 'bad'); } finally { setRunning(false); }
  }

  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <h2>Risk and margin</h2>
      <p className="sub">Finds the hidden costs in the tender conditions and adds them to your BOQ cost. The percentages are planning allowances to challenge, not rules.</p>
      <ErrorNote error={risks.error} onRetry={risks.reload} />
      {aiOn ? (
        <div className="ai-doc">
          <label className="field" style={{ margin: 0 }}>Tender document (PDF, up to 10 MB)<input type="file" accept="application/pdf,.pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
          <label className="field" style={{ margin: 0 }}>Or paste the text of the NIT and special conditions<textarea value={paste} onChange={(e) => setPaste(e.target.value)} disabled={!!file} spellCheck={false} rows={4} /></label>
          <div><button className="btn primary" onClick={read} disabled={busy || (!file && !paste.trim() && !hasSource)}>{busy ? 'Reading…' : f ? 'Read again' : 'Find the risks'}</button>{hasSource && !file && !paste.trim() && <span className="muted small"> Uses the notice already saved for this tender.</span>}</div>
        </div>
      ) : <p className="muted small">Turn on the AI assistant to read tender documents for risks.</p>}

      {f && (
        <>
          <h3 style={{ margin: '16px 0 6px' }}>What the tender says</h3>
          <table className="tbl">
            <tbody>
              {Object.entries(f.risks).sort(([, a], [, b]) => (a.present === 'yes' ? 0 : a.present === 'unclear' ? 1 : 2) - (b.present === 'yes' ? 0 : b.present === 'unclear' ? 1 : 2)).map(([tag, r]) => (
                <tr key={tag}>
                  <td style={{ width: 210 }} className="strong">{labels[tag] ?? tag}</td>
                  <td style={{ width: 120 }}><span className={`badge ${PRESENT[r.present][0]}`}>{PRESENT[r.present][1]}</span></td>
                  <td>{r.detail}{r.clause && <span className="muted small"> (clause {r.clause})</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3 style={{ margin: '16px 0 6px' }}>Margin check</h3>
          <div className="ws-grid">
            <label className="field">Expected L1 price (₹)<input inputMode="numeric" value={l1} onChange={(e) => setL1(e.target.value)} placeholder="Lowest bid you expect" />{num(l1) ? <span className="date-say">{inr(num(l1))}</span> : null}</label>
            <label className="field">Your planned quote (% above + / below −)<input inputMode="decimal" value={quote} onChange={(e) => setQuote(e.target.value)} placeholder="e.g. -8" /></label>
          </div>
          {sim?.unpriced.filter((u) => u.why.startsWith('Needs the share')).map((u) => (
            <label className="field" key={u.tag}>{u.label}: what % of your cost does this touch?
              <input inputMode="decimal" value={expo[u.tag] ?? ''} onChange={(e) => setExpo({ ...expo, [u.tag]: e.target.value })} placeholder="e.g. 10" />
            </label>
          ))}
          <div className="head-actions">
            <button className="btn primary" onClick={run} disabled={running}>{running ? 'Working…' : sim ? 'Run again' : 'Work out the range'}</button>
            <label className="muted small"><input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} /> Count BOQ lines marked "Check"</label>
          </div>
          {sim && <SimView sim={sim} />}
        </>
      )}
    </section>
  );
}

function SimView({ sim }: { sim: Simulation }) {
  return (
    <div style={{ marginTop: 14 }}>
      {sim.warnings.map((w) => <div className="alert warn" key={w}>{w}</div>)}
      <dl className="facts">
        <dt>Your BOQ cost before risks</dt><dd>{inr(sim.base_cost, false)} <span className="muted small">({sim.coverage_pct.toFixed(0)}% priced from your catalogue)</span></dd>
        <dt>Risk allowances</dt><dd>{inr(sim.risk_low, false)} to {inr(sim.risk_high, false)}</dd>
        <dt>Cost with risks</dt><dd><strong>{inr(sim.cost_low, false)} to {inr(sim.cost_high, false)}</strong></dd>
        {sim.break_even_pct_low !== undefined && sim.break_even_pct_high !== undefined && <><dt>Break-even quote</dt><dd>{pct(sim.break_even_pct_low)} to {pct(sim.break_even_pct_high)} against the estimate</dd></>}
        {sim.margin_at_l1_low !== undefined && sim.margin_at_l1_high !== undefined && sim.l1_price !== undefined && <><dt>Margin if L1 is {inr(sim.l1_price, false)}</dt><dd>{inr(sim.margin_at_l1_low, false)} to {inr(sim.margin_at_l1_high, false)}</dd></>}
      </dl>
      {sim.adjustments.length > 0 && (
        <table className="tbl" style={{ marginTop: 10 }}>
          <thead><tr><th>Risk</th><th>Basis</th><th className="num">Low</th><th className="num">High</th></tr></thead>
          <tbody>{sim.adjustments.map((a) => <tr key={a.tag}><td className="strong">{a.label}</td><td className="muted small">{a.basis}</td><td className="num">{inr(a.low, false)}</td><td className="num">{inr(a.high, false)}</td></tr>)}</tbody>
        </table>
      )}
      {sim.unpriced.length > 0 && <><h3 style={{ margin: '14px 0 4px' }}>Not priced</h3><ul className="muted small">{sim.unpriced.map((u) => <li key={u.tag}><strong>{u.label}:</strong> {u.why}</li>)}</ul></>}
      {sim.exposures.map((x) => <p key={x.tag} className="muted small"><strong>{x.label}:</strong> up to {inr(x.amount, false)} ({x.detail}). This is an exposure, not part of the cost above.</p>)}
      {sim.cash_flow.length > 0 && <><h3 style={{ margin: '14px 0 4px' }}>Cash tied up</h3><ul className="muted small">{sim.cash_flow.map((c) => <li key={c.label}><strong>{c.label}:</strong> {c.detail}</li>)}</ul></>}
    </div>
  );
}
