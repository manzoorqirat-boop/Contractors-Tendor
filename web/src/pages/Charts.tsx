import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

const tick = { fill: 'var(--muted)', fontSize: 12 };
const tooltip = {
  contentStyle: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text)', fontSize: 13 },
  cursor: { fill: 'var(--surface-2)' },
  labelStyle: { color: 'var(--text)', fontWeight: 600 },
};

interface Props<T extends object> {
  data: T[];
  category: keyof T & string;
  value: keyof T & string;
  color: string;
  label: string;
  horizontal?: boolean;
  formatCategory?: (v: string) => string;
  height?: number;
  /** 0 shows every label (few categories); the default thins out crowded date axes. */
  interval?: number | 'preserveStartEnd';
}

/** One-series bar chart in the page's colours. `label` names the value in the tooltip. */
export function BarBlock<T extends object>({ data, category, value, color, label, horizontal, formatCategory, height = 240, interval = 'preserveStartEnd' }: Props<T>) {
  const fmt = formatCategory ?? ((v: string) => v);
  const catKey: string = category;
  const valKey: string = value;
  const rows = data as Record<string, unknown>[]; // recharts types its keys loosely; ours are checked by Props above
  return (
    <div className="chart" style={{ height }} role="img" aria-label={`${label} by ${category}`}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} layout={horizontal ? 'vertical' : 'horizontal'} margin={{ top: 4, right: 12, bottom: 0, left: horizontal ? 4 : -12 }}>
          <CartesianGrid stroke="var(--grid)" vertical={horizontal ? true : false} horizontal={horizontal ? false : true} />
          {horizontal ? (
            <>
              <XAxis type="number" tick={tick} tickLine={false} axisLine={false} allowDecimals={false} />
              <YAxis type="category" dataKey={catKey} tick={tick} tickLine={false} axisLine={false} width={130} tickFormatter={(v: string) => (v.length > 20 ? v.slice(0, 19) + '…' : v)} />
            </>
          ) : (
            <>
              <XAxis dataKey={catKey} tick={tick} tickLine={false} axisLine={{ stroke: 'var(--grid)' }} interval={interval} tickFormatter={fmt} />
              <YAxis tick={tick} tickLine={false} axisLine={false} allowDecimals={false} />
            </>
          )}
          <Tooltip {...tooltip} formatter={(v) => [v as number, label]} labelFormatter={(l) => fmt(String(l))} />
          <Bar dataKey={valKey} fill={color} radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]} maxBarSize={32} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
