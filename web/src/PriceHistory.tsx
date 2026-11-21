import { useState } from 'react';
import { api } from './api';
import { fmtDate } from './format';
import { useLoad } from './hooks';
import { Only } from './session';
import { useConfirm } from './confirm';
import { useToast } from './ui';
import type { Backtest, MarketImport } from './types';

const SAMPLE = 'Tender ID,Department,Work type,Estimated cost,Awarded amount,No. of bidders,Award date\n2026_PWD_101,PWD Nashik,Roads & highways,"50,00,000","46,00,000",6,05-11-2026\n2026_WR_204,Water Resources,Irrigation,2.5 crore,2.6 crore,3,10-10-2026\n';

function Verdict({ b }: { b: Backtest | null }) {
  if (!b) return null;
  if (b.tested === 0) return <p className="muted small">The price model tests itself once there are {b.needed ?? 16} results with dates: it predicts each one from the ones before it and checks. You have {b.have ?? 0} dated so far.</p>;
  return (
    <p className="small">
      <strong>Tested on {b.tested} past results</strong>, each predicted only from the ones before it: the real winning price fell in the predicted middle half {b.inside_middle_half_pct}% of the time (about 50% is right) and in the middle 80% {b.inside_middle_80_pct}% of the time (about 80% is right).
      The model's middle guess was off by {b.median_error_pts} points, against {b.plain_median_error_pts} for simply averaging everything{(b.better_than_plain_pct ?? 0) > 0 ? `, which is ${b.better_than_plain_pct}% better overall` : ''}.
    </p>
  );
}

/** The award results the price advisor learns from: your own decided bids count by themselves; more can be imported from a spreadsheet. */
export function PriceHistoryCard() {
  const toast = useToast();
  const confirm = useConfirm();
  const data = useLoad(() => api.marketResults(), []);
  const test = useLoad(() => api.marketBacktest(), []);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<MarketImport | null>(null);
  const [err, setErr] = useState('');
  const s = data.data;

  const reload = () => { data.reload(); test.reload(); };

  async function run() {
    setBusy(true); setErr(''); setResult(null);
    try {
      const r = await api.marketImport(text);
      setResult(r);
      if (r.added > 0) setText('');
      reload();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  async function pick(file: File | undefined) {
    if (!file) return;
    if (file.size > 900 * 1024) { setErr('That file is over 900 KB. Split it and import it in parts.'); return; }
    setErr('');
    setText(await file.text());
  }

  async function clear() {
    if (!(await confirm({ title: 'Remove all imported results?', body: 'The price advisor will go back to learning only from your own decided bids. Your own bids are not touched.', confirmLabel: 'Remove them', danger: true }))) return;
    try { const r = await api.marketClear('import'); toast(`${r.removed} imported results removed`); reload(); } catch (e) { setErr((e as Error).message); }
  }

  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <h2>Price history</h2>
      <p className="sub">What the lowest bidder actually quoted on tenders that have been awarded, against each estimate. The price advisor learns from it: the more similar results it has, the better its win chances. Your own won and lost bids count by themselves; you can add more from a spreadsheet of award results.</p>
      {data.error && <div className="alert bad">{data.error}</div>}
      {s && (
        <p>
          <strong>{s.usable} result{s.usable === 1 ? '' : 's'} in use</strong>: {s.own} from your own bids, {s.imported} imported{s.first && s.last ? `, awarded ${fmtDate(s.first)} to ${fmtDate(s.last)}` : ''}.
          {s.usable < 15 && ' About 15 or more similar results make the advice useful; 40 or more make it good.'}
        </p>
      )}
      <Verdict b={test.data} />
      <Only role="bidder">
        <details style={{ margin: '10px 0' }}>
          <summary>Import award results</summary>
          <p className="sub" style={{ marginTop: 8 }}>
            Paste rows from a spreadsheet, or choose a CSV file. It needs a header row with at least the buyer's <strong>estimated cost</strong> and the <strong>awarded amount</strong> (or lowest bid); department, work type, number of bidders and award date are optional but make the advice better; a column naming the winner (such as “Awarded to”) also builds the list of who usually wins.
            Amounts can be written 5,00,000, Rs. 5,00,000 or 2.5 crore; dates day first. A price under a third of the estimate or more than twice it is left out as a typing error. The same tender imported twice is kept once. Public award lists on the portals, or your own records, are good sources.
          </p>
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={6} placeholder={SAMPLE} aria-label="Award results to import" style={{ width: '100%', fontFamily: 'monospace', fontSize: 'var(--fs-xs)' }} />
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 8 }}>
            <input type="file" accept=".csv,.tsv,.txt,text/csv,text/plain" onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ''; }} aria-label="Choose a results file" />
            <button type="button" className="btn primary small" onClick={run} disabled={busy || !text.trim()}>{busy ? 'Importing…' : 'Import results'}</button>
            <a className="btn small" href={`data:text/csv;charset=utf-8,${encodeURIComponent(SAMPLE)}`} download="award-results-sample.csv">Download a sample file</a>
          </div>
        </details>
        {err && <div className="alert bad" role="alert">{err}</div>}
        {result && (
          <div className={`alert ${result.added > 0 ? 'ok' : 'warn'}`} role="status">
            <strong>{result.added} added</strong>{result.duplicates ? `, ${result.duplicates} already there` : ''}{result.rejected_total ? `, ${result.rejected_total} left out` : ''}.
            {result.hint && <p className="small" style={{ margin: '6px 0 0' }}>{result.hint}</p>}
            {result.rejected.length > 0 && <ul className="small" style={{ margin: '6px 0 0', paddingLeft: 18 }}>{result.rejected.slice(0, 6).map((r) => <li key={`${r.line}-${r.reason}`}>{r.line ? `Line ${r.line}: ` : ''}{r.reason}</li>)}{result.rejected.length > 6 && <li>and {result.rejected_total - 6} more</li>}</ul>}
          </div>
        )}
        {s && s.imported > 0 && <button type="button" className="btn small" onClick={clear}>Remove all imported results</button>}
      </Only>
    </section>
  );
}
