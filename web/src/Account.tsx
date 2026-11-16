import { useEffect, useState } from 'react';
import { api } from './api';
import { Logo } from './Brand';
import { useConfirm } from './confirm';
import { useLoad } from './hooks';
import { useToast } from './ui';
import type { AuditEntry, Role, UserRow } from './types';

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString('en-IN', { day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit' }) : 'Never');

/** Change your own password. When `forced`, it is the only thing on the screen (a temporary password must be replaced first). */
export function ChangePassword({ forced, onDone, onCancel, onLogout }: { forced?: boolean; onDone: () => void; onCancel?: () => void; onLogout?: () => void }) {
  const toast = useToast();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [again, setAgain] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const mismatch = again !== '' && next !== again;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy || mismatch) return;
    setBusy(true); setError('');
    try { await api.changePassword(current, next); toast('Password changed. Your other devices were signed out.'); onDone(); }
    catch (err) { setError((err as Error).message); setBusy(false); }
  }
  const form = (
    <form onSubmit={submit} noValidate>
      {error && <div className="alert bad" role="alert">{error}</div>}
      <label className="lf"><span>{forced ? 'Temporary password' : 'Current password'}</span><input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" autoFocus aria-invalid={!!error && !current} /></label>
      <label className="lf"><span>New password</span><input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" aria-describedby="pw-hint" /></label>
      <p className="muted small" id="pw-hint" style={{ margin: '-6px 0 12px' }}>At least 12 characters. A few random words joined together is easy to remember and hard to guess.</p>
      <label className="lf"><span>Type the new password again</span><input type="password" value={again} onChange={(e) => setAgain(e.target.value)} autoComplete="new-password" aria-invalid={mismatch} aria-describedby={mismatch ? 'pw-mismatch' : undefined} />
        {mismatch && <em className="lf-warn" id="pw-mismatch">The two passwords are different</em>}</label>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <button className="btn primary" disabled={busy || !current || !next || !again || mismatch}>{busy ? 'Saving…' : 'Change password'}</button>
        {onCancel && <button type="button" className="btn" onClick={onCancel}>Cancel</button>}
        {forced && onLogout && <button type="button" className="link-btn" onClick={onLogout}>Sign out</button>}
      </div>
    </form>
  );
  if (!forced) return form;
  return (
    <div className="login">
      <div className="login-shell" style={{ gridTemplateColumns: '1fr', maxWidth: 460 }}>
        <main className="login-card">
          <div className="login-brand" style={{ marginBottom: 12 }}><Logo /></div>
          <h1>Choose your own password</h1>
          <p className="login-sub">Your administrator gave you a temporary password. Replace it with one only you know before you continue.</p>
          {form}
        </main>
      </div>
    </div>
  );
}

const ROLE_BADGE: Record<Role, string> = { viewer: 'plain', bidder: 'accent', admin: 'ok' };

/** Settings > People: add accounts, set roles, switch people off, reset passwords. */
export function UsersPanel() {
  const toast = useToast();
  const confirm = useConfirm();
  const { data, error, reload } = useLoad(() => api.users(), []);
  const [form, setForm] = useState({ username: '', name: '', role: 'bidder', password: '' });
  const [formError, setFormError] = useState('');
  const [busy, setBusy] = useState(false);
  const [resetting, setResetting] = useState<number | null>(null);
  const [resetPw, setResetPw] = useState('');

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setFormError('');
    try {
      await api.createUser(form);
      toast(`Account created for ${form.username}. Give them the temporary password; they will choose their own at first sign-in.`);
      setForm({ username: '', name: '', role: 'bidder', password: '' }); reload();
    } catch (err) { setFormError((err as Error).message); } finally { setBusy(false); }
  }
  async function change(u: UserRow, patch: { role?: string; active?: boolean; password?: string }) {
    try { await api.editUser(u.id, patch); reload(); return true; } catch (err) { toast((err as Error).message, 'bad'); return false; }
  }
  async function toggleActive(u: UserRow) {
    if (u.active && !(await confirm({ title: `Switch off ${u.username}?`, body: 'They are signed out at once and cannot sign in again until you switch them back on. Their bids and history stay.', confirmLabel: 'Switch off', danger: true }))) return;
    if (await change(u, { active: !u.active })) toast(u.active ? `${u.username} is switched off.` : `${u.username} can sign in again.`);
  }
  async function reset(u: UserRow) {
    if (await change(u, { password: resetPw })) { toast(`Password reset. ${u.username} is signed out and must choose a new password at the next sign-in.`); setResetting(null); setResetPw(''); }
  }

  if (error) return <div className="alert bad">{error}</div>;
  if (!data) return <div className="empty"><span className="spinner" /> Loading…</div>;
  return (
    <div className="set-grid">
      <section className="card" style={{ gridColumn: '1 / -1' }}>
        <h2>People who can sign in</h2>
        <p className="sub">The owner login is set on the server (APP_USERNAME and APP_PASSWORD) and always works. Everyone else is listed here.</p>
        <div className="scroll-x">
          <table className="tbl">
            <thead><tr><th>Person</th><th>Role</th><th>Last sign-in</th><th>Status</th><th /></tr></thead>
            <tbody>
              {data.users.map((u) => (
                <tr key={u.id}>
                  <td><strong>{u.name || u.username}</strong><div className="muted small">{u.username}</div></td>
                  <td>{u.owner ? <span className="badge ok">Admin (owner)</span>
                    : <select aria-label={`Role of ${u.username}`} value={u.role} onChange={(e) => change(u, { role: e.target.value })} style={{ width: 'auto' }}>
                        {data.roles.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
                      </select>}</td>
                  <td className="muted">{u.owner ? 'Server login' : when(u.last_login)}</td>
                  <td>{u.active ? (u.must_change ? <span className="badge warn">Must choose a password</span> : <span className="badge ok">Active</span>) : <span className="badge plain">Switched off</span>}</td>
                  <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    {!u.owner && (resetting === u.id
                      ? <span style={{ display: 'inline-flex', gap: 6 }}>
                          <input type="text" autoComplete="off" placeholder="New temporary password" aria-label={`New temporary password for ${u.username}`} value={resetPw} onChange={(e) => setResetPw(e.target.value)} style={{ width: 200 }} />
                          <button type="button" className="btn small primary" disabled={resetPw.length < data.min_password} onClick={() => reset(u)}>Reset</button>
                          <button type="button" className="btn small" onClick={() => { setResetting(null); setResetPw(''); }}>Cancel</button>
                        </span>
                      : <>
                          <button type="button" className="btn small" onClick={() => { setResetting(u.id); setResetPw(''); }}>Reset password</button>{' '}
                          <button type="button" className="btn small" onClick={() => toggleActive(u)}>{u.active ? 'Switch off' : 'Switch on'}</button>
                        </>)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="card">
        <h2>Add a person</h2>
        <form onSubmit={add} noValidate>
          {formError && <div className="alert bad" role="alert">{formError}</div>}
          <label className="field">Name<input type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} autoComplete="off" /></label>
          <label className="field">Username<span className="hint">Letters, numbers, dot, dash or underscore. They type this to sign in.</span><input type="text" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value.toLowerCase() })} autoComplete="off" autoCapitalize="none" spellCheck={false} /></label>
          <label className="field">Role<select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>{data.roles.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}</select>
            <span className="hint">{data.roles.find((r) => r.key === form.role)?.text}</span></label>
          <label className="field">Temporary password<span className="hint">At least {data.min_password} characters. Tell them in person or by phone, not by email. They must replace it at first sign-in.</span><input type="text" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} autoComplete="off" spellCheck={false} /></label>
          <button className="btn primary" disabled={busy || !form.username || form.password.length < data.min_password}>{busy ? 'Adding…' : 'Add person'}</button>
        </form>
      </section>
      <section className="card">
        <h2>What each role can do</h2>
        <dl className="roles">
          {data.roles.map((r) => <div key={r.key}><dt><span className={`badge ${ROLE_BADGE[r.key]}`}>{r.label}</span></dt><dd>{r.text}.</dd></div>)}
        </dl>
        <p className="muted small">Role changes apply at the next click. Switching someone off signs them out at once.</p>
      </section>
    </div>
  );
}

/** Settings > Activity: who changed what, newest first. */
export function ActivityPanel() {
  const people = useLoad(() => api.users(), []);
  const [user, setUser] = useState('');
  const [rows, setRows] = useState<AuditEntry[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const load = (more: boolean) => {
    setLoading(true);
    api.audit(user, more ? next ?? undefined : undefined).then((r) => { setRows((cur) => (more ? [...cur, ...r.entries] : r.entries)); setNext(r.next); setError(''); }, (e) => setError((e as Error).message)).finally(() => setLoading(false));
  };
  useEffect(() => { load(false); }, [user]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <section className="card">
      <h2>Activity</h2>
      <p className="sub">Every change, refusal, sign-in and download of private files. Reading pages is not logged.</p>
      <label className="field" style={{ maxWidth: 260 }}>Person
        <select value={user} onChange={(e) => setUser(e.target.value)}>
          <option value="">Everyone</option>
          {people.data?.users.map((u) => <option key={u.id} value={u.username}>{u.name || u.username}</option>)}
        </select>
      </label>
      {error && <div className="alert bad">{error}</div>}
      <div className="scroll-x">
        <table className="tbl">
          <thead><tr><th>When</th><th>Who</th><th>What</th><th>On</th><th>From</th></tr></thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.id}>
                <td className="muted" style={{ whiteSpace: 'nowrap' }}>{when(e.at)}</td>
                <td><strong>{e.username}</strong>{e.role && <div className="muted small">{e.role}</div>}</td>
                <td>{e.action.startsWith('Refused') || e.action === 'Failed sign-in' ? <span className="badge bad">{e.action}</span> : e.action}</td>
                <td className="mono muted">{e.target}</td>
                <td className="mono muted">{e.client}</td>
              </tr>
            ))}
            {!loading && rows.length === 0 && <tr><td colSpan={5} className="empty">Nothing recorded yet.</td></tr>}
          </tbody>
        </table>
      </div>
      {next && <button type="button" className="btn" onClick={() => load(true)} disabled={loading} style={{ marginTop: 10 }}>{loading ? 'Loading…' : 'Show older'}</button>}
    </section>
  );
}
