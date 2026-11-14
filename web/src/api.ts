import type { AppConfig, Filters, Run, Settings, Stats, TenderPage } from './types';

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

let onUnauthorised: () => void = () => {};
export function setUnauthorisedHandler(fn: () => void) { onUnauthorised = fn; }

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { credentials: 'same-origin', ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } });
  } catch {
    throw new ApiError(0, 'Cannot reach the server. Check your connection and try again.');
  }
  if (res.status === 401 && path !== '/api/login') onUnauthorised();
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === 'string') message = body.detail;
      else if (Array.isArray(body.detail)) message = 'Some of the values are not valid.';
    } catch { /* not JSON: keep the generic message */ }
    throw new ApiError(res.status, message);
  }
  return res.json() as Promise<T>;
}

export function query(f: Partial<Filters>, extra: Record<string, string | number> = {}): string {
  const p = new URLSearchParams();
  if (f.q) p.set('q', f.q);
  if (f.portal) p.set('portal', f.portal);
  if (f.category) p.set('category', f.category);
  if (f.status) p.set('status', f.status);
  if (f.min_value) p.set('min_value', f.min_value);
  if (f.sort) p.set('sort', f.sort);
  for (const [k, v] of Object.entries(extra)) p.set(k, String(v));
  return p.toString();
}

export const api = {
  me: () => call<{ authenticated: boolean }>('/api/me'),
  login: (password: string) => call<{ ok: boolean }>('/api/login', { method: 'POST', body: JSON.stringify({ password }) }),
  logout: () => call<{ ok: boolean }>('/api/logout', { method: 'POST' }),
  config: () => call<AppConfig>('/api/config'),
  stats: () => call<Stats>('/api/stats'),
  tenders: (f: Filters, pageSize = 25) => call<TenderPage>(`/api/tenders?${query(f, { page: f.page, page_size: pageSize })}`),
  exportUrl: (f: Filters) => `/api/tenders/export?${query(f)}`,
  runs: () => call<{ runs: Run[]; running: number | null }>('/api/runs'),
  run: (id: number) => call<Run>(`/api/runs/${id}`),
  startRun: (portals: string[], sendEmail: boolean) => call<{ id: number }>('/api/runs', { method: 'POST', body: JSON.stringify({ portals, send_email: sendEmail }) }),
  settings: () => call<Settings>('/api/settings'),
  saveSettings: (s: Partial<Settings>) => call<Settings>('/api/settings', { method: 'PUT', body: JSON.stringify(s) }),
  testEmail: () => call<{ sent_to: number }>('/api/settings/test-email', { method: 'POST' }),
};
