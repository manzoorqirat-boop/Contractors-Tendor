import { api } from './api';
import { useLoad } from './hooks';
import { tr } from './i18n';
import type { WinChance as WinData } from './types';

/** The chance of winning, learned from your own decided bids. A number appears only once there is enough history and it has beaten your overall win rate in testing. */
export function WinChanceCard({ bidId }: { bidId?: number }) {
  const { data, error } = useLoad<WinData>(() => (bidId ? api.winChance(bidId) : api.winModel()), [bidId]);
  if (error || !data || data.applies === false) return null;
  const overall = data.overall_pct === null ? null : `${data.overall_pct}%`;
  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <h2>{tr('Win chance')}</h2>
      {data.state === 'too_few' && (
        <>
          <p>{tr('Not enough history to say yet: {decided} of {need} decided bids recorded.', { decided: data.decided, need: data.need })}{data.decided > 0 ? ' ' + (overall ? tr('{won} won, {overall} overall.', { won: data.won, overall }) : tr('{won} won.', { won: data.won })) : ''}</p>
          <p className="sub">{tr('A personal score appears after {need} won or lost bids, and only if it beats simply using your overall win rate when tested on your own past bids. Mark bids Won or Lost, with the prices, to build it.', { need: data.need })}</p>
        </>
      )}
      {data.state === 'no_edge' && (
        <>
          <p>{overall ? tr('Your {decided} decided bids do not yet show what makes you win: a score built from them was no better than your overall win rate of {overall} when tested, so none is shown.', { decided: data.decided, overall }) : tr('Your {decided} decided bids do not yet show what makes you win: a score built from them was no better than your overall win rate when tested, so none is shown.', { decided: data.decided })}</p>
          <p className="sub">{tr('That is a result, not a failure: it can change as more bids are decided.')}</p>
        </>
      )}
      {data.state === 'ready' && (
        <>
          {data.score_pct !== null && bidId ? (
            <>
              <p style={{ margin: '0 0 4px' }}><span style={{ fontSize: 'var(--fs-2xl)', fontWeight: 700 }}>{data.score_pct}%</span> {tr('chance of winning this bid')}{overall ? <span className="muted"> {tr('(your overall win rate is {overall})', { overall })}</span> : null}</p>
              {data.parts.length > 0 && (
                <ul className="small" style={{ margin: '4px 0 8px', paddingLeft: 18 }}>
                  {data.parts.map((p) => <li key={`${p.feature}-${p.value}`}>{(() => { const v = { feature: p.feature[0].toUpperCase() + p.feature.slice(1), value: p.value, won: p.wins, of: p.of }; return p.shift > 0 ? tr('{feature} “{value}”: you won {won} of {of} such bids, which raises it.', v) : tr('{feature} “{value}”: you won {won} of {of} such bids, which lowers it.', v); })()}</li>)}
                </ul>
              )}
            </>
          ) : <p>{tr('The model has earned a score on your history.')}</p>}
          {data.test && data.test.brier_model !== undefined && (
            <p className="muted small">{tr('Tested on {tested} of your past bids, each predicted only from the ones before it: its predictions were closer to what happened than your overall win rate (error {model} against {baseline}; lower is better).', { tested: data.test.tested, model: data.test.brier_model, baseline: data.test.brier_baseline ?? '' })}</p>
          )}
        </>
      )}
      <p className="muted small" style={{ marginTop: 8 }}>{tr('A hint from your own past bids, not a promise. It cannot see who else is bidding or what the department wants this year, and it only knows the bids you chose to make.')}</p>
    </section>
  );
}
