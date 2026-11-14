import { toParams } from './filters';
import type { AiStatus, AppConfig, AskResult, Brief, DocFacts, Facets, Filters, Pivot, Run, SavedView, Settings, Stats, TenderDetail, TenderPage } from './types';

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

export const api = {
  me: () => call<{ authenticated: boolean }>('/api/me'),
  login: (username: string, password: string) => call<{ ok: boolean }>('/api/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  logout: () => call<{ ok: boolean }>('/api/logout', { method: 'POST' }),
  config: () => call<AppConfig>('/api/config'),
  stats: (f: Filters) => call<Stats>(`/api/stats?${toParams(f)}`),
  tenders: (f: Filters, sort: string, page: number, pageSize = 25) => call<TenderPage>(`/api/tenders?${toParams(f)}&sort=${sort}&page=${page}&page_size=${pageSize}`),
  exportUrl: (f: Filters, sort = 'closing') => `/api/tenders/export?${toParams(f)}&sort=${sort}`,
  pivot: (f: Filters, groupBy: string, splitBy: string, top = 12) =>
    call<Pivot>(`/api/analytics?${toParams(f)}&group_by=${groupBy}${splitBy ? `&split_by=${splitBy}` : ''}&top=${top}`),
  facets: () => call<Facets>('/api/facets'),
  tender: (key: string) => call<TenderDetail>(`/api/tenders/detail?key=${encodeURIComponent(key)}`),
  star: (key: string, starred: boolean) => call<{ key: string; starred: boolean }>('/api/tenders/star', { method: 'POST', body: JSON.stringify({ key, starred }) }),
  summary: (key: string, refresh = false) => call<{ summary: string; cached: boolean }>('/api/tenders/summary', { method: 'POST', body: JSON.stringify({ key, refresh }) }),
  brief: (key: string, refresh = false) => call<{ brief: Brief; cached: boolean }>('/api/tenders/brief', { method: 'POST', body: JSON.stringify({ key, refresh }) }),
  document: (key: string, text: string, pdf_base64: string) => call<{ doc: DocFacts }>('/api/tenders/document', { method: 'POST', body: JSON.stringify({ key, text, pdf_base64 }) }),
  ask: (question: string) => call<AskResult>('/api/ask', { method: 'POST', body: JSON.stringify({ question }) }),
  aiStatus: () => call<AiStatus>('/api/ai/status'),
  aiTest: () => call<{ ok: boolean; model: string; fast_model: string }>('/api/ai/test', { method: 'POST' }),
  aiScore: () => call<{ scored: number; labelled: number; unlabelled: number }>('/api/ai/score', { method: 'POST' }),
  views: () => call<{ views: SavedView[] }>('/api/views'),
  saveViews: (views: SavedView[]) => call<{ views: SavedView[] }>('/api/views', { method: 'PUT', body: JSON.stringify({ views }) }),
  runs: () => call<{ runs: Run[]; running: number | null }>('/api/runs'),
  run: (id: number) => call<Run>(`/api/runs/${id}`),
  startRun: (portals: string[], sendEmail: boolean) => call<{ id: number }>('/api/runs', { method: 'POST', body: JSON.stringify({ portals, send_email: sendEmail }) }),
  settings: () => call<Settings>('/api/settings'),
  saveSettings: (s: Partial<Settings>) => call<Settings>('/api/settings', { method: 'PUT', body: JSON.stringify(s) }),
  testEmail: () => call<{ sent_to: number }>('/api/settings/test-email', { method: 'POST' }),
};
