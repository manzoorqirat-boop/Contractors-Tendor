import { api } from './api';
import { fmtDate } from './format';
import { useLoad } from './hooks';
import { ScrollX } from './ScrollX';
import type { Rivals as RivalsData } from './types';

/** Who has won tenders like this one: from your bidder lists and from imported awards that name the winner. Shown only when there are enough results to show a pattern. */
export function RivalsCard({ bidId }: { bidId: number }) {
  const { data, error } = useLoad<RivalsData>(() => api.rivals(bidId), [bidId]);
  if (error || !data) return null;
  if (data.rows.length === 0) {
    return (
      <section className="card" style={{ marginBottom: 14 }}>
        <h2>Who usually wins here</h2>
        <p className="muted">Not enough winners on record yet{data.total ? ` (${data.total} so far)` : ''}. Record who bid on your tenders (Result tab, Who bid), or import award results that name the winner (Bids, Results view, Price history: a column such as “Awarded to”). Three or more are needed to show a pattern.</p>
      </section>
    );
  }
  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <h2>Who usually wins here</h2>
      <p className="sub">Among {data.total} {data.basis} on record ({data.own} from your own bids, {data.market} imported). “Usually wins at” is the middle of their winning prices against the estimate; a negative number is below it.</p>
      {data.top3_share_pct !== null && <p className="small">The three most frequent winners took {data.top3_share_pct}% of these awards.{data.top3_share_pct >= 60 ? ' A few firms winning most of the work is worth knowing before you bid; it can mean they are good at it, local, or well matched to this buyer.' : ''}</p>}
      <ScrollX label="Who usually wins here">
        <table className="tbl">
          <thead><tr><th>Company</th><th className="num">Wins</th><th className="num">Share</th><th className="num">Usually wins at</th><th>Last win</th><th>Against you</th></tr></thead>
          <tbody>
            {data.rows.map((r) => (
              <tr key={r.key}>
                <td><strong>{r.name}</strong>{r.mine && <span className="badge ok" style={{ marginLeft: 6 }}>You</span>}</td>
                <td className="num">{r.wins}</td><td className="num">{r.share_pct}%</td>
                <td className="num">{r.usual_vs_estimate === null ? <span className="muted">-</span> : `${r.usual_vs_estimate > 0 ? '+' : ''}${r.usual_vs_estimate}%`}{r.priced > 0 && <div className="muted small">{r.priced} price{r.priced === 1 ? '' : 's'}</div>}</td>
                <td className="muted small">{r.last ? fmtDate(r.last) : '-'}</td>
                <td className="small">{r.mine ? <span className="muted">-</span> : r.met > 0 ? `You finished ahead in ${r.ahead} of ${r.met}` : <span className="muted">Not met yet</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </ScrollX>
      <p className="muted small" style={{ marginTop: 8 }}>This shows who won, not why. It is only as complete as the awards on record.</p>
    </section>
  );
}
