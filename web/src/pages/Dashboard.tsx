import { api } from '../api';
import type { Tab } from '../App';
import { duration, fmtDate, fmtDay, headline, inr } from '../format';
import { useLoad } from '../hooks';
import type { AppConfig, Filters } from '../types';
import { BarBlock } from './Charts';

interface Props { config: AppConfig; go: (t: Tab, f?: Partial<Filters>) => void }

export function Dashboard({ config, go }: Props) {
  const { data: s, error, loading } = useLoad(() => api.stats(), []);
  if (error) return <div className="alert bad">{error}</div>;
  if (!s) return <div className="empty"><span className="spinner" /> Loading…</div>;

  const k = s.kpi;
  const last = s.last_run;

  if (k.total_kept === 0) {
    return (
      <div className="card empty" style={{ padding: 40 }}>
        <h2 style={{ marginBottom: 6 }}>No tenders yet</h2>
        <p className="sub" style={{ marginBottom: 16 }}>Nothing has been collected so far. The first run reads every recent tender, so it can take 20 to 40 minutes.</p>
        <button className="btn primary" onClick={() => go('runs')}>Go to Runs</button>
      </div>
    );
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <p className="sub">Civil tenders from {config.portals.length} portals, read daily.</p>
        </div>
        <button className="btn primary" onClick={() => go('runs')}>Run now</button>
      </div>

      <div className="grid kpis">
        <Kpi label="Open civil tenders" value={k.open.toLocaleString('en-IN')} note={`${k.total_kept.toLocaleString('en-IN')} collected in all`} onClick={() => go('tenders', { status: 'open' })} />
        <Kpi label="New today" value={k.new_today.toLocaleString('en-IN')} note="first seen since midnight" onClick={() => go('tenders', { status: 'open', sort: 'first_seen' })} />
        <Kpi label={`Closing in ${s.soon_days} days`} value={k.closing_soon.toLocaleString('en-IN')} note="bid soon" tone="warn" onClick={() => go('tenders', { status: 'closing_soon' })} />
        <Kpi label="Value of open tenders" value={inr(k.open_value)} note="where a value is stated" />
        <div className="card kpi">
          <div className="label">Last run</div>
          {last ? (
            <>
              <div className="num" style={{ fontSize: 18, marginTop: 6 }}>
                <span className={`badge ${last.status === 'ok' ? 'ok' : last.status === 'failed' ? 'bad' : 'warn'}`}>{last.status === 'ok' ? 'Succeeded' : last.status === 'failed' ? 'Failed' : 'Running'}</span>
              </div>
              <div className="note">{fmtDate(last.started, true)} · {duration(last.started, last.finished)}</div>
            </>
          ) : <div className="note">Never run</div>}
        </div>
      </div>

      <div className="grid cols-2">
        <section className="card">
          <h2>New tenders per day</h2>
          <p className="sub">Civil tenders first seen, last 30 days</p>
          <BarBlock data={s.new_per_day} category="day" value="count" color="var(--c1)" label="New tenders" formatCategory={fmtDay} />
        </section>
        <section className="card">
          <h2>Closing in the next 14 days</h2>
          <p className="sub">Open civil tenders by closing date</p>
          <BarBlock data={s.closing_per_day} category="day" value="count" color="var(--c3)" label="Closing" formatCategory={fmtDay} />
        </section>
      </div>

      <div className="grid cols-2">
        <section className="card">
          <h2>Open tenders by portal</h2>
          <p className="sub">Number of open civil tenders</p>
          <BarBlock data={s.by_portal} category="short" value="count" color="var(--c2)" label="Open tenders" horizontal height={Math.max(160, s.by_portal.length * 52)} />
        </section>
        <section className="card">
          <h2>By category</h2>
          <p className="sub">As labelled on the portal</p>
          <BarBlock data={s.by_category} category="category" value="count" color="var(--c4)" label="Open tenders" horizontal height={Math.max(180, s.by_category.length * 30)} />
        </section>
      </div>

      <div className="grid cols-2">
        <section className="card">
          <h2>By estimated value</h2>
          <p className="sub">Size of the work, in rupees</p>
          <BarBlock data={s.by_value} category="band" value="count" color="var(--c5)" label="Open tenders" interval={0} />
        </section>
        <section className="card">
          <h2>Biggest open tenders</h2>
          <p className="sub">By estimated value</p>
          <div className="scroll-x">
            <table className="tbl">
              <tbody>
                {s.biggest.map((t) => (
                  <tr key={t.key} className="click" onClick={() => go('tenders', { q: t.tender_id, status: 'open' })}>
                    <td><div style={{ fontWeight: 600 }}>{headline(t).slice(0, 110)}{headline(t).length > 110 ? '…' : ''}</div><div className="sub" style={{ fontSize: 12 }}>{t.portal_name} · closes {fmtDate(t.closing)}</div></td>
                    <td className="num" style={{ whiteSpace: 'nowrap', fontWeight: 700 }}>{inr(t.value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <section className="card">
        <h2>Departments with the most open tenders</h2>
        <p className="sub">Top 10 across all portals</p>
        <div className="scroll-x">
          <table className="tbl">
            <thead><tr><th>Department</th><th>Portal</th><th className="num">Open tenders</th><th className="num">Value</th></tr></thead>
            <tbody>
              {s.departments.map((d) => (
                <tr key={d.portal + d.department} className="click" onClick={() => go('tenders', { q: d.department, portal: d.portal, status: 'open' })}>
                  <td>{d.department}</td><td>{d.portal_name}</td><td className="num">{d.count}</td><td className="num">{d.value > 0 ? inr(d.value) : '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      {loading && null}
    </>
  );
}

function Kpi({ label, value, note, onClick, tone }: { label: string; value: string; note?: string; onClick?: () => void; tone?: 'warn' }) {
  const body = (
    <>
      <div className="label">{label}</div>
      <div className="num" style={tone === 'warn' ? { color: 'var(--warn)' } : undefined}>{value}</div>
      {note && <div className="note">{note}</div>}
    </>
  );
  return onClick ? (
    <button className="card kpi" style={{ textAlign: 'left', cursor: 'pointer', font: 'inherit', color: 'inherit' }} onClick={onClick}>{body}</button>
  ) : <div className="card kpi">{body}</div>;
}
