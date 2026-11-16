import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';

/** A box that scrolls sideways when its content (a wide table) does not fit. When it does scroll, it can be focused and moved with the arrow keys. */
export function ScrollX({ children, label, style }: { children: ReactNode; label: string; style?: CSSProperties }) {
  const ref = useRef<HTMLDivElement>(null);
  const [scrolls, setScrolls] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const check = () => setScrolls(el.scrollWidth > el.clientWidth + 1);
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => ro.disconnect();
  }, []);
  return <div ref={ref} className="scroll-x" style={style} {...(scrolls ? { tabIndex: 0, role: 'region', 'aria-label': label } : {})}>{children}</div>;
}
