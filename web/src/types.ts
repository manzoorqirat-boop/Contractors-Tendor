export interface PortalInfo { key: string; name: string; state: string; search_url: string }
export interface Dimension { key: string; label: string; time: boolean }
export interface AppConfig {
  portals: PortalInfo[]; mail_configured: boolean; recipient_count: number; now: string; timezone: string; dimensions: Dimension[]; soon_days: number; ai_enabled?: boolean;
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
export interface TenderDetail extends Tender { summary: string | null; brief: Brief | null; doc: DocFacts | null; related: Related[]; bid_id?: number | null; history?: string[] }
export interface AiStatus { configured: boolean; model: string; fast_model: string; profile_set: boolean; labelled: number; unlabelled: number }
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
  extra_keywords: string[]; exclude_words: string[]; company_profile: string; ai_digest_intro: boolean; ai_hide_noncivil: boolean; mail_configured: boolean; mail_from: string; available_portals: { key: string; name: string }[]; custom_portals?: NewPortal[]; logo_text: string;
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
export interface SavedView { id: string; name: string; filters: Record<string, unknown> }

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
  doc: DocFacts | null; brief: Brief | null; eligibility: Eligibility | null; drafts: Record<string, string> | null; portal_facts: { label: string; value: string }[] | null;
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
export interface FetchResult { facts: { label: string; value: string }[]; added: number; nit: 'read' | 'ai_off' | 'ai_failed' | 'login' | 'failed' | 'no_link'; message: string; corrigenda: string[] }
