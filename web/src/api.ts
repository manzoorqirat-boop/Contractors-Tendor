import { toParams } from './filters';
import type { PortalDiagnosis, WhatsAppStatus, Forecast, Plan, Rivals, WinChance, Clause, CorrigendumRead, Backtest, MarketImport, MarketSummary, CoverageReport, BidMoney, MoneySummary, PortalChoice, Learning, MutedDepartment, CleanupPreview, SsoInfo, SetupStatus, Device, ApiToken, Webhook, EraseResult, LetterTemplate, TemplateField, TemplateVersion, FilledLetter, CompareTender, Bidder, Competitor, BidComment, Person, Signoff, BackupStatus, AuditEntry, Me, UserRow, UsersData, Advice, AiStatus, BoqView, CatalogueData, CatalogueImport, RiskData, SimInput, Simulation, AppConfig, AskResult, BidAlert, BidDetail, BidItem, BidList, BidStats, ManualFields, ManualListing, FetchResult, Brief, CompanyFact, DocFacts, Eligibility, VaultData, VaultDoc, Facets, Filters, NewPortal, Pivot, PortalHealth, PortalStatus, Run, SavedView, Settings, Stats, TenderDetail, TenderPage } from './types';

export class ApiError extends Error {
  constructor(public status: number, message: string, public needCode = false) { super(message); }
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
    let needCode = false;
    try {
      const body = await res.json();
      needCode = body.need_code === true;
      if (typeof body.detail === 'string') message = body.detail;
      else if (Array.isArray(body.detail)) message = 'Some of the values are not valid.';
    } catch { /* not JSON: keep the generic message */ }
    throw new ApiError(res.status, message, needCode);
  }
  return res.json() as Promise<T>;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const MAX_WAIT_MS = 20 * 60 * 1000;

/** For actions that can take minutes: the server answers at once with a ticket, and this waits for the real answer.
 *  The caller gets the result, or the same ApiError it would have got from a direct answer. */
async function job<T>(path: string, init?: RequestInit): Promise<T> {
  const first = await call<T | { _job: string; state: string }>(path, init);
  if (!first || typeof first !== 'object' || !('_job' in first)) return first as T;
  const started = Date.now();
  for (let wait = 700; Date.now() - started < MAX_WAIT_MS; wait = Math.min(wait + 300, 2500)) {
    await sleep(wait);
    const r = await call<{ state: string; result?: T; status?: number; detail?: string }>(`/api/jobs/${first._job}`);
    if (r.state === 'done') return r.result as T;
    if (r.state === 'failed') throw new ApiError(r.status ?? 500, r.detail || 'The action failed.');
  }
  throw new ApiError(0, 'This is taking much longer than expected. Check the page in a few minutes: it may still finish.');
}

export const api = {
  me: () => call<{ authenticated: boolean; user: Me | null }>('/api/me'),
  users: () => call<UsersData>('/api/users'),
  createUser: (b: { username: string; name: string; role: string; password: string; email?: string }) => call<UserRow>('/api/users', { method: 'POST', body: JSON.stringify(b) }),
  editUser: (id: number, b: { name?: string; role?: string; active?: boolean; password?: string; reset_2fa?: boolean; email?: string }) => call<UserRow>(`/api/users/${id}`, { method: 'PATCH', body: JSON.stringify(b) }),
  audit: (user: string, before?: number) => call<{ entries: AuditEntry[]; next: number | null }>(`/api/audit?limit=50${user ? `&user=${encodeURIComponent(user)}` : ''}${before ? `&before=${before}` : ''}`),
  changePassword: (current: string, next: string) => call<{ ok: boolean }>('/api/account/password', { method: 'POST', body: JSON.stringify({ current, new: next }) }),
  login: (username: string, password: string, code = '') => call<{ ok: boolean }>('/api/login', { method: 'POST', body: JSON.stringify({ username, password, code }) }),
  twoStepStart: () => call<{ secret: string; uri: string }>('/api/account/2fa/start', { method: 'POST' }),
  twoStepEnable: (code: string) => call<{ ok: boolean }>('/api/account/2fa/enable', { method: 'POST', body: JSON.stringify({ code }) }),
  twoStepDisable: (password: string) => call<{ ok: boolean }>('/api/account/2fa/disable', { method: 'POST', body: JSON.stringify({ password }) }),
  endOtherSessions: () => call<{ ok: boolean }>('/api/account/sessions/end-others', { method: 'POST' }),
  logout: () => call<{ ok: boolean }>('/api/logout', { method: 'POST' }),
  config: () => call<AppConfig>('/api/config'),
  stats: (f: Filters) => call<Stats>(`/api/stats?${toParams(f)}`),
  tenders: (f: Filters, sort: string, page: number, pageSize = 25) => call<TenderPage>(`/api/tenders?${toParams(f)}&sort=${sort}&page=${page}&page_size=${pageSize}`),
  exportUrl: (f: Filters, sort = 'closing', format: 'csv' | 'xlsx' = 'csv', keys: string[] = []) => `/api/tenders/export?${toParams(f)}&sort=${sort}&format=${format}${keys.map((k) => `&keys=${encodeURIComponent(k)}`).join('')}`,
  starBulk: (keys: string[], starred: boolean) => call<{ changed: number; starred: boolean }>('/api/tenders/star-bulk', { method: 'POST', body: JSON.stringify({ keys, starred }) }),
  docChecked: (id: number, checked: boolean) => call<{ doc_checked: { by: string; at: string } | null }>(`/api/bids/${id}/doc-checked`, { method: 'POST', body: JSON.stringify({ checked }) }),
  people: () => call<{ people: Person[] }>('/api/people'),
  myWork: () => call<{ items: { item_id: number; text: string; bid_id: number; title: string; description: string; tender_id: string; closing: string | null }[] }>('/api/my-work'),
  comments: (id: number) => call<{ comments: BidComment[] }>(`/api/bids/${id}/comments`),
  addComment: (id: number, text: string) => call<BidComment>(`/api/bids/${id}/comments`, { method: 'POST', body: JSON.stringify({ text }) }),
  deleteComment: (id: number, cid: number) => call<{ removed: number }>(`/api/bids/${id}/comments/${cid}`, { method: 'DELETE' }),
  signoff: (id: number, action: string, note: string) => call<{ signoff: Signoff | null }>(`/api/bids/${id}/signoff`, { method: 'POST', body: JSON.stringify({ action, note }) }),
  setOwnEmail: (email: string) => call<{ email: string }>('/api/account/email', { method: 'POST', body: JSON.stringify({ email }) }),
  readAward: (id: number, text: string, pdf_base64: string) => job<{ award: { winner: string; winning_price: number | null; awarded: string; estimate: number | null; notes: string; bidders: Bidder[] } }>(`/api/bids/${id}/award`, { method: 'POST', body: JSON.stringify({ text, pdf_base64 }) }),
  bidders: (id: number) => call<{ bidders: Bidder[] }>(`/api/bids/${id}/bidders`),
  saveBidders: (id: number, bidders: Bidder[]) => call<{ bidders: Bidder[]; updated: Record<string, unknown> }>(`/api/bids/${id}/bidders`, { method: 'PUT', body: JSON.stringify({ bidders }) }),
  competitors: () => call<{ competitors: Competitor[] }>('/api/competitors'),
  createBidsBulk: (keys: string[]) => call<{ created: number[]; existing: number[]; missing: string[] }>('/api/bids/bulk', { method: 'POST', body: JSON.stringify({ keys }) }),
  pivot: (f: Filters, groupBy: string, splitBy: string, top = 12) =>
    call<Pivot>(`/api/analytics?${toParams(f)}&group_by=${groupBy}${splitBy ? `&split_by=${splitBy}` : ''}&top=${top}`),
  facets: () => call<Facets>('/api/facets'),
  tender: (key: string) => call<TenderDetail>(`/api/tenders/detail?key=${encodeURIComponent(key)}`),
  star: (key: string, starred: boolean) => call<{ key: string; starred: boolean }>('/api/tenders/star', { method: 'POST', body: JSON.stringify({ key, starred }) }),
  summary: (key: string, refresh = false) => call<{ summary: string; cached: boolean }>('/api/tenders/summary', { method: 'POST', body: JSON.stringify({ key, refresh }) }),
  brief: (key: string, refresh = false) => call<{ brief: Brief; cached: boolean }>('/api/tenders/brief', { method: 'POST', body: JSON.stringify({ key, refresh }) }),
  document: (key: string, text: string, pdf_base64: string) => job<{ doc: DocFacts }>('/api/tenders/document', { method: 'POST', body: JSON.stringify({ key, text, pdf_base64 }) }),
  ask: (question: string) => call<AskResult>('/api/ask', { method: 'POST', body: JSON.stringify({ question }) }),
  aiStatus: () => call<AiStatus>('/api/ai/status'),
  backupStatus: () => call<BackupStatus>('/api/backup/status'),
  offsiteNow: () => job<{ ok: boolean }>('/api/backup/offsite-now', { method: 'POST' }),
  aiTest: () => call<{ ok: boolean; model: string; fast_model: string }>('/api/ai/test', { method: 'POST' }),
  aiScore: () => job<{ scored: number; labelled: number; unlabelled: number }>('/api/ai/score', { method: 'POST' }),
  views: () => call<{ views: SavedView[] }>('/api/views'),
  saveViews: (views: SavedView[]) => call<{ views: SavedView[] }>('/api/views', { method: 'PUT', body: JSON.stringify({ views }) }),
  runs: () => call<{ runs: Run[]; running: number | null }>('/api/runs'),
  run: (id: number) => call<Run>(`/api/runs/${id}`),
  startRun: (portals: string[], sendEmail: boolean) => call<{ id: number }>('/api/runs', { method: 'POST', body: JSON.stringify({ portals, send_email: sendEmail }) }),
  companyProfile: () => call<{ company_profile: string }>('/api/company-profile'),
  saveCompanyProfile: (company_profile: string) => call<{ company_profile: string }>('/api/company-profile', { method: 'PUT', body: JSON.stringify({ company_profile }) }),
  settings: () => call<Settings>('/api/settings'),
  saveSettings: (s: Partial<Settings>) => call<Settings>('/api/settings', { method: 'PUT', body: JSON.stringify(s) }),
  branding: () => call<{ logo_text: string; accent: string; logo_url: string | null }>('/api/branding'),
  uploadLogo: (file_base64: string) => call<{ ok: boolean; width: number; height: number; bytes: number; logo_url: string | null }>('/api/settings/logo', { method: 'PUT', body: JSON.stringify({ file_base64 }) }),
  removeLogo: () => call<{ ok: boolean; removed: boolean; logo_url: null }>('/api/settings/logo', { method: 'DELETE' }),
  portals: () => call<{ portals: PortalStatus[]; manual: PortalStatus[] }>('/api/portals'),
  portalHealth: () => call<{ items: PortalHealth[]; problems: PortalHealth[] }>('/api/portal-health'),
  marketResults: () => call<MarketSummary>('/api/market-results?limit=20'),
  marketImport: (text: string) => call<MarketImport>('/api/market-results/import', { method: 'POST', body: JSON.stringify({ text }) }),
  marketClear: (source: 'import' | 'manual') => call<{ removed: number }>(`/api/market-results?source=${source}`, { method: 'DELETE' }),
  marketBacktest: () => call<Backtest>('/api/market-results/backtest'),
  portalCoverage: () => call<CoverageReport>('/api/portals/coverage'),
  testPortalCoverage: (key: string) => call<{ key: string; verdict: string }>(`/api/portals/${encodeURIComponent(key)}/coverage`, { method: 'POST' }),
  addPortal: (p: NewPortal) => call<PortalStatus>('/api/portals', { method: 'POST', body: JSON.stringify(p) }),
  checkPortal: (key: string, enable = false) => call<PortalStatus>(`/api/portals/${encodeURIComponent(key)}/check${enable ? '?enable=true' : ''}`, { method: 'POST' }),
  portalCatalogue: () => call<{ entries: PortalChoice[] }>('/api/portals/catalogue'),
  addPortalsBulk: (ids: string[]) => call<{ added: string[]; skipped: { id: string; reason: string }[] }>('/api/portals/bulk', { method: 'POST', body: JSON.stringify({ ids }) }),
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
  bidDocument: (id: number, key: string, text: string, pdf_base64: string) => job<{ doc: DocFacts; added: number }>(`/api/bids/${id}/document`, { method: 'POST', body: JSON.stringify({ key, text, pdf_base64 }) }),
  manualRead: (text: string, pdf_base64: string) => job<{ listing: ManualListing }>('/api/tenders/manual/read', { method: 'POST', body: JSON.stringify({ text, pdf_base64 }) }),
  manualCheck: (id: string) => call<{ existing: { key: string; portal_name: string; organisation: string; manual: boolean }[] }>(`/api/tenders/manual/check?tender_id=${encodeURIComponent(id)}`),
  manualCreate: (b: ManualFields & { text: string; pdf_base64: string; read_document: boolean }) => job<{ key: string; doc_read: boolean; doc_error: string; source_kept: boolean }>('/api/tenders/manual', { method: 'POST', body: JSON.stringify(b) }),
  manualEdit: (key: string, b: ManualFields) => call<{ key: string }>(`/api/tenders/manual?key=${encodeURIComponent(key)}`, { method: 'PATCH', body: JSON.stringify(b) }),
  manualDelete: (key: string) => call<{ removed: string }>(`/api/tenders/manual?key=${encodeURIComponent(key)}`, { method: 'DELETE' }),
  fetchPortal: (id: number) => job<FetchResult>(`/api/bids/${id}/fetch`, { method: 'POST' }),
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
  uploadBoq: (id: number, filename: string, file_base64: string) => job<BoqView>(`/api/bids/${id}/boq`, { method: 'POST', body: JSON.stringify({ filename, file_base64 }) }),
  setBoqLineCost: (id: number, line: number, cost: number | null) => call<BoqView>(`/api/bids/${id}/boq/lines/${line}/cost`, { method: 'POST', body: JSON.stringify({ cost }) }),
  confirmBoqLine: (id: number, line: number, item_code: string | null) => call<BoqView>(`/api/bids/${id}/boq/lines/${line}`, { method: 'POST', body: JSON.stringify({ item_code }) }),
  bidRisks: (id: number) => call<RiskData>(`/api/bids/${id}/risks`),
  readRisks: (id: number, text: string, pdf_base64: string) => job<RiskData>(`/api/bids/${id}/risks`, { method: 'POST', body: JSON.stringify({ text, pdf_base64 }) }),
  advice: (id: number, minMargin: number, targetMargin: number) => call<Advice>(`/api/bids/${id}/advice?min_margin=${minMargin}&target_margin=${targetMargin}`),
  simulate: (id: number, input: SimInput) => call<Simulation>(`/api/bids/${id}/risks/simulate`, { method: 'POST', body: JSON.stringify(input) }),
  testWebhook: (url: string) => call<{ ok: boolean }>('/api/settings/test-webhook', { method: 'POST', body: JSON.stringify({ url }) }),
  calendar: () => call<{ enabled: boolean; url: string }>('/api/calendar'),
  newCalendarToken: () => call<{ enabled: boolean; url: string }>('/api/calendar/token', { method: 'POST' }),
  stopCalendar: () => call<{ enabled: boolean; url: string }>('/api/calendar', { method: 'DELETE' }),
  ssoStatus: () => call<{ enabled: boolean; button_text: string }>('/api/sso/status'),
  ssoInfo: () => call<SsoInfo>('/api/sso/info'),
  requestReset: (who: string) => call<{ ok: boolean; message: string }>('/api/password-reset/request', { method: 'POST', body: JSON.stringify({ who }) }),
  confirmReset: (token: string, password: string) => call<{ ok: boolean }>('/api/password-reset/confirm', { method: 'POST', body: JSON.stringify({ token, password }) }),
  mySessions: () => call<{ sessions: Device[] }>('/api/account/sessions'),
  endSession: (ref: string) => call<{ ok: boolean }>(`/api/account/sessions/${ref}`, { method: 'DELETE' }),
  exportOwnUrl: '/api/account/export',
  exportPersonUrl: (id: number) => `/api/users/${id}/export`,
  erasePerson: (id: number, confirm: string) => call<EraseResult>(`/api/users/${id}/erase`, { method: 'POST', body: JSON.stringify({ confirm }) }),
  tokens: () => call<{ tokens: ApiToken[]; roles: string[] }>('/api/tokens'),
  createToken: (name: string, role: string, days: number | null) => call<{ token: ApiToken; secret: string }>('/api/tokens', { method: 'POST', body: JSON.stringify({ name, role, days }) }),
  revokeToken: (id: number) => call<{ revoked: number }>(`/api/tokens/${id}`, { method: 'DELETE' }),
  webhooks: () => call<{ webhooks: Webhook[]; events: { key: string; label: string }[] }>('/api/webhooks'),
  createWebhook: (name: string, url: string, events: string[]) => call<{ webhook: Webhook; secret: string }>('/api/webhooks', { method: 'POST', body: JSON.stringify({ name, url, events }) }),
  editWebhook: (id: number, patch: { name?: string; url?: string; events?: string[]; active?: boolean }) => call<Webhook>(`/api/webhooks/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  newWebhookSecret: (id: number) => call<{ secret: string }>(`/api/webhooks/${id}/secret`, { method: 'POST' }),
  testHook: (id: number) => call<{ ok: boolean }>(`/api/webhooks/${id}/test`, { method: 'POST' }),
  deleteWebhook: (id: number) => call<{ removed: number }>(`/api/webhooks/${id}`, { method: 'DELETE' }),
  hideTenders: (keys: string[], hidden: boolean, mute_department = false) => call<{ changed: number; hidden: boolean; muted: string[] }>('/api/tenders/hide', { method: 'POST', body: JSON.stringify({ keys, hidden, mute_department }) }),
  bidMoney: (id: number) => call<BidMoney>(`/api/bids/${id}/money`),
  addMoney: (id: number, body: Record<string, unknown>) => call<BidMoney>(`/api/bids/${id}/money`, { method: 'POST', body: JSON.stringify(body) }),
  changeMoney: (id: number, moneyId: number, body: Record<string, unknown>) => call<BidMoney>(`/api/bids/${id}/money/${moneyId}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteMoney: (id: number, moneyId: number) => call<BidMoney>(`/api/bids/${id}/money/${moneyId}`, { method: 'DELETE' }),
  moneySummary: () => call<MoneySummary>('/api/money'),
  learning: () => call<Learning>('/api/learning'),
  mutes: () => call<{ departments: MutedDepartment[]; hidden_tenders: number }>('/api/mutes'),
  setMute: (department: string, muted: boolean) => call<{ changed: boolean; departments: MutedDepartment[] }>('/api/mutes', { method: 'POST', body: JSON.stringify({ department, muted }) }),
  companySize: () => call<{ min: number | null; max: number | null }>('/api/company-size'),
  saveCompanySize: (min: number | null, max: number | null) => call<{ min: number | null; max: number | null }>('/api/company-size', { method: 'PUT', body: JSON.stringify({ min, max }) }),
  cleanupPreview: (days: number) => call<CleanupPreview>(`/api/cleanup/preview?days=${days}`),
  cleanup: (days: number) => call<{ closed: number; placeholders: number }>('/api/cleanup', { method: 'POST', body: JSON.stringify({ days }) }),
  setup: () => call<SetupStatus>('/api/setup'),
  loadSample: () => call<{ added: number; tenders: number }>('/api/sample-data', { method: 'POST' }),
  removeSample: () => call<{ tenders: number; bids: number }>('/api/sample-data', { method: 'DELETE' }),
  packUrl: (id: number) => `/api/bids/${id}/pack`,
  letterTemplates: () => call<{ templates: LetterTemplate[]; fields: TemplateField[] }>('/api/letter-templates'),
  createTemplate: (name: string, body: string) => call<LetterTemplate>('/api/letter-templates', { method: 'POST', body: JSON.stringify({ name, body }) }),
  saveTemplate: (id: number, name: string, body: string, note: string) => call<LetterTemplate>(`/api/letter-templates/${id}`, { method: 'PUT', body: JSON.stringify({ name, body, note }) }),
  templateVersions: (id: number) => call<{ versions: TemplateVersion[] }>(`/api/letter-templates/${id}/versions`),
  restoreTemplate: (id: number, version: number) => call<LetterTemplate>(`/api/letter-templates/${id}/restore`, { method: 'POST', body: JSON.stringify({ version }) }),
  deleteTemplate: (id: number) => call<{ removed: number }>(`/api/letter-templates/${id}`, { method: 'DELETE' }),
  fillLetter: (bidId: number, template_id: number) => call<FilledLetter>(`/api/bids/${bidId}/letter`, { method: 'POST', body: JSON.stringify({ template_id }) }),
  compareTenders: (keys: string[]) => call<{ tenders: CompareTender[] }>(`/api/tenders/compare?${keys.map((k) => `keys=${encodeURIComponent(k)}`).join('&')}`),
  findClauses: (bidId: number, text: string, pdf_base64: string) => call<{ clauses: Clause[]; chars: number; note: string }>(`/api/bids/${bidId}/clauses`, { method: 'POST', body: JSON.stringify({ text, pdf_base64 }) }),
  readCorrigendum: (bidId: number, text: string, pdf_base64: string) => job<CorrigendumRead>(`/api/bids/${bidId}/corrigenda/read`, { method: 'POST', body: JSON.stringify({ text, pdf_base64 }) }),
  diagnosePortal: (key: string) => call<PortalDiagnosis>(`/api/portals/${encodeURIComponent(key)}/diagnose`),
  whatsappTry: (text: string, role: string) => call<{ reply: string; role: string }>('/api/whatsapp/try', { method: 'POST', body: JSON.stringify({ text, role }) }),
  whatsappStatus: () => call<WhatsAppStatus>('/api/whatsapp/status'),
  forecast: () => call<Forecast>('/api/forecast'),
  plan: (budget: number | null, margin: number) => call<Plan>(`/api/plan?margin=${margin}${budget === null ? '' : `&budget=${budget}`}`),
  rivals: (bidId: number) => call<Rivals>(`/api/bids/${bidId}/rivals`),
  winChance: (bidId: number) => call<WinChance>(`/api/bids/${bidId}/win-chance`),
  winModel: () => call<WinChance>('/api/win-model'),
  recordCorrigendum: (bidId: number, note: string, closing: string) => call<BidDetail>(`/api/bids/${bidId}/corrigenda`, { method: 'POST', body: JSON.stringify({ note, closing: closing || null }) }),
  testEmail: () => call<{ sent_to: number }>('/api/settings/test-email', { method: 'POST' }),
};
