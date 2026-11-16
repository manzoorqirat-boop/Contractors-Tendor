import { useState, type ReactNode } from 'react';
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';
import { api } from '../api';
import { FilterBar } from '../FilterBar';
import { mergeDrill } from '../filters';
import { chain, fmtDate, fmtDay, headline, inr, plural, relative } from '../format';
import { useLoad } from '../hooks';
import type { Nav } from '../App';
import { ChartSkeleton, Skeleton, useToast } from '../ui';
import { useConfirm } from '../confirm';
import { ManualBanner } from '../ManualPortals';
import { Only, useSession } from '../session';
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

const FIRST = ['soonest', 'closing', 'biggest', 'found'];  // widget ids shown first, in this order; the rest keep their order after them

const rankOf = (id: string) => { const i = FIRST.indexOf(id); return i < 0 ? FIRST.length : i; };

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

  const widgets: DashWidget[] = [
    { id: 'found', title: 'Found per day', node: (
      <Card title="Found per day" sub="Civil tenders first seen, last 30 days" explore={() => go('explore', filters, { group_by: 'first_seen_day', split_by: 'portal', measure, chart: 'stacked' })} loading={found.loading && !found.data}>
              {found.data && <DayBars data={fillDays(found.data, seenFrom, 30, measure)} money={measure === 'value'} color="var(--c1)" onOpen={open} />}
            </Card>
    ) },
    { id: 'closing', title: 'Closing in the next 14 days', node: (
      <Card title="Closing in the next 14 days" sub="Open tenders by closing date" explore={() => go('explore', filters, { group_by: 'closing_day', split_by: 'category', measure, chart: 'stacked' })} loading={closing.loading && !closing.data}>
              {closing.data && <DayBars data={fillDays(closing.data, today, 14, measure)} money={measure === 'value'} color="var(--c3)" onOpen={open} />}
            </Card>
    ) },
    { id: 'soonest', title: 'Closing soonest', node: (
      <ClosingList filters={filters} now={config.now} go={go} />
    ) },
    { id: 'biggest', title: 'Biggest open tenders', node: (
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
    ) },
    { id: 'portal', title: 'By portal', node: (
      <Card title="By portal" sub={MEASURES[measure]} explore={() => go('explore', filters, { group_by: 'portal', split_by: 'category', measure })} loading={portals.loading && !portals.data}>
              {portals.data && <Donut p={portals.data} measure={measure} onOpen={open} />}
            </Card>
    ) },
    { id: 'category', title: 'By category', node: (
      <Card title="By category" sub="As labelled on the portal" explore={() => go('explore', filters, { group_by: 'category', split_by: 'portal', measure })} loading={categories.loading && !categories.data}>
              {categories.data && <Series p={categories.data} measure={measure} color="var(--c4)" horizontal onOpen={open} />}
            </Card>
    ) },
    { id: 'values', title: 'By estimated value', node: (
      <Card title="By estimated value" sub="Size of the work (L = lakh, Cr = crore)" explore={() => go('explore', filters, { group_by: 'value_band', split_by: 'portal', measure })} loading={values.loading && !values.data}>
              {values.data && <Series p={values.data} measure={measure} color="var(--c5)" onOpen={open} />}
            </Card>
    ) },
    { id: 'window', title: 'When they close', node: (
      <Card title="When they close" sub="Time left to bid" explore={() => go('explore', filters, { group_by: 'closes_in', split_by: 'portal', measure })} loading={window_.loading && !window_.data}>
              {window_.data && <Series p={window_.data} measure={measure} color="var(--c3)" onOpen={open} />}
            </Card>
    ) },
    { id: 'depts', title: 'Departments with the most tenders', node: (
      <Card title="Departments with the most tenders" sub="Top 10" explore={() => go('explore', filters, { group_by: 'department', split_by: 'value_band', measure })} loading={depts.loading && !depts.data}>
              {depts.data && <Series p={depts.data} measure={measure} color="var(--c1)" horizontal onOpen={open} />}
            </Card>
    ) },
  ];

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
          <Only role="bidder"><button className="btn primary" onClick={() => go('runs')}>Run now</button></Only>
        </div>
      </div>

      <SetupCard onChanged={() => stats.reload()} />

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

          {/* What needs doing first: tenders about to close, then the biggest ones; the charts follow. A layout a person arranged themselves is kept. */}
          <DashGrid widgets={[...widgets].sort((a, b) => rankOf(a.id) - rankOf(b.id) || widgets.indexOf(a) - widgets.indexOf(b))} wide={['depts']} />
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

/** "₹70.50 crore" is shown as a big amount with a smaller unit, so a long figure fits its card instead of being cut off. */
function withUnit(value?: string) {
  const m = value?.match(/^(.+?)\s+(crore|lakh)$/);
  return m ? <>{m[1]}<span className="unit">{m[2]}</span></> : value;
}

/** What a new installation still needs, worked out by the server from what is stored. Disappears when everything is done, or when hidden. Administrators can also try the app with sample tenders. */
function SetupCard({ onChanged }: { onChanged: () => void }) {
  const { can } = useSession();
  const toast = useToast();
  const confirm = useConfirm();
  const [hidden, setHidden] = useState(() => { try { return localStorage.getItem('setup-dismissed') === '1'; } catch { return false; } });
  const [busy, setBusy] = useState(false);
  const setup = useLoad(() => (can('admin') ? api.setup() : Promise.resolve(null)), []);
  const d = setup.data;
  if (hidden || !can('admin') || !d) return null;
  const todo = d.steps.filter((s) => !s.done && !s.optional);
  if (todo.length === 0 && !d.sample.loaded) return null;
  async function sample(load: boolean) {
    if (!load && !(await confirm({ title: 'Remove the sample data?', body: 'The sample tenders and any bids you started on them (with their checklists and comments) are deleted. Your real tenders and bids are not touched.', confirmLabel: 'Remove sample data', danger: true }))) return;
    setBusy(true);
    try {
      if (load) { const r = await api.loadSample(); toast(`${r.added} sample tenders added. They are marked "Sample data" and never emailed.`); }
      else { const r = await api.removeSample(); toast(`Removed ${r.tenders} sample tenders and ${r.bids} bids.`); }
      setup.reload(); onChanged(); window.dispatchEvent(new CustomEvent('tenders-changed'));
    } catch (e) { toast((e as Error).message, 'bad'); } finally { setBusy(false); }
  }
  return (
    <section className="card setup-card" aria-label="Finish setting up">
      <div className="setup-head"><h2>Finish setting up <span className="badge warn">{todo.length > 0 ? `${d.done} of ${d.total} done` : 'Sample data is on'}</span></h2>
        <button type="button" className="link-btn" onClick={() => { try { localStorage.setItem('setup-dismissed', '1'); } catch { /* ignore */ } setHidden(true); }}>Hide</button></div>
      {todo.length > 0 && (
        <ul className="setup-list">
          {d.steps.map((s) => (
            <li key={s.key} className={s.done ? 'done' : ''}>
              <span className="setup-mark" aria-hidden>{s.done ? '✓' : '○'}</span>
              <div><strong>{s.title}</strong><span className="sr-only">{s.done ? ' (done)' : ' (to do)'}</span>{!s.done && <div className="muted small">{s.why}</div>}</div>
              {!s.done && <a className="btn small" href={s.href}>{s.where}</a>}
            </li>
          ))}
        </ul>
      )}
      <div style={{ marginTop: 10, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        {d.sample.loaded
          ? <><span className="muted small">{d.sample.tenders} sample tenders are in the list, marked "Sample data".</span><button type="button" className="btn small" onClick={() => sample(false)} disabled={busy}>Remove sample data</button></>
          : <><button type="button" className="btn small" onClick={() => sample(true)} disabled={busy}>Try it with sample tenders</button><span className="muted small">Adds a few fake tenders so every page has something to show. Never emailed. One button removes them.</span></>}
      </div>
    </section>
  );
}

function Kpi({ label, value, note, onClick, tone, loading }: { label: string; value?: string; note?: string; onClick?: () => void; tone?: 'warn' | 'good'; loading?: boolean }) {
  const body = loading ? <><div className="label">{label}</div><Skeleton h={30} w="60%" style={{ margin: '6px 0' }} /><Skeleton h={12} w="80%" /></> : (
    <>
      <div className="label">{label}</div>
      <div className="num" style={tone === 'warn' ? { color: 'var(--warn)' } : undefined}>{withUnit(value)}</div>
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

// ---- configurable grid: drag to reorder, resize, hide; saved in this browser ---------------------------------------------
interface DashWidget { id: string; title: string; node: ReactNode }
interface Layout { order: string[]; hidden: string[]; wide: string[] }
const LAYOUT_KEY = 'dash-layout-v1';

function loadLayout(ids: string[], wide: string[]): Layout {
  try {
    const r = JSON.parse(localStorage.getItem(LAYOUT_KEY) || 'null');
    if (r && Array.isArray(r.order)) {
      const keep = (xs: unknown) => (Array.isArray(xs) ? xs.filter((i): i is string => typeof i === 'string' && ids.includes(i)) : []);
      const order = keep(r.order);
      return { order: [...order, ...ids.filter((i) => !order.includes(i))], hidden: keep(r.hidden), wide: keep(r.wide) };
    }
  } catch { /* storage blocked or damaged: use the default */ }
  return { order: ids, hidden: [], wide };
}

function DashGrid({ widgets, wide }: { widgets: DashWidget[]; wide: string[] }) {
  const ids = widgets.map((w) => w.id);
  const [layout, setLayout] = useState<Layout>(() => loadLayout(ids, wide));
  const [edit, setEdit] = useState(false);
  const [drag, setDrag] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const byId = new Map(widgets.map((w) => [w.id, w]));
  const save = (l: Layout) => { setLayout(l); try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(l)); } catch { /* ignore */ } };
  const visible = layout.order.filter((i) => !layout.hidden.includes(i));
  const move = (from: string, to: string) => {
    if (from === to) return;
    const rest = layout.order.filter((i) => i !== from);
    rest.splice(rest.indexOf(to), 0, from);
    save({ ...layout, order: rest });
  };
  const nudge = (id: string, d: -1 | 1) => { const j = visible.indexOf(id) + d; if (j >= 0 && j < visible.length) move(id, visible[j]); };
  const toggle = (key: 'hidden' | 'wide', id: string) => save({ ...layout, [key]: layout[key].includes(id) ? layout[key].filter((i) => i !== id) : [...layout[key], id] });

  return (
    <>
      <div className="dash-bar">
        {edit ? <span className="muted">Drag a card by its handle, or use the arrows. Changes save automatically.</span> : <span />}
        <span className="head-actions">
          {edit && <button className="btn small" onClick={() => save({ order: ids, hidden: [], wide })}>Reset layout</button>}
          <button className={`btn small${edit ? ' primary' : ''}`} onClick={() => setEdit(!edit)} aria-pressed={edit}>{edit ? 'Done' : 'Customize'}</button>
        </span>
      </div>
      <div className={`dash-grid${edit ? ' editing' : ''}`}>
        {visible.map((id) => {
          const w = byId.get(id);
          if (!w) return null;
          return (
            <div key={id} className={`widget${layout.wide.includes(id) ? ' w-full' : ''}${drag === id ? ' dragging' : ''}${over === id && drag !== id ? ' drop-here' : ''}`}
              draggable={edit}
              onDragStart={(e) => { setDrag(id); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', id); }}
              onDragOver={(e) => { if (edit && drag) { e.preventDefault(); setOver(id); } }}
              onDrop={(e) => { e.preventDefault(); if (drag) move(drag, id); setDrag(null); setOver(null); }}
              onDragEnd={() => { setDrag(null); setOver(null); }}>
              {edit && (
                <div className="w-bar">
                  <span className="w-grip" aria-hidden>⠿</span><strong>{w.title}</strong>
                  <span className="w-btns">
                    <button className="btn small" onClick={() => nudge(id, -1)} aria-label={`Move ${w.title} earlier`}>↑</button>
                    <button className="btn small" onClick={() => nudge(id, 1)} aria-label={`Move ${w.title} later`}>↓</button>
                    <button className="btn small" onClick={() => toggle('wide', id)}>{layout.wide.includes(id) ? 'Half width' : 'Full width'}</button>
                    <button className="btn small" onClick={() => toggle('hidden', id)} aria-label={`Hide ${w.title}`}>Hide</button>
                  </span>
                </div>
              )}
              {w.node}
            </div>
          );
        })}
      </div>
      {edit && layout.hidden.length > 0 && (
        <div className="w-tray"><span className="muted">Hidden:</span>{layout.hidden.map((id) => <button key={id} className="btn small" onClick={() => toggle('hidden', id)}>+ {byId.get(id)?.title}</button>)}</div>
      )}
    </>
  );
}

function ClosingList({ filters, now, go }: { filters: Filters; now: string; go: Nav['go'] }) {
  const f: Filters = { ...filters, status: 'open' };
  const { data } = useLoad(() => api.tenders(f, 'closing', 1, 6), [JSON.stringify(f)]);
  return (
    <section className="card">
      <div className="card-head"><div><h2>Closing soonest</h2><p className="sub">Open tenders, nearest deadline first</p></div><button className="link-btn small" onClick={() => go('tenders', f)}>See all →</button></div>
      {!data ? <Skeleton h={180} /> : data.items.length === 0 ? <div className="empty">Nothing open right now.</div> : (
        <ul className="soon-list">
          {data.items.map((t) => { const rel = relative(t.closing, now); return (
            <li key={t.key}><button type="button" onClick={() => go('tenders', { ...filters, q: t.tender_id })}>
              <span className="soon-main"><span className="strong">{headline(t).slice(0, 90)}{headline(t).length > 90 ? '…' : ''}</span><span className="muted small">{chain(t.organisation)[0]} · {t.value_text}</span></span>
              <span className={`when pill ${rel.tone}`}>{rel.text || fmtDate(t.closing)}</span>
            </button></li>
          ); })}
        </ul>
      )}
    </section>
  );
}

function Donut({ p, measure, onOpen }: { p: Pivot; measure: Measure; onOpen: (d: Drill | null) => void }) {
  const data = p.rows.filter((r) => r.key !== 'Other').map((r, i) => ({ name: labelFor(p.group_by, r.key, p.labels), n: metricOf(r, measure), drill: r.drill, fill: `var(--c${(i % 8) + 1})` }));
  const total = data.reduce((a, d) => a + d.n, 0);
  const fmt = (n: number) => (measure === 'value' ? inr(n) : n.toLocaleString('en-IN'));
  if (data.length === 0 || total === 0) return <div className="empty">Nothing to show.</div>;
  return (
    <div className="donut">
      <div className="donut-chart">
        <ResponsiveContainer width="100%" height={190}>
          <PieChart>
            <Pie data={data} dataKey="n" nameKey="name" innerRadius="64%" outerRadius="92%" paddingAngle={2} stroke="none" onClick={(_: unknown, i: number) => onOpen(data[i]?.drill ?? null)}>
              {data.map((d) => <Cell key={d.name} fill={d.fill} style={{ cursor: 'pointer' }} />)}
            </Pie>
            <Tooltip formatter={(v) => fmt(Number(v))} />
          </PieChart>
        </ResponsiveContainer>
        <div className="donut-total"><strong>{fmt(total)}</strong><span>{measure === 'value' ? 'total value' : 'tenders'}</span></div>
      </div>
      <ul className="legend">
        {data.map((d) => <li key={d.name}><button type="button" onClick={() => onOpen(d.drill)}><i style={{ background: d.fill }} /><span className="legend-name">{d.name}</span><span className="muted">{Math.round((100 * d.n) / total)}%</span></button></li>)}
      </ul>
    </div>
  );
}
