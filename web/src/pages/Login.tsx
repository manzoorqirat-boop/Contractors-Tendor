import { useState } from 'react';
import { api } from '../api';

export function Login({ onDone }: { onDone: () => void }) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api.login(password);
      onDone();
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <form className="card" onSubmit={submit}>
        <span className="logo" aria-hidden><svg width="16" height="16" viewBox="0 0 32 32"><path d="M9 22V10h14M9 16h10M9 22h14" stroke="#fff" strokeWidth="3" fill="none" strokeLinecap="round" /></svg></span>
        <h1>Tender Dashboard</h1>
        <p className="sub" style={{ marginBottom: 18 }}>Civil tenders from government portals.</p>
        {error && <div className="alert bad" role="alert">{error}</div>}
        <label className="field">
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus autoComplete="current-password" required />
        </label>
        <button className="btn primary" style={{ width: '100%', justifyContent: 'center' }} disabled={busy || !password}>{busy ? 'Checking…' : 'Log in'}</button>
      </form>
    </div>
  );
}
