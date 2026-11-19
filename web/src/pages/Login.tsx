import { Logo } from '../Brand';
import { useEffect, useState } from 'react';
import { api, type ApiError } from '../api';
import { tr } from '../i18n';
import { LanguageSwitch } from '../LanguageSwitch';

function greeting(): string {
  const h = Number(new Intl.DateTimeFormat('en-GB', { hour: 'numeric', hour12: false, timeZone: 'Asia/Kolkata' }).format(new Date())) % 24;
  return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
}

/** Decorative analytics illustration: bars, a trend line and a donut, drawn with the theme colours. */
function AnalyticsArt() {
  const bars = [46, 72, 58, 96, 80, 118, 104];
  return (
    <svg className="login-art" viewBox="0 0 420 300" aria-hidden="true" focusable="false">
      <rect x="1" y="1" width="418" height="298" rx="18" className="art-card" />
      <text x="24" y="36" className="art-label">{tr('Sample illustration, not your data')}</text>
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
  const [code, setCode] = useState('');
  const [needCode, setNeedCode] = useState(false);
  const [busy, setBusy] = useState(false);
  const [forgot, setForgot] = useState(false);
  const [who, setWho] = useState('');
  const [sent, setSent] = useState('');
  const [online, setOnline] = useState<boolean | null>(null);
  const [sso, setSso] = useState<{ enabled: boolean; button_text: string } | null>(null);
  const [resetToken, setResetToken] = useState(() => /^#\/reset\/([\w-]{20,200})$/.exec(location.hash)?.[1] ?? '');
  const [newPassword, setNewPassword] = useState('');
  const [again, setAgain] = useState('');
  const [done, setDone] = useState(false);

  useEffect(() => {
    api.ssoStatus().then(setSso, () => setSso(null));
    const m = new URLSearchParams(location.search).get('sso_error');  // the provider's answer was refused: say why, then tidy the address
    if (m) { setError(m); history.replaceState(null, '', location.pathname + location.hash); }
  }, []);
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
      await api.login(username.trim(), password, code.trim());
      onDone();
    } catch (err) {
      const e = err as ApiError;
      if (e.needCode) { setNeedCode(true); setError(''); setCode(''); }
      else if (needCode) { setError(e.message); setCode(''); }  // a wrong code: keep the password and the code box, clear only the code
      else { setError(e.message); setPassword(''); setCode(''); }
      setBusy(false);
    }
  }

  async function askReset(e: React.FormEvent) {
    e.preventDefault();
    if (busy || !who.trim()) return;
    setBusy(true); setError('');
    try { setSent((await api.requestReset(who.trim())).message); }
    catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  async function chooseNew(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (newPassword !== again) { setError('The two passwords do not match.'); return; }
    setBusy(true); setError('');
    try { await api.confirmReset(resetToken, newPassword); setDone(true); history.replaceState(null, '', location.pathname + location.search); }
    catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  function backToSignIn() { setResetToken(''); setDone(false); setNewPassword(''); setAgain(''); setError(''); }

  return (
    <div className="login">
      <div className="login-shell">
        <aside className="login-hero">
          <div className="login-brand">
            <Logo />
            <span className="login-brand-name">Tend<b>AI</b><span className="brand-by">by QMSofts</span></span>
          </div>
          <h2>{tr('Tender intelligence for civil contractors')}</h2>
          <p>{tr('Every new civil tender, sorted, analysed and in your inbox before you start your day.')}</p>
          <AnalyticsArt />
        </aside>

        {resetToken ? (
          <main className="login-card">
            <h1>{done ? tr('Password changed') : tr('Choose a new password')}</h1>
            {done ? (
              <>
                <p className="login-sub">{tr('You can now sign in with your new password.')}</p>
                <button className="btn primary login-submit" onClick={backToSignIn}>{tr('Go to sign in')}</button>
              </>
            ) : (
              <form onSubmit={chooseNew} noValidate>
                <p className="login-sub">Use at least 12 characters. A few random words joined together is easy to remember and hard to guess.</p>
                {error && <div className="alert bad" role="alert">{error}</div>}
                <label className="lf"><span>{tr('New password')}</span><input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} autoComplete="new-password" autoFocus /></label>
                <label className="lf"><span>{tr('Type it again')}</span><input type="password" value={again} onChange={(e) => setAgain(e.target.value)} autoComplete="new-password" /></label>
                <button className="btn primary login-submit" disabled={busy || !newPassword || !again}>{busy ? tr('Saving…') : tr('Save new password')}</button>
                <p style={{ marginTop: 12 }}><button type="button" className="link-btn" onClick={backToSignIn}>{tr('Back to sign in')}</button></p>
              </form>
            )}
          </main>
        ) : (
        <main className="login-card">
          <div className="login-brand login-brand-small"><Logo /></div>
          <h1>{tr('Welcome back')}</h1>
          <p className="login-sub">{greeting()}. Sign in to TendAI by QMSofts.</p>

          <form onSubmit={submit} noValidate>
            {error && <div className="alert bad" role="alert">{error}</div>}

            <label className="lf">
              <span>{tr('Username')}</span>
              <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus autoComplete="username" autoCapitalize="none" spellCheck={false} placeholder={tr('Enter your username')} />
            </label>

            <label className="lf">
              <span className="lf-row"><span>{tr('Password')}</span><button type="button" className="lf-forgot" onClick={() => setForgot((v) => !v)} aria-expanded={forgot}>{tr('Forgot password?')}</button></span>
              <span className="lf-box">
                <input type={show ? 'text' : 'password'} value={password} onChange={(e) => setPassword(e.target.value)}
                  onKeyUp={(e) => setCaps(e.getModifierState('CapsLock'))} onBlur={() => setCaps(false)}
                  autoComplete="current-password" placeholder={tr('Enter your password')} />
                <button type="button" className="lf-toggle" onClick={() => setShow((v) => !v)} aria-pressed={show}>{show ? tr('Hide') : tr('Show')}<span className="sr-only"> {tr('password')}</span></button>
              </span>
              {caps && <em className="lf-warn">{tr('Caps Lock is on')}</em>}
            </label>

            {needCode && (
              <label className="lf">
                <span>{tr('Two-step code')}</span>
                <input type="text" inputMode="numeric" autoComplete="one-time-code" autoFocus maxLength={7} value={code} onChange={(e) => setCode(e.target.value)} placeholder="6-digit code from your app" aria-describedby="code-hint" />
                <em className="lf-warn" id="code-hint" style={{ fontStyle: 'normal' }}>Open your authenticator app and type the current code for TendAI.</em>
              </label>
            )}

            <button className="btn primary login-submit" disabled={busy || !username.trim() || !password || (needCode && code.replace(/\s/g, '').length < 6)}>{busy ? tr('Signing in…') : tr('Sign in')}</button>
          </form>

          {forgot && (
            <form onSubmit={askReset} className="lf-help" style={{ marginTop: 14 }}>
              <strong>{tr('Forgot your password?')}</strong>
              {sent ? <div className="alert ok" role="status">{sent}</div> : (
                <>
                  <span className="muted small">Type your username or the email address on your account and a link to choose a new password is sent to it.</span>
                  {error && <div className="alert bad" role="alert">{error}</div>}
                  <label className="lf"><span>{tr('Username or email')}</span><input type="text" value={who} onChange={(e) => setWho(e.target.value)} autoComplete="username" autoCapitalize="none" spellCheck={false} /></label>
                  <button className="btn" disabled={busy || !who.trim()}>{busy ? tr('Sending…') : tr('Email me a link')}</button>
                  <span className="muted small">No email on your account, or none arrives? Ask your administrator to set a new password for you.</span>
                </>
              )}
            </form>
          )}

          {sso?.enabled && (
            <div style={{ marginTop: 14 }}>
              <p className="muted small" style={{ textAlign: 'center', margin: '0 0 8px' }}>{tr('or')}</p>
              <a className="btn login-submit" href="/api/sso/login" style={{ display: 'block', textAlign: 'center', textDecoration: 'none' }}>{sso.button_text}</a>
            </div>
          )}

          <p className="login-status">
            <span className={`dot ${online === null ? '' : online ? 'up' : 'down'}`} aria-hidden />
            {online === null ? tr('Checking server…') : online ? tr('Server online') : tr('Server unreachable')}
          </p>
        </main>
        )}
      </div>
      <footer className="login-foot"><LanguageSwitch /> © {new Date().getFullYear()} TendAI by QMSofts</footer>
    </div>
  );
}
