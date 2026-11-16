import { useState } from 'react';
import { api } from './api';
import { fmtDate } from './format';
import { tr } from './i18n';
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

function SsoCard() {
  const toast = useToast();
  const info = useLoad(() => api.ssoInfo(), []);
  const d = info.data;
  async function copy(text: string) { try { await navigator.clipboard.writeText(text); toast('Copied'); } catch { toast('Could not copy', 'bad'); } }
  return (
    <section className="card" style={{ gridColumn: '1 / -1' }}>
      <h2>Single sign-on {d && <span className={`badge ${d.enabled ? 'ok' : 'plain'}`} style={{ marginLeft: 6 }}>{d.enabled ? 'On' : 'Not set up'}</span>}</h2>
      {info.loading && !d && <Skeleton h={40} />}
      {d?.problem && <div className="alert bad" role="alert">Single sign-on is not active: {d.problem}.</div>}
      {d && !d.enabled && !d.problem && (
        <p className="sub">Lets people sign in with the company's Google Workspace, Microsoft Entra ID, Okta or Keycloak account instead of a separate password. Your administrator sets <code>SSO_ISSUER</code>, <code>SSO_CLIENT_ID</code>, <code>SSO_CLIENT_SECRET</code> and <code>SSO_ALLOWED_DOMAINS</code> on the server (see the README). The owner login always keeps working with its password.</p>
      )}
      {d?.enabled && (
        <>
          <p className="sub">People at <strong>{d.allowed_domains.join(', ') || 'no domain yet (set SSO_ALLOWED_DOMAINS)'}</strong> can sign in through <strong>{d.issuer}</strong>. A new person is created as <strong>{d.default_role}</strong>; change their role in People. The provider's own sign-in (and its two-step check) replaces this app's password and code for them. Switching a person off here keeps them out.</p>
          <dl className="facts">
            <dt>Redirect address to register with the provider</dt><dd className="mono">{d.redirect_uri} <button type="button" className="link-btn" onClick={() => copy(d.redirect_uri)}>Copy</button></dd>
            <dt>Client ID</dt><dd className="mono">{d.client_id}</dd>
          </dl>
        </>
      )}
    </section>
  );
}

export function IntegrationsPanel() {
  return <div className="set-grid"><SsoCard /><TokensCard /><WebhooksCard /></div>;
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
        <label className="field">Keep collected tenders for (days)<span className="hint">30 to 3650 (45 suggested). Counted from when a tender was found, and only once it has closed: an open or starred tender is never removed, and a bid keeps its own copy.</span><input type="number" min={30} max={3650} value={t} onChange={(e) => setTender(e.target.value)} style={{ maxWidth: 160 }} /></label>
        <button className="btn" disabled={busy || !dirty}>{busy ? 'Saving…' : 'Save'}</button>
      </form>
      {encryption && (encryption.enabled
        ? <p className="sub" style={{ marginTop: 12 }}>Two-step secrets, webhook secrets and every uploaded file (the Vault and saved notices) are stored encrypted with <code>DATA_ENCRYPTION_KEY</code>. Keep that phrase safe somewhere other than the server: without it, the files and any restored backup cannot be read.{encryption.rotating ? ' A previous key is still set (DATA_ENCRYPTION_KEY_OLD): remove it after the next restart.' : ''}{encryption.plain_files || encryption.plain_secrets ? ` Still plain: ${encryption.plain_files} file(s), ${encryption.plain_secrets} secret(s).` : ''}{encryption.problem ? ` ${encryption.problem}` : ''}</p>
        : <p className="sub" style={{ marginTop: 12 }}>Uploaded papers and two-step secrets are stored as they are. To encrypt them, your administrator sets <code>DATA_ENCRYPTION_KEY</code> on the server (a long private phrase, at least 16 characters). Everything already stored is encrypted at the next start.</p>)}
    </section>
  );
}

/** Removes closed tenders nobody needs, after showing exactly how many. Starred tenders and tenders with a bid are never touched. */
export function CleanupCard() {
  const toast = useToast();
  const confirm = useConfirm();
  const [days, setDays] = useState('30');
  const [busy, setBusy] = useState(false);
  const n = Number(days);
  const valid = Number.isInteger(n) && n >= 7 && n <= 3650;
  const preview = useLoad(() => (valid ? api.cleanupPreview(n) : Promise.resolve(null)), [valid ? n : 0]);
  const p = preview.data;
  const total = p ? p.closed + p.placeholders : 0;
  async function run() {
    if (!p || !(await confirm({ title: `Delete ${total.toLocaleString('en-IN')} old tenders?`, body: `${p.closed} closed tenders (${p.ignored_among_them} of them ones you hid) and ${p.placeholders} placeholders for tenders judged not civil. Starred tenders and tenders with a bid are not touched. This cannot be undone.`, confirmLabel: 'Delete', danger: true }))) return;
    setBusy(true);
    try { const r = await api.cleanup(n); toast(`Removed ${r.closed} closed tenders and ${r.placeholders} placeholders`); preview.reload(); }
    catch (e) { toast((e as Error).message, 'bad'); } finally { setBusy(false); }
  }
  return (
    <section className="card stack-fields">
      <h2>{tr('Clean up closed tenders')}</h2>
      <p className="sub">{tr('Tenders that closed a while ago are deleted automatically on every run (see the keep time above). This removes them now, and shows what it would remove first.')}</p>
      <label className="field">{tr('Closed more than (days) ago')}<input type="number" min={7} max={3650} value={days} onChange={(e) => setDays(e.target.value)} style={{ maxWidth: 160 }} aria-invalid={!valid || undefined} /></label>
      {!valid && <p className="field-err">Use a whole number of days from 7 to 3650.</p>}
      {valid && preview.loading && !p && <Skeleton h={50} />}
      {valid && p && (
        <>
          <dl className="facts">
            <dt>Closed tenders that would go</dt><dd><strong>{p.closed.toLocaleString('en-IN')}</strong> of {p.tenders_now.toLocaleString('en-IN')} kept{p.ignored_among_them > 0 && <span className="muted small"> ({p.ignored_among_them} of them hidden by you)</span>}</dd>
            <dt>Placeholders for non-civil tenders</dt><dd>{p.placeholders.toLocaleString('en-IN')}</dd>
            <dt>Kept because starred or bid on</dt><dd>{p.kept_because_starred_or_bid.toLocaleString('en-IN')}</dd>
          </dl>
          {p.oldest.length > 0 && <details><summary className="muted small">Oldest that would go</summary><ul className="small" style={{ margin: '6px 0 0 18px', padding: 0 }}>{p.oldest.map((o) => <li key={o.tender_id}>{o.tender_id}: {o.title.slice(0, 80)}</li>)}</ul></details>}
          <div className="d-actions"><button type="button" className="btn danger" disabled={busy || total === 0} onClick={run}>{total === 0 ? 'Nothing to clean up' : busy ? 'Deleting…' : `Delete ${total.toLocaleString('en-IN')} old tenders`}</button></div>
        </>
      )}
    </section>
  );
}
