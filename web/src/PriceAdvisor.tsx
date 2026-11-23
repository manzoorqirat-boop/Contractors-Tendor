import { useEffect, useState } from 'react';
import { api } from './api';
import { inr } from './format';
import { tr } from './i18n';
import { Skeleton, useToast } from './ui';
import type { Advice } from './types';

const sgn = (p: number) => `${p > 0 ? '+' : ''}${p.toFixed(1)}%`;
const CONF: Record<Advice['confidence'], string> = { none: tr('no history yet'), rough: tr('rough guide'), fair: tr('fair basis'), good: tr('good basis') };

/** Where to quote: your cost range against how past lowest bidders priced similar tenders. */
export function PriceAdvisor({ bidId, onUse }: { bidId: number; onUse: (price: number) => void }) {
  const toast = useToast();
  const [minM, setMinM] = useState('3');
  const [tgtM, setTgtM] = useState('8');
  const [adv, setAdv] = useState<Advice | null>(null);
  const [idx, setIdx] = useState(0);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(true);

  async function load(min = minM, tgt = tgtM) {
    setBusy(true); setErr('');
    try {
      const a = await api.advice(bidId, Number(min), Number(tgt));
      setAdv(a);
      const at = a.points.findIndex((p) => p.pct >= a.recommended_pct - 1e-9);
      setIdx(Math.max(0, at));
    } catch (e) { setAdv(null); setErr((e as Error).message); } finally { setBusy(false); }
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [bidId]);

  const head = <h2>{tr('Price advisor')}</h2>;
  if (busy && !adv) return <section className="card" style={{ marginBottom: 14 }}>{head}<Skeleton h={90} /></section>;
  if (!adv) {
    return (
      <section className="card" style={{ marginBottom: 14 }}>
        {head}
        <p className="sub">{err || tr('Needs a costed BOQ.')}</p>
        <button className="btn small" onClick={() => load()}>{tr('Try again')}</button>
      </section>
    );
  }

  const pts = adv.points;
  const sel = pts[Math.min(idx, pts.length - 1)];
  const x0 = pts[0].pct, x1 = pts[pts.length - 1].pct;
  const W = 640, H = 170, L = 34, R = 10, T = 10, B = 24;
  const X = (p: number) => L + ((p - x0) / (x1 - x0 || 1)) * (W - L - R);
  const Y = (w: number) => T + (1 - w / 100) * (H - T - B);
  const curve = pts.every((p) => p.win !== null) ? pts.map((p, i) => `${i ? 'L' : 'M'}${X(p.pct).toFixed(1)},${Y(p.win as number).toFixed(1)}`).join(' ') : '';
  const losing = sel.margin_worst < 0;

  return (
    <section className="card" style={{ marginBottom: 14 }}>
      {head}
      <p className="sub">
        {adv.mode === 'with_risks'
          ? tr('Your cost is {low} to {high} once the risk allowances are added, against a buyer\'s estimate of {estimate}.', { low: inr(adv.cost_low, false), high: inr(adv.cost_high, false), estimate: inr(adv.estimate, false) })
          : tr('Your cost is {low} before any risk allowances, against a buyer\'s estimate of {estimate}.', { low: inr(adv.cost_low, false), estimate: inr(adv.estimate, false) })}
        {' '}{tr('Break-even is {floor} at the minimum margin, and {target} at your target margin.', { floor: sgn(adv.floor_pct), target: sgn(adv.target_pct) })}
      </p>

      <div className="adv-controls">
        <label>{tr('Minimum margin %')}<input type="number" min={0} max={40} step={0.5} value={minM} onChange={(e) => setMinM(e.target.value)} /></label>
        <label>{tr('Target margin %')}<input type="number" min={1} max={45} step={0.5} value={tgtM} onChange={(e) => setTgtM(e.target.value)} /></label>
        <button className="btn small" onClick={() => load()} disabled={busy}>{busy ? tr('Updating…') : tr('Update')}</button>
      </div>
      {err && <div className="alert bad" role="alert">{err}</div>}

      <svg className="adv-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={tr('Chance of winning at each quote, from the history of similar tenders')}>
        <rect x={L} y={T} width={Math.max(0, X(Math.max(x0, Math.min(x1, adv.floor_pct))) - L)} height={H - T - B} fill="var(--bad-soft)" />
        {[0, 50, 100].map((w) => <g key={w}><line x1={L} x2={W - R} y1={Y(w)} y2={Y(w)} stroke="var(--grid)" /><text x={L - 6} y={Y(w) + 4} textAnchor="end">{w}%</text></g>)}
        {curve && <path d={curve} fill="none" stroke="var(--accent)" strokeWidth={2.5} />}
        <line x1={X(adv.floor_pct)} x2={X(adv.floor_pct)} y1={T} y2={H - B} stroke="var(--bad)" strokeDasharray="4 3" />
        <line x1={X(adv.target_pct)} x2={X(adv.target_pct)} y1={T} y2={H - B} stroke="var(--ok)" strokeDasharray="4 3" />
        <line x1={X(adv.recommended_pct)} x2={X(adv.recommended_pct)} y1={T} y2={H - B} stroke="var(--accent)" />
        <line x1={X(sel.pct)} x2={X(sel.pct)} y1={T} y2={H - B} stroke="var(--text)" strokeWidth={2} />
        {curve && sel.win !== null && <circle cx={X(sel.pct)} cy={Y(sel.win)} r={5} fill="var(--text)" />}
        <text x={L} y={H - 6}>{sgn(x0)}</text>
        <text x={W - R} y={H - 6} textAnchor="end">{sgn(x1)}</text>
        <text x={X(sel.pct)} y={H - 6} textAnchor="middle" style={{ fill: 'var(--text)', fontWeight: 700 }}>{sgn(sel.pct)}</text>
      </svg>
      <div className="adv-legend">
        {curve && <span><i style={{ borderColor: 'var(--accent)' }} />{tr('Chance of winning')}</span>}
        <span><i style={{ borderColor: 'var(--bad)', borderTopStyle: 'dashed' }} />{tr('Minimum margin')}</span>
        <span><i style={{ borderColor: 'var(--ok)', borderTopStyle: 'dashed' }} />{tr('Target margin')}</span>
        <span><i style={{ borderColor: 'var(--text)' }} />{tr('Selected quote')}</span>
        <span>{tr('Shaded: loses money if every risk hits')}</span>
      </div>

      <input className="adv-slider" type="range" min={0} max={pts.length - 1} value={idx} onChange={(e) => setIdx(Number(e.target.value))} aria-label={tr('Quote against the estimate')} aria-valuetext={tr('{pct} against the estimate', { pct: sgn(sel.pct) })} />

      <div className="adv-read">
        <div><div className="k">{tr('Quote')}</div><div className="v">{sgn(sel.pct)}</div><div className="muted small">{inr(sel.price, false)}</div></div>
        <div><div className="k">{tr('Profit if every risk hits')}</div><div className={`v ${losing ? 'bad' : ''}`}>{inr(sel.margin_worst, false)}</div><div className="muted small">{tr('{pct}% of the price', { pct: sel.margin_pct_worst.toFixed(1) })}</div></div>
        <div><div className="k">{tr('Profit if none hit')}</div><div className="v">{inr(sel.margin_best, false)}</div></div>
        <div><div className="k">{tr('Chance of winning')}</div><div className="v">{sel.win === null ? '-' : `${sel.win.toFixed(0)}%`}</div><div className="muted small">{sel.expected === null ? tr('No history yet') : tr('Expected profit {amount}', { amount: inr(sel.expected, false) })}</div></div>
      </div>

      <div className="head-actions">
        <button className="btn small" onClick={() => setIdx(Math.max(0, pts.findIndex((p) => p.pct >= adv.recommended_pct - 1e-9)))}>{tr('Go to recommended ({pct})', { pct: sgn(adv.recommended_pct) })}</button>
        <button className="btn small primary" onClick={() => { onUse(Math.round(sel.price)); toast(tr('Quoted price set to {amount}. Find it under Result.', { amount: inr(sel.price, false) })); }}>{tr('Use this as my quoted price')}</button>
      </div>
      {adv.my_value ? <p className="muted small" style={{ marginTop: 8 }}>{tr('Your recorded quoted price is {amount}.', { amount: inr(adv.my_value, false) })}</p> : null}

      <p className="muted small" style={{ marginTop: 10 }}>
        {adv.samples >= 3
          ? <>{tr('Based on {basis}: {confidence}.', { basis: adv.basis, confidence: CONF[adv.confidence] })} {adv.quantiles?.p50 !== undefined && <>{tr('Lowest bidders on similar tenders usually priced between {low} and {high} against the estimate (the middle half; the middle is {mid}).', { low: sgn(adv.quantiles.p25), high: sgn(adv.quantiles.p75), mid: sgn(adv.quantiles.p50) })} </>}{tr('Win chance is the share of those where the lowest bidder priced at or above this quote; groups with few results borrow from wider ones. Add more results on the Bids page, Results view, under Price history.')}</>
          : (adv.samples === 1 ? tr('No win chance yet: you have {n} recorded result and 3 are needed. Import award results on the Bids page, Results view, under Price history, or record the winning price on your own bids.', { n: 1 }) : tr('No win chance yet: you have {n} recorded results and 3 are needed. Import award results on the Bids page, Results view, under Price history, or record the winning price on your own bids.', { n: adv.samples }))}
      </p>
      {adv.notes.length > 0 && <ul className="small" style={{ margin: '6px 0 0', paddingLeft: 18 }}>{adv.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
    </section>
  );
}
