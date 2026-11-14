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
  return { data, error, loading, reload };
}

export function useDebounced<T>(value: T, ms = 350): T {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}
