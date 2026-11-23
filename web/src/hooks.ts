import { useCallback, useEffect, useRef, useState } from 'react';

export interface Loaded<T> { data: T | null; error: string | null; loading: boolean; reload: () => void }

/** Loads on mount and whenever `deps` change. A slow earlier request can never overwrite a newer one. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const latest = useRef(0);

  useEffect(() => {
    const id = ++latest.current;
    setLoading(true);
    fn().then(
      (d) => { if (id === latest.current) { setData(d); setError(null); setLoading(false); } },
      (e: Error) => { if (id === latest.current) { setError(e.message); setLoading(false); } },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  // A load that failed (most likely for want of a connection) is tried again by itself when the browser says the connection is back.
  const failed = useRef(false);
  failed.current = error !== null;
  useEffect(() => {
    const again = () => { if (failed.current) reload(); };
    window.addEventListener('online', again);
    return () => window.removeEventListener('online', again);
  }, [reload]);
  return { data, error, loading, reload };
}

/** The server's clock (India time, no zone) kept moving on the page: the time it said when the page loaded, plus time since. Refreshes every 30 s. */
export function useServerNow(serverNow: string): string {
  const [, tick] = useState(0);
  const base = useRef({ serverNow, loadedAt: Date.now() });
  if (base.current.serverNow !== serverNow) base.current = { serverNow, loadedAt: Date.now() };
  useEffect(() => { const id = setInterval(() => tick((n) => n + 1), 30000); return () => clearInterval(id); }, []);
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/.exec(base.current.serverNow);
  if (!m) return serverNow;
  const ms = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]) + (Date.now() - base.current.loadedAt);
  return new Date(ms).toISOString().slice(0, 19);
}

export function useDebounced<T>(value: T, ms = 350): T {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}

/** Whether the browser thinks it has a connection. It can say "online" when the server is still unreachable, but "offline" is reliable. */
export function useOnline(): boolean {
  const [online, setOnline] = useState(() => (typeof navigator === 'undefined' ? true : navigator.onLine));
  useEffect(() => {
    const up = () => setOnline(true), down = () => setOnline(false);
    window.addEventListener('online', up);
    window.addEventListener('offline', down);
    return () => { window.removeEventListener('online', up); window.removeEventListener('offline', down); };
  }, []);
  return online;
}
