import { useState } from 'react';
import { api } from '../api';
import type { Nav } from '../App';
import { FilterBar } from '../FilterBar';
import { mergeDrill } from '../filters';
import { inr, plural } from '../format';
import { useLoad } from '../hooks';
import { ChartSkeleton, useToast } from '../ui';
import type { AppConfig, ChartKind, Drill, Measure } from '../types';
import { MEASURES, PivotChart, PivotTable, labelFor, pivotToCsv } from './Charts';

// One click sets a whole combination. The selectors below can build any other.
const PRESETS: { label: string; group: string; split: string; measure?: Measure }[] = [
  { label: 'Portal by category', group: 'portal', split: 'category' },
  { label: 'Department by value', group: 'department', split: 'value_band' },
  { label: 'Category by closing week', group: 'closing_week', split: 'category' },
  { label: 'Location by category', group: 'location', split: 'category' },
  { label: 'Closing window by portal', group: 'closes_in', split: 'portal' },
  { label: 'Found per day by portal', group: 'first_seen_day', split: 'portal' },
  { label: 'Value band by portal', group: 'value_band', split: 'portal' },
  { label: 'Category by value', group: 'category', split: 'value_band' },
  { label: 'Money by department', group: 'department', split: '', measure: 'value' },
];

const ORDERED = new Set(['value_band', 'closes_in']);

export function Explore({ config, nav }: { config: AppConfig; nav: Nav }) {
  const { filters, display } = nav;
  const toast = useToast();
  const [top, setTop] = useState(12);
  const group = display.group_by;
  const split = display.split_by === group ? '' : display.split_by;
  const measure = display.measure;
  const chart: ChartKind = split ? display.chart : display.chart === 'table' ? 'table' : 'bars';
  const dims = config.dimensions;
  const dimInfo = (k: string) => dims.find((d) => d.key === k);
  const dimLabel = (k: string) => dimInfo(k)?.label ?? k;
  const horizontal = !dimInfo(group)?.time && !ORDERED.has(group);

  const pivot = useLoad(() => api.pivot(filters, group, split, top), [JSON.stringify(filters), group, split, top]);
  const open = (drill: Drill) => nav.go('tenders', mergeDrill(filters, drill));

  function download() {
    if (!pivot.data) return;
    const url = URL.createObjectURL(new Blob([pivotToCsv(pivot.data, measure)], { type: 'text/csv;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url; a.download = `${group}${split ? '-by-' + split : ''}.csv`; a.click();
    URL.revokeObjectURL(url);
    toast('Table downloaded');
  }

  const p = pivot.data;
  const title = `${MEASURES[measure]} by ${dimLabel(group).toLowerCase()}${split ? ` and ${dimLabel(split).toLowerCase()}` : ''}`;
  const active = (g: string, s: string, m?: Measure) => g === group && s === split && (m ?? measure) === measure;

  return (
    <>
      <div className="page-head">
        <div><h1>Explore</h1><p className="sub">Combine any two things, see the numbers, and click a bar or cell to open its tenders.</p></div>
      </div>

      <FilterBar filters={filters} display={display} config={config} facets={nav.facets} onChange={nav.setFilters} onApplyView={(f, d) => nav.go('explore', f, d)} />

      <section className="card" style={{ marginBottom: 14 }}>
        <h2>Pick a combination</h2>
        <div className="preset-row" role="group" aria-label="Ready-made combinations">
          {PRESETS.map((x) => (
            <button key={x.label} type="button" className={`chip pick${active(x.group, x.split, x.measure ?? measure) ? ' on' : ''}`} aria-pressed={active(x.group, x.split, x.measure ?? measure)}
              onClick={() => nav.setDisplay({ group_by: x.group, split_by: x.split, measure: x.measure ?? measure, chart: x.split ? (display.chart === 'bars' ? 'stacked' : display.chart) : 'bars' })}>{x.label}</button>
          ))}
        </div>
        <div className="controls">
          <label className="field-inline">Group by
            <select value={group} onChange={(e) => nav.setDisplay({ group_by: e.target.value, split_by: split === e.target.value ? '' : split })}>
              {dims.map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
            </select>
          </label>
          <label className="field-inline">Split by
            <select value={split} onChange={(e) => nav.setDisplay({ split_by: e.target.value, chart: e.target.value && display.chart === 'bars' ? 'stacked' : display.chart })}>
              <option value="">Nothing</option>
              {dims.filter((d) => d.key !== group).map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
            </select>
          </label>
          <div className="field-inline">Measure
            <div className="seg" role="group" aria-label="Measure">
              {(['count', 'value', 'avg'] as Measure[]).map((m) => <button key={m} type="button" aria-pressed={measure === m} className={measure === m ? 'on' : ''} onClick={() => nav.setDisplay({ measure: m })}>{m === 'count' ? 'Tenders' : m === 'value' ? 'Total value' : 'Average'}</button>)}
            </div>
          </div>
          <div className="field-inline">Show as
            <div className="seg" role="group" aria-label="Chart type">
              {split ? (
                <>
                  <button type="button" aria-pressed={chart === 'stacked'} className={chart === 'stacked' ? 'on' : ''} onClick={() => nav.setDisplay({ chart: 'stacked' })}>Stacked</button>
                  <button type="button" aria-pressed={chart === 'bars'} className={chart === 'bars' ? 'on' : ''} onClick={() => nav.setDisplay({ chart: 'bars' })}>Grouped</button>
                </>
              ) : <button type="button" aria-pressed={chart === 'bars'} className={chart === 'bars' ? 'on' : ''} onClick={() => nav.setDisplay({ chart: 'bars' })}>Bars</button>}
              <button type="button" aria-pressed={chart === 'table'} className={chart === 'table' ? 'on' : ''} onClick={() => nav.setDisplay({ chart: 'table' })}>Table</button>
            </div>
          </div>
          <label className="field-inline">Show top
            <select value={top} onChange={(e) => setTop(Number(e.target.value))}>{[8, 12, 20, 30].map((n) => <option key={n} value={n}>{n}</option>)}</select>
          </label>
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>{title}</h2>
            <p className="sub">{p ? `${plural(p.total.count, 'tender')}${p.total.value ? ` worth ${inr(p.total.value)}` : ''}${filtersText(filters)}` : 'Loading…'}</p>
          </div>
          <div className="head-actions">
            <button className="btn small" onClick={download} disabled={!p || p.rows.length === 0}>Download table</button>
            <button className="btn small" onClick={() => nav.go('tenders', filters)}>Open all in Tenders</button>
          </div>
        </div>

        {pivot.error && <div className="alert bad">{pivot.error}</div>}
        {!p && !pivot.error && <ChartSkeleton height={380} />}
        {p && p.rows.length === 0 && <div className="empty">No tenders match these filters.</div>}
        {p && p.rows.length > 0 && (
          <div style={{ opacity: pivot.loading ? 0.55 : 1 }} aria-busy={pivot.loading}>
            {chart === 'table'
              ? <PivotTable pivot={p} measure={measure} onSelect={open} />
              : <PivotChart pivot={p} measure={measure} stacked={chart === 'stacked'} horizontal={horizontal} height={horizontal ? Math.max(300, p.rows.length * (split && chart === 'bars' ? 20 + p.splits.length * 14 : 34)) : 380} onSelect={open} />}
            {p.rows.some((r) => r.key === 'Other') && <p className="muted small">"Other" adds up everything beyond the top {top}. Raise "Show top" to see more.</p>}
            {measure === 'avg' && <p className="muted small">Average of tenders that state a value; tenders with no stated value are not counted in it.</p>}
          </div>
        )}
        {p && <span className="sr-only" aria-live="polite">{labelFor(group, p.rows[0]?.key ?? '', p.labels)}</span>}
      </section>
    </>
  );
}

function filtersText(f: { portal: string[]; category: string[]; q: string }) {
  const n = f.portal.length + f.category.length + (f.q ? 1 : 0);
  return n ? ' matching your filters' : '';
}
