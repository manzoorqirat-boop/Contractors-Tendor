import { api } from './api';
import { fmtDate, inr } from './format';
import { useLoad } from './hooks';
import { tr } from './i18n';
import { ScrollX } from './ScrollX';
import type { Forecast as ForecastData } from './types';

/** What to expect: how many new civil tenders are likely in the next four weeks (tested on the app's own history), and which tenders come round again about this time each year. */
export function ForecastCard() {
  const { data: f, error } = useLoad<ForecastData>(() => api.forecast(), []);
  if (error || !f) return null;
  const t = f.total;
  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <h2>{tr('What to expect')}</h2>
      {!t && (
        <p>{tr('Not enough history to forecast yet. The app has been collecting for {days} days ({weeks} full weeks); it needs about 6 full weeks of daily runs, and a year before it can allow for the seasons.', { days: f.history_days, weeks: f.weeks_observed })}</p>
      )}
      {t && (
        <>
          {f.enough ? (
            <p style={{ margin: '0 0 4px' }}>{tr('About')} <strong style={{ fontSize: 'var(--fs-xl)' }}>{t.expected}</strong> {tr('new civil tenders in the next four weeks')}{t.low !== null && t.high !== null ? <> {tr('(likely between {low} and {high})', { low: t.low, high: t.high })}</> : null}{tr(', roughly {perWeek} a week. The last four weeks had {recent}.', { perWeek: t.per_week, recent: f.recent_four_weeks })}</p>
          ) : (
            <p style={{ margin: '0 0 4px' }}>{tr('The last four weeks had {recent} new civil tenders. The forecast method (about {expected} for the next four) has not yet beaten simply expecting the same as lately on this history, so treat it as no better than that.', { recent: f.recent_four_weeks, expected: t.expected })}</p>
          )}
          {f.backtest && f.backtest.mae !== undefined && (
            <p className="muted small">{tr('Tested on {tested} past four-week windows, each predicted only from the weeks before it: off by {mae} tenders on average, against {naive} for repeating the last four weeks.', { tested: f.backtest.tested, mae: f.backtest.mae, naive: f.backtest.naive_mae ?? '' })} {f.season_used ? tr('The time of year is allowed for.') : tr('The time of year is not allowed for until there is a year of history.')}</p>
          )}
          {f.departments.length > 0 && (
            <ScrollX label={tr('Expected tenders by department')}>
              <table className="tbl">
                <thead><tr><th>{tr('Department')}</th><th className="num">{tr('Expected, next 4 weeks')}</th><th className="num">{tr('Last 4 weeks')}</th></tr></thead>
                <tbody>{f.departments.map((d) => <tr key={d.dept}><td>{d.dept}</td><td className="num">{d.expected}</td><td className="num">{d.last_four_weeks}</td></tr>)}</tbody>
              </table>
            </ScrollX>
          )}
        </>
      )}
      {f.coming_round.length > 0 && (
        <>
          <h3 style={{ margin: '14px 0 4px' }}>{tr('Likely to come round again')}</h3>
          <p className="sub">{tr('Tendered about this time last year, with nothing like it seen from the same department this season. Worth watching for; not a promise.')}</p>
          <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
            {f.coming_round.map((r) => <li key={`${r.dept}-${r.title}`}><strong>{r.title}</strong> <span className="muted">({r.dept}; {tr('last year')} {fmtDate(r.last_year)}{r.value ? `, ${inr(r.value)}` : ''})</span></li>)}
          </ul>
        </>
      )}
      <p className="muted small" style={{ marginTop: 8 }}>{tr('Weeks when the app was not collecting are left out, not counted as quiet. Counts are of civil tenders the app kept, so they follow your portal and filter settings.')}</p>
    </section>
  );
}
