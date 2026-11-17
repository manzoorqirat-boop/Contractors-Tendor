import { ScrollX } from '../ScrollX';
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { inr, inrShort } from '../format';
import type { Drill, Measure, Pivot } from '../types';
import { MEASURES, SERIES, fmtMetric, labelFor, metricOf } from './chartdata';

export * from './chartdata';   // the helpers live in chartdata.ts; they are still importable from here

const tick = { fill: 'var(--muted)', fontSize: 12 };
const tooltipStyle = {
  contentStyle: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text)', fontSize: 13 },
  cursor: { fill: 'var(--surface-2)' },
  labelStyle: { color: 'var(--text)', fontWeight: 600 },
};
const OTHER = 'var(--c0)';

export interface SimpleProps<T extends object> {
  data: T[]; category: keyof T & string; value: keyof T & string; color: string; label: string;
  horizontal?: boolean; formatCategory?: (v: string) => string; height?: number; interval?: number | 'preserveStartEnd';
  onSelect?: (index: number) => void; money?: boolean;
}

/** One-series bar chart. With `onSelect`, bars are clickable. */
export function BarBlock<T extends object>({ data, category, value, color, label, horizontal, formatCategory, height = 240, interval = 'preserveStartEnd', onSelect, money }: SimpleProps<T>) {
  const fmt = formatCategory ?? ((v: string) => v);
  const catKey: string = category;
  const valKey: string = value;
  const rows = data as Record<string, unknown>[];
  const num = (v: unknown) => (money ? inr(Number(v)) : Number(v).toLocaleString('en-IN'));
  return (
    <div className="chart" style={{ height }} role="img" aria-label={`${label} by ${category}`}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} layout={horizontal ? 'vertical' : 'horizontal'} margin={{ top: 4, right: 12, bottom: 0, left: horizontal ? 4 : -12 }}>
          <CartesianGrid stroke="var(--grid)" vertical={!!horizontal} horizontal={!horizontal} />
          {horizontal ? (
            <>
              <XAxis type="number" tick={tick} tickLine={false} axisLine={false} allowDecimals={false} tickFormatter={money ? inrShort : undefined} />
              <YAxis type="category" dataKey={catKey} tick={tick} tickLine={false} axisLine={false} width={150} tickFormatter={(v: string) => (fmt(v).length > 24 ? fmt(v).slice(0, 23) + '…' : fmt(v))} />
            </>
          ) : (
            <>
              <XAxis dataKey={catKey} tick={tick} tickLine={false} axisLine={{ stroke: 'var(--grid)' }} interval={interval} tickFormatter={fmt} />
              <YAxis tick={tick} tickLine={false} axisLine={false} allowDecimals={false} tickFormatter={money ? inrShort : undefined} />
            </>
          )}
          <Tooltip {...tooltipStyle} formatter={(v) => [num(v), label]} labelFormatter={(l) => fmt(String(l))} />
          <Bar dataKey={valKey} fill={color} radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]} maxBarSize={32} isAnimationActive={false}
            cursor={onSelect ? 'pointer' : undefined} onClick={onSelect ? (_d, i) => onSelect(i) : undefined} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

interface PivotProps {
  pivot: Pivot; measure: Measure; stacked: boolean; horizontal: boolean; height?: number; onSelect: (drill: Drill) => void;
}

/** Bars for one dimension, or stacked / grouped bars when it is split by a second one. */
export function PivotChart({ pivot, measure, stacked, horizontal, height = 380, onSelect }: PivotProps) {
  const split = pivot.split_by !== null;
  const series = split ? pivot.splits : ['_'];
  const data = pivot.rows.map((r) => {
    const row: Record<string, unknown> = { name: labelFor(pivot.group_by, r.key, pivot.labels), __drill: r.drill ?? null, __drills: {} as Record<string, Drill> };
    for (const s of series) {
      const cell = split ? r.parts[s] : r;
      row[s] = metricOf(cell, measure);
      if (cell?.drill || (!split && r.drill)) (row.__drills as Record<string, Drill>)[s] = (split ? cell?.drill : r.drill) as Drill;
    }
    return row;
  });
  const colour = (s: string, i: number) => (s === 'Other' ? OTHER : SERIES[i % SERIES.length]);
  const money = measure !== 'count';
  const click = (s: string) => (d: unknown) => {
    const payload = (d as { payload?: Record<string, unknown> }).payload ?? (d as Record<string, unknown>);
    const drill = (payload.__drills as Record<string, Drill> | undefined)?.[s] ?? (payload.__drill as Drill | null);
    if (drill) onSelect(drill);
  };
  return (
    <div className="chart" style={{ height }} role="img" aria-label={`${MEASURES[measure]} by ${pivot.group_by}${split ? ` and ${pivot.split_by}` : ''}. A table view is available.`}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout={horizontal ? 'vertical' : 'horizontal'} margin={{ top: 4, right: 12, bottom: 0, left: horizontal ? 4 : -4 }}>
          <CartesianGrid stroke="var(--grid)" vertical={horizontal} horizontal={!horizontal} />
          {horizontal ? (
            <>
              <XAxis type="number" tick={tick} tickLine={false} axisLine={false} tickFormatter={money ? inrShort : undefined} allowDecimals={false} />
              <YAxis type="category" dataKey="name" tick={tick} tickLine={false} axisLine={false} width={200} tickFormatter={(v: string) => (v.length > 32 ? v.slice(0, 31) + '…' : v)} />
            </>
          ) : (
            <>
              <XAxis dataKey="name" tick={tick} tickLine={false} axisLine={{ stroke: 'var(--grid)' }} interval={0} angle={data.length > 8 ? -35 : 0} textAnchor={data.length > 8 ? 'end' : 'middle'} height={data.length > 8 ? 70 : 30} />
              <YAxis tick={tick} tickLine={false} axisLine={false} tickFormatter={money ? inrShort : undefined} allowDecimals={false} />
            </>
          )}
          <Tooltip {...tooltipStyle} formatter={(v, name) => [fmtMetric(Number(v), measure), split ? labelFor(pivot.split_by ?? '', String(name), pivot.labels) : MEASURES[measure]]} />
          {split && <Legend wrapperStyle={{ fontSize: 12, color: 'var(--muted)' }} formatter={(v: string) => labelFor(pivot.split_by ?? '', v, pivot.labels)} />}
          {series.map((s, i) => (
            <Bar key={s} dataKey={s} stackId={stacked && split ? 'a' : undefined} fill={split ? colour(s, i) : SERIES[0]} cursor="pointer" isAnimationActive={false}
              maxBarSize={horizontal ? 28 : 48} radius={stacked && split ? 0 : horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]} onClick={click(s)} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** The same numbers as a table, shaded like a heat map. Every cell opens its tenders. */
export function PivotTable({ pivot, measure, onSelect }: { pivot: Pivot; measure: Measure; onSelect: (drill: Drill) => void }) {
  const split = pivot.split_by !== null;
  const cols = split ? pivot.splits : ['_'];
  const all = pivot.rows.flatMap((r) => cols.map((c) => metricOf(split ? r.parts[c] : r, measure)));
  const max = Math.max(1, ...all);
  const shade = (v: number) => (v > 0 ? `color-mix(in srgb, var(--accent) ${Math.round(8 + (v / max) * 52)}%, transparent)` : undefined);
  return (
    <ScrollX label="Chart table">
      <table className="tbl heat">
        <thead>
          <tr>
            <th>{pivot.group_by.replace('_', ' ')}</th>
            {split ? cols.map((c) => <th key={c} className="num">{labelFor(pivot.split_by ?? '', c, pivot.labels, false)}</th>) : <th className="num">{MEASURES[measure]}</th>}
            {split && <th className="num">Total</th>}
          </tr>
        </thead>
        <tbody>
          {pivot.rows.map((r) => (
            <tr key={r.key}>
              <th scope="row">{labelFor(pivot.group_by, r.key, pivot.labels, false)}</th>
              {cols.map((c) => {
                const cell = split ? r.parts[c] : r;
                const v = metricOf(cell, measure);
                const drill = cell?.drill ?? (!split ? r.drill : undefined);
                return (
                  <td key={c} className={`num${drill && v ? ' click' : ''}`} style={{ background: shade(v) }}>
                    {drill && v ? <button type="button" className="cell-btn" onClick={() => onSelect(drill)}>{fmtMetric(v, measure)}</button> : v ? fmtMetric(v, measure) : <span className="muted">-</span>}
                  </td>
                );
              })}
              {split && <td className="num strong">{fmtMetric(metricOf(r, measure), measure)}</td>}
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">Total</th>
            <td colSpan={cols.length + (split ? 1 : 0)} className="num strong">
              {measure === 'count' ? pivot.total.count.toLocaleString('en-IN') : measure === 'value' ? inr(pivot.total.value) : ''}
            </td>
          </tr>
        </tfoot>
      </table>
    </ScrollX>
  );
}

export function pivotToCsv(p: Pivot, measure: Measure): string {
  const cols = p.split_by ? p.splits : [];
  const esc = (v: string | number) => {
    let s = String(v);
    if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const head = [p.group_by, ...(cols.length ? cols.map((c) => labelFor(p.split_by ?? '', c, p.labels)) : [MEASURES[measure]]), ...(cols.length ? ['Total'] : [])];
  const lines = [head.map(esc).join(',')];
  for (const r of p.rows) {
    const cells = cols.length ? cols.map((c) => metricOf(r.parts[c], measure)) : [metricOf(r, measure)];
    lines.push([labelFor(p.group_by, r.key, p.labels), ...cells, ...(cols.length ? [metricOf(r, measure)] : [])].map((v) => esc(typeof v === 'number' ? Math.round(v) : v)).join(','));
  }
  return '﻿' + lines.join('\n');
}
