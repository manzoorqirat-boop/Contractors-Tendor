import { useEffect, useState } from 'react';
import { api } from '../api';
import { inr } from '../format';
import { useLoad } from '../hooks';
import type { AppConfig, Settings } from '../types';

const lines = (s: string) => s.split(/[\n,;]+/).map((x) => x.trim()).filter(Boolean);

export function SettingsPage({ onSaved }: { config: AppConfig; onSaved: () => void }) {
  const { data, error } = useLoad(() => api.settings(), []);
  const [form, setForm] = useState<Settings | null>(null);
  const [recipients, setRecipients] = useState('');
  const [extra, setExtra] = useState('');
  const [exclude, setExclude] = useState('');
  const [minValue, setMinValue] = useState('0');
  const [msg, setMsg] = useState<{ tone: 'ok' | 'bad'; text: string } | null>(null);
  const [busy, setBusy] = useState<'save' | 'test' | null>(null);

  const load = (s: Settings) => {
    setForm(s);
    setRecipients(s.recipients.join('\n'));
    setExtra(s.extra_keywords.join('\n'));
    setExclude(s.exclude_words.join('\n'));
    setMinValue(String(s.min_value || 0));
  };
  useEffect(() => { if (data) load(data); }, [data]);

  if (error) return <div className="alert bad">{error}</div>;
  if (!form) return <div className="empty"><span className="spinner" /> Loading…</div>;

  const patch = (p: Partial<Settings>) => setForm({ ...form, ...p });
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
      });
      load(saved);
      onSaved();
      setMsg({ tone: 'ok', text: 'Saved. Changes apply from the next run.' });
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

        <button className="btn primary" disabled={busy !== null}>{busy === 'save' ? 'Saving…' : 'Save settings'}</button>
      </form>
    </>
  );
}
