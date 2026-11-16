import { ScrollX } from './ScrollX';
import { useEffect, useState } from 'react';
import { api } from './api';
import { Logo } from './Brand';
import { useConfirm } from './confirm';
import { useLoad } from './hooks';
import { useToast } from './ui';
import { resetPeople } from './Team';
import type { AuditEntry, Me, Role, UserRow } from './types';

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

/** Account & security: password, two-step sign-in, and signing out other devices. */
export function AccountPanel({ user, onClose, onChanged }: { user: Me; onClose: () => void; onChanged: () => void }) {
  const toast = useToast();
  const [setup, setSetup] = useState<{ secret: string; uri: string } | null>(null);
  const [code, setCode] = useState('');
  const [offPw, setOffPw] = useState('');
  const [asking, setAsking] = useState(false);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<unknown>, done: string) => { setBusy(true); setErr(''); try { await fn(); toast(done); onChanged(); return true; } catch (e) { setErr((e as Error).message); return false; } finally { setBusy(false); } };
  const group = (s: string) => s.replace(/(.{4})/g, '$1 ').trim();
  return (
    <div className="acct">
      <section>
        <h3>Password</h3>
        {user.owner ? <p className="sub">The owner login's password is set on the server (APP_PASSWORD).</p> : <ChangePassword onDone={onClose} onCancel={onClose} />}
      </section>
      {!user.owner && <EmailField current={user.email ?? ''} onSaved={onChanged} />}
      <section>
        <h3>Two-step sign-in {user.two_step && <span className="badge ok">On</span>}</h3>
        {user.owner && <p className="sub">The owner login uses the server variable OWNER_TOTP_SECRET to require a code. Ask whoever manages the server to set it.</p>}
        {!user.owner && !user.two_step && !setup && (
          <>
            <p className="sub">Adds a 6-digit code from an authenticator app (Google Authenticator, Microsoft Authenticator, Authy, 1Password) to your password, so a stolen password alone is not enough.</p>
            <button type="button" className="btn" disabled={busy} onClick={async () => { setBusy(true); setErr(''); try { setSetup(await api.twoStepStart()); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); } }}>Set up two-step sign-in</button>
          </>
        )}
        {setup && (
          <div className="ai-doc">
            <p><strong>1.</strong> In your authenticator app choose <em>Add account</em> then <em>Enter a setup key</em>, and type this key (account: {user.username}, type: time-based):</p>
            <p className="mono acct-key" aria-label="Setup key">{group(setup.secret)}</p>
            <p className="muted small">On a phone you can also <a href={setup.uri}>open the setup link</a> in the authenticator app.</p>
            <label className="lf"><span><strong>2.</strong> Type the 6-digit code the app shows now</span>
              <input type="text" inputMode="numeric" autoComplete="one-time-code" maxLength={7} value={code} onChange={(e) => setCode(e.target.value)} /></label>
            <div style={{ display: 'flex', gap: 8 }}>
              <button type="button" className="btn primary" disabled={busy || code.replace(/\s/g, '').length < 6} onClick={async () => { if (await run(() => api.twoStepEnable(code), 'Two-step sign-in is on.')) { setSetup(null); setCode(''); } }}>Turn on</button>
              <button type="button" className="btn" onClick={() => { setSetup(null); setCode(''); }}>Cancel</button>
            </div>
          </div>
        )}
        {user.two_step && !user.owner && (asking ? (
          <div style={{ display: 'flex', gap: 8, alignItems: 'end' }}>
            <label className="lf" style={{ margin: 0 }}><span>Your password</span><input type="password" autoComplete="current-password" value={offPw} onChange={(e) => setOffPw(e.target.value)} /></label>
            <button type="button" className="btn danger" disabled={busy || !offPw} onClick={async () => { if (await run(() => api.twoStepDisable(offPw), 'Two-step sign-in is off.')) { setAsking(false); setOffPw(''); } }}>Turn off</button>
            <button type="button" className="btn" onClick={() => { setAsking(false); setOffPw(''); }}>Cancel</button>
          </div>
        ) : <button type="button" className="btn" onClick={() => setAsking(true)}>Turn off two-step sign-in</button>)}
        {err && <div className="alert bad" role="alert" style={{ marginTop: 10 }}>{err}</div>}
      </section>
      <DevicesSection busy={busy} onSignOutOthers={() => run(() => api.endOtherSessions(), 'Other devices were signed out.')} owner={user.owner} />
      {!user.owner && (
        <section>
          <h3>Your data</h3>
          <p className="sub">A copy of what TendAI keeps about you: your account details (never the password), your activity, comments and tasks.</p>
          <a className="btn" href={api.exportOwnUrl} download>Download my data (.json)</a>
        </section>
      )}
    </div>
  );
}

function DevicesSection({ busy, onSignOutOthers, owner }: { busy: boolean; onSignOutOthers: () => void; owner: boolean }) {
  const toast = useToast();
  const list = useLoad(() => api.mySessions(), []);
  const sessions = list.data?.sessions ?? [];
  async function end(ref: string) {
    try { await api.endSession(ref); toast('That device was signed out.'); list.reload(); } catch (e) { toast((e as Error).message, 'bad'); list.reload(); }
  }
  const seen = (t: number) => new Date(t * 1000).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
  return (
    <section>
      <h3>Devices</h3>
      <p className="sub">Where you are signed in. Spot one you do not recognise, or one on a shared computer you no longer use? Sign it out.</p>
      {list.error && <div className="alert bad">{list.error}</div>}
      {sessions.length > 0 && (
        <ul className="plain" style={{ margin: '0 0 10px', padding: 0, listStyle: 'none' }}>
          {sessions.map((d) => (
            <li key={d.ref} style={{ display: 'flex', gap: 10, alignItems: 'center', justifyContent: 'space-between', padding: '6px 0', borderTop: '1px solid var(--border)' }}>
              <span><strong>{d.device}</strong>{d.current && <span className="badge ok" style={{ marginLeft: 6 }}>This device</span>}<div className="muted small">Last used {seen(d.last_seen)}{d.client ? ` · ${d.client}` : ''}</div></span>
              {!d.current && <button type="button" className="btn small" onClick={() => end(d.ref)}>Sign out</button>}
            </li>
          ))}
        </ul>
      )}
      {sessions.length > 1 && <button type="button" className="btn" disabled={busy} onClick={() => { onSignOutOthers(); setTimeout(list.reload, 400); }}>Sign out all other devices</button>}
      {owner && <p className="muted small">The owner login can be used on several devices at once.</p>}
    </section>
  );
}

function EmailField({ current, onSaved }: { current: string; onSaved: () => void }) {
  const toast = useToast();
  const [v, setV] = useState(current);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr('');
    try { await api.setOwnEmail(v.trim()); toast(v.trim() ? 'Email saved' : 'Email removed'); onSaved(); } catch (er) { setErr((er as Error).message); } finally { setBusy(false); }
  }
  return (
    <section>
      <h3>Email</h3>
      <p className="sub">Used to tell you when a task is given to you, when someone mentions you, and when a bid you asked about is approved. It is not shown to other people.</p>
      <form onSubmit={save} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <label className="lf" style={{ margin: 0, flex: '1 1 220px' }}><span>Your email address</span>
          <input type="email" value={v} onChange={(e) => setV(e.target.value)} autoComplete="email" aria-invalid={!!err || undefined} aria-describedby={err ? 'email-err' : undefined} />
          {err && <em className="lf-warn" id="email-err" style={{ fontStyle: 'normal' }}>{err}</em>}</label>
        <button className="btn" style={{ marginTop: 22 }} disabled={busy || v.trim() === current}>{busy ? 'Saving…' : 'Save'}</button>
      </form>
    </section>
  );
}

const ROLE_BADGE: Record<Role, string> = { viewer: 'plain', bidder: 'accent', admin: 'ok' };

/** Settings > People: add accounts, set roles, switch people off, reset passwords. */
export function UsersPanel() {
  const toast = useToast();
  const confirm = useConfirm();
  const { data, error, reload } = useLoad(() => api.users(), []);
  const [form, setForm] = useState({ username: '', name: '', role: 'bidder', password: '', email: '' });
  const [formError, setFormError] = useState('');
  const [busy, setBusy] = useState(false);
  const [resetting, setResetting] = useState<number | null>(null);
  const [resetPw, setResetPw] = useState('');
  const [erasing, setErasing] = useState<number | null>(null);
  const [eraseText, setEraseText] = useState('');

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setFormError('');
    try {
      await api.createUser(form);
      toast(`Account created for ${form.username}. Give them the temporary password; they will choose their own at first sign-in.`);
      setForm({ username: '', name: '', role: 'bidder', password: '', email: '' }); resetPeople(); reload();
    } catch (err) { setFormError((err as Error).message); } finally { setBusy(false); }
  }
  async function change(u: UserRow, patch: { role?: string; active?: boolean; password?: string; reset_2fa?: boolean }) {
    try { await api.editUser(u.id, patch); reload(); return true; } catch (err) { toast((err as Error).message, 'bad'); return false; }
  }
  async function toggleActive(u: UserRow) {
    if (u.active && !(await confirm({ title: `Switch off ${u.username}?`, body: 'They are signed out at once and cannot sign in again until you switch them back on. Their bids and history stay.', confirmLabel: 'Switch off', danger: true }))) return;
    if (await change(u, { active: !u.active })) toast(u.active ? `${u.username} is switched off.` : `${u.username} can sign in again.`);
  }
  async function resetTwoStep(u: UserRow) {
    if (!(await confirm({ title: `Reset two-step sign-in for ${u.username}?`, body: 'Use this when they have lost their phone. They sign in with just their password, and can set the app up again under Account & security.', confirmLabel: 'Reset two-step', danger: true }))) return;
    if (await change(u, { reset_2fa: true })) toast(`Two-step sign-in reset for ${u.username}.`);
  }
  async function erase(u: UserRow) {
    try {
      const r = await api.erasePerson(u.id, eraseText);
      toast(`Erased. Their name was removed from ${r.changed.comments ?? 0} comment(s), ${r.changed.activity ?? 0} log entries and ${r.changed.bids_owned ?? 0} bid(s). It now reads ${r.handle}.`);
      setErasing(null); setEraseText(''); reload();
    } catch (err) { toast((err as Error).message, 'bad'); }
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
        <ScrollX label="People who can sign in">
          <table className="tbl">
            <thead><tr><th>Person</th><th>Role</th><th>Last sign-in</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {data.users.map((u) => (
                <tr key={u.id}>
                  <td><strong>{u.name || u.username}</strong><div className="muted small">{u.username}{u.email ? ` · ${u.email}` : ''}</div></td>
                  <td>{u.owner ? <span className="badge ok">Admin (owner)</span>
                    : <select aria-label={`Role of ${u.username}`} value={u.role} onChange={(e) => change(u, { role: e.target.value })} style={{ width: 'auto' }}>
                        {data.roles.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
                      </select>}</td>
                  <td className="muted">{u.owner ? 'Server login' : when(u.last_login)}</td>
                  <td>{u.active ? (u.must_change ? <span className="badge warn">Must choose a password</span> : <span className="badge ok">Active</span>) : <span className="badge plain">Switched off</span>}{u.two_step && <span className="badge accent" style={{ marginLeft: 6 }} title="Signs in with a code from an authenticator app">Two-step</span>}</td>
                  <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    {!u.owner && (resetting === u.id
                      ? <span style={{ display: 'inline-flex', gap: 6 }}>
                          <input type="text" autoComplete="off" placeholder="New temporary password" aria-label={`New temporary password for ${u.username}`} value={resetPw} onChange={(e) => setResetPw(e.target.value)} style={{ width: 200 }} />
                          <button type="button" className="btn small primary" disabled={resetPw.length < data.min_password} onClick={() => reset(u)}>Reset</button>
                          <button type="button" className="btn small" onClick={() => { setResetting(null); setResetPw(''); }}>Cancel</button>
                        </span>
                      : <>
                          <button type="button" className="btn small" onClick={() => { setResetting(u.id); setResetPw(''); }}>Reset password</button>{' '}
                          {u.two_step && <><button type="button" className="btn small" onClick={() => resetTwoStep(u)}>Reset two-step</button>{' '}</>}
                          <button type="button" className="btn small" onClick={() => toggleActive(u)}>{u.active ? 'Switch off' : 'Switch on'}</button>{' '}
                          <a className="btn small" href={api.exportPersonUrl(u.id)} download title="Everything stored about this person, as a file">Download data</a>
                          {!u.active && !u.username.startsWith('erased-') && (erasing === u.id
                            ? <span style={{ display: 'inline-flex', gap: 6, marginLeft: 6 }}>
                                <input type="text" autoComplete="off" placeholder={`Type ${u.username} to confirm`} aria-label={`Type ${u.username} to confirm erasing`} value={eraseText} onChange={(e) => setEraseText(e.target.value)} style={{ width: 200 }} />
                                <button type="button" className="btn small danger" disabled={eraseText.trim().toLowerCase() !== u.username} onClick={() => erase(u)}>Erase</button>
                                <button type="button" className="btn small" onClick={() => { setErasing(null); setEraseText(''); }}>Cancel</button>
                              </span>
                            : <button type="button" className="btn small danger" style={{ marginLeft: 6 }} onClick={() => { setErasing(u.id); setEraseText(''); }} title="Remove who this person was: name, email, password, and their name on comments and the log">Erase data</button>)}
                        </>)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollX>
      </section>
      <section className="card">
        <h2>Add a person</h2>
        <form onSubmit={add} noValidate>
          {formError && <div className="alert bad" role="alert">{formError}</div>}
          <label className="field">Name<input type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} autoComplete="off" /></label>
          <label className="field">Username<span className="hint">Letters, numbers, dot, dash or underscore. They type this to sign in.</span><input type="text" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value.toLowerCase() })} autoComplete="off" autoCapitalize="none" spellCheck={false} /></label>
          <label className="field">Email (optional)<span className="hint">For task, mention and approval notices.</span><input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} autoComplete="off" /></label>
          <label className="field">Role<select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>{data.roles.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}</select>
            <span className="hint">{data.roles.find((r) => r.key === form.role)?.text}</span></label>
          <label className="field">Temporary password<span className="hint">At least {data.min_password} characters. Tell them in person or by phone, not by email. They must replace it at first sign-in.</span><input type="text" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} autoComplete="off" spellCheck={false} /></label>
          <button className="btn primary" disabled={busy || !form.username || form.password.length < data.min_password}>{busy ? 'Adding…' : 'Add person'}</button>
        </form>
      </section>
      <ApprovalSetting />
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

/** One switch: must an administrator approve a bid before it moves from Interested to Preparing? Saves at once. */
function ApprovalSetting() {
  const toast = useToast();
  const cur = useLoad(() => api.settings(), []);
  const [busy, setBusy] = useState(false);
  async function toggle(on: boolean) {
    setBusy(true);
    try { await api.saveSettings({ require_signoff: on }); cur.reload(); toast(on ? 'Bids now need an administrator\'s approval before Preparing.' : 'Approval is optional again.'); } catch (e) { toast((e as Error).message, 'bad'); } finally { setBusy(false); }
  }
  return (
    <section className="card">
      <h2>Approval before bidding</h2>
      <p className="sub">When on, a bidder asks for approval on the bid's Team tab, and an administrator approves or declines it. Administrators are not held up by this.</p>
      <label className="check"><input type="checkbox" checked={!!cur.data?.require_signoff} disabled={busy || !cur.data} onChange={(e) => toggle(e.target.checked)} />Require approval before a bid moves from Interested to Preparing</label>
    </section>
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
      <ScrollX label="Activity log">
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
      </ScrollX>
      {next && <button type="button" className="btn" onClick={() => load(true)} disabled={loading} style={{ marginTop: 10 }}>{loading ? 'Loading…' : 'Show older'}</button>}
    </section>
  );
}
