import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, setUnauthorisedHandler } from './api';
import { DISPLAY, EMPTY, fromParams, toParams } from './filters';
import { useLoad } from './hooks';
import { AskBar } from './ai';
import { Dashboard } from './pages/Dashboard';
import { AddTenderHost } from './AddTender';
import { Login } from './pages/Login';
import { InstallButton, OfflineBar } from './pwa';
import { loadBranding, Logo } from './Brand';
import { ToastProvider, Drawer } from './ui';
import { ConfirmProvider } from './confirm';
import { SessionProvider, useSession } from './session';
import { AccountPanel, ChangePassword } from './Account';
import { ShortcutsHelp, useShortcuts } from './Shortcuts';
import type { Display, Facets, Filters, Me } from './types';

const BidsPage = lazy(() => import('./pages/Bids').then((m) => ({ default: m.BidsPage })));
const Explore = lazy(() => import('./pages/Explore').then((m) => ({ default: m.Explore })));
const RunsPage = lazy(() => import('./pages/Runs').then((m) => ({ default: m.RunsPage })));
const SettingsPage = lazy(() => import('./pages/Settings').then((m) => ({ default: m.SettingsPage })));
const TendersPage = lazy(() => import('./pages/Tenders').then((m) => ({ default: m.TendersPage })));
const VaultPage = lazy(() => import('./pages/Vault').then((m) => ({ default: m.VaultPage })));

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
  const [me, setMe] = useState<Me | null | undefined>(undefined); // undefined: still asking the server; null: signed out
  const [unreachable, setUnreachable] = useState(false);
  const refresh = useCallback(() => api.me().then((r) => { setUnreachable(false); setMe(r.authenticated ? r.user : null); }, (e: unknown) => {
    if (e instanceof ApiError && e.status === 0) setUnreachable(true); else setMe(null);  // no connection is not the same as signed out
  }), []);
  useEffect(() => { window.addEventListener('online', refresh); return () => window.removeEventListener('online', refresh); }, [refresh]);
  useEffect(() => { applyTheme(readTheme()); loadBranding(); }, []);
  useEffect(() => { setUnauthorisedHandler(() => setMe(null)); refresh(); }, [refresh]);
  const logout = () => api.logout().finally(() => setMe(null));

  return (
    <ToastProvider>
      <ConfirmProvider>
        {me === undefined && unreachable ? (
          <div className="login"><main className="login-card" style={{ maxWidth: 420, margin: 'auto' }}>
            <h1>You are offline</h1>
            <p className="login-sub">TendAI cannot reach the server. Check your connection. The page tries again by itself when the connection returns.</p>
            <button className="btn primary" onClick={() => { setUnreachable(false); refresh(); }}>Try again</button>
          </main></div>)
          : me === undefined ? <div className="login"><span className="spinner" aria-label="Loading" /></div>
          : me === null ? <Login onDone={refresh} />
          : me.must_change ? <ChangePassword forced onDone={refresh} onLogout={logout} />
          : <SessionProvider user={me}><Main onLogout={logout} refreshMe={refresh} /></SessionProvider>}
      </ConfirmProvider>
    </ToastProvider>
  );
}

function Main({ onLogout, refreshMe }: { onLogout: () => void; refreshMe: () => void }) {
  const initial = useRef(parseHash()).current;
  const [tab, setTab] = useState<Tab>(initial.tab);
  const [bidId, setBidId] = useState<number | null>(initial.bidId);
  const [filters, setFiltersState] = useState<Filters>(initial.filters);
  const [display, setDisplayState] = useState<Display>(initial.display);
  const [theme, setTheme] = useState<Theme>(readTheme);
  const { user, can } = useSession();
  const [changingPassword, setChangingPassword] = useState(false);
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
  // Changes on the portal can arrive while the page stays open: look again every 5 minutes and when the tab is brought back.
  useEffect(() => {
    const again = () => { if (!document.hidden) alerts.reload(); };
    const timer = setInterval(again, 5 * 60 * 1000);
    document.addEventListener('visibilitychange', again);
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', again); };
  }, [alerts.reload]); // eslint-disable-line react-hooks/exhaustive-deps
  // The browser tab and history entries name the page, and show how many bids changed on the portal.
  const unseen = alerts.data?.unseen ?? 0;
  useEffect(() => { document.title = `${unseen > 0 ? `(${unseen}) ` : ''}${TABS.find((t) => t.id === tab)?.label ?? 'TendAI'} · TendAI by QMSofts`; }, [tab, unseen]);
  const [moreOpen, setMoreOpen] = useState(false);
  useEffect(() => {
    if (!moreOpen) return;
    const away = (e: MouseEvent) => { if (!(e.target as HTMLElement).closest('.side-foot')) setMoreOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setMoreOpen(false); };
    document.addEventListener('mousedown', away); document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc); };
  }, [moreOpen]);
  /** Where a main-menu link points. A plain click changes the page in place; ctrl/cmd-click, middle-click and "open in new tab" use this address. */
  const hrefFor = (t: Tab) => { const q = t === 'explore' || t === 'tenders' ? toParams(filters).toString() : ''; return `#/${t}${q ? `?${q}` : ''}`; };
  const go1 = (e: React.MouseEvent, t: Tab, withFilters?: boolean) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    go(t, withFilters ? filters : undefined);
  };

  const nav: Nav = { filters, display, facets: facets.data, setFilters, setDisplay, go };
  const tabAllowed = useCallback((t: Tab) => t !== 'settings' || can('admin'), [can]);
  const [help, setHelp] = useShortcuts(useCallback((t: Tab) => go(t), [go]), tabAllowed);
  const cycleTheme = () => { const next = THEMES[(THEMES.indexOf(theme) + 1) % 3]; setTheme(next); applyTheme(next); try { localStorage.setItem('theme', next); } catch { /* ignore */ } };

  return (
    <div className="app">
      <AddTenderHost />
      {help && <ShortcutsHelp onClose={() => setHelp(false)} allowed={tabAllowed} />}
      {changingPassword && user && <Drawer title="Account & security" onClose={() => setChangingPassword(false)}><AccountPanel user={user} onClose={() => setChangingPassword(false)} onChanged={refreshMe} /></Drawer>}
      <a href="#main" className="skip" onClick={(e) => { e.preventDefault(); document.getElementById('main')?.focus(); }}>Skip to content</a>
      <aside className="sidebar">
        <div className="brand" aria-label="TendAI by QMSofts">
          <Logo />
          <span className="brand-name"><span className="brand-word">Tend<span className="brand-ai">AI</span></span><span className="brand-by">by QMSofts</span></span>
        </div>
        <nav className="nav" aria-label="Main">
          {MAIN_TABS.map((t) => <a key={t.id} className="nav-link" href={hrefFor(t.id)} aria-current={tab === t.id ? 'page' : undefined} onClick={(e) => go1(e, t.id, t.id === 'explore' || t.id === 'tenders')}><NavIcon id={t.id} /><span className="nav-label">{t.label}</span>{t.id === 'bids' && (alerts.data?.unseen ?? 0) > 0 && <span className="count-pill alert-pill" title="Changes on tenders you are bidding for">{alerts.data!.unseen}</span>}</a>)}
        </nav>
        <div className={`side-foot${moreOpen ? ' open' : ''}`}>
          <button type="button" className="more-toggle" aria-expanded={moreOpen} aria-controls="side-more" onClick={() => setMoreOpen((o) => !o)}>{user?.name || 'Menu'} <span aria-hidden>▾</span></button>
          <div id="side-more" className="side-more" onClick={() => setMoreOpen(false)}>
          {UTILITY_TABS.filter((t) => t.id !== 'settings' || can('admin')).map((t) => <a key={t.id} className="link-btn nav-link" href={`#/${t.id}`} aria-current={tab === t.id ? 'page' : undefined} onClick={(e) => go1(e, t.id)}><NavIcon id={t.id} /><span className="nav-label">{t.label}</span></a>)}
          <button className="link-btn" onClick={cycleTheme} title="Change colour theme" aria-label={`Theme: ${theme}. Click to change.`}><span className={`theme-dot ${theme}`} aria-hidden /><span className="nav-label">Theme: {theme === 'dark' ? 'Dark' : theme === 'light' ? 'Light' : 'Auto'}</span></button>
          {user && (
            <div className="who" title={`${user.name} · ${user.role}`}>
              <span className="who-name">{user.name || user.username}</span><span className="who-role">{user.role}</span>
            </div>
          )}
          {user && <button className="link-btn" onClick={() => setChangingPassword(true)}><span className="nav-label">Account &amp; security</span></button>}
          <InstallButton />
          <button className="link-btn" onClick={() => setHelp(true)}><span className="nav-label">Keyboard shortcuts</span></button>
          <button className="link-btn" onClick={onLogout}><span className="nav-label">Log out</span></button>
          </div>
        </div>
      </aside>
      <div className="content">
      <OfflineBar />
      {config.error && <div className="alert bad">{config.error}</div>}
      {config.data?.storage_warning && <div className="alert bad" role="alert"><strong>Your data is not safe.</strong> {config.data.storage_warning}</div>}
      {config.data?.ai_enabled && (tab === 'dashboard' || tab === 'explore' || tab === 'tenders') && <div className="askwrap"><AskBar nav={nav} /></div>}
      <main id="main" tabIndex={-1}>
        {config.data && (
          <Suspense fallback={<div className="empty"><span className="spinner" aria-hidden /> Loading…</div>}>
            {tab === 'dashboard' && <Dashboard config={config.data} nav={nav} />}
            {tab === 'explore' && <Explore config={config.data} nav={nav} />}
            {tab === 'tenders' && <TendersPage config={config.data} nav={nav} />}
            {tab === 'bids' && <BidsPage config={config.data} bidId={bidId} onOpen={(id) => { push.current = true; setBidId(id); window.scrollTo(0, 0); }} />}
            {tab === 'vault' && <VaultPage config={config.data} />}
            {tab === 'runs' && <RunsPage config={config.data} />}
            {tab === 'settings' && (can('admin') ? <SettingsPage config={config.data} onSaved={config.reload} /> : <div className="alert warn">Settings are for administrators.</div>)}
          </Suspense>
        )}
      </main>
      </div>
    </div>
  );
}

export { EMPTY, DISPLAY };
