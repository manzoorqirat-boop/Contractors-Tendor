import { useEffect, useState } from 'react';
import { api } from './api';
import { fmtDate, inr } from './format';
import { Drawer, Skeleton } from './ui';
import type { CompareTender } from './types';

type Row = { label: string; cell: (t: CompareTender) => React.ReactNode; best?: (ts: CompareTender[]) => number | null };
const lowest = (get: (t: CompareTender) => number | null) => (ts: CompareTender[]) => {
  const v = ts.map(get);
  const ok = v.filter((x): x is number => x !== null);
  return ok.length > 1 ? v.indexOf(Math.min(...ok)) : null;
};
const highest = (get: (t: CompareTender) => number | null) => (ts: CompareTender[]) => {
  const v = ts.map(get);
  const ok = v.filter((x): x is number => x !== null);
  return ok.length > 1 ? v.indexOf(Math.max(...ok)) : null;
};

const ROWS: Row[] = [
  { label: 'Work', cell: (t) => t.description || t.title },
  { label: 'Department', cell: (t) => t.organisation.split('||').filter(Boolean).join(' › ') || '-' },
  { label: 'Portal', cell: (t) => t.portal_name },
  { label: 'Location', cell: (t) => t.location || '-' },
  { label: 'Type of work', cell: (t) => t.work_type ?? '-' },
  { label: 'Estimated value', cell: (t) => (t.value ? inr(t.value, false) : '-'), best: highest((t) => t.value) },
  { label: 'EMD', cell: (t) => (t.emd ? inr(t.emd, false) : '-'), best: lowest((t) => t.emd) },
  { label: 'EMD as % of value', cell: (t) => (t.emd_pct !== null ? `${t.emd_pct}%` : '-'), best: lowest((t) => t.emd_pct) },
  { label: 'Published', cell: (t) => fmtDate(t.published) },
  { label: 'Closes', cell: (t) => fmtDate(t.closing, true) },
  { label: 'Time left', cell: (t) => (t.days_left === null ? '-' : t.days_left < 0 ? 'Closed' : t.days_left < 1 ? 'Under a day' : `${Math.floor(t.days_left)} day${Math.floor(t.days_left) === 1 ? '' : 's'}`), best: highest((t) => (t.days_left !== null && t.days_left >= 0 ? t.days_left : null)) },
  { label: 'Bid opens', cell: (t) => fmtDate(t.opening, true) },
  { label: 'Fit with your profile', cell: (t) => (t.fit ? `${t.fit}/5${t.fit_reason ? `: ${t.fit_reason}` : ''}` : '-'), best: highest((t) => t.fit) },
];

/** Two to four tenders side by side. The best value in a row is marked, only where one is clearly "better" (cheaper EMD, more time, better fit). */
export function CompareDrawer({ keys, onClose, onOpenBid }: { keys: string[]; onClose: () => void; onOpenBid: (id: number) => void }) {
  const [rows, setRows] = useState<CompareTender[] | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let stop = false;
    api.compareTenders(keys).then((r) => { if (!stop) setRows(r.tenders); }, (e: Error) => { if (!stop) setError(e.message); });
    return () => { stop = true; };
  }, [keys.join('|')]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Drawer title={`Compare ${keys.length} tenders`} onClose={onClose} wide>
      {error && <div className="alert bad">{error}</div>}
      {!rows && !error && <Skeleton h={200} />}
      {rows && (
        <div style={{ overflowX: 'auto' }} tabIndex={0} role="region" aria-label="Comparison table">
          <table className="tbl cmp">
            <thead>
              <tr><th scope="col"><span className="sr-only">Detail</span></th>{rows.map((t) => <th scope="col" key={t.key}>{t.tender_id}{t.bid_id && <div><button type="button" className="link-btn" onClick={() => onOpenBid(t.bid_id!)}>Open bid</button></div>}</th>)}</tr>
            </thead>
            <tbody>
              {ROWS.map((r) => {
                const best = r.best?.(rows) ?? null;
                return (
                  <tr key={r.label}>
                    <th scope="row" className="muted small">{r.label}</th>
                    {rows.map((t, i) => <td key={t.key} className={best === i ? 'best' : ''}>{r.cell(t)}{best === i && <span className="sr-only"> (best in this row)</span>}</td>)}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted small" style={{ marginTop: 10 }}>Bold means the better figure in that row: the smaller EMD, the larger value, the more time or the better fit. It is a prompt to look, not a recommendation.</p>
    </Drawer>
  );
}
