import { tr } from '../i18n';
import { ScrollX } from '../ScrollX';
import { ErrorNote } from '../ui';
import { useEffect, useState } from 'react';
import { api } from '../api';
import { duration, fmtDate } from '../format';
import { useLoad } from '../hooks';
import { ReadOnly } from '../session';
import type { AppConfig, Run } from '../types';

export function RunsPage({ config }: { config: AppConfig }) {
  const history = useLoad(() => api.runs(), []);
  const [portals, setPortals] = useState<string[]>(config.portals.map((p) => p.key));
  const [sendEmail, setSendEmail] = useState(false);
  const [watching, setWatching] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [starting, setStarting] = useState(false);

  // If a run is already going (started earlier, or by the schedule), pick it up.
  useEffect(() => { if (history.data?.running && watching === null) setWatching(history.data.running); }, [history.data, watching]);

  async function start() {
    setError('');
    setStarting(true);
    try {
      const { id } = await api.startRun(portals, sendEmail);
      setWatching(id);
      history.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  }

  const toggle = (key: string) => setPortals((p) => (p.includes(key) ? p.filter((x) => x !== key) : [...p, key]));
  const busy = history.data?.running != null;

  return (
    <>
      <div className="page-head"><div><h1>{tr('Runs')}</h1><p className="sub">{tr('Collect the latest tenders now, or see what the daily run did.')}</p></div></div>

      <ReadOnly>
      <section className="card" style={{ marginBottom: 14 }}>
        <h2>{tr('Collect tenders now')}</h2>
        <p className="sub">{tr('Reads the portals for tenders you have not seen yet. The first run can take 20 to 40 minutes; later ones are shorter.')}</p>
        {error && <div className="alert bad" role="alert">{error}</div>}
        <fieldset style={{ border: 0, padding: 0, margin: '0 0 8px' }}>
          <legend style={{ fontWeight: 600, marginBottom: 4 }}>Portals</legend>
          {config.portals.map((p) => (
            <label key={p.key} className="check"><input type="checkbox" checked={portals.includes(p.key)} onChange={() => toggle(p.key)} />{p.name}</label>
          ))}
        </fieldset>
        <label className="check">
          <input type="checkbox" checked={sendEmail} onChange={(e) => setSendEmail(e.target.checked)} disabled={!config.mail_configured} />
          Also email the digest to the {config.recipient_count} recipient{config.recipient_count === 1 ? '' : 's'}
        </label>
        {!config.mail_configured && <p className="sub" style={{ marginTop: 0 }}>{tr('Emailing is not switched on yet. Ask whoever installed TendAI to set it up (the README explains how).')}</p>}
        <div style={{ marginTop: 12 }}>
          <button className="btn primary" onClick={start} disabled={starting || busy || portals.length === 0}>
            {busy ? <><span className="spinner" /> {tr('A run is in progress')}</> : starting ? tr('Starting…') : tr('Collect tenders now')}
          </button>
        </div>
      </section>
      </ReadOnly>

      {watching !== null && <Live id={watching} onDone={() => { history.reload(); }} />}

      <section className="card" style={{ marginTop: 14 }}>
        <h2>History</h2>
        <p className="sub">The last 30 runs. Select one for its log.</p>
        <ErrorNote error={history.error} onRetry={history.reload} />
        {history.data && history.data.runs.length === 0 && <div className="empty">No runs yet.</div>}
        <ScrollX label="Run history">
          <table className="tbl">
            <thead><tr><th>Started</th><th>Type</th><th>Status</th><th className="num">New civil</th><th className="num">Emailed</th><th className="num">Took</th></tr></thead>
            <tbody>
              {history.data?.runs.map((r) => (
                <tr key={r.id} className="click" onClick={() => setWatching(r.id)} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter') setWatching(r.id); }}>
                  <td>{fmtDate(r.started, true)}</td>
                  <td>{r.trigger === 'scheduled' ? 'Daily' : 'Manual'}</td>
                  <td><StatusBadge run={r} />{r.status === 'failed' && r.error && <div className="muted small" title={r.error}>{r.error.slice(0, 80)}</div>}</td>
                  <td className="num">{r.new_civil}</td>
                  <td className="num">{r.send_email ? r.emailed_count : '-'}</td>
                  <td className="num">{duration(r.started, r.finished)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollX>
      </section>
    </>
  );
}

function StatusBadge({ run }: { run: Run }) {
  if (run.status === 'running') return <span className="badge warn">Running</span>;
  if (run.status === 'failed') return <span className="badge bad">Failed</span>;
  const problems = run.reports.filter((r) => r.failed).length;
  return problems ? <span className="badge warn">Done, {problems} portal{problems > 1 ? 's' : ''} down</span> : <span className="badge ok">Succeeded</span>;
}

function Live({ id, onDone }: { id: number; onDone: () => void }) {
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    let finishedReported = false;
    const poll = async () => {
      try {
        const r = await api.run(id);
        if (stop) return;
        setRun(r);
        setError('');
        if (r.status !== 'running') { if (!finishedReported) { finishedReported = true; onDone(); } return; }
      } catch (e) {
        if (!stop) setError((e as Error).message);
      }
      timer = setTimeout(poll, 2000);
    };
    setRun(null);
    poll();
    return () => { stop = true; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  if (!run) return <section className="card">{error ? <div className="alert bad">{error}</div> : <span className="spinner" />}</section>;
  const lines = (run.log ?? []).join('\n');
  return (
    <section className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
        <div>
          <h2>Run #{run.id} <StatusBadge run={run} /></h2>
          <p className="sub" style={{ margin: 0 }}>Started {fmtDate(run.started, true)} · {duration(run.started, run.finished)}{run.status === 'running' && <> · <span className="spinner" /></>}</p>
        </div>
        <div style={{ textAlign: 'right' }}><strong>{run.new_civil}</strong> new civil{run.send_email && <> · <strong>{run.emailed_count}</strong> emailed</>}</div>
      </div>
      {error && <div className="alert warn">Lost touch with the server for a moment: {error}. Still trying.</div>}
      {run.error && <div className="alert bad">{run.error}</div>}
      {run.reports.length > 0 && (
        <ScrollX label="Run details" style={{ marginBottom: 10 }}>
          <table className="tbl">
            <thead><tr><th>Portal</th><th className="num">Departments</th><th className="num">Listed</th><th className="num">New civil</th><th>Problems</th></tr></thead>
            <tbody>
              {run.reports.map((r) => (
                <tr key={r.portal}><td>{r.name}</td><td className="num">{r.departments}</td><td className="num">{r.listed}</td><td className="num">{r.new_civil}</td>
                  <td>{r.errors.length === 0 ? '-' : <span className="badge bad" title={r.errors.join('\n')}>{r.errors[0].slice(0, 70)}</span>}</td></tr>
              ))}
            </tbody>
          </table>
        </ScrollX>
      )}
      <div className="log" role="log" aria-live="off" tabIndex={0}>{lines || 'Waiting for the first message…'}</div>
    </section>
  );
}
