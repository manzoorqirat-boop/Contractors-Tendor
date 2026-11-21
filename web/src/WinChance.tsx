import { api } from './api';
import { useLoad } from './hooks';
import type { WinChance as WinData } from './types';

const sgn = (n: number) => (n > 0 ? 'raises it' : 'lowers it');

/** The chance of winning, learned from your own decided bids. A number appears only once there is enough history and it has beaten your overall win rate in testing. */
export function WinChanceCard({ bidId }: { bidId?: number }) {
  const { data, error } = useLoad<WinData>(() => (bidId ? api.winChance(bidId) : api.winModel()), [bidId]);
  if (error || !data || data.applies === false) return null;
  const overall = data.overall_pct === null ? null : `${data.overall_pct}%`;
  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <h2>Win chance</h2>
      {data.state === 'too_few' && (
        <>
          <p>Not enough history to say yet: <strong>{data.decided} of {data.need}</strong> decided bids recorded{data.decided > 0 ? ` (${data.won} won${overall ? `, ${overall} overall` : ''})` : ''}.</p>
          <p className="sub">A personal score appears after {data.need} won or lost bids, and only if it beats simply using your overall win rate when tested on your own past bids. Mark bids Won or Lost, with the prices, to build it.</p>
        </>
      )}
      {data.state === 'no_edge' && (
        <>
          <p>Your {data.decided} decided bids do not yet show what makes you win: a score built from them was <strong>no better than your overall win rate{overall ? ` of ${overall}` : ''}</strong> when tested, so none is shown.</p>
          <p className="sub">That is a result, not a failure: it can change as more bids are decided.</p>
        </>
      )}
      {data.state === 'ready' && (
        <>
          {data.score_pct !== null && bidId ? (
            <>
              <p style={{ margin: '0 0 4px' }}><span style={{ fontSize: 'var(--fs-2xl)', fontWeight: 700 }}>{data.score_pct}%</span> chance of winning this bid{overall ? <span className="muted"> (your overall win rate is {overall})</span> : null}</p>
              {data.parts.length > 0 && (
                <ul className="small" style={{ margin: '4px 0 8px', paddingLeft: 18 }}>
                  {data.parts.map((p) => <li key={`${p.feature}-${p.value}`}>{p.feature[0].toUpperCase() + p.feature.slice(1)} “{p.value}”: you won {p.wins} of {p.of} such bids, which {sgn(p.shift)}.</li>)}
                </ul>
              )}
            </>
          ) : <p>The model has earned a score on your history.</p>}
          {data.test && data.test.brier_model !== undefined && (
            <p className="muted small">Tested on {data.test.tested} of your past bids, each predicted only from the ones before it: its predictions were closer to what happened than your overall win rate (error {data.test.brier_model} against {data.test.brier_baseline}; lower is better).</p>
          )}
        </>
      )}
      <p className="muted small" style={{ marginTop: 8 }}>A hint from your own past bids, not a promise. It cannot see who else is bidding or what the department wants this year, and it only knows the bids you chose to make.</p>
    </section>
  );
}
