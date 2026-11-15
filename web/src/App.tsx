import { useCallback, useEffect, useRef, useState } from 'react';
import { api, setUnauthorisedHandler } from './api';
import { DISPLAY, EMPTY, fromParams, toParams } from './filters';
import { useLoad } from './hooks';
import { AskBar } from './ai';
import { Dashboard } from './pages/Dashboard';
import { BidsPage } from './pages/Bids';
import { Explore } from './pages/Explore';
import { Login } from './pages/Login';
import { RunsPage } from './pages/Runs';
import { SettingsPage } from './pages/Settings';
import { TendersPage } from './pages/Tenders';
import { VaultPage } from './pages/Vault';
import { loadBranding, Logo } from './Brand';
import { ToastProvider } from './ui';
import type { Display, Facets, Filters } from './types';

export type Tab = 'dashboard' | 'explore' | 'tenders' | 'bids' | 'vault' | 'runs' | 'settings';
const TABS: { id: Tab; label: string }[] = [
  { id: 'dashboard', label: 'Dashboard' }, { id: 'explore', label: 'Explore' }, { id: 'tenders', label: 'Tenders' }, { id: 'bids', label: 'Bids' }, { id: 'vault', label: 'Vault' }, { id: 'runs', label: 'Runs' }, { id: 'settings', label: 'Settings' },
];

// Daily work stays in the main bar; Runs and Settings are set-up pages, so they sit with the account links.
const UTILITY: Tab[] = ['runs', 'settings'];
const MAIN_TABS = TABS.filter((t) => !UTILITY.includes(t.id));
const UTILITY_TABS = TABS.filter((t) => UTILITY.includes(t.id));

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


const ICONS: Record<Tab, string> = {
  dashboard: 'M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z',
  explore: 'M11 4a7 7 0 105.2 11.7l3.5 3.5 1.4-1.4-3.5-3.5A7 7 0 0011 4zm0 2a5 5 0 110 10 5 5 0 010-10z',
  tenders: 'M4 4h16v4H4V4zm0 6h16v4H4v-4zm0 6h16v4H4v-4z',
  bids: 'M5 3h14v18l-7-4-7 4V3z',
  vault: 'M4 6a2 2 0 012-2h12a2 2 0 012 2v12a2 2 0 01-2 2H6a2 2 0 01-2-2V6zm8 3a3 3 0 100 6 3 3 0 000-6z',
  runs: 'M8 5v14l11-7L8 5z',
  settings: 'M12 8a4 4 0 100 8 4 4 0 000-8zm8.5 5.5v-3l-2.1-.6a6.9 6.9 0 00-.7-1.6l1-1.9-2.1-2.1-1.9 1a6.9 6.9 0 00-1.6-.7L12.5 2.5h-3l-.6 2.1a6.9 6.9 0 00-1.6.7l-1.9-1-2.1 2.1 1 1.9c-.3.5-.5 1-.7 1.6l-2.1.6v3l2.1.6c.2.6.4 1.1.7 1.6l-1 1.9 2.1 2.1 1.9-1c.5.3 1 .5 1.6.7l.6 2.1h3l.6-2.1c.6-.2 1.1-.4 1.6-.7l1.9 1 2.1-2.1-1-1.9c.3-.5.5-1 .7-1.6l2.1-.6z',
};
function NavIcon({ id }: { id: Tab }) { return <svg className="ico" viewBox="0 0 24 24" aria-hidden><path d={ICONS[id]} /></svg>; }

function readTheme(): Theme {
  try { const t = localStorage.getItem('theme'); if (t === 'light' || t === 'dark') return t; } catch { /* storage blocked: follow the system */ }
  return 'system';
}
function applyTheme(t: Theme) {
  if (t === 'system') document.documentElement.removeAttribute('data-theme'); else document.documentElement.setAttribute('data-theme', t);
}

function parseHash(): { tab: Tab; bidId: number | null; filters: Filters; display: Display } {
  const raw = location.hash.replace(/^#\/?/, '');
  const [path, query = ''] = raw.split('?');
  const [head, sub] = path.split('/');  // "#/bids/12" is bid 12's page
  const tab = TABS.find((t) => t.id === head)?.id ?? 'dashboard';
  const bidId = tab === 'bids' && /^\d+$/.test(sub ?? '') ? Number(sub) : null;
  return { tab, bidId, ...fromParams(new URLSearchParams(query)) };
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
  const [bidId, setBidId] = useState<number | null>(initial.bidId);
  const [filters, setFiltersState] = useState<Filters>(initial.filters);
  const [display, setDisplayState] = useState<Display>(initial.display);
  const [theme, setTheme] = useState<Theme>(readTheme);
  const push = useRef(false);
  const config = useLoad(() => api.config(), []);
  const facets = useLoad(() => api.facets(), [tab === 'runs' || tab === 'settings' || tab === 'bids' || tab === 'vault' ? 'other' : 'data']);

  // The address bar always describes the current view, so a refresh, a bookmark or a shared link returns to it.
  useEffect(() => {
    const url = `#/${tab}${tab === 'bids' && bidId ? `/${bidId}` : ''}${(() => { const q = toParams(filters, display).toString(); return q ? `?${q}` : ''; })()}`;
    if (url === location.hash) return;
    if (push.current) { history.pushState(null, '', url); push.current = false; } else history.replaceState(null, '', url);
  }, [tab, bidId, filters, display]);

  useEffect(() => {
    const onPop = () => { const s = parseHash(); setTab(s.tab); setBidId(s.bidId); setFiltersState(s.filters); setDisplayState(s.display); };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const setFilters = useCallback((f: Filters) => { setFiltersState(f); setDisplayState((d) => (d.page === 1 ? d : { ...d, page: 1 })); }, []);
  const setDisplay = useCallback((d: Partial<Display>) => setDisplayState((cur) => ({ ...cur, ...d })), []);
  const go = useCallback((t: Tab, f?: Filters, d?: Partial<Display>) => {
    push.current = true;
    setTab(t);
    setBidId(null);
    if (f) setFiltersState(f);
    if (d) setDisplayState((cur) => ({ ...cur, page: 1, ...d }));
    window.scrollTo(0, 0);
  }, []);

  // The tender panel's "Start a bid" button announces itself here, so no page has to know about bids.
  useEffect(() => {
    const open = (e: Event) => { push.current = true; setTab('bids'); setBidId((e as CustomEvent<number>).detail); window.scrollTo(0, 0); };
    window.addEventListener('open-bid', open);
    return () => window.removeEventListener('open-bid', open);
  }, []);
  const alerts = useLoad(() => api.bidAlerts(), [tab, bidId]);
  // The browser tab and history entries name the page.
  useEffect(() => { document.title = `${TABS.find((t) => t.id === tab)?.label ?? 'TendAI'} · TendAI by QMSofts`; }, [tab]);

  const nav: Nav = { filters, display, facets: facets.data, setFilters, setDisplay, go };
  const cycleTheme = () => { const next = THEMES[(THEMES.indexOf(theme) + 1) % 3]; setTheme(next); applyTheme(next); try { localStorage.setItem('theme', next); } catch { /* ignore */ } };

  return (
    <div className="app">
      <a href="#main" className="skip" onClick={(e) => { e.preventDefault(); document.getElementById('main')?.focus(); }}>Skip to content</a>
      <aside className="sidebar">
        <div className="brand" aria-label="TendAI by QMSofts">
          <Logo />
          <span className="brand-name">Tend<span className="brand-ai">AI</span><span className="brand-by">by QMSofts</span></span>
        </div>
        <nav className="nav" aria-label="Main">
          {MAIN_TABS.map((t) => <button key={t.id} aria-current={tab === t.id ? 'page' : undefined} onClick={() => go(t.id, t.id === 'explore' || t.id === 'tenders' ? filters : undefined)}><NavIcon id={t.id} /><span className="nav-label">{t.label}</span>{t.id === 'bids' && (alerts.data?.unseen ?? 0) > 0 && <span className="count-pill alert-pill" title="Changes on tenders you are bidding for">{alerts.data!.unseen}</span>}</button>)}
        </nav>
        <div className="side-foot">
          {UTILITY_TABS.map((t) => <button key={t.id} className="link-btn" aria-current={tab === t.id ? 'page' : undefined} onClick={() => go(t.id)}><NavIcon id={t.id} /><span className="nav-label">{t.label}</span></button>)}
          <button className="link-btn" onClick={cycleTheme} title="Change colour theme" aria-label={`Theme: ${theme}. Click to change.`}><span className={`theme-dot ${theme}`} aria-hidden /><span className="nav-label">Theme: {theme === 'dark' ? 'Dark' : theme === 'light' ? 'Light' : 'Auto'}</span></button>
          <button className="link-btn" onClick={onLogout}><span className="nav-label">Log out</span></button>
        </div>
      </aside>
      <div className="content">
      {config.error && <div className="alert bad">{config.error}</div>}
      {config.data?.ai_enabled && (tab === 'dashboard' || tab === 'explore' || tab === 'tenders') && <div className="askwrap"><AskBar nav={nav} /></div>}
      <main id="main" tabIndex={-1}>
        {config.data && (
          <>
            {tab === 'dashboard' && <Dashboard config={config.data} nav={nav} />}
            {tab === 'explore' && <Explore config={config.data} nav={nav} />}
            {tab === 'tenders' && <TendersPage config={config.data} nav={nav} />}
            {tab === 'bids' && <BidsPage config={config.data} bidId={bidId} onOpen={(id) => { push.current = true; setBidId(id); window.scrollTo(0, 0); }} />}
            {tab === 'vault' && <VaultPage config={config.data} />}
            {tab === 'runs' && <RunsPage config={config.data} />}
            {tab === 'settings' && <SettingsPage config={config.data} onSaved={config.reload} />}
          </>
        )}
      </main>
      </div>
    </div>
  );
}

export { EMPTY, DISPLAY };
