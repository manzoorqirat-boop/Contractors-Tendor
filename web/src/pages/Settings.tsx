import { useEffect, useState } from 'react';
import { api } from '../api';
import { inr } from '../format';
import { useLoad } from '../hooks';
import { resetAiStatus } from '../ai';
import { PortalsManager } from '../ManualPortals';
import { setLogoText } from '../Brand';
import type { AiStatus, AppConfig, Settings } from '../types';

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
  const [profile, setProfile] = useState('');
  const [logoText, setLogoTextField] = useState('QM');
  const [ai, setAi] = useState<AiStatus | null>(null);
  const loadAi = () => api.aiStatus().then(setAi, () => setAi(null));
  useEffect(() => { loadAi(); }, []);

  const load = (s: Settings) => {
    setForm(s);
    setRecipients(s.recipients.join('\n'));
    setExtra(s.extra_keywords.join('\n'));
    setExclude(s.exclude_words.join('\n'));
    setMinValue(String(s.min_value || 0));
    setProfile(s.company_profile || '');
    setLogoTextField(s.logo_text || 'QM');
  };
  useEffect(() => { if (data) load(data); }, [data]);

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
        company_profile: profile, logo_text: logoText, ai_digest_intro: form.ai_digest_intro, ai_hide_noncivil: form.ai_hide_noncivil,
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
      if (kind === 'aitest') { const r = await api.aiTest(); setMsg({ tone: 'ok', text: `Claude answered. Main model: ${r.model}. Bulk model: ${r.fast_model}.` }); }
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

  return (
    <>
      <div className="page-head"><div><h1>Settings</h1><p className="sub">Who gets the digest, when, and what counts as a civil tender.</p></div></div>
      <form onSubmit={save}>
        {msg && <div className={`alert ${msg.tone}`} role="status">{msg.text}</div>}

        <section className="card" style={{ marginBottom: 14 }}>
          <h2>Logo</h2>
          <p className="sub">The letters in the blue tile at the top left, on the sign-in screen and in the browser tab. Applies after you press Save.</p>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <label className="field" style={{ margin: 0 }}>
              Letters
              <span className="hint">1 to 3 letters or numbers</span>
              <input value={logoText} onChange={(e) => setLogoTextField(e.target.value.replace(/[^\p{L}\p{N}]/gu, '').slice(0, 3))} maxLength={3} style={{ maxWidth: 120 }} aria-label="Logo letters" />
            </label>
            <PreviewLogo text={logoText} />
          </div>
        </section>

        <section className="card" style={{ marginBottom: 14 }}>
          <h2>Daily email</h2>
          <p className="sub">{form.mail_configured ? <>Sent from <strong>{form.mail_from}</strong> through Brevo.</> : 'Email is not set up on the server: BREVO_API_KEY and MAIL_FROM are missing.'}</p>
          <label className="field">
            Recipients
            <span className="hint">One email address per line.</span>
            <textarea value={recipients} onChange={(e) => setRecipients(e.target.value)} spellCheck={false} />
          </label>
          <label className="check"><input type="checkbox" checked={form.schedule_enabled} onChange={(e) => patch({ schedule_enabled: e.target.checked })} />Send the digest every day</label>
          <label className="field" style={{ maxWidth: 200, marginTop: 10 }}>
            Time (India time)
            <input type="time" value={form.send_time} onChange={(e) => patch({ send_time: e.target.value })} required />
          </label>
          <label className="check"><input type="checkbox" checked={form.send_empty} onChange={(e) => patch({ send_empty: e.target.checked })} />Send even when there is nothing new (so you know it is working)</label>
          <div style={{ marginTop: 12 }}>
            <button type="button" className="btn" onClick={test} disabled={busy !== null || !form.mail_configured}>{busy === 'test' ? 'Sending…' : 'Send a test email'}</button>
          </div>
        </section>

        <section className="card" style={{ marginBottom: 14 }}>
          <h2>Portals</h2>
          <p className="sub">Which portals are read by the daily run and by Run now.</p>
          {form.available_portals.map((p) => (
            <label key={p.key} className="check"><input type="checkbox" checked={form.portals.includes(p.key)} onChange={() => togglePortal(p.key)} />{p.name}</label>
          ))}
        </section>

        <PortalsManager onChange={refreshPortals} />

        <section className="card" style={{ marginBottom: 14 }}>
          <h2>What counts as a civil tender</h2>
          <p className="sub">A tender is kept when its title, description or category mentions civil work, and dropped when its category is electrical, mechanical, supplies or services.</p>
          <label className="field">
            Minimum value (rupees)
            <span className="hint">Ignore tenders worth less than this. Currently {minValue && Number(minValue) > 0 ? inr(Number(minValue)) : 'no minimum'}. Tenders with no stated value are always kept.</span>
            <input type="number" min={0} step={100000} value={minValue} onChange={(e) => setMinValue(e.target.value)} style={{ maxWidth: 220 }} />
          </label>
          <label className="field">
            Extra words that mean civil work
            <span className="hint">One per line, for example: kerb, retaining wall, tile work.</span>
            <textarea value={extra} onChange={(e) => setExtra(e.target.value)} spellCheck={false} />
          </label>
          <label className="field">
            Words that rule a tender out
            <span className="hint">One per line, for example: painting only, furniture.</span>
            <textarea value={exclude} onChange={(e) => setExclude(e.target.value)} spellCheck={false} />
          </label>
        </section>

        <section className="card" style={{ marginBottom: 14 }}>
          <h2>AI assistant {ai && <span className={`badge ${ai.configured ? 'ok' : 'plain'}`} style={{ marginLeft: 6 }}>{ai.configured ? 'On' : 'Off'}</span>}</h2>
          <p className="sub">{ai?.configured ? <>Connected. Main model <strong>{ai.model}</strong>, bulk model <strong>{ai.fast_model}</strong>. {ai.labelled} open tenders labelled, {ai.unlabelled} waiting.</> : 'Off. Add ANTHROPIC_API_KEY to the server variables to turn on summaries, fit scores, work types, the ask bar, the document reader and bid briefs.'}</p>
          <label className="field">
            About your company
            <span className="hint">Used to score how well each tender fits you. Say what work you do, where, the sizes you can take and your registration class. Saving a change re-scores open tenders on the next run.</span>
            <textarea value={profile} onChange={(e) => setProfile(e.target.value)} maxLength={2000} placeholder="Class A civil contractor. Roads and drainage in Pune and Nashik. Projects from 50 lakh to 10 crore. 8 years of PWD work." />
          </label>
          <label className="check"><input type="checkbox" checked={form.ai_digest_intro} onChange={(e) => patch({ ai_digest_intro: e.target.checked })} />Start the daily email with a short "worth a look today" paragraph</label>
          <label className="check"><input type="checkbox" checked={form.ai_hide_noncivil} onChange={(e) => patch({ ai_hide_noncivil: e.target.checked })} />Hide tenders Claude judges not to be civil work (off: they stay visible with a "May not be civil" tag)</label>
          <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button type="button" className="btn" onClick={() => aiAction('aitest')} disabled={busy !== null || !ai?.configured}>{busy === 'aitest' ? 'Testing…' : 'Test AI connection'}</button>
            <button type="button" className="btn" onClick={() => aiAction('score')} disabled={busy !== null || !ai?.configured}>{busy === 'score' ? 'Labelling…' : 'Label tenders now'}</button>
          </div>
        </section>

        <button className="btn primary" disabled={busy !== null}>{busy === 'save' ? 'Saving…' : 'Save settings'}</button>
      </form>
    </>
  );
}

/** What the tile will look like with the letters typed so far, before saving. */
function PreviewLogo({ text }: { text: string }) {
  const t = text.trim() || 'QM';
  return <span className={`logo len${Math.min(t.length, 3)}`} aria-label={`Preview: ${t}`} style={{ width: 40, height: 40, borderRadius: 10, fontSize: t.length === 1 ? 22 : t.length === 2 ? 17 : 13 }}>{t}</span>;
}
