import { Suspense, lazy, type ReactElement } from 'react';
import { ChartSkeleton } from '../ui';
import type { SimpleProps } from './Charts';

// The charting library is the largest part of the page (about 400 KB). These load it after the screen is up, with a placeholder of the
// same height, so the numbers and lists appear first and nothing jumps when the chart arrives.
type Rows = SimpleProps<Record<string, unknown>>;
const Bar = lazy(() => import('./Charts').then((m) => ({ default: m.BarBlock as (props: Rows) => ReactElement })));

export function BarBlock<T extends object>(props: SimpleProps<T>) {
  // lazy() cannot carry the type parameter through, so the props are handed over as plain rows; the callers above are still fully checked
  return <Suspense fallback={<ChartSkeleton height={props.height ?? 240} />}><Bar {...(props as unknown as Rows)} /></Suspense>;
}
