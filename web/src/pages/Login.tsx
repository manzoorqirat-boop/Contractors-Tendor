import { useEffect, useState } from 'react';
import { api } from '../api';

function greeting(): string {
  const h = Number(new Intl.DateTimeFormat('en-GB', { hour: 'numeric', hour12: false, timeZone: 'Asia/Kolkata' }).format(new Date())) % 24;
  return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
}

export function Login({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [show, setShow] = useState(false);
  const [caps, setCaps] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [online, setOnline] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;
    const ping = () => fetch('/api/health').then((r) => alive && setOnline(r.ok), () => alive && setOnline(false));
    ping();
    const id = setInterval(ping, 30000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await api.login(username.trim(), password);
      onDone();
    } catch (err) {
      setError((err as Error).message);
      setPassword('');
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <main className="login-card">
        <span className="logo login-logo" aria-hidden>
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></svg>
        </span>
        <h1>Data Analytics</h1>
        <p className="login-sub">{greeting()}. Sign in to continue.</p>

        <form onSubmit={submit} noValidate>
          {error && <div className="alert bad" role="alert">{error}</div>}

          <label className="lf">
            <span>Username</span>
            <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus autoComplete="username" autoCapitalize="none" spellCheck={false} placeholder="Enter your username" />
          </label>

          <label className="lf">
            <span>Password</span>
            <span className="lf-box">
              <input type={show ? 'text' : 'password'} value={password} onChange={(e) => setPassword(e.target.value)}
                onKeyUp={(e) => setCaps(e.getModifierState('CapsLock'))} onBlur={() => setCaps(false)}
                autoComplete="current-password" placeholder="Enter your password" />
              <button type="button" className="lf-toggle" onClick={() => setShow((v) => !v)} aria-pressed={show} aria-label={show ? 'Hide password' : 'Show password'}>{show ? 'Hide' : 'Show'}</button>
            </span>
            {caps && <em className="lf-warn">Caps Lock is on</em>}
          </label>

          <button className="btn primary login-submit" disabled={busy || !username.trim() || !password}>{busy ? 'Signing in…' : 'Sign in'}</button>
        </form>

        <p className="login-status">
          <span className={`dot ${online === null ? '' : online ? 'up' : 'down'}`} aria-hidden />
          {online === null ? 'Checking server…' : online ? 'Server online' : 'Server unreachable'}
        </p>
      </main>
      <footer className="login-foot">© {new Date().getFullYear()} Data Analytics</footer>
    </div>
  );
}
