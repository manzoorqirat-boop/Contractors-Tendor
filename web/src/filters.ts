import { fmtDate, inr } from './format';
import type { AppConfig, Display, Drill, Filters } from './types';

export const NONE = '__none__'; // the server's word for "not stated"

export const EMPTY: Filters = { q: '', status: 'open', portal: [], category: [], department: [], location: [], work_type: [] };
export const DISPLAY: Display = { sort: 'closing', page: 1, group_by: 'portal', split_by: 'category', measure: 'count', chart: 'stacked', layout: 'cards' };

const LISTS = ['portal', 'category', 'department', 'location', 'work_type'] as const;
const STRINGS = ['hidden', 'min_fit', 'min_value', 'max_value', 'value_lt', 'closing_from', 'closing_to', 'seen_from', 'seen_to', 'published_from', 'published_to', 'closing_within', 'since_days'] as const;
const FLAGS = ['no_value', 'no_closing', 'no_published', 'starred', 'fits_me'] as const;

/** Filters -> URL parameters. Empty things are left out, so links stay short. */
export function toParams(f: Filters, d?: Partial<Display>): URLSearchParams {
  const p = new URLSearchParams();
  if (f.q) p.set('q', f.q);
  if (f.status !== 'open') p.set('status', f.status);
  for (const k of LISTS) for (const v of f[k]) p.append(k, v);
  for (const k of STRINGS) if (f[k]) p.set(k, String(f[k]));
  for (const k of FLAGS) if (f[k]) p.set(k, 'true');
  if (d) for (const [k, v] of Object.entries(d)) if (v !== undefined && v !== '' && v !== (DISPLAY as unknown as Record<string, unknown>)[k]) p.set(k, String(v));
  return p;
}

export function fromParams(p: URLSearchParams): { filters: Filters; display: Display } {
  const f: Filters = { ...EMPTY, portal: [], category: [], department: [], location: [], work_type: [] };
  f.q = p.get('q') ?? '';
  const status = p.get('status');
  if (status === 'open' || status === 'closing_soon' || status === 'closed' || status === 'all') f.status = status;
  for (const k of LISTS) f[k] = p.getAll(k);
  for (const k of STRINGS) { const v = p.get(k); if (v) f[k] = v; }
  for (const k of FLAGS) if (p.get(k) === 'true') f[k] = true;
  const d: Display = { ...DISPLAY };
  d.sort = p.get('sort') ?? d.sort;
  d.page = Math.max(1, Number(p.get('page')) || 1);
  d.group_by = p.get('group_by') ?? d.group_by;
  d.split_by = p.has('split_by') ? p.get('split_by') ?? '' : d.split_by;
  const m = p.get('measure'); if (m === 'count' || m === 'value' || m === 'avg') d.measure = m;
  const c = p.get('chart'); if (c === 'bars' || c === 'stacked' || c === 'table') d.chart = c;
  d.layout = p.get('layout') === 'table' ? 'table' : 'cards';
  return { filters: f, display: d };
}

/** A drill-down from a chart, laid over the filters already in force. Lists are replaced; ranges only ever narrow. */
export function mergeDrill(f: Filters, drill: Drill): Filters {
  const out: Filters = { ...f };
  const bag = out as unknown as Record<string, unknown>;
  for (const [k, v] of Object.entries(drill)) {
    const cur = bag[k];
    if (k === 'status') { if (v !== 'all') bag[k] = v; }
    else if (Array.isArray(v)) bag[k] = v;
    else if (k.endsWith('_from')) bag[k] = cur && String(cur) > String(v) ? cur : String(v);
    else if (k.endsWith('_to')) bag[k] = cur && String(cur) < String(v) ? cur : String(v);
    else if (k === 'min_value') bag[k] = String(Math.max(Number(cur ?? 0), Number(v)));
    else if (k === 'value_lt') bag[k] = String(Math.min(Number(cur ?? Infinity), Number(v)));
    else bag[k] = v;
  }
  return out;
}

export function isFiltered(f: Filters): boolean {
  return describe(f).length > 0;
}

export interface Chip { id: string; label: string; remove: (f: Filters) => Filters }

const without = (f: Filters, ...keys: (keyof Filters)[]): Filters => {
  const out = { ...f } as unknown as Record<string, unknown>;
  for (const k of keys) out[k] = Array.isArray(f[k]) ? [] : undefined;
  return out as unknown as Filters;
};

/** The filters in force as removable chips, in plain words. */
export function describe(f: Filters, config?: AppConfig): Chip[] {
  const chips: Chip[] = [];
  const name = (v: string) => (v === NONE ? 'Not stated' : v);
  if (f.q) chips.push({ id: 'q', label: `Search: "${f.q}"`, remove: (x) => ({ ...x, q: '' }) });
  if (f.status !== 'open') chips.push({ id: 'status', label: { closing_soon: 'Closing soon', closed: 'Closed', all: 'Open and closed', open: 'Open' }[f.status], remove: (x) => ({ ...x, status: 'open' }) });
  for (const k of LISTS) {
    if (f[k].length) {
      const label = k === 'portal' ? 'Portal' : k === 'work_type' ? 'Work type' : k[0].toUpperCase() + k.slice(1);
      const names = f[k].map((v) => (k === 'portal' ? config?.portals.find((p) => p.key === v)?.state ?? v : name(v)));
      chips.push({ id: k, label: `${label}: ${names.length > 2 ? `${names.slice(0, 2).join(', ')} +${names.length - 2}` : names.join(', ')}`, remove: (x) => without(x, k) });
    }
  }
  if (f.min_value || f.value_lt || f.max_value) {
    const lo = f.min_value ? inr(Number(f.min_value)) : null;
    const hi = f.value_lt ?? f.max_value;
    chips.push({ id: 'value', label: lo && hi ? `Value ${lo} to ${inr(Number(hi))}` : lo ? `Value ${lo} or more` : `Value under ${inr(Number(hi))}`, remove: (x) => without(x, 'min_value', 'max_value', 'value_lt') });
  }
  if (f.no_value) chips.push({ id: 'no_value', label: 'Value not stated', remove: (x) => without(x, 'no_value') });
  if (f.closing_within) chips.push({ id: 'within', label: `Closes within ${f.closing_within} days`, remove: (x) => without(x, 'closing_within') });
  const range = (id: string, label: string, a?: string, b?: string, ...keys: (keyof Filters)[]) => {
    if (!a && !b) return;
    const show = (v?: string) => (v ? fmtDate(v.slice(0, 10)) : '');
    chips.push({ id, label: `${label} ${a ? show(a) : '…'} to ${b ? show(b) : '…'}`, remove: (x) => without(x, ...keys) });
  };
  range('closing', 'Closes', f.closing_from, f.closing_to, 'closing_from', 'closing_to');
  range('seen', 'Found', f.seen_from, f.seen_to, 'seen_from', 'seen_to');
  range('published', 'Published', f.published_from, f.published_to, 'published_from', 'published_to');
  if (f.no_closing) chips.push({ id: 'no_closing', label: 'No closing date', remove: (x) => without(x, 'no_closing') });
  if (f.no_published) chips.push({ id: 'no_published', label: 'No publish date', remove: (x) => without(x, 'no_published') });
  if (f.since_days) chips.push({ id: 'since', label: `Found in the last ${f.since_days} days`, remove: (x) => without(x, 'since_days') });
  if (f.min_fit) chips.push({ id: 'min_fit', label: `Fit ${f.min_fit}+ of 5`, remove: (x) => without(x, 'min_fit') });
  if (f.starred) chips.push({ id: 'starred', label: 'Starred only', remove: (x) => without(x, 'starred') });
  if (f.fits_me) chips.push({ id: 'fits_me', label: 'Fits my size', remove: (x) => without(x, 'fits_me') });
  if (f.hidden === 'only') chips.push({ id: 'hidden', label: 'Ignored tenders only', remove: (x) => without(x, 'hidden') });
  return chips;
}

export const VALUE_PRESETS: { label: string; min?: string; max?: string }[] = [
  { label: 'Any value' },
  { label: 'Under 10 lakh', max: '1000000' },
  { label: '10 lakh to 1 crore', min: '1000000', max: '10000000' },
  { label: '1 crore and above', min: '10000000' },
  { label: '10 crore and above', min: '100000000' },
];
