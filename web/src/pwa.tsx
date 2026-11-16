import { useEffect, useState } from 'react';
import { tr } from './i18n';

interface InstallEvent extends Event { prompt: () => Promise<void>; userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }> }

/** True while the browser has a connection. */
export function useOnline(): boolean {
  const [online, setOnline] = useState(() => navigator.onLine);
  useEffect(() => {
    const on = () => setOnline(true), off = () => setOnline(false);
    window.addEventListener('online', on); window.addEventListener('offline', off);
    return () => { window.removeEventListener('online', on); window.removeEventListener('offline', off); };
  }, []);
  return online;
}

/** Shown while there is no connection. The page can still open from the installed copy, but nothing can be loaded or saved. */
export function OfflineBar() {
  const online = useOnline();
  if (online) return null;
  return <div className="alert warn" role="status"><strong>{tr('You are offline.')}</strong> {tr('The page is open, but tenders and bids cannot be loaded or saved until the connection is back.')}</div>;
}

/** "Install app" in the menu, when the browser offers it (Chrome, Edge and Android). On an iPhone use Share > Add to Home Screen. */
export function InstallButton() {
  const [ev, setEv] = useState<InstallEvent | null>(null);
  useEffect(() => {
    const ask = (e: Event) => { e.preventDefault(); setEv(e as InstallEvent); };
    const done = () => setEv(null);
    window.addEventListener('beforeinstallprompt', ask); window.addEventListener('appinstalled', done);
    return () => { window.removeEventListener('beforeinstallprompt', ask); window.removeEventListener('appinstalled', done); };
  }, []);
  if (!ev) return null;
  return <button className="link-btn" onClick={async () => { await ev.prompt(); await ev.userChoice.catch(() => undefined); setEv(null); }}><span className="nav-label">{tr('Install app')}</span></button>;
}
