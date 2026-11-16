import { useState } from 'react';
import { api } from './api';
import { plural } from './format';
import { useToast } from './ui';
import type { BidDetail } from './types';

export type WsTab = 'decide' | 'checklist' | 'price' | 'letters' | 'result';

const PROBLEM = ['expired', 'expires_before_close', 'missing'];

interface Part { label: string; weight: number; score: number; todo: string }

/** How ready a bid is to submit, as a percentage, and what is still missing. Steps the app cannot help with (no AI) do not count against it. */
export function readiness(bid: BidDetail, aiOn: boolean): { pct: number; todo: string[] } {
  const items = bid.items;
  const docs = items.filter((i) => i.kind === 'document');
  const docsNotReady = docs.filter((i) => !i.done && i.match && PROBLEM.includes(i.match.status)).length;
  const open = items.filter((i) => !i.done).length;
  const parts: Part[] = [
    { label: 'Checklist', weight: 50, score: items.length ? 50 * (items.length - open) / items.length : 0, todo: items.length ? `${plural(open, 'checklist item')} not ticked` : 'The checklist is empty: read the tender document or add steps' },
    { label: 'Papers', weight: 15, score: docs.length ? 15 * (docs.length - docsNotReady) / docs.length : 15, todo: `${plural(docsNotReady, 'required paper')} not ready in the Vault (missing, expired or expiring before closing)` },
  ];
  if (aiOn || bid.doc) parts.push({ label: 'Document', weight: 10, score: bid.doc ? 10 : 0, todo: 'The tender document has not been read yet' });
  if (aiOn || bid.eligibility) parts.push({ label: 'Eligibility', weight: 10, score: bid.eligibility ? 10 : 0, todo: 'The eligibility check has not been run' });
  parts.push({ label: 'Price', weight: 15, score: bid.my_value ? 15 : 0, todo: 'Your quoted price is not recorded yet (Result tab)' });
  const total = parts.reduce((a, p) => a + p.weight, 0);
  const got = parts.reduce((a, p) => a + p.score, 0);
  return { pct: Math.round((100 * got) / total), todo: parts.filter((p) => p.score < p.weight).map((p) => p.todo) };
}

const STEPS: { tab: WsTab; label: string }[] = [
  { tab: 'decide', label: 'Decide' }, { tab: 'checklist', label: 'Checklist' }, { tab: 'price', label: 'Price' }, { tab: 'letters', label: 'Letters' }, { tab: 'result', label: 'Result' },
];

function stepDone(bid: BidDetail, tab: WsTab): boolean {
  switch (tab) {
    case 'decide': return !!(bid.eligibility || bid.brief) || bid.stage !== 'interested';
    case 'checklist': return bid.items.length > 0 && bid.items.every((i) => i.done);
    case 'price': return !!bid.my_value;
    case 'letters': return !!bid.drafts && Object.keys(bid.drafts).length > 0;
    case 'result': return bid.stage === 'won' || bid.stage === 'lost';
  }
}

/** The five steps of a bid with how far each has got, how ready the whole bid is, and the one check only a person can do. */
export function BidProgress({ bid, aiOn, tab, onGo, onChange }: { bid: BidDetail; aiOn: boolean; tab: WsTab; onGo: (t: WsTab) => void; onChange: (patch: Partial<BidDetail>) => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const active = bid.stage === 'interested' || bid.stage === 'preparing';
  const r = readiness(bid, aiOn);
  const tone = r.pct >= 85 ? 'ok' : r.pct >= 50 ? 'warn' : 'bad';

  async function mark(checked: boolean) {
    setBusy(true);
    try { const out = await api.docChecked(bid.id, checked); onChange({ doc_checked: out.doc_checked }); toast(checked ? 'Marked as checked against the notice' : 'Check removed'); }
    catch (e) { toast((e as Error).message, 'bad'); } finally { setBusy(false); }
  }

  return (
    <section className="card bidprog" aria-label="Bid progress">
      <ol className="steps">
        {STEPS.map((s, i) => {
          const done = stepDone(bid, s.tab);
          return (
            <li key={s.tab} className={`${done ? 'done' : ''}${tab === s.tab ? ' here' : ''}`}>
              <button type="button" onClick={() => onGo(s.tab)} aria-current={tab === s.tab ? 'step' : undefined}>
                <span className="dot" aria-hidden>{done ? '✓' : i + 1}</span>
                <span className="lbl">{s.label}<span className="sr-only">{done ? ' (done)' : ' (to do)'}</span></span>
              </button>
            </li>
          );
        })}
      </ol>
      {active && (
        <div className="ready">
          <div className="ready-head">
            <strong>Ready to submit</strong>
            <span className={`badge ${tone}`}>{r.pct}%</span>
          </div>
          <div className="meter" role="progressbar" aria-valuenow={r.pct} aria-valuemin={0} aria-valuemax={100} aria-label="Ready to submit"><span className={tone} style={{ width: `${r.pct}%` }} /></div>
          {r.todo.length > 0
            ? <ul className="ready-todo">{r.todo.slice(0, 4).map((t) => <li key={t}>{t}</li>)}{r.todo.length > 4 && <li>and {r.todo.length - 4} more</li>}</ul>
            : <p className="muted small" style={{ margin: '6px 0 0' }}>Everything the app can check is in place. Do a last read of the notice before you submit.</p>}
        </div>
      )}
      {bid.doc && (
        <div className={`ai-check ${bid.doc_checked ? 'ok' : 'warn'}`}>
          {bid.doc_checked
            ? <><span>✓ The AI-read details were checked against the notice by <strong>{bid.doc_checked.by}</strong> on {new Date(bid.doc_checked.at).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })}.</span>
                <button type="button" className="link-btn" disabled={busy} onClick={() => mark(false)}>Undo</button></>
            : <><span><strong>AI-read details are not checked yet.</strong> Compare the requirements, amounts and dates with the notice itself before you rely on them.</span>
                <button type="button" className="btn small" disabled={busy} onClick={() => mark(true)}>I checked them against the notice</button></>}
        </div>
      )}
    </section>
  );
}
