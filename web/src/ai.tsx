import { toParams } from './filters';
import type { AiStatus, BoqView, CatalogueData, CatalogueImport, RiskData, SimInput, Simulation, AppConfig, AskResult, BidAlert, BidDetail, BidItem, BidList, BidStats, ManualFields, ManualListing, FetchResult, Brief, CompanyFact, DocFacts, Eligibility, VaultData, VaultDoc, Facets, Filters, NewPortal, Pivot, PortalStatus, Run, SavedView, Settings, Stats, TenderDetail, TenderPage } from './types';

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
  branding: () => call<{ logo_text: string }>('/api/branding'),
  portals: () => call<{ portals: PortalStatus[]; manual: PortalStatus[] }>('/api/portals'),
  addPortal: (p: NewPortal) => call<PortalStatus>('/api/portals', { method: 'POST', body: JSON.stringify(p) }),
  checkPortal: (key: string) => call<PortalStatus>(`/api/portals/${encodeURIComponent(key)}/check`, { method: 'POST' }),
  removePortal: (key: string) => call<{ removed: string }>(`/api/portals/${encodeURIComponent(key)}`, { method: 'DELETE' }),
  bids: () => call<BidList>('/api/bids'),
  createBid: (key: string) => call<{ id: number; created: boolean }>('/api/bids', { method: 'POST', body: JSON.stringify({ key }) }),
  bid: (id: number) => call<BidDetail>(`/api/bids/${id}`),
  patchBid: (id: number, patch: Record<string, unknown>) => call<BidDetail>(`/api/bids/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteBid: (id: number) => call<{ removed: number }>(`/api/bids/${id}`, { method: 'DELETE' }),
  addItem: (id: number, text: string, kind: string) => call<BidItem>(`/api/bids/${id}/items`, { method: 'POST', body: JSON.stringify({ text, kind }) }),
  patchItem: (id: number, item: number, patch: Record<string, unknown>) => call<BidItem>(`/api/bids/${id}/items/${item}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteItem: (id: number, item: number) => call<{ removed: number }>(`/api/bids/${id}/items/${item}`, { method: 'DELETE' }),
  generateItems: (id: number) => call<{ added: number }>(`/api/bids/${id}/items/generate`, { method: 'POST' }),
  changesSeen: (id: number) => call<{ ok: boolean }>(`/api/bids/${id}/changes/seen`, { method: 'POST' }),
  bidDocument: (id: number, key: string, text: string, pdf_base64: string) => call<{ doc: DocFacts; added: number }>(`/api/bids/${id}/document`, { method: 'POST', body: JSON.stringify({ key, text, pdf_base64 }) }),
  manualRead: (text: string, pdf_base64: string) => call<{ listing: ManualListing }>('/api/tenders/manual/read', { method: 'POST', body: JSON.stringify({ text, pdf_base64 }) }),
  manualCheck: (id: string) => call<{ existing: { key: string; portal_name: string; organisation: string; manual: boolean }[] }>(`/api/tenders/manual/check?tender_id=${encodeURIComponent(id)}`),
  manualCreate: (b: ManualFields & { text: string; pdf_base64: string; read_document: boolean }) => call<{ key: string; doc_read: boolean; doc_error: string; source_kept: boolean }>('/api/tenders/manual', { method: 'POST', body: JSON.stringify(b) }),
  manualEdit: (key: string, b: ManualFields) => call<{ key: string }>(`/api/tenders/manual?key=${encodeURIComponent(key)}`, { method: 'PATCH', body: JSON.stringify(b) }),
  manualDelete: (key: string) => call<{ removed: string }>(`/api/tenders/manual?key=${encodeURIComponent(key)}`, { method: 'DELETE' }),
  fetchPortal: (id: number) => call<FetchResult>(`/api/bids/${id}/fetch`, { method: 'POST' }),
  bidBrief: (id: number, refresh = false) => call<{ brief: Brief; cached: boolean }>(`/api/bids/${id}/brief`, { method: 'POST', body: JSON.stringify({ key: '', refresh }) }),
  eligibility: (id: number) => call<Eligibility>(`/api/bids/${id}/eligibility`, { method: 'POST' }),
  draft: (id: number, kind: string, instructions: string, refresh: boolean) => call<{ kind: string; text: string; cached: boolean }>(`/api/bids/${id}/draft`, { method: 'POST', body: JSON.stringify({ kind, instructions, refresh }) }),
  bidAlerts: (days = 7) => call<{ alerts: BidAlert[]; unseen: number }>(`/api/bids/alerts?days=${days}`),
  bidStats: () => call<BidStats>('/api/bids/stats'),
  vault: () => call<VaultData>('/api/vault'),
  addVault: (doc: { name: string; kind: string; issued: string; expiry: string; notes: string; filename: string; file_base64: string }) => call<VaultDoc>('/api/vault', { method: 'POST', body: JSON.stringify(doc) }),
  patchVault: (id: number, patch: Record<string, unknown>) => call<VaultDoc>(`/api/vault/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteVault: (id: number) => call<{ removed: number }>(`/api/vault/${id}`, { method: 'DELETE' }),
  vaultFileUrl: (id: number) => `/api/vault/${id}/file`,
  saveFacts: (facts: CompanyFact[]) => call<{ facts: CompanyFact[] }>('/api/company-facts', { method: 'PUT', body: JSON.stringify({ facts }) }),
  boqCatalogue: () => call<CatalogueData>('/api/boq/catalogue'),
  importCatalogue: (kind: string, csv_text: string) => call<CatalogueImport>('/api/boq/catalogue', { method: 'POST', body: JSON.stringify({ kind, csv_text }) }),
  bidBoq: (id: number, include_review: boolean) => call<BoqView>(`/api/bids/${id}/boq?include_review=${include_review}`),
  uploadBoq: (id: number, filename: string, file_base64: string) => call<BoqView>(`/api/bids/${id}/boq`, { method: 'POST', body: JSON.stringify({ filename, file_base64 }) }),
  confirmBoqLine: (id: number, line: number, item_code: string | null) => call<BoqView>(`/api/bids/${id}/boq/lines/${line}`, { method: 'POST', body: JSON.stringify({ item_code }) }),
  bidRisks: (id: number) => call<RiskData>(`/api/bids/${id}/risks`),
  readRisks: (id: number, text: string, pdf_base64: string) => call<RiskData>(`/api/bids/${id}/risks`, { method: 'POST', body: JSON.stringify({ text, pdf_base64 }) }),
  simulate: (id: number, input: SimInput) => call<Simulation>(`/api/bids/${id}/risks/simulate`, { method: 'POST', body: JSON.stringify(input) }),
  testEmail: () => call<{ sent_to: number }>('/api/settings/test-email', { method: 'POST' }),
};
