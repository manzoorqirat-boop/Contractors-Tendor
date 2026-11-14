import { useEffect, useState } from 'react';
import { api } from '../api';

const PORTALS = ['Maharashtra', 'Delhi', 'Uttar Pradesh', 'Central (CPPP)'];
const POINTS = [
  { title: 'Civil works only', text: 'Roads, buildings, drainage, water and more, filtered from every listing.' },
  { title: 'Daily digest', text: 'New and closing-soon tenders emailed with a spreadsheet attached.' },
  { title: 'Explore and compare', text: 'Slice by portal, department, value band or closing date.' },
];

function greeting(): string {
  const h = Number(new Intl.DateTimeFormat('en-GB', { hour: 'numeric', hour12: false, timeZone: 'Asia/Kolkata' }).format(new Date())) % 24;
  return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
}

export function Login({ onDone }: { onDone: () => void }) {
  const [password, setPassword] = useState('');
  const [show, setShow] = useState(false);
  const [caps, setCaps] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [online, setOnline] = useState<boolean | null>(null);
  const [now, setNow] = useState(() => new Date());

  // Smart details: live server status, clock in India time, and a greeting.
  useEffect(() => {
    let alive = true;
    const ping = () => fetch('/api/health').then((r) => alive && setOnline(r.ok), () => alive && setOnline(false));
    ping();
    const a = setInterval(ping, 30000);
    const b = setInterval(() => setNow(new Date()), 30000);
    return () => { alive = false; clearInterval(a); clearInterval(b); };
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await api.login(password);
      onDone();
    } catch (err) {
      setError((err as Error).message);
      setPassword('');
      setBusy(false);
    }
  }

  const date = new Intl.DateTimeFormat('en-IN', { weekday: 'long', day: 'numeric', month: 'long', hour: 'numeric', minute: '2-digit', timeZone: 'Asia/Kolkata' }).format(now);

  return (
    <div className="login">
      <div className="login-wrap">
        <aside className="login-info">
          <span className="logo" aria-hidden><svg width="16" height="16" viewBox="0 0 32 32"><path d="M9 22V10h14M9 16h10M9 22h14" stroke="#fff" strokeWidth="3" fill="none" strokeLinecap="round" /></svg></span>
          <h1>Tender Dashboard</h1>
          <p className="sub">Civil tenders from government portals, collected and sorted for you.</p>
          <ul className="login-points">
            {POINTS.map((p) => <li key={p.title}><strong>{p.title}</strong><span>{p.text}</span></li>)}
          </ul>
          <div className="login-portals" aria-label="Portals covered">
            {PORTALS.map((p) => <span key={p} className="badge plain">{p}</span>)}
          </div>
        </aside>

        <form className="card" onSubmit={submit}>
          <h2>{greeting()}</h2>
          <p className="sub" style={{ marginBottom: 16 }}>{date} IST</p>
          {error && <div className="alert bad" role="alert">{error}</div>}
          <label className="field">
            Password
            <span className="pw">
              <input type={show ? 'text' : 'password'} value={password} onChange={(e) => setPassword(e.target.value)}
                onKeyUp={(e) => setCaps(e.getModifierState('CapsLock'))} onBlur={() => setCaps(false)}
                autoFocus autoComplete="current-password" required />
              <button type="button" className="link-btn" onClick={() => setShow((s) => !s)} aria-pressed={show}>{show ? 'Hide' : 'Show'}</button>
            </span>
          </label>
          {caps && <p className="small" style={{ color: 'var(--warn)', margin: '-4px 0 10px' }}>Caps Lock is on.</p>}
          <button className="btn primary" style={{ width: '100%', justifyContent: 'center' }} disabled={busy || !password}>{busy ? 'Checking…' : 'Log in'}</button>
          <p className="login-status small muted">
            <span className={`dot ${online === null ? '' : online ? 'up' : 'down'}`} aria-hidden />
            {online === null ? 'Checking server…' : online ? 'Server online' : 'Server unreachable'}
            <span> · Session lasts 7 days</span>
          </p>
        </form>
      </div>
      <footer className="login-foot small muted">© {now.getFullYear()} Tender Dashboard · Data from public government portals</footer>
    </div>
  );
}
