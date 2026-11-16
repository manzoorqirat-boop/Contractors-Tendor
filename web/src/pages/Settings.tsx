import { useEffect, useState } from 'react';
import { api } from '../api';
import { inr } from '../format';
import { useLoad } from '../hooks';
import { resetAiStatus } from '../ai';
import { PortalsManager } from '../ManualPortals';
import { setLogoText } from '../Brand';
import { Tabs } from '../Tabs';
import type { AiStatus, AppConfig, Settings } from '../types';
import '../compact.css';

type Tab = 'email' | 'portals' | 'filters' | 'ai' | 'backup';
let lastTab: Tab = 'email';
const norm = (v: { recipients: string; extra: string; exclude: string; minValue: string; logoText: string; f: Settings }) => JSON.stringify([
  lines(v.recipients), lines(v.extra), lines(v.exclude), Number(v.minValue) || 0, v.logoText, v.f.send_time, v.f.schedule_enabled, v.f.send_empty,
  [...v.f.portals].sort(), v.f.ai_digest_intro, v.f.ai_hide_noncivil]);
const lines = (s: string) => s.split(/[\n,;]+/).map((x) => x.trim()).filter(Boolean);

export function SettingsPage({ onSaved }: { config: AppConfig; onSaved: () => void }) {
  const { data, error } = useLoad(() => api.settings(), []);
  const [form, setForm] = useState<Settings | null>(null);
  const [recipients, setRecipients] = useState('');
  const [extra, setExtra] = useState('');
  const [exclude, setExclude] = useState('');
  const [minValue, setMinValue] = useState('0');
  const [msg, setMsg] = useState<{ tone: 'ok' | 'bad'; text: string } | null>(null);
  const [busy, setBusy] = useState<'save' | 'test' | 'aitest' | 'score' | null>(null);
  const [logoText, setLogoTextField] = useState('QM');
  const [ai, setAi] = useState<AiStatus | null>(null);
  const [tab, setTabState] = useState<Tab>(lastTab);
  const [snap, setSnap] = useState('');
  const setTab = (t: Tab) => { lastTab = t; setTabState(t); };
  const loadAi = () => api.aiStatus().then(setAi, () => setAi(null));
  useEffect(() => { loadAi(); }, []);

  const load = (s: Settings) => {
    setForm(s);
    setRecipients(s.recipients.join('\n'));
    setExtra(s.extra_keywords.join('\n'));
    setExclude(s.exclude_words.join('\n'));
    setMinValue(String(s.min_value || 0));
    setLogoTextField(s.logo_text || 'QM');
    setSnap(norm({ recipients: s.recipients.join('\n'), extra: s.extra_keywords.join('\n'), exclude: s.exclude_words.join('\n'), minValue: String(s.min_value || 0), logoText: s.logo_text || 'QM', f: s }));
  };
  useEffect(() => { if (data) load(data); }, [data]);
  const dirty = !!form && snap !== '' && norm({ recipients, extra, exclude, minValue, logoText, f: form }) !== snap;
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  if (error) return <div className="alert bad">{error}</div>;
  if (!form) return <div className="empty"><span className="spinner" /> Loading…</div>;

  const patch = (p: Partial<Settings>) => setForm({ ...form, ...p });
  const refreshPortals = async () => {
    // A portal was added or removed on the server: show it in the switches below without losing switches changed but not yet saved.
    const s = await api.settings();
    const had = new Set(form.available_portals.map((p) => p.key));
    const keys = new Set(s.available_portals.map((p) => p.key));
    setForm((f) => f && { ...f, available_portals: s.available_portals, portals: [...f.portals.filter((k) => keys.has(k)), ...s.portals.filter((k) => !had.has(k))] });
    onSaved();
  };
  const togglePortal = (key: string) => patch({ portals: form.portals.includes(key) ? form.portals.filter((x) => x !== key) : [...form.portals, key] });

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!form) return;
    setBusy('save');
    setMsg(null);
    try {
      const saved = await api.saveSettings({
        recipients: lines(recipients), send_time: form.send_time, schedule_enabled: form.schedule_enabled, send_empty: form.send_empty,
        portals: form.portals, min_value: Number(minValue) || 0, extra_keywords: lines(extra), exclude_words: lines(exclude),
        logo_text: logoText, ai_digest_intro: form.ai_digest_intro, ai_hide_noncivil: form.ai_hide_noncivil,
      });
      loadAi();
      load(saved);
      setLogoText(saved.logo_text);
      onSaved();
      setMsg({ tone: 'ok', text: 'Saved. Changes apply from the next run.' });
    } catch (err) {
      setMsg({ tone: 'bad', text: (err as Error).message });
    } finally {
      setBusy(null);
    }
  }

  async function aiAction(kind: 'aitest' | 'score') {
    setBusy(kind);
    setMsg(null);
    try {
      if (kind === 'aitest') { await api.aiTest(); setMsg({ tone: 'ok', text: 'The AI assistant answered. The connection works.' }); }
      else { const r = await api.aiScore(); setMsg({ tone: 'ok', text: `Labelled ${r.scored} tenders. ${r.unlabelled} open tenders still to do.` }); }
      resetAiStatus();
      loadAi();
    } catch (err) {
      setMsg({ tone: 'bad', text: (err as Error).message });
    } finally {
      setBusy(null);
    }
  }

  async function test() {
    setBusy('test');
    setMsg(null);
    try {
      const r = await api.testEmail();
      setMsg({ tone: 'ok', text: `Test email sent to ${r.sent_to} recipient${r.sent_to === 1 ? '' : 's'}. It uses the saved list, so save any changes first.` });
    } catch (err) {
      setMsg({ tone: 'bad', text: (err as Error).message });
    } finally {
      setBusy(null);
    }
  }

  const SaveBar = (
    <div className="set-save">
      {dirty && <span className="set-dirty" role="status">Unsaved changes</span>}
      <button className="btn primary" disabled={busy !== null || !dirty}>{busy === 'save' ? 'Saving…' : 'Save'}</button>
    </div>
  );

  return (
    <>
      <div className="page-head"><div><h1>Settings</h1><p className="sub">Who gets the digest, when, and what counts as a civil tender.</p></div></div>
      <form onSubmit={save}>
        <div className="set-bar">
          <Tabs<Tab> label="Settings sections" value={tab} onChange={setTab} tabs={[
            { key: 'email', label: 'Daily email' }, { key: 'portals', label: <>Portals<span className="tab-dot">{form.portals.length}</span></> },
            { key: 'filters', label: 'Civil filter' }, { key: 'ai', label: 'AI & logo' }, { key: 'backup', label: 'Backup' }]} />
          {SaveBar}
        </div>
        {msg && <div className={`alert ${msg.tone}`} role="status">{msg.text}</div>}

        {tab === 'email' && (
          <section className="card">
            <h2>Daily email</h2>
            <p className="sub">{form.mail_configured ? <>Sent from <strong>{form.mail_from}</strong> through Brevo.</> : 'Email is not set up on the server: BREVO_API_KEY and MAIL_FROM are missing.'}</p>
            <div className="set-grid">
              <label className="field">
                Recipients
                <span className="hint">One email address per line.</span>
                <textarea value={recipients} onChange={(e) => setRecipients(e.target.value)} spellCheck={false} />
              </label>
              <div>
                <label className="check"><input type="checkbox" checked={form.schedule_enabled} onChange={(e) => patch({ schedule_enabled: e.target.checked })} />Send the digest every day</label>
                <label className="field" style={{ maxWidth: 200, margin: '8px 0' }}>
                  Time (India time)
                  <input type="time" value={form.send_time} onChange={(e) => patch({ send_time: e.target.value })} required />
                </label>
                <label className="check"><input type="checkbox" checked={form.send_empty} onChange={(e) => patch({ send_empty: e.target.checked })} />Send even when there is nothing new</label>
                <div style={{ marginTop: 12 }}>
                  <button type="button" className="btn" onClick={test} disabled={busy !== null || !form.mail_configured}>{busy === 'test' ? 'Sending…' : 'Send a test email'}</button>
                  <p className="muted small" style={{ marginTop: 6 }}>The test uses the saved list, so save any changes first.</p>
                </div>
              </div>
            </div>
          </section>
        )}

        {tab === 'portals' && (
          <>
            <section className="card" style={{ marginBottom: 14 }}>
              <h2>Portals to read</h2>
              <p className="sub">Used by the daily run and by Run now.</p>
              <div className="set-grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))' }}>
                {form.available_portals.map((p) => (
                  <label key={p.key} className="check"><input type="checkbox" checked={form.portals.includes(p.key)} onChange={() => togglePortal(p.key)} />{p.name}</label>
                ))}
              </div>
            </section>
            <PortalsManager onChange={refreshPortals} />
          </>
        )}

        {tab === 'filters' && (
          <section className="card">
            <h2>What counts as a civil tender</h2>
            <p className="sub">Kept when its title, description or category mentions civil work. Dropped when its category is electrical, mechanical, supplies or services.</p>
            <div className="set-grid">
              <label className="field">
                Minimum value (rupees)
                <span className="hint">Currently {minValue && Number(minValue) > 0 ? inr(Number(minValue)) : 'no minimum'}. Tenders with no stated value are kept.</span>
                <input type="number" min={0} step={100000} value={minValue} onChange={(e) => setMinValue(e.target.value)} style={{ maxWidth: 220 }} />
              </label>
              <label className="field">
                Extra words that mean civil work
                <span className="hint">One per line, for example: kerb, retaining wall.</span>
                <textarea value={extra} onChange={(e) => setExtra(e.target.value)} spellCheck={false} />
              </label>
              <label className="field">
                Words that rule a tender out
                <span className="hint">One per line, for example: painting only, furniture.</span>
                <textarea value={exclude} onChange={(e) => setExclude(e.target.value)} spellCheck={false} />
              </label>
            </div>
          </section>
        )}

        {tab === 'ai' && (
          <div className="set-grid">
            <section className="card ai-card">
              <h2>AI assistant {ai && <span className={`badge ${ai.configured ? 'ok' : 'plain'}`} style={{ marginLeft: 6 }}>{ai.configured ? 'On' : 'Off'}</span>}</h2>
              <p className="sub">{ai?.configured ? <>Connected. {ai.labelled} open tenders labelled, {ai.unlabelled} waiting.{ai.daily_limit ? <> AI requests today: {ai.calls_today ?? 0} of {ai.daily_limit}.</> : null}</> : 'Off. Ask your administrator to switch it on in the server settings to get summaries, fit scores, work types, the ask bar, the document reader and bid briefs.'}</p>
              <label className="check"><input type="checkbox" checked={form.ai_digest_intro} onChange={(e) => patch({ ai_digest_intro: e.target.checked })} />Start the daily email with a short "worth a look today" paragraph</label>
              <label className="check"><input type="checkbox" checked={form.ai_hide_noncivil} onChange={(e) => patch({ ai_hide_noncivil: e.target.checked })} />Hide tenders the AI judges not to be civil work (off: they stay visible with a "May not be civil" tag)</label>
              <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button type="button" className="btn ai" onClick={() => aiAction('aitest')} disabled={busy !== null || !ai?.configured}>{busy === 'aitest' ? 'Testing…' : 'Test AI connection'}</button>
                <button type="button" className="btn ai" onClick={() => aiAction('score')} disabled={busy !== null || !ai?.configured}>{busy === 'score' ? 'Labelling…' : 'Label tenders now'}</button>
              </div>
              <p className="muted small" style={{ marginTop: 8 }}>Your company profile is in the Vault, under Company.</p>
            </section>
            <section className="card">
              <h2>Logo</h2>
              <p className="sub">The letters in the tile at the top left, on the sign-in screen and in the browser tab.</p>
              <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                <label className="field" style={{ margin: 0 }}>
                  Letters
                  <span className="hint">1 to 3 letters or numbers</span>
                  <input value={logoText} onChange={(e) => setLogoTextField(e.target.value.replace(/[^\p{L}\p{N}]/gu, '').slice(0, 3))} maxLength={3} style={{ maxWidth: 120 }} aria-label="Logo letters" />
                </label>
                <PreviewLogo text={logoText} />
              </div>
            </section>
          </div>
        )}

        {tab === 'backup' && <BackupPanel />}
      </form>
    </>
  );
}

/** Download everything the app keeps, and show whether the automatic daily copy is running. */
function BackupPanel() {
  const { data, error } = useLoad(() => api.backupStatus(), []);
  const when = data?.latest_day ? new Date(`${data.latest_day}T00:00:00`).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : null;
  return (
    <div className="set-grid">
      <section className="card">
        <h2>Download a backup</h2>
        <p className="sub">One zip with all tenders, bids, the BOQ catalogue and every file in the Vault. Save it somewhere other than this server (your computer, a cloud drive). It contains your company papers, so keep it private.</p>
        <a className="btn primary" href="/api/backup" download>Download backup (.zip)</a>
        <p className="muted small" style={{ marginTop: 8 }}>It can take a minute if the Vault holds many files. Make one before you change the server set-up, and regularly (weekly is a good habit).</p>
      </section>
      <section className="card">
        <h2>Automatic daily copy {data && <span className={`badge ${data.enabled && data.latest_day ? 'ok' : 'warn'}`} style={{ marginLeft: 6 }}>{data.enabled ? (data.latest_day ? 'Running' : 'Waiting for the first copy') : 'Off'}</span>}</h2>
        {error && <div className="alert bad">{error}</div>}
        {data && (data.enabled
          ? <p className="sub">{when ? <>Last copy: {when}. </> : null}The last {data.keep_days} days of the databases are kept on the server's volume. This protects against a bad edit or a damaged file, <strong>not</strong> against losing the volume, which is why the download above matters.</p>
          : <p className="sub">Off: the app is running without a database file.</p>)}
        {data?.storage_warning && <div className="alert bad" role="alert">{data.storage_warning}</div>}
        <details>
          <summary>How to restore a backup</summary>
          <p className="sub">Your administrator runs this on the server, with the zip uploaded there: <code>python -m tender_digest.backup restore backup.zip --target-dir /data --force</code>, then restarts the service. Existing data is moved aside, not deleted.</p>
        </details>
      </section>
    </div>
  );
}

/** What the tile will look like with the letters typed so far, before saving. */
function PreviewLogo({ text }: { text: string }) {
  const t = text.trim() || 'QM';
  return <span className={`logo len${Math.min(t.length, 3)}`} aria-label={`Preview: ${t}`} style={{ width: 40, height: 40, borderRadius: 10, fontSize: t.length === 1 ? 22 : t.length === 2 ? 17 : 13 }}>{t}</span>;
}
