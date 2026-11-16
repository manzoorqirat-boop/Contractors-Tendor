import { useState } from 'react';
import { api } from './api';
import { fmtDate } from './format';
import { useLoad } from './hooks';
import { ScrollX } from './ScrollX';
import { Skeleton, useToast } from './ui';
import { useConfirm } from './confirm';
import type { Webhook } from './types';

const ago = (t: number | null) => (t ? new Date(t * 1000).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' }) : 'never');

/** A secret that is shown once, with a copy button. Closing it means it can never be read again. */
function ShowOnce({ title, secret, onClose }: { title: string; secret: string; onClose: () => void }) {
  const toast = useToast();
  async function copy() { try { await navigator.clipboard.writeText(secret); toast('Copied'); } catch { toast('Could not copy: select it and copy by hand', 'bad'); } }
  return (
    <div className="alert warn" role="status" style={{ marginBottom: 12 }}>
      <strong>{title}</strong>
      <p style={{ margin: '6px 0' }}>Copy it now. It is shown only once; if it is lost, make a new one.</p>
      <p className="mono acct-key" style={{ wordBreak: 'break-all' }}>{secret}</p>
      <div style={{ display: 'flex', gap: 8 }}><button type="button" className="btn small" onClick={copy}>Copy</button><button type="button" className="btn small" onClick={onClose}>I have saved it</button></div>
    </div>
  );
}

function TokensCard() {
  const toast = useToast();
  const confirm = useConfirm();
  const data = useLoad(() => api.tokens(), []);
  const [name, setName] = useState('');
  const [role, setRole] = useState('viewer');
  const [days, setDays] = useState('90');
  const [secret, setSecret] = useState('');
  const [busy, setBusy] = useState(false);
  async function make(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try { const r = await api.createToken(name, role, days ? Number(days) : null); setSecret(r.secret); setName(''); data.reload(); }
    catch (err) { toast((err as Error).message, 'bad'); } finally { setBusy(false); }
  }
  async function revoke(id: number, label: string) {
    if (!(await confirm({ title: `Revoke "${label}"?`, body: 'Whatever uses this token stops working at once. This cannot be undone; make a new token if it is needed again.', confirmLabel: 'Revoke', danger: true }))) return;
    try { await api.revokeToken(id); data.reload(); toast('Token revoked'); } catch (err) { toast((err as Error).message, 'bad'); }
  }
  const live = (data.data?.tokens ?? []).filter((t) => !t.revoked);
  return (
    <section className="card stack-fields" style={{ gridColumn: '1 / -1' }}>
      <h2>API tokens</h2>
      <p className="sub">Lets a spreadsheet, an ERP or an automation read from (or work in) TendAI without anyone's password. Send it as <code>Authorization: Bearer &lt;token&gt;</code>. A token is view-only or can work on bids, never an administrator, and everything it does is in the Activity log under its name.</p>
      {secret && <ShowOnce title="Your new token" secret={secret} onClose={() => setSecret('')} />}
      {data.loading && !data.data && <Skeleton h={60} />}
      {live.length > 0 && (
        <ScrollX label="API tokens">
          <table className="tbl">
            <thead><tr><th>Name</th><th>Access</th><th>Starts with</th><th>Last used</th><th>Expires</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {live.map((t) => (
                <tr key={t.id}>
                  <td><strong>{t.name}</strong><div className="muted small">made by {t.created_by || '?'} on {fmtDate(t.created)}</div></td>
                  <td>{t.role === 'viewer' ? 'View only' : 'Can work on bids'}</td>
                  <td className="mono">{t.hint}</td><td>{ago(t.last_used)}</td><td>{t.expires ? fmtDate(t.expires) : 'No expiry'}</td>
                  <td style={{ textAlign: 'right' }}><button type="button" className="btn small danger" onClick={() => revoke(t.id, t.name)}>Revoke</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollX>
      )}
      <form onSubmit={make} style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12, alignItems: 'end', marginTop: 12 }}>
        <label className="field" style={{ margin: 0 }}>Name<input value={name} maxLength={60} onChange={(e) => setName(e.target.value)} placeholder="e.g. Finance spreadsheet" /></label>
        <label className="field" style={{ margin: 0 }}>Access
          <select value={role} onChange={(e) => setRole(e.target.value)}><option value="viewer">View only</option><option value="bidder">Can work on bids</option></select></label>
        <label className="field" style={{ margin: 0 }}>Lasts
          <select value={days} onChange={(e) => setDays(e.target.value)}><option value="30">30 days</option><option value="90">90 days</option><option value="365">1 year</option><option value="">No expiry</option></select></label>
        <div><button className="btn primary" disabled={busy || !name.trim()}>{busy ? 'Making…' : 'Make a token'}</button></div>
      </form>
    </section>
  );
}

const STATUS_BADGE: Record<string, string> = { ok: 'ok', retrying: 'warn', failed: 'bad' };

function WebhooksCard() {
  const toast = useToast();
  const confirm = useConfirm();
  const data = useLoad(() => api.webhooks(), []);
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [picked, setPicked] = useState<string[]>(['bid.stage_changed']);
  const [secret, setSecret] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const events = data.data?.events ?? [];
  const label = (k: string) => events.find((e) => e.key === k)?.label ?? k;

  async function make(e: React.FormEvent) {
    e.preventDefault();
    setBusy('new');
    try { const r = await api.createWebhook(name, url, picked); setSecret(r.secret); setName(''); setUrl(''); data.reload(); }
    catch (err) { toast((err as Error).message, 'bad'); } finally { setBusy(null); }
  }
  async function act(h: Webhook, what: 'test' | 'toggle' | 'secret' | 'delete') {
    setBusy(`${what}${h.id}`);
    try {
      if (what === 'test') { await api.testHook(h.id); toast('The test message was delivered'); }
      else if (what === 'toggle') { await api.editWebhook(h.id, { active: !h.active }); }
      else if (what === 'secret') {
        if (!(await confirm({ title: 'Make a new secret?', body: 'The old secret stops working. Update the receiver with the new one straight away.', confirmLabel: 'Make a new secret' }))) return;
        setSecret((await api.newWebhookSecret(h.id)).secret);
      } else {
        if (!(await confirm({ title: `Remove "${h.name}"?`, body: 'Messages waiting to be sent to it are dropped.', confirmLabel: 'Remove', danger: true }))) return;
        await api.deleteWebhook(h.id);
      }
      data.reload();
    } catch (err) { toast((err as Error).message, 'bad'); data.reload(); } finally { setBusy(null); }
  }
  const toggle = (k: string) => setPicked((p) => (p.includes(k) ? p.filter((x) => x !== k) : [...p, k]));
  return (
    <section className="card stack-fields" style={{ gridColumn: '1 / -1' }}>
      <h2>Outgoing webhooks</h2>
      <p className="sub">TendAI tells another system when something happens, such as a bid changing stage. Each message is JSON, signed with the webhook's secret (<code>X-TendAI-Signature: sha256=HMAC(secret, timestamp + "." + body)</code>), and retried a few times if the receiver is down. The address must be a public https address.</p>
      {secret && <ShowOnce title="The webhook's secret" secret={secret} onClose={() => setSecret('')} />}
      {data.loading && !data.data && <Skeleton h={60} />}
      {(data.data?.webhooks ?? []).map((h) => (
        <div key={h.id} style={{ borderTop: '1px solid var(--border)', padding: '10px 0' }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <strong>{h.name}</strong>
            <span className={`badge ${h.active ? 'ok' : 'plain'}`}>{h.active ? 'On' : 'Off'}</span>
            {h.last_status && <span className={`badge ${STATUS_BADGE[h.last_status]}`}>{h.last_status === 'ok' ? 'Last delivery worked' : h.last_status === 'retrying' ? 'Retrying' : 'Gave up'}</span>}
            {h.waiting > 0 && <span className="muted small">{h.waiting} waiting</span>}
          </div>
          <div className="muted small mono" style={{ wordBreak: 'break-all' }}>{h.url}</div>
          <div className="muted small">Sends: {h.events.map(label).join('; ')}. Secret {h.secret_hint}.</div>
          {h.last_error && <div className="small" style={{ color: 'var(--bad)' }}>{h.last_error}</div>}
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
            <button type="button" className="btn small" disabled={busy !== null || !h.active} onClick={() => act(h, 'test')}>{busy === `test${h.id}` ? 'Sending…' : 'Send a test'}</button>
            <button type="button" className="btn small" disabled={busy !== null} onClick={() => act(h, 'toggle')}>{h.active ? 'Turn off' : 'Turn on'}</button>
            <button type="button" className="btn small" disabled={busy !== null} onClick={() => act(h, 'secret')}>New secret</button>
            <button type="button" className="btn small danger" disabled={busy !== null} onClick={() => act(h, 'delete')}>Remove</button>
          </div>
        </div>
      ))}
      <form onSubmit={make} style={{ marginTop: 12 }}>
        <h3 style={{ margin: '0 0 8px' }}>Add a webhook</h3>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12 }}>
          <label className="field" style={{ margin: 0 }}>Name<input value={name} maxLength={60} onChange={(e) => setName(e.target.value)} placeholder="e.g. ERP" /></label>
          <label className="field" style={{ margin: 0 }}>Address (https)<input value={url} maxLength={500} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/hooks/tendai" inputMode="url" /></label>
        </div>
        <fieldset style={{ border: 0, padding: 0, margin: '10px 0' }}>
          <legend className="muted small">Send when</legend>
          {events.map((e) => <label key={e.key} className="check"><input type="checkbox" checked={picked.includes(e.key)} onChange={() => toggle(e.key)} />{e.label}</label>)}
        </fieldset>
        <button className="btn primary" disabled={busy !== null || !name.trim() || !url.trim() || picked.length === 0}>{busy === 'new' ? 'Adding…' : 'Add webhook'}</button>
      </form>
    </section>
  );
}

export function IntegrationsPanel() {
  return <div className="set-grid"><TokensCard /><WebhooksCard /></div>;
}

/** How long data is kept, and whether files and secrets are encrypted. Retention saves on its own button. */
export function DataPrivacyCard({ encryption }: { encryption?: { enabled: boolean; problem: string; plain_files: number; plain_secrets: number; rotating: boolean } }) {
  const toast = useToast();
  const s = useLoad(() => api.settings(), []);
  const [audit, setAudit] = useState<string | null>(null);
  const [tender, setTender] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const a = audit ?? String(s.data?.audit_days ?? 365), t = tender ?? String(s.data?.tender_days ?? 120);
  const dirty = audit !== null || tender !== null;
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try { await api.saveSettings({ audit_days: Number(a), tender_days: Number(t) }); setAudit(null); setTender(null); s.reload(); toast('Saved'); }
    catch (err) { toast((err as Error).message, 'bad'); } finally { setBusy(false); }
  }
  return (
    <section className="card stack-fields">
      <h2>Keeping data, and encryption {encryption && <span className={`badge ${encryption.enabled ? (encryption.problem || encryption.plain_files || encryption.plain_secrets ? 'warn' : 'ok') : 'warn'}`} style={{ marginLeft: 6 }}>{encryption.enabled ? 'Encrypted' : 'Not encrypted'}</span>}</h2>
      <form onSubmit={save}>
        <label className="field">Keep the activity log for (days)<span className="hint">30 to 3650. Older entries are removed once a day.</span><input type="number" min={30} max={3650} value={a} onChange={(e) => setAudit(e.target.value)} style={{ maxWidth: 160 }} /></label>
        <label className="field">Keep collected tenders for (days)<span className="hint">30 to 3650. Tenders you started a bid on are never removed: the bid keeps its own copy.</span><input type="number" min={30} max={3650} value={t} onChange={(e) => setTender(e.target.value)} style={{ maxWidth: 160 }} /></label>
        <button className="btn" disabled={busy || !dirty}>{busy ? 'Saving…' : 'Save'}</button>
      </form>
      {encryption && (encryption.enabled
        ? <p className="sub" style={{ marginTop: 12 }}>Two-step secrets, webhook secrets and every uploaded file (the Vault and saved notices) are stored encrypted with <code>DATA_ENCRYPTION_KEY</code>. Keep that phrase safe somewhere other than the server: without it, the files and any restored backup cannot be read.{encryption.rotating ? ' A previous key is still set (DATA_ENCRYPTION_KEY_OLD): remove it after the next restart.' : ''}{encryption.plain_files || encryption.plain_secrets ? ` Still plain: ${encryption.plain_files} file(s), ${encryption.plain_secrets} secret(s).` : ''}{encryption.problem ? ` ${encryption.problem}` : ''}</p>
        : <p className="sub" style={{ marginTop: 12 }}>Uploaded papers and two-step secrets are stored as they are. To encrypt them, your administrator sets <code>DATA_ENCRYPTION_KEY</code> on the server (a long private phrase, at least 16 characters). Everything already stored is encrypted at the next start.</p>)}
    </section>
  );
}
