import { useCallback, useEffect, useRef, useState } from 'react';
import { api, setUnauthorisedHandler } from './api';
import { DISPLAY, EMPTY, fromParams, toParams } from './filters';
import { useLoad } from './hooks';
import { AskBar } from './ai';
import { Dashboard } from './pages/Dashboard';
import { Explore } from './pages/Explore';
import { Login } from './pages/Login';
import { RunsPage } from './pages/Runs';
import { SettingsPage } from './pages/Settings';
import { TendersPage } from './pages/Tenders';
import { loadBranding, Logo } from './Brand';
import { ToastProvider } from './ui';
import type { Display, Facets, Filters } from './types';

export type Tab = 'dashboard' | 'explore' | 'tenders' | 'runs' | 'settings';
const TABS: { id: Tab; label: string }[] = [
  { id: 'dashboard', label: 'Dashboard' }, { id: 'explore', label: 'Explore' }, { id: 'tenders', label: 'Tenders' }, { id: 'runs', label: 'Runs' }, { id: 'settings', label: 'Settings' },
];

/** What every page that shows tenders needs: the filters, the display choices, and a way to change them or jump elsewhere. */
export interface Nav {
  filters: Filters; display: Display; facets: Facets | null;
  setFilters: (f: Filters) => void;
  setDisplay: (d: Partial<Display>) => void;
  /** Go to another page, optionally with new filters/display. Adds a history entry, so Back returns. */
  go: (tab: Tab, filters?: Filters, display?: Partial<Display>) => void;
}

type Theme = 'system' | 'light' | 'dark';
const THEMES: Theme[] = ['system', 'light', 'dark'];

function readTheme(): Theme {
  try { const t = localStorage.getItem('theme'); if (t === 'light' || t === 'dark') return t; } catch { /* storage blocked: follow the system */ }
  return 'system';
}
function applyTheme(t: Theme) {
  if (t === 'system') document.documentElement.removeAttribute('data-theme'); else document.documentElement.setAttribute('data-theme', t);
}

function parseHash(): { tab: Tab; filters: Filters; display: Display } {
  const raw = location.hash.replace(/^#\/?/, '');
  const [path, query = ''] = raw.split('?');
  const tab = TABS.find((t) => t.id === path)?.id ?? 'dashboard';
  return { tab, ...fromParams(new URLSearchParams(query)) };
}

export function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  useEffect(() => { applyTheme(readTheme()); loadBranding(); }, []);
  useEffect(() => {
    setUnauthorisedHandler(() => setAuthed(false));
    api.me().then((r) => setAuthed(r.authenticated), () => setAuthed(false));
  }, []);

  return (
    <ToastProvider>
      {authed === null ? <div className="login"><span className="spinner" aria-label="Loading" /></div>
        : !authed ? <Login onDone={() => setAuthed(true)} />
        : <Main onLogout={() => api.logout().finally(() => setAuthed(false))} />}
    </ToastProvider>
  );
}

function Main({ onLogout }: { onLogout: () => void }) {
  const initial = useRef(parseHash()).current;
  const [tab, setTab] = useState<Tab>(initial.tab);
  const [filters, setFiltersState] = useState<Filters>(initial.filters);
  const [display, setDisplayState] = useState<Display>(initial.display);
  const [theme, setTheme] = useState<Theme>(readTheme);
  const push = useRef(false);
  const config = useLoad(() => api.config(), []);
  const facets = useLoad(() => api.facets(), [tab === 'runs' || tab === 'settings' ? 'other' : 'data']);

  // The address bar always describes the current view, so a refresh, a bookmark or a shared link returns to it.
  useEffect(() => {
    const url = `#/${tab}${(() => { const q = toParams(filters, display).toString(); return q ? `?${q}` : ''; })()}`;
    if (url === location.hash) return;
    if (push.current) { history.pushState(null, '', url); push.current = false; } else history.replaceState(null, '', url);
  }, [tab, filters, display]);

  useEffect(() => {
    const onPop = () => { const s = parseHash(); setTab(s.tab); setFiltersState(s.filters); setDisplayState(s.display); };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const setFilters = useCallback((f: Filters) => { setFiltersState(f); setDisplayState((d) => (d.page === 1 ? d : { ...d, page: 1 })); }, []);
  const setDisplay = useCallback((d: Partial<Display>) => setDisplayState((cur) => ({ ...cur, ...d })), []);
  const go = useCallback((t: Tab, f?: Filters, d?: Partial<Display>) => {
    push.current = true;
    setTab(t);
    if (f) setFiltersState(f);
    if (d) setDisplayState((cur) => ({ ...cur, page: 1, ...d }));
    window.scrollTo(0, 0);
  }, []);

  const nav: Nav = { filters, display, facets: facets.data, setFilters, setDisplay, go };
  const cycleTheme = () => { const next = THEMES[(THEMES.indexOf(theme) + 1) % 3]; setTheme(next); applyTheme(next); try { localStorage.setItem('theme', next); } catch { /* ignore */ } };

  return (
    <div className="shell">
      <a href="#main" className="skip" onClick={(e) => { e.preventDefault(); document.getElementById('main')?.focus(); }}>Skip to content</a>
      <header className="topbar">
        <div className="brand" aria-label="TendAI by QMSofts">
          <Logo />
          Tend<span className="brand-ai">AI</span><span className="brand-by">by QMSofts</span>
        </div>
        <nav className="nav" aria-label="Main">
          {TABS.map((t) => <button key={t.id} aria-current={tab === t.id ? 'page' : undefined} onClick={() => go(t.id, t.id === 'dashboard' || t.id === 'runs' || t.id === 'settings' ? undefined : filters)}>{t.label}</button>)}
        </nav>
        <button className="link-btn" onClick={cycleTheme} title="Change colour theme" aria-label={`Theme: ${theme}. Click to change.`}>Theme: {theme === 'dark' ? 'Dark' : theme === 'light' ? 'Light' : 'Auto'}</button>
        <button className="link-btn" onClick={onLogout}>Log out</button>
      </header>

      {config.error && <div className="alert bad">{config.error}</div>}
      {config.data?.ai_enabled && (tab === 'dashboard' || tab === 'explore' || tab === 'tenders') && <div className="askwrap"><AskBar nav={nav} /></div>}
      <main id="main" tabIndex={-1}>
        {config.data && (
          <>
            {tab === 'dashboard' && <Dashboard config={config.data} nav={nav} />}
            {tab === 'explore' && <Explore config={config.data} nav={nav} />}
            {tab === 'tenders' && <TendersPage config={config.data} nav={nav} />}
            {tab === 'runs' && <RunsPage config={config.data} />}
            {tab === 'settings' && <SettingsPage config={config.data} onSaved={config.reload} />}
          </>
        )}
      </main>
    </div>
  );
}

export { EMPTY, DISPLAY };
