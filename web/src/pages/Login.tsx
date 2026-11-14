import { useEffect, useState } from 'react';
import { api } from '../api';

function greeting(): string {
  const h = Number(new Intl.DateTimeFormat('en-GB', { hour: 'numeric', hour12: false, timeZone: 'Asia/Kolkata' }).format(new Date())) % 24;
  return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
}

/** Decorative analytics illustration: bars, a trend line and a donut, drawn with the theme colours. */
function AnalyticsArt() {
  const bars = [46, 72, 58, 96, 80, 118, 104];
  return (
    <svg className="login-art" viewBox="0 0 420 300" role="img" aria-label="Charts showing tender trends">
      <rect x="1" y="1" width="418" height="298" rx="18" className="art-card" />
      <text x="24" y="36" className="art-label">New tenders · last 7 days</text>
      <text x="24" y="66" className="art-num">1,284</text>
      <rect x="112" y="48" width="52" height="22" rx="11" className="art-pill" />
      <text x="138" y="63" textAnchor="middle" className="art-pill-t">+12%</text>
      {[0, 1, 2].map((i) => <line key={i} x1="24" x2="262" y1={110 + i * 45} y2={110 + i * 45} className="art-grid" />)}
      {bars.map((h, i) => <rect key={i} x={30 + i * 34} y={246 - h} width="20" height={h} rx="5" className={i === 5 ? 'art-bar hot' : 'art-bar'} />)}
      <polyline points="40,190 74,160 108,172 142,128 176,140 210,96 244,108" className="art-line" />
      <circle cx="210" cy="96" r="5" className="art-dot" />
      <circle cx="342" cy="106" r="42" className="art-ring" />
      <circle cx="342" cy="106" r="42" className="art-ring seg1" strokeDasharray="120 264" transform="rotate(-90 342 106)" />
      <circle cx="342" cy="106" r="42" className="art-ring seg2" strokeDasharray="60 264" strokeDashoffset="-124" transform="rotate(-90 342 106)" />
      <text x="342" y="112" textAnchor="middle" className="art-donut">AI</text>
      <g className="art-legend">
        <circle cx="300" cy="176" r="4" className="lg1" /><text x="310" y="180">Roads &amp; bridges</text>
        <circle cx="300" cy="198" r="4" className="lg2" /><text x="310" y="202">Buildings</text>
        <circle cx="300" cy="220" r="4" className="lg3" /><text x="310" y="224">Water &amp; drainage</text>
      </g>
    </svg>
  );
}

export function Login({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [show, setShow] = useState(false);
  const [caps, setCaps] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [forgot, setForgot] = useState(false);
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
      <div className="login-shell">
        <aside className="login-hero">
          <div className="login-brand">
            <span className="logo" aria-hidden><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></svg></span>
            <span className="login-brand-name">Tend<b>AI</b></span>
          </div>
          <h2>Tender intelligence for civil contractors</h2>
          <p>Every new civil tender, sorted, analysed and in your inbox before you start your day.</p>
          <AnalyticsArt />
        </aside>

        <main className="login-card">
          <h1>Welcome back</h1>
          <p className="login-sub">{greeting()}. Sign in to your TendAI workspace.</p>

          <form onSubmit={submit} noValidate>
            {error && <div className="alert bad" role="alert">{error}</div>}

            <label className="lf">
              <span>Username</span>
              <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus autoComplete="username" autoCapitalize="none" spellCheck={false} placeholder="Enter your username" />
            </label>

            <label className="lf">
              <span className="lf-row"><span>Password</span><button type="button" className="lf-forgot" onClick={() => setForgot((v) => !v)} aria-expanded={forgot}>Forgot password?</button></span>
              <span className="lf-box">
                <input type={show ? 'text' : 'password'} value={password} onChange={(e) => setPassword(e.target.value)}
                  onKeyUp={(e) => setCaps(e.getModifierState('CapsLock'))} onBlur={() => setCaps(false)}
                  autoComplete="current-password" placeholder="Enter your password" />
                <button type="button" className="lf-toggle" onClick={() => setShow((v) => !v)} aria-pressed={show} aria-label={show ? 'Hide password' : 'Show password'}>{show ? 'Hide' : 'Show'}</button>
              </span>
              {caps && <em className="lf-warn">Caps Lock is on</em>}
            </label>

            {forgot && (
              <div className="alert warn lf-help" role="note">
                <strong>Reset the password</strong>
                <span>Ask your administrator. They can set a new one in the hosting dashboard (the <code>APP_PASSWORD</code> variable), and it works after the service restarts. The username is <code>APP_USERNAME</code>.</span>
              </div>
            )}

            <button className="btn primary login-submit" disabled={busy || !username.trim() || !password}>{busy ? 'Signing in…' : 'Sign in'}</button>
          </form>

          <p className="login-status">
            <span className={`dot ${online === null ? '' : online ? 'up' : 'down'}`} aria-hidden />
            {online === null ? 'Checking server…' : online ? 'Server online' : 'Server unreachable'}
          </p>
        </main>
      </div>
      <footer className="login-foot">© {new Date().getFullYear()} TendAI</footer>
    </div>
  );
}
