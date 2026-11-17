import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';
import type { Drill } from '../types';

export interface Slice { name: string; n: number; drill: Drill | null; fill: string }

/** The ring itself. It lives apart from the Dashboard because it is the part that needs the charting library, which loads after the page is up. */
export default function DonutChart({ data, format, onOpen }: { data: Slice[]; format: (n: number) => string; onOpen: (d: Drill | null) => void }) {
  return (
    <ResponsiveContainer width="100%" height={190}>
      <PieChart>
        <Pie data={data} dataKey="n" nameKey="name" innerRadius="64%" outerRadius="92%" paddingAngle={2} stroke="none" onClick={(_: unknown, i: number) => onOpen(data[i]?.drill ?? null)}>
          {data.map((d) => <Cell key={d.name} fill={d.fill} style={{ cursor: 'pointer' }} />)}
        </Pie>
        <Tooltip formatter={(v) => format(Number(v))} />
      </PieChart>
    </ResponsiveContainer>
  );
}
