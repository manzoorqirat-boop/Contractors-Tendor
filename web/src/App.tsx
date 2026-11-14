import { useEffect, useState } from 'react';
import { api, setUnauthorisedHandler } from './api';
import { useLoad } from './hooks';
import { Dashboard } from './pages/Dashboard';
import { Login } from './pages/Login';
import { RunsPage } from './pages/Runs';
import { SettingsPage } from './pages/Settings';
import { TendersPage } from './pages/Tenders';
import type { Filters } from './types';

export type Tab = 'dashboard' | 'tenders' | 'runs' | 'settings';
const TABS: { id: Tab; label: string }[] = [
  { id: 'dashboard', label: 'Dashboard' }, { id: 'tenders', label: 'Tenders' }, { id: 'runs', label: 'Runs' }, { id: 'settings', label: 'Settings' },
];

export const defaultFilters: Filters = { q: '', portal: '', category: '', status: 'open', min_value: '', sort: 'closing', page: 1 };

export function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);

  useEffect(() => {
    setUnauthorisedHandler(() => setAuthed(false));
    api.me().then((r) => setAuthed(r.authenticated), () => setAuthed(false));
  }, []);

  if (authed === null) return <div className="login"><span className="spinner" aria-label="Loading" /></div>;
  if (!authed) return <Login onDone={() => setAuthed(true)} />;
  return <Main onLogout={() => api.logout().finally(() => setAuthed(false))} />;
}

function Main({ onLogout }: { onLogout: () => void }) {
  const [tab, setTab] = useState<Tab>(() => (TABS.find((t) => t.id === location.hash.slice(1))?.id ?? 'dashboard'));
  const [filters, setFilters] = useState<Filters>(defaultFilters);
  const config = useLoad(() => api.config(), []);

  const go = (t: Tab, f?: Partial<Filters>) => {
    if (f) setFilters({ ...defaultFilters, ...f });
    setTab(t);
    history.replaceState(null, '', `#${t}`);
    window.scrollTo(0, 0);
  };

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden><svg width="16" height="16" viewBox="0 0 32 32"><path d="M9 22V10h14M9 16h10M9 22h14" stroke="#fff" strokeWidth="3" fill="none" strokeLinecap="round" /></svg></span>
          Tender Dashboard
        </div>
        <nav className="nav" aria-label="Main">
          {TABS.map((t) => <button key={t.id} aria-current={tab === t.id ? 'page' : undefined} onClick={() => go(t.id)}>{t.label}</button>)}
        </nav>
        <button className="link-btn" onClick={onLogout}>Log out</button>
      </header>

      {config.error && <div className="alert bad">{config.error}</div>}
      {config.data && (
        <main>
          {tab === 'dashboard' && <Dashboard config={config.data} go={go} />}
          {tab === 'tenders' && <TendersPage config={config.data} filters={filters} setFilters={setFilters} />}
          {tab === 'runs' && <RunsPage config={config.data} />}
          {tab === 'settings' && <SettingsPage config={config.data} onSaved={config.reload} />}
        </main>
      )}
    </div>
  );
}
