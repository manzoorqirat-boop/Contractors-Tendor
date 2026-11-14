export interface PortalInfo { key: string; name: string; state: string; search_url: string }
export interface Dimension { key: string; label: string; time: boolean }
export interface AppConfig {
  portals: PortalInfo[]; mail_configured: boolean; recipient_count: number; now: string; timezone: string; dimensions: Dimension[]; soon_days: number;
}

export interface PortalReport { portal: string; name: string; departments: number; listed: number; new_civil: number; errors: string[]; failed: boolean }
export interface Run {
  id: number; trigger: 'manual' | 'scheduled'; status: 'running' | 'ok' | 'failed'; started: string; finished: string | null;
  portals: string[]; send_email: boolean; reports: PortalReport[]; new_civil: number; emailed_count: number; error: string | null; log?: string[];
}

export interface Tender {
  key: string; portal: string; portal_name: string; search_url: string; tender_id: string; first_seen: string; emailed: number;
  closing: string | null; published: string | null; opening?: string | null; value: number | null; value_text: string; emd: number | null;
  category: string; location: string; organisation: string; title: string; description: string; starred: boolean;
}
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
export interface Facets { portal: Facet[]; category: Facet[]; department: Facet[]; location: Facet[] }

export interface Settings {
  recipients: string[]; send_time: string; schedule_enabled: boolean; send_empty: boolean; portals: string[]; min_value: number;
  extra_keywords: string[]; exclude_words: string[]; mail_configured: boolean; mail_from: string; available_portals: { key: string; name: string }[];
}

export type Status = 'open' | 'closing_soon' | 'closed' | 'all';
export type Measure = 'count' | 'value' | 'avg';
export type ChartKind = 'bars' | 'stacked' | 'table';

/** Everything that narrows which tenders are shown. The same object drives the dashboard, Explore and Tenders pages. */
export interface Filters {
  q: string; status: Status; portal: string[]; category: string[]; department: string[]; location: string[];
  min_value?: string; max_value?: string; value_lt?: string; no_value?: boolean; no_closing?: boolean; no_published?: boolean; starred?: boolean;
  closing_from?: string; closing_to?: string; seen_from?: string; seen_to?: string; published_from?: string; published_to?: string;
  closing_within?: string; since_days?: string;
}

/** Display choices that are not filters. */
export interface Display { sort: string; page: number; group_by: string; split_by: string; measure: Measure; chart: ChartKind; layout: 'cards' | 'table' }
export interface SavedView { id: string; name: string; filters: Record<string, unknown> }
