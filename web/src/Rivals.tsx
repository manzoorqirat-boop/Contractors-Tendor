import { api } from './api';
import { fmtDate } from './format';
import { useLoad } from './hooks';
import { tr } from './i18n';
import { ScrollX } from './ScrollX';
import type { Rivals as RivalsData } from './types';

/** Who has won tenders like this one: from your bidder lists and from imported awards that name the winner. Shown only when there are enough results to show a pattern. */
export function RivalsCard({ bidId }: { bidId: number }) {
  const { data, error } = useLoad<RivalsData>(() => api.rivals(bidId), [bidId]);
  if (error || !data) return null;
  if (data.rows.length === 0) {
    return (
      <section className="card" style={{ marginBottom: 14 }}>
        <h2>{tr('Who usually wins here')}</h2>
        <p className="muted">{tr('Not enough winners on record yet.')}{data.total ? ` ${tr('({total} so far)', { total: data.total })}` : ''} {tr('Record who bid on your tenders (Result tab, Who bid), or import award results that name the winner (Bids, Results view, Price history: a column such as “Awarded to”). Three or more are needed to show a pattern.')}</p>
      </section>
    );
  }
  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <h2>{tr('Who usually wins here')}</h2>
      <p className="sub">{tr('Among {total} {basis} on record ({own} from your own bids, {market} imported). “Usually wins at” is the middle of their winning prices against the estimate; a negative number is below it.', { total: data.total, basis: data.basis, own: data.own, market: data.market })}</p>
      {data.top3_share_pct !== null && <p className="small">{tr('The three most frequent winners took {pct}% of these awards.', { pct: data.top3_share_pct })}{data.top3_share_pct >= 60 ? ' ' + tr('A few firms winning most of the work is worth knowing before you bid; it can mean they are good at it, local, or well matched to this buyer.') : ''}</p>}
      <ScrollX label={tr('Who usually wins here')}>
        <table className="tbl">
          <thead><tr><th>{tr('Company')}</th><th className="num">{tr('Wins')}</th><th className="num">{tr('Share')}</th><th className="num">{tr('Usually wins at')}</th><th>{tr('Last win')}</th><th>{tr('Against you')}</th></tr></thead>
          <tbody>
            {data.rows.map((r) => (
              <tr key={r.key}>
                <td><strong>{r.name}</strong>{r.mine && <span className="badge ok" style={{ marginLeft: 6 }}>{tr('You')}</span>}</td>
                <td className="num">{r.wins}</td><td className="num">{r.share_pct}%</td>
                <td className="num">{r.usual_vs_estimate === null ? <span className="muted">-</span> : `${r.usual_vs_estimate > 0 ? '+' : ''}${r.usual_vs_estimate}%`}{r.priced > 0 && <div className="muted small">{r.priced === 1 ? tr('{n} price', { n: 1 }) : tr('{n} prices', { n: r.priced })}</div>}</td>
                <td className="muted small">{r.last ? fmtDate(r.last) : '-'}</td>
                <td className="small">{r.mine ? <span className="muted">-</span> : r.met > 0 ? tr('You finished ahead in {ahead} of {met}', { ahead: r.ahead, met: r.met }) : <span className="muted">{tr('Not met yet')}</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </ScrollX>
      <p className="muted small" style={{ marginTop: 8 }}>{tr('This shows who won, not why. It is only as complete as the awards on record.')}</p>
    </section>
  );
}
