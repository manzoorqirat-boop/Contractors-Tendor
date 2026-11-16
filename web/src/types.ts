export interface PortalInfo { key: string; name: string; state: string; search_url: string }
export interface Dimension { key: string; label: string; time: boolean }
export interface AppConfig {
  portals: PortalInfo[]; mail_configured: boolean; recipient_count: number; now: string; timezone: string; dimensions: Dimension[]; soon_days: number; ai_enabled?: boolean; storage_warning?: string; require_signoff?: boolean;
}

export interface PortalReport { portal: string; name: string; departments: number; listed: number; new_civil: number; errors: string[]; failed: boolean; access?: string }
export interface Run {
  id: number; trigger: 'manual' | 'scheduled'; status: 'running' | 'ok' | 'failed'; started: string; finished: string | null;
  portals: string[]; send_email: boolean; reports: PortalReport[]; new_civil: number; emailed_count: number; error: string | null; log?: string[];
}

export interface Tender {
  key: string; portal: string; portal_name: string; search_url: string; tender_id: string; first_seen: string; emailed: number;
  closing: string | null; published: string | null; opening?: string | null; value: number | null; value_text: string; emd: number | null;
  category: string; location: string; organisation: string; title: string; description: string; starred: boolean;
  work_type?: string | null; fit?: number | null; fit_reason?: string | null; ai_civil?: number | null;
}
export interface Brief { recommendation: 'Bid' | 'Maybe' | 'No bid'; headline: string; reasons: string[]; risks: string[]; checks: string[] }
export interface DocFacts { summary: string; requirements: string[]; financial: { label: string; value: string }[]; dates: { label: string; value: string }[]; documents: string[]; penalties: string[]; red_flags: string[] }
export interface Related { key: string; tender_id: string; title: string; closing: string | null; first_seen: string; value: number | null; similarity: number }
export interface TenderDetail extends Tender { summary: string | null; brief: Brief | null; doc: DocFacts | null; related: Related[]; bid_id?: number | null; history?: string[]; manual?: boolean; has_source?: boolean }
export interface AiStatus { configured: boolean; model: string; fast_model: string; profile_set: boolean; labelled: number; unlabelled: number; calls_today?: number; daily_limit?: number }
export interface AskResult { filters: Record<string, unknown>; sort: string | null; explanation: string }
export interface TenderPage { items: Tender[]; total: number; page: number; page_size: number; categories: string[] }

export interface Stats {
  kpi: {
    open: number; new_today: number; new_yesterday: number; closing_soon: number; closing_week: number; open_value: number; avg_value: number;
    starred: number; total_kept: number;
  };
  biggest: { key: string; portal: string; portal_name: string; tender_id: string; closing: string | null; value: number; category: string; organisation: string; title: string; description: string }[];
  last_run: Run | null;
  soon_days: number;
}

export type Drill = Record<string, unknown>;
export interface Cell { count: number; value: number; avg: number | null; drill?: Drill }
export interface PivotRow { key: string; count: number; value: number; avg: number | null; drill: Drill | null; parts: Record<string, Cell> }
export interface Pivot { group_by: string; split_by: string | null; rows: PivotRow[]; splits: string[]; total: { count: number; value: number }; labels: Record<string, string> }
export interface Facet { value: string; count: number; label?: string }
export interface Facets { portal: Facet[]; category: Facet[]; department: Facet[]; location: Facet[]; work_type: Facet[] }

export type AccessKind = 'ok' | 'captcha' | 'login' | 'otp' | 'blocked' | 'different_software' | 'unreachable' | 'unknown';
/** One portal and whether the app can read it on its own. `manual` means the person has to check it by hand. */
export interface PortalStatus {
  key: string; name: string; state: string; base_url: string; custom: boolean; enabled: boolean;
  kind: AccessKind; label: string; advice: string; manual: boolean; detail: string; checked: string | null; departments: number; tenders: number;
}
export interface NewPortal { name: string; state: string; base_url: string }

export interface Settings {
  recipients: string[]; send_time: string; schedule_enabled: boolean; send_empty: boolean; portals: string[]; min_value: number;
  extra_keywords: string[]; exclude_words: string[]; company_profile: string; require_signoff?: boolean; reminders_on?: boolean; webhook_url?: string; ai_digest_intro: boolean; ai_hide_noncivil: boolean; mail_configured: boolean; mail_from: string; available_portals: { key: string; name: string }[]; custom_portals?: NewPortal[]; logo_text: string;
}

export type Status = 'open' | 'closing_soon' | 'closed' | 'all';
export type Measure = 'count' | 'value' | 'avg';
export type ChartKind = 'bars' | 'stacked' | 'table';

/** Everything that narrows which tenders are shown. The same object drives the dashboard, Explore and Tenders pages. */
export interface Filters {
  q: string; status: Status; portal: string[]; category: string[]; department: string[]; location: string[]; work_type: string[];
  min_fit?: string; min_value?: string; max_value?: string; value_lt?: string; no_value?: boolean; no_closing?: boolean; no_published?: boolean; starred?: boolean;
  closing_from?: string; closing_to?: string; seen_from?: string; seen_to?: string; published_from?: string; published_to?: string;
  closing_within?: string; since_days?: string;
}

/** Display choices that are not filters. */
export interface Display { sort: string; page: number; group_by: string; split_by: string; measure: Measure; chart: ChartKind; layout: 'cards' | 'table' }
export interface SavedView { id: string; name: string; filters: Record<string, unknown>; alert?: boolean }

// ---- bid workspace -------------------------------------------------------------------------------------------------
export type Stage = 'interested' | 'preparing' | 'submitted' | 'opened' | 'won' | 'lost' | 'dropped';
export interface BidSummary {
  id: number; key: string; tender_id: string; portal: string; portal_name: string; title: string; description: string; organisation: string; work_type: string | null;
  value: number | null; value_text: string; emd: number | null; closing: string | null; opening: string | null; stage: Stage; owner: string | null;
  emd_due: string | null; prebid_date: string | null; query_deadline: string | null; my_value: number | null; l1_value: number | null;
  items_total: number; items_done: number; unseen: number; has_doc: number; stage_changed: string;
}
export interface BidList { bids: BidSummary[]; counts: Record<Stage, number>; stages: { key: Stage; label: string }[]; unseen: number }
export type MatchStatus = 'ok' | 'expired' | 'expires_before_close' | 'missing' | 'unknown';
export interface VaultMatch { status: MatchStatus; vault_id: number | null; name: string; expiry: string | null; kind: string; chosen: boolean }
export interface BidItem { id: number; kind: 'task' | 'document' | 'condition'; text: string; done: boolean; owner: string | null; vault_id: number | null; note: string | null; match: VaultMatch | null }
export interface BidChange { id: number; at: string; what: string; old: string | null; new: string | null; seen: boolean }
export interface EligibilityCheck { requirement: string; status: 'met' | 'gap' | 'unclear'; evidence: string; action: string }
export interface Eligibility { verdict: string; summary: string; checks: EligibilityCheck[]; checked?: string }
export interface BidDetail extends Omit<BidSummary, 'has_doc'> {
  published: string | null; category: string; location: string; emd: number | null; notes: string | null; l1_bidder: string | null; result_note: string | null; search_url: string;
  signoff?: Signoff | null; comment_count?: number;
  doc: DocFacts | null; doc_checked?: { by: string; at: string } | null; brief: Brief | null; eligibility: Eligibility | null; drafts: Record<string, string> | null; portal_facts: { label: string; value: string }[] | null; manual?: boolean; has_source?: boolean; portal_checked?: string | null;
  items: BidItem[]; changes: BidChange[]; unseen: number; history: string[];
}
export interface BidAlert { bid_id: number | null; kind: string; label: string; date: string; days_left: number; urgency: 'overdue' | 'today' | 'soon' | 'later'; title: string; tender_id: string; extra: string }
export interface StatGroup { label: string; won: number; lost: number; won_value: number }
export interface BidStats {
  total: number; in_progress: number; submitted: number; dropped: number; won: number; lost: number; win_rate: number | null; won_value: number;
  avg_gap_to_l1: number | null; gap_samples: number; by_work_type: StatGroup[]; by_department: StatGroup[]; by_size: StatGroup[];
}
export interface VaultDoc { id: number; name: string; kind: string; issued: string | null; expiry: string | null; notes: string; filename: string; size: number; created: string; status: 'ok' | 'expired' | 'expires_before_close' }
export interface CompanyFact { label: string; value: string }
export interface VaultData { documents: VaultDoc[]; kinds: { key: string; label: string }[]; facts: CompanyFact[] }
export interface FetchResult { facts: { label: string; value: string }[]; added: number; nit: 'read' | 'ai_off' | 'ai_failed' | 'login' | 'failed' | 'no_link'; message: string; corrigenda: string[]; boq?: FetchStep; risks?: FetchStep }
export interface FetchStep { state: string; message: string; lines?: number | null }

// ---- BOQ engine ----------------------------------------------------------------------------------------------------
export type BoqStatus = 'AUTO_MATCHED' | 'NEEDS_REVIEW' | 'NEW_NON_SOR_ITEM';
export interface BoqCost { basis: string; unit_cost: number | null; variance_pct: number | null; complete: boolean; cost_amount: number | null; note: string }
export interface BoqCandidate { code: string; name: string; spec: string; uom: string; rate: number | null; score: number; conflicts: string[] }
export interface BoqLine {
  line_no: number; sr: string | null; section: string | null; description: string; uom: string | null; uom_raw: string | null; qty: number | null;
  est_rate: number | null; est_amount: number | null; status: BoqStatus; tier: string | null; confidence: number | null; item_code: string | null; item_name: string;
  candidates: BoqCandidate[]; reason: string | null; cost: BoqCost | null;
}
export interface BoqPricing {
  estimate_total: number; priced_lines: number; total_lines: number; coverage_pct: number; priced_estimate: number; priced_cost: number;
  projected_cost: number; break_even_pct: number | null; assumption: string; include_review: boolean;
}
export interface BoqDoc { id: number; source_name: string; rate_type: 'item_rate' | 'percentage_rate' | 'unknown'; estimated_total: number | null; warnings: string[]; parsed_at: string; counts: Record<string, number>; lines: BoqLine[] }
export interface BoqView { catalogue: Record<string, number>; ai: boolean; boq: BoqDoc | null; pricing: BoqPricing | null }
export interface CatalogueItem { code: string; name: string; uom: string; rate: number | null; composite: boolean }
export interface CatalogueData { counts: Record<string, number>; items: CatalogueItem[] }
export interface CatalogueImport { result: { added: number; updated: number; errors: string[] }; counts: Record<string, number> }
export interface RiskFinding { present: 'yes' | 'no_mention' | 'unclear'; detail: string; clause: string }
export interface RiskData {
  findings: { risks: Record<string, RiskFinding>; facts: Record<string, string | number | null>; updated_at: string } | null;
  tags: { tag: string; label: string }[];
}
export interface SimAdjustment { tag: string; label: string; low: number; high: number; basis: string; detail: string; clause: string }
export interface Simulation {
  base_cost: number; adjustments: SimAdjustment[]; unpriced: { tag: string; label: string; why: string; detail: string; clause: string }[]; unclear: string[];
  cash_flow: { label: string; detail: string }[]; exposures: { tag: string; label: string; amount: number; detail: string }[]; warnings: string[];
  risk_low: number; risk_high: number; cost_low: number; cost_high: number; estimate?: number; break_even_pct_low?: number; break_even_pct_high?: number;
  l1_price?: number; margin_at_l1_low?: number; margin_at_l1_high?: number; coverage_pct: number; priced_lines: number; total_lines: number;
}
export interface AdvicePoint { pct: number; price: number; margin_worst: number; margin_best: number; margin_pct_worst: number; win: number | null; expected: number | null }
export interface Advice {
  mode: 'with_risks' | 'boq_only'; estimate: number; cost_low: number; cost_high: number; floor_pct: number; target_pct: number; recommended_pct: number;
  samples: number; confidence: 'none' | 'rough' | 'fair' | 'good'; basis: string; points: AdvicePoint[]; notes: string[]; coverage_pct: number;
  my_value: number | null; min_margin: number; target_margin: number;
}
export interface SimInput { l1_price: number | null; quoted_pct: number | null; exposure: Record<string, number>; include_review: boolean }

export interface ManualListing { tender_id: string; reference: string; title: string; description: string; organisation: string; location: string; value: number | null; emd: number | null; published: string; closing: string; opening: string; portal_name: string; form_of_contract: string }
export interface ManualFields { tender_id?: string; title?: string; description?: string; reference?: string; organisation?: string; location?: string; value?: number | null; emd?: number | null; published?: string; closing?: string; opening?: string; portal_name?: string; clear_value?: boolean; clear_emd?: boolean }

export interface Person { username: string; name: string }
export interface BidComment { id: number; at: string; username: string; text: string }
export interface Signoff { state: 'requested' | 'approved' | 'declined'; requested_by: string; by: string; at: string; note: string }
export type Role = 'viewer' | 'bidder' | 'admin';
export interface Me { id: number; username: string; name: string; role: Role; owner: boolean; must_change: boolean; two_step: boolean; email?: string }
export interface UserRow { id: number; username: string; name: string; role: Role; active: boolean; must_change: boolean; two_step?: boolean; email?: string; created: string | null; last_login: string | null; owner: boolean }
export interface UsersData { users: UserRow[]; roles: { key: Role; label: string; text: string }[]; min_password: number }
export interface AuditEntry { id: number; at: string; username: string; role: string | null; action: string; target: string; detail: string; client: string }

export interface BackupStatus {
  enabled: boolean; keep_days: number; latest_day: string | null; days: string[]; storage_warning: string;
  offsite: { configured: boolean; bucket: string; endpoint: string; last_ok: string | null; last_error: string | null; last_daily: string | null; last_weekly: string | null; keep_daily: number; keep_weekly: number };
}
