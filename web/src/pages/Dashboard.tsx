import type { ReactNode } from 'react';
import { api } from '../api';
import { FilterBar } from '../FilterBar';
import { mergeDrill } from '../filters';
import { fmtDate, fmtDay, headline, inr, plural } from '../format';
import { useLoad } from '../hooks';
import type { Nav } from '../App';
import { ChartSkeleton, Skeleton } from '../ui';
import { ManualBanner } from '../ManualPortals';
import type { AppConfig, Drill, Filters, Measure, Pivot } from '../types';
import { BarBlock, labelFor, metricOf, MEASURES } from './Charts';

const day = (iso: string, plus = 0) => {
  const d = new Date(Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10) + plus));
  return d.toISOString().slice(0, 10);
};

/** Time charts list only the days that have tenders; this adds the empty days so the axis is honest. */
function fillDays(p: Pivot | null, start: string, n: number, measure: Measure) {
  const byKey = new Map((p?.rows ?? []).map((r) => [r.key, r]));
  return Array.from({ length: n }, (_, i) => {
    const key = day(start, i);
    const r = byKey.get(key);
    return { key, value: metricOf(r, measure), drill: r?.drill ?? null };
  });
}

function usePivot(f: Filters, groupBy: string, top = 12) {
  return useLoad(() => api.pivot(f, groupBy, '', top), [JSON.stringify(f), groupBy, top]);
}

export function Dashboard({ config, nav }: { config: AppConfig; nav: Nav }) {
  const { filters, display, go } = nav;
  const measure: Measure = display.measure === 'value' ? 'value' : 'count';
  const today = config.now.slice(0, 10);
  const stats = useLoad(() => api.stats(filters), [JSON.stringify(filters)]);

  const seenFrom = day(today, -29);
  const portals = usePivot({ ...filters, status: filters.status === 'closed' ? 'closed' : 'open' }, 'portal');
  const categories = usePivot({ ...filters, status: filters.status === 'closed' ? 'closed' : 'open' }, 'category');
  const values = usePivot({ ...filters, status: filters.status === 'closed' ? 'closed' : 'open' }, 'value_band');
  const window_ = usePivot({ ...filters, status: 'all' }, 'closes_in');
  const depts = usePivot({ ...filters, status: filters.status === 'closed' ? 'closed' : 'open' }, 'department', 10);
  const found = usePivot({ ...filters, status: 'all', seen_from: seenFrom }, 'first_seen_day', 40);
  const closing = usePivot({ ...filters, status: 'open', closing_within: '14', closing_from: today }, 'closing_day', 20);

  const open = (drill: Drill | null) => { if (drill) go('tenders', mergeDrill(filters, drill)); };
  const k = stats.data?.kpi;
  const delta = k ? k.new_today - k.new_yesterday : 0;
  const empty = k && k.total_kept === 0 && !stats.loading;

  const kpiGo = (patch: Partial<Filters>) => go('tenders', { ...filters, ...patch }, undefined);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <p className="sub">{k ? <>{k.closing_week > 0 ? <><b className="hl-warn">{plural(k.closing_week, 'tender')}</b> close within 7 days</> : 'Nothing closes within 7 days'}{k.new_today > 0 ? <> · <b>{k.new_today}</b> found today</> : ''}. Select any number or bar to see its tenders.</> : `Civil tenders from ${config.portals.length} portals.`}</p>
        </div>
        <div className="head-actions">
          <div className="seg" role="group" aria-label="Chart measure">
            {(['count', 'value'] as Measure[]).map((m) => <button key={m} type="button" aria-pressed={measure === m} className={measure === m ? 'on' : ''} onClick={() => nav.setDisplay({ measure: m })}>{m === 'count' ? 'Tenders' : 'Value'}</button>)}
          </div>
          <button className="btn primary" onClick={() => go('runs')}>Run now</button>
        </div>
      </div>

      <ManualBanner onOpen={() => go('settings')} />

      <FilterBar filters={filters} display={display} config={config} facets={nav.facets} onChange={nav.setFilters} onApplyView={(f, d) => go('dashboard', f, d)} hideStatus />

      {stats.error && <div className="alert bad">{stats.error}</div>}
      {empty ? (
        <div className="card empty" style={{ padding: 40 }}>
          <svg className="empty-ico" viewBox="0 0 24 24" aria-hidden><path d="M11 4a7 7 0 105.2 11.7l3.5 3.5 1.4-1.4-3.5-3.5A7 7 0 0011 4zm0 2a5 5 0 110 10 5 5 0 010-10z" /></svg><h2 style={{ marginBottom: 6 }}>No tenders match</h2>
          <p className="sub" style={{ marginBottom: 16 }}>{Object.keys(filters).length && (filters.portal.length || filters.category.length || filters.q) ? 'Try clearing a filter above.' : 'Nothing has been collected yet. Go to Runs and press Run now; the first run takes 20 to 40 minutes.'}</p>
          <button className="btn primary" onClick={() => go('runs')}>Go to Runs</button>
        </div>
      ) : (
        <>
          <div className="grid kpis">
            <Kpi loading={!k} label="Open civil tenders" value={k?.open.toLocaleString('en-IN')} note={k ? `${k.total_kept.toLocaleString('en-IN')} collected in all` : ''} onClick={() => kpiGo({ status: 'open' })} />
            <Kpi loading={!k} label="Found today" value={k?.new_today.toLocaleString('en-IN')} note={k ? (delta === 0 ? 'same as yesterday' : `${delta > 0 ? '▲' : '▼'} ${Math.abs(delta)} vs yesterday`) : ''} tone={delta > 0 ? 'good' : undefined} onClick={() => go('tenders', { ...filters, status: 'all', seen_from: today }, { sort: 'first_seen' })} />
            <Kpi loading={!k} label={`Closing in ${config.soon_days} days`} value={k?.closing_soon.toLocaleString('en-IN')} note="bid soon" tone="warn" onClick={() => kpiGo({ status: 'closing_soon' })} />
            <Kpi loading={!k} label="Closing this week" value={k?.closing_week.toLocaleString('en-IN')} note="within 7 days" onClick={() => kpiGo({ status: 'open', closing_within: '7' })} />
            <Kpi loading={!k} label="Value of open tenders" value={k ? inr(k.open_value) : undefined} note={k ? `average ${inr(k.avg_value)}` : ''} />
            <Kpi loading={!k} label="Starred" value={k?.starred.toLocaleString('en-IN')} note="your watchlist" onClick={() => kpiGo({ starred: true, status: 'open' })} />
          </div>

          <div className="grid cols-2">
            <Card title="Found per day" sub="Civil tenders first seen, last 30 days" explore={() => go('explore', filters, { group_by: 'first_seen_day', split_by: 'portal', measure, chart: 'stacked' })} loading={found.loading && !found.data}>
              {found.data && <DayBars data={fillDays(found.data, seenFrom, 30, measure)} money={measure === 'value'} color="var(--c1)" onOpen={open} />}
            </Card>
            <Card title="Closing in the next 14 days" sub="Open tenders by closing date" explore={() => go('explore', filters, { group_by: 'closing_day', split_by: 'category', measure, chart: 'stacked' })} loading={closing.loading && !closing.data}>
              {closing.data && <DayBars data={fillDays(closing.data, today, 14, measure)} money={measure === 'value'} color="var(--c3)" onOpen={open} />}
            </Card>
          </div>

          <div className="grid cols-2">
            <Card title="By portal" sub={MEASURES[measure]} explore={() => go('explore', filters, { group_by: 'portal', split_by: 'category', measure })} loading={portals.loading && !portals.data}>
              {portals.data && <Series p={portals.data} measure={measure} color="var(--c2)" horizontal onOpen={open} />}
            </Card>
            <Card title="By category" sub="As labelled on the portal" explore={() => go('explore', filters, { group_by: 'category', split_by: 'portal', measure })} loading={categories.loading && !categories.data}>
              {categories.data && <Series p={categories.data} measure={measure} color="var(--c4)" horizontal onOpen={open} />}
            </Card>
          </div>

          <div className="grid cols-2">
            <Card title="By estimated value" sub="Size of the work (L = lakh, Cr = crore)" explore={() => go('explore', filters, { group_by: 'value_band', split_by: 'portal', measure })} loading={values.loading && !values.data}>
              {values.data && <Series p={values.data} measure={measure} color="var(--c5)" onOpen={open} />}
            </Card>
            <Card title="When they close" sub="Time left to bid" explore={() => go('explore', filters, { group_by: 'closes_in', split_by: 'portal', measure })} loading={window_.loading && !window_.data}>
              {window_.data && <Series p={window_.data} measure={measure} color="var(--c3)" onOpen={open} />}
            </Card>
          </div>

          <div className="grid cols-2">
            <Card title="Departments with the most tenders" sub="Top 10" explore={() => go('explore', filters, { group_by: 'department', split_by: 'value_band', measure })} loading={depts.loading && !depts.data}>
              {depts.data && <Series p={depts.data} measure={measure} color="var(--c1)" horizontal onOpen={open} />}
            </Card>
            <section className="card">
              <h2>Biggest open tenders</h2>
              <p className="sub">By estimated value. Select one to see it in the list.</p>
              {!stats.data ? <Skeleton h={180} /> : stats.data.biggest.length === 0 ? <div className="empty">No tender with a stated value.</div> : (
                <table className="tbl"><tbody>
                  {stats.data.biggest.map((t) => (
                    <tr key={t.key} className="click" tabIndex={0} onClick={() => go('tenders', { ...filters, q: t.tender_id })} onKeyDown={(e) => { if (e.key === 'Enter') go('tenders', { ...filters, q: t.tender_id }); }}>
                      <td><div className="strong">{headline(t).slice(0, 110)}{headline(t).length > 110 ? '…' : ''}</div><div className="muted small">{t.portal_name} · closes {fmtDate(t.closing)}</div></td>
                      <td className="num nowrap strong">{inr(t.value)}</td>
                    </tr>
                  ))}
                </tbody></table>
              )}
            </section>
          </div>
        </>
      )}
      <p className="muted small" style={{ marginTop: 6 }}>{k ? `Showing ${plural(k.total_kept, 'tender')} that match your filters.` : ''}</p>
    </>
  );
}

function Card({ title, sub, children, explore, loading }: { title: string; sub: string; children?: ReactNode; explore: () => void; loading?: boolean }) {
  return (
    <section className="card">
      <div className="card-head">
        <div><h2>{title}</h2><p className="sub">{sub}</p></div>
        <button className="link-btn small" onClick={explore} title="Open this in Explore to split it by another dimension">Explore →</button>
      </div>
      {loading ? <ChartSkeleton /> : children}
    </section>
  );
}

function Kpi({ label, value, note, onClick, tone, loading }: { label: string; value?: string; note?: string; onClick?: () => void; tone?: 'warn' | 'good'; loading?: boolean }) {
  const body = loading ? <><div className="label">{label}</div><Skeleton h={30} w="60%" style={{ margin: '6px 0' }} /><Skeleton h={12} w="80%" /></> : (
    <>
      <div className="label">{label}</div>
      <div className="num" style={tone === 'warn' ? { color: 'var(--warn)' } : undefined}>{value}</div>
      <div className={`note${tone === 'good' ? ' good' : ''}`}>{note}</div>
    </>
  );
  return onClick && !loading ? <button className={`card kpi clickable${tone ? ` tone-${tone}` : ''}`} onClick={onClick}>{body}</button> : <div className={`card kpi${tone ? ` tone-${tone}` : ''}`}>{body}</div>;
}

function Series({ p, measure, color, horizontal, onOpen }: { p: Pivot; measure: Measure; color: string; horizontal?: boolean; onOpen: (d: Drill | null) => void }) {
  // The folded-together "Other" bar would dwarf a top-N chart; Explore keeps it, with a note.
  const data = p.rows.filter((r) => r.key !== 'Other').map((r) => ({ name: labelFor(p.group_by, r.key, p.labels), n: metricOf(r, measure), drill: r.drill }));
  if (data.length === 0) return <div className="empty">Nothing to show.</div>;
  return <BarBlock data={data} category="name" value="n" color={color} label={MEASURES[measure]} horizontal={horizontal} money={measure === 'value'}
    interval={0} height={horizontal ? Math.max(180, data.length * 30) : 240} onSelect={(i) => onOpen(data[i]?.drill ?? null)} />;
}

function DayBars({ data, money, color, onOpen }: { data: { key: string; value: number; drill: Drill | null }[]; money: boolean; color: string; onOpen: (d: Drill | null) => void }) {
  const rows = data.map((d) => ({ name: d.key, n: d.value, drill: d.drill }));
  return <BarBlock data={rows} category="name" value="n" color={color} label={money ? 'Value' : 'Tenders'} money={money} formatCategory={fmtDay} onSelect={(i) => onOpen(rows[i]?.drill ?? null)} />;
}
