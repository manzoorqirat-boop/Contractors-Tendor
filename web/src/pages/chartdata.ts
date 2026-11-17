import { fmtDay, inr } from '../format';
import type { Cell, Measure } from '../types';

// The parts of the charts that need no charting library: colours, labels and the numbers behind them. Kept apart from Charts.tsx so the
// Dashboard can use them straight away while the library itself loads a moment later.

export const SERIES = ['var(--c1)', 'var(--c2)', 'var(--c3)', 'var(--c4)', 'var(--c5)', 'var(--c7)', 'var(--c8)', 'var(--c6)'];

export function metricOf(c: Pick<Cell, 'count' | 'value' | 'avg'> | undefined, m: Measure): number {
  if (!c) return 0;
  return m === 'count' ? c.count : m === 'value' ? c.value : c.avg ?? 0;
}
export const fmtMetric = (v: number, m: Measure) => (m === 'count' ? v.toLocaleString('en-IN') : inr(v));
export const MEASURES: Record<Measure, string> = { count: 'Number of tenders', value: 'Total value', avg: 'Average value' };

/** "2026-11-09" -> "9 Nov" or "Week of 9 Nov", depending on the dimension. Other values pass through. */
const BAND: Record<string, string> = { 'Under 1 lakh': '< 1 L', '1 to 10 lakh': '1–10 L', '10 lakh to 1 crore': '10 L–1 Cr', '1 to 10 crore': '1–10 Cr', 'Over 10 crore': '> 10 Cr' };

export function labelFor(dim: string, key: string, labels: Record<string, string> = {}, short = true): string {
  if (dim === 'portal') return labels[key] ?? key;
  if (dim === 'value_band') return short ? BAND[key] ?? key : key;
  if (/^\d{4}-\d{2}-\d{2}$/.test(key)) return dim === 'closing_week' ? `Week of ${fmtDay(key)}` : fmtDay(key);
  return key;
}
