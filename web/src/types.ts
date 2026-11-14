export interface PortalInfo { key: string; name: string; state: string; search_url: string }
export interface AppConfig { portals: PortalInfo[]; mail_configured: boolean; recipient_count: number; now: string; timezone: string }

export interface PortalReport { portal: string; name: string; departments: number; listed: number; new_civil: number; errors: string[]; failed: boolean }
export interface Run {
  id: number; trigger: 'manual' | 'scheduled'; status: 'running' | 'ok' | 'failed'; started: string; finished: string | null;
  portals: string[]; send_email: boolean; reports: PortalReport[]; new_civil: number; emailed_count: number; error: string | null; log?: string[];
}

export interface Tender {
  key: string; portal: string; portal_name: string; search_url: string; tender_id: string; first_seen: string; emailed: number;
  closing: string | null; published: string | null; value: number | null; value_text: string; emd: number | null;
  category: string; location: string; organisation: string; title: string; description: string;
}
export interface TenderPage { items: Tender[]; total: number; page: number; page_size: number; categories: string[] }

export interface Stats {
  kpi: { open: number; new_today: number; closing_soon: number; open_value: number; total_kept: number };
  by_portal: { portal: string; name: string; short: string; count: number; value: number }[];
  by_category: { category: string; count: number }[];
  by_value: { band: string; count: number }[];
  departments: { department: string; portal: string; portal_name: string; count: number; value: number }[];
  new_per_day: { day: string; count: number }[];
  closing_per_day: { day: string; count: number }[];
  biggest: { key: string; portal: string; portal_name: string; tender_id: string; closing: string | null; value: number; category: string; organisation: string; title: string; description: string }[];
  last_run: Run | null;
  soon_days: number;
}

export interface Settings {
  recipients: string[]; send_time: string; schedule_enabled: boolean; send_empty: boolean; portals: string[]; min_value: number;
  extra_keywords: string[]; exclude_words: string[]; mail_configured: boolean; mail_from: string; available_portals: { key: string; name: string }[];
}

export interface Filters { q: string; portal: string; category: string; status: string; min_value: string; sort: string; page: number }
