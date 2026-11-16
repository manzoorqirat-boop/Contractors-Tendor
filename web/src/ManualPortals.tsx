import { ScrollX } from './ScrollX';
import { useState } from 'react';
import { api } from './api';
import { fmtDate } from './format';
import { useLoad } from './hooks';
import { Only } from './session';
import { useConfirm } from './confirm';
import type { PortalStatus } from './types';
import { tr } from './i18n';

const tone = (k: PortalStatus['kind']) => (k === 'ok' ? 'ok' : k === 'unknown' ? 'plain' : 'warn');
type Msg = { tone: 'ok' | 'bad' | 'warn'; text: string } | null;

/** Short notice for the Dashboard: which portals the app could not read and so need checking by hand. */
export function ManualBanner({ onOpen }: { onOpen: () => void }) {
  const { data } = useLoad(() => api.portals(), []);
  const manual = data?.manual ?? [];
  if (manual.length === 0) return null;
  return (
    <div className="alert warn" role="status" style={{ display: 'flex', gap: 12, alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap' }}>
      <span>
        <strong>{manual.length} portal{manual.length > 1 ? 's' : ''} to check by hand:</strong>{' '}
        {manual.map((p) => `${p.name} (${p.label})`).join(', ')}. The app cannot read {manual.length > 1 ? 'them' : 'it'} on its own, so nothing from {manual.length > 1 ? 'them' : 'it'} is in the numbers below.
      </span>
      <span style={{ display: 'flex', gap: 8 }}>
        <Only role="bidder"><button className="btn small" onClick={() => window.dispatchEvent(new CustomEvent('add-tender'))}>Add a tender by hand</button></Only>
        <button className="btn small" onClick={onOpen}>See details</button>
      </span>
    </div>
  );
}

/** Add portals, see which ones the app can read, and keep the list of those that need a person. */
export function PortalsManager({ onChange }: { onChange: () => void }) {
  const confirm = useConfirm();
  const list = useLoad(() => api.portals(), []);
  const [form, setForm] = useState({ name: '', state: '', base_url: '' });
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<Msg>(null);
  const portals = list.data?.portals ?? [];
  const manual = list.data?.manual ?? [];

  async function add() {
    setBusy('add');
    setMsg(null);
    try {
      const p = await api.addPortal(form);
      setForm({ name: '', state: '', base_url: '' });
      setMsg(p.kind === 'ok'
        ? { tone: 'ok', text: `${p.name} added. The app can read it (${p.departments} departments, ${p.tenders.toLocaleString('en-IN')} open tenders) and it is switched on.` }
        : { tone: 'warn', text: `${p.name} added to the check-by-hand list. ${p.label}: ${p.detail || p.advice}` });
      list.reload();
      onChange();
    } catch (err) {
      setMsg({ tone: 'bad', text: (err as Error).message });
    } finally {
      setBusy(null);
    }
  }

  async function check(keys: string[]) {
    setBusy(keys.length === 1 ? keys[0] : 'all');
    setMsg(null);
    try {
      for (const key of keys) await api.checkPortal(key); // one at a time: each is a polite request to a government server
      list.reload();
    } catch (err) {
      setMsg({ tone: 'bad', text: (err as Error).message });
    } finally {
      setBusy(null);
    }
  }

  async function remove(p: PortalStatus) {
    if (!(await confirm({ title: `Remove ${p.name}?`, body: 'The portal is no longer read. Tenders already collected from it stay in the app.', confirmLabel: 'Remove portal', danger: true }))) return;
    setBusy(p.key);
    setMsg(null);
    try {
      await api.removePortal(p.key);
      list.reload();
      onChange();
    } catch (err) {
      setMsg({ tone: 'bad', text: (err as Error).message });
    } finally {
      setBusy(null);
    }
  }

  const disabled = busy !== null;
  return (
    <>
      <section className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <div>
            <h2>Check these by hand</h2>
            <p className="sub">Portals the app could not read on its own. It never tries to get past a captcha, a login or an OTP, so for these you open the portal yourself.</p>
          </div>
          <button type="button" className="btn small" onClick={() => check(portals.map((p) => p.key))} disabled={disabled || portals.length === 0}>
            {busy === 'all' ? <><span className="spinner" /> Checking…</> : 'Check all portals now'}
          </button>
        </div>
        {list.error && <div className="alert bad">{list.error}</div>}
        {list.loading && !list.data && <span className="spinner" />}
        {list.data && manual.length === 0 && (
          <p className="sub" style={{ margin: 0 }}>
            {portals.some((p) => p.kind === 'unknown') ? 'Some portals have not been checked yet. Press “Check all portals now”.' : 'The app can read every portal in the list.'}
          </p>
        )}
        {manual.map((p) => (
          <div key={p.key} style={{ padding: '10px 0', borderTop: '1px solid var(--border)' }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <strong>{p.name}</strong> <span className={`badge ${tone(p.kind)}`}>{p.label}</span>
            </div>
            <p style={{ margin: '4px 0' }}>{p.advice}</p>
            {p.detail && <p className="sub" style={{ margin: '0 0 6px' }}>{p.detail}</p>}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <a className="btn small" href={p.base_url} target="_blank" rel="noopener noreferrer">Open the portal</a>
              <a className="btn small" href={`/api/portals/${p.key}/sample`} download title="Saves what the portal returns to this server, so a reader can be written or corrected from it">Download a sample</a>
              <button type="button" className="btn small" onClick={() => check([p.key])} disabled={disabled}>{busy === p.key ? tr('Checking…') : 'Check again'}</button>
            </div>
          </div>
        ))}
      </section>

      <section className="card" style={{ marginBottom: 14 }}>
        <h2>All portals</h2>
        <p className="sub">What the app found the last time it looked: when a run reads a portal, or when you press Check.</p>
        <ScrollX label="Portals">
          <table className="tbl">
            <thead><tr><th>Portal</th><th>Status</th><th className="num">Departments</th><th>Last checked</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {portals.map((p) => (
                <tr key={p.key}>
                  <td>{p.name}{p.custom && <span className="badge plain" style={{ marginLeft: 6 }}>Added by you</span>}</td>
                  <td><span className={`badge ${tone(p.kind)}`} title={p.detail || undefined}>{p.label}</span></td>
                  <td className="num">{p.kind === 'ok' ? p.departments : '-'}</td>
                  <td>{p.checked ? fmtDate(p.checked, true) : '-'}</td>
                  <td style={{ whiteSpace: 'nowrap', textAlign: 'right' }}>
                    <button type="button" className="btn small" onClick={() => check([p.key])} disabled={disabled}>{busy === p.key ? tr('Checking…') : 'Check'}</button>
                    {p.custom && <> <button type="button" className="btn small" onClick={() => remove(p)} disabled={disabled}>Remove</button></>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollX>
      </section>

      <PortalCatalogue onDone={() => { list.reload(); onChange(); }} />

      <section className="card" style={{ marginBottom: 14 }}>
        <h2>Add a portal</h2>
        <p className="sub">Paste the portal's address. The app checks it straight away: if it can read it, it is switched on; if not, it goes on the check-by-hand list with the reason.</p>
        {msg && <div className={`alert ${msg.tone}`} role="alert">{msg.text}</div>}
        <details style={{ marginBottom: 12 }}>
          <summary>{tr('About GeM, IREPS and nProcure')}</summary>
          <ul className="muted small" style={{ margin: '6px 0 6px 18px', padding: 0 }}>
            <li><strong>GeM</strong> has its own reader, written without access to the live site. Add and check says whether it works; if it fails, <em>Download a sample</em> on the portal's row saves what GeM returned so the reader can be corrected. GeM lists goods and services too: only civil work is kept, and GeM bids show no estimated value.</li>
            <li><strong>IREPS</strong> needs a mobile-OTP login for its tender lists. The app never gets past a login, captcha or OTP, so it will go on the check-by-hand list; bring a tender in with <em>Add a tender</em>.</li>
            <li><strong>nProcure (Gujarat)</strong> is different software with no reader yet, and its address ends in .com, so your administrator must add <code>.nprocure.com</code> to <code>PORTAL_HOST_SUFFIXES</code> first. After adding it, <em>Download a sample</em> gives what a reader needs to be written.</li>
          </ul>
</details>
        {/* A div, not a form: this card sits inside the Settings form, and a nested form would also save the settings. */}
        <div onKeyDown={(e) => { if (e.key === 'Enter' && !disabled) { e.preventDefault(); add(); } }}>
          <label className="field">Name
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Kerala (etenders)" maxLength={80} required />
          </label>
          <label className="field">State or body <span className="hint">Optional. Shown in charts. Defaults to the name.</span>
            <input value={form.state} onChange={(e) => setForm({ ...form, state: e.target.value })} placeholder="Kerala" maxLength={40} />
          </label>
          <label className="field">Address
            <span className="hint">Must start with https:// and end in .gov.in or .nic.in. For portals like Maharashtra's, include the part after the site name, such as /nicgep.</span>
            <input value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} placeholder="https://etenders.kerala.gov.in/nicgep" inputMode="url" autoCapitalize="none" autoCorrect="off" required />
          </label>
          <button className="btn primary" type="button" onClick={add} disabled={disabled}>{busy === 'add' ? <><span className="spinner" /> Checking the portal…</> : 'Add and check'}</button>
        </div>
      </section>
    </>
  );
}

/** Tick the portals to add. Each is saved, then checked one at a time (a polite request to a government server); only the ones the app can read are switched on. */
function PortalCatalogue({ onDone }: { onDone: () => void }) {
  const cat = useLoad(() => api.portalCatalogue(), []);
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [progress, setProgress] = useState<string | null>(null);
  const [result, setResult] = useState<Msg>(null);
  const entries = cat.data?.entries ?? [];
  const open = entries.filter((e) => !e.added);
  const groups = [...new Set(entries.map((e) => e.group))];
  const toggle = (id: string) => setTicked((t) => { const n = new Set(t); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const allTicked = open.length > 0 && open.every((e) => ticked.has(e.id));

  async function addTicked() {
    const ids = [...ticked];
    if (ids.length === 0) return;
    setResult(null);
    setProgress(tr('Saving…'));
    try {
      const { added, skipped } = await api.addPortalsBulk(ids);
      let ok = 0; let byHand = 0; let notReached = 0;
      for (let i = 0; i < added.length; i++) {
        setProgress(`${tr('Checking')} ${i + 1} / ${added.length}…`);
        try {
          const p = await api.checkPortal(added[i], true);
          if (p.kind === 'ok') ok += 1; else if (p.kind === 'unreachable') notReached += 1; else byHand += 1;
        } catch { notReached += 1; }
      }
      const parts = [`${ok} ${tr('can be read and are switched on')}`];
      if (byHand) parts.push(`${byHand} ${tr('need checking by hand')}`);
      if (notReached) parts.push(`${notReached} ${tr('did not answer (try Check again later)')}`);
      if (skipped.length) parts.push(`${skipped.length} ${tr('skipped')}: ${skipped.map((s) => s.reason).join('; ')}`);
      setResult({ tone: ok > 0 || added.length === 0 ? 'ok' : 'warn', text: `${added.length} ${tr('added')}: ${parts.join(', ')}.` });
      setTicked(new Set());
      cat.reload();
      onDone();
    } catch (err) {
      setResult({ tone: 'bad', text: (err as Error).message });
    } finally {
      setProgress(null);
    }
  }

  return (
    <section className="card" style={{ marginBottom: 14 }}>
      <div className="card-head">
        <div>
          <h2>{tr('Add more portals')}</h2>
          <p className="sub">{tr('Most state portals run the same software as the built-in ones. Tick the ones you bid in. Each is checked as it is added and only the ones this server can read are switched on. The addresses are from public knowledge and have not been opened from here, so some may be wrong, moved, or refuse data centres: those are listed under "Check these by hand" and cost nothing. Every portal you switch on makes the daily collection take longer.')}</p>
        </div>
        <button type="button" className="btn small" style={{ whiteSpace: 'nowrap', flexShrink: 0 }} onClick={() => setTicked(allTicked ? new Set() : new Set(open.map((e) => e.id)))} disabled={progress !== null || open.length === 0}>
          {allTicked ? tr('Untick all') : tr('Tick all not yet added')}
        </button>
      </div>
      {cat.error && <div className="alert bad">{cat.error}</div>}
      {cat.loading && !cat.data && <span className="spinner" />}
      {result && <div className={`alert ${result.tone}`} role="status">{result.text}</div>}
      {groups.map((g) => (
        <fieldset key={g} style={{ border: 0, padding: 0, margin: '0 0 10px' }}>
          <legend className="muted small" style={{ padding: 0, marginBottom: 4 }}>{g}</legend>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: '4px 16px' }}>
            {entries.filter((e) => e.group === g).map((e) => (
              <label key={e.id} className="check" style={{ margin: 0, opacity: e.added ? 0.6 : 1 }}>
                <input type="checkbox" checked={e.added || ticked.has(e.id)} disabled={e.added || progress !== null} onChange={() => toggle(e.id)} />
                <span>{e.name}{e.added && <span className="muted small"> ({tr('already added')})</span>}{e.note && !e.added && <span className="muted small"> · {e.note}</span>}</span>
              </label>
            ))}
          </div>
        </fieldset>
      ))}
      <button type="button" className="btn primary" onClick={addTicked} disabled={progress !== null || ticked.size === 0}>
        {progress ? <><span className="spinner" /> {progress}</> : `${tr('Add and check')} ${ticked.size > 0 ? `(${ticked.size})` : ''}`}
      </button>
    </section>
  );
}
