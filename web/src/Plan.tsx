import { useState } from 'react';
import { api } from './api';
import { fmtDate, inr, parseMoney } from './format';
import { ScrollX } from './ScrollX';
import { Only } from './session';
import type { Plan } from './types';

const KEY = 'plan-inputs';

function saved(): { cash: string; margin: string } {
  try { const v = JSON.parse(localStorage.getItem(KEY) || ''); return { cash: String(v.cash ?? ''), margin: String(v.margin ?? '8') }; } catch { return { cash: '', margin: '8' }; }
}

/** Which open bids to submit with a limited amount of free cash for earnest money: the set with the most expected profit, and why each other bid is left out. */
export function PlanCard() {
  const start = saved();
  const [cash, setCash] = useState(start.cash);
  const [margin, setMargin] = useState(start.margin);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  async function run() {
    const budget = cash.trim() ? parseMoney(cash) : null;
    if (cash.trim() && budget === null) { setErr('Write the cash as a number, for example 5,00,000 or 5 lakh.'); return; }
    const m = Number(margin);
    if (!(m >= 0 && m <= 60)) { setErr('The margin must be between 0 and 60 per cent.'); return; }
    setBusy(true); setErr('');
    try {
      setPlan(await api.plan(budget, m));
      try { localStorage.setItem(KEY, JSON.stringify({ cash, margin })); } catch { /* storage blocked: the inputs are just not remembered */ }
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  return (
    <Only role="bidder">
      <section className="card" style={{ marginBottom: 14 }}>
        <h2>Which bids to submit</h2>
        <p className="sub">Each bid ties up its earnest money (EMD) until the result. Tell it how much free cash you can put into EMDs and it picks the open bids that earn the most expected profit within that cash. Expected profit is the chance of winning × the estimate × your margin.</p>
        <div className="adv-controls">
          <label>Free cash for EMDs (₹)<input type="text" className="wide" value={cash} onChange={(e) => setCash(e.target.value)} placeholder="e.g. 5,00,000 or 5 lakh" inputMode="text" /></label>
          <label>Margin %<input type="number" min={0} max={60} step={0.5} value={margin} onChange={(e) => setMargin(e.target.value)} /></label>
          <button className="btn small primary" onClick={run} disabled={busy}>{busy ? 'Working it out…' : 'Plan my bids'}</button>
        </div>
        {err && <div className="alert bad" role="alert">{err}</div>}
        {plan && (plan.candidates === 0
          ? <p className="muted">No open bids to choose from: bids in Interested or Preparing that have not closed yet appear here.</p>
          : (
            <>
              <p style={{ marginTop: 10 }}>
                <strong>Submit {plan.submit} of {plan.candidates}</strong>{plan.available !== null ? <>: uses {inr(plan.emd_used, false)} of your {inr(plan.available, false)}, leaving {inr(plan.left ?? 0, false)}. </> : <>. No cash figure was given, so none were cut. </>}
                Expected profit about <strong>{inr(plan.expected_profit, false)}</strong>.
                {plan.cash_locked > 0 && <span className="muted"> ({inr(plan.cash_locked, false)} is already locked in other bids, not counted here.)</span>}
              </p>
              {plan.with_more_cash && plan.with_more_cash.extra_cash > 0 && <p className="small">With {inr(plan.with_more_cash.extra_cash, false)} more you could also bid on “{plan.with_more_cash.title}” (expected profit {inr(plan.with_more_cash.profit, false)}).</p>}
              {plan.emd_unknown > 0 && <div className="alert warn" role="status">{plan.emd_unknown} chosen bid{plan.emd_unknown === 1 ? ' has' : 's have'} no EMD on record, so it was counted as no EMD: check the tender.</div>}
              <ScrollX label="Which bids to submit">
                <table className="tbl">
                  <thead><tr><th>Decision</th><th>Bid</th><th>Closes</th><th className="num">EMD</th><th className="num">Chance</th><th className="num">Expected profit</th></tr></thead>
                  <tbody>
                    {plan.rows.map((r) => (
                      <tr key={r.id}>
                        <td><span className={`badge ${r.decision === 'submit' ? 'ok' : 'plain'}`}>{r.decision === 'submit' ? 'Submit' : 'Skip'}</span></td>
                        <td><a href={`#/bids/${r.id}`}>{r.title || r.tender_id}</a>{r.reason && <div className="muted small">{r.reason}</div>}</td>
                        <td className="small">{r.closing ? fmtDate(r.closing, true) : '-'}</td>
                        <td className="num">{r.emd_known ? inr(r.emd, false) : <span className="muted">not known</span>}</td>
                        <td className="num">{Math.round(r.p * 100)}%<div className="muted small">{r.p_source}</div></td>
                        <td className="num">{r.profit > 0 ? inr(r.profit, false) : '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </ScrollX>
              <p className="muted small" style={{ marginTop: 8 }}>A rough guide: it uses the estimate for the contract value, the same margin for every bid, assumes each EMD comes back, and cannot know your capacity or relationships. The chance of winning comes from your win model when it has earned a score, else your overall win rate, else an assumed 20%.</p>
            </>
          ))}
      </section>
    </Only>
  );
}
