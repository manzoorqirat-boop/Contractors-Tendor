// The server speaks in India time without a zone ("2026-11-14T17:30:00"). Show it exactly as given: no browser conversion.
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function parts(iso: string) {
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2}))?)?/.exec(iso);
  if (!m) return null;
  return { y: +m[1], mo: +m[2], d: +m[3], h: m[4] === undefined ? null : +m[4], mi: m[5] === undefined ? null : +m[5], s: m[6] === undefined ? 0 : +m[6] };
}

export function fmtDate(iso: string | null | undefined, withTime = false): string {
  if (!iso) return '-';
  const p = parts(iso);
  if (!p) return iso;
  const date = `${p.d} ${MONTHS[p.mo - 1]} ${p.y}`;
  if (!withTime || p.h === null) return date;
  const h12 = p.h % 12 === 0 ? 12 : p.h % 12;
  return `${date}, ${h12}:${String(p.mi).padStart(2, '0')} ${p.h >= 12 ? 'PM' : 'AM'}`;
}

export function fmtDay(iso: string): string {
  const p = parts(iso);
  return p ? `${p.d} ${MONTHS[p.mo - 1]}` : iso;
}

function asUtc(iso: string): number {
  const p = parts(iso)!;
  return Date.UTC(p.y, p.mo - 1, p.d, p.h ?? 0, p.mi ?? 0, p.s);
}

/** "Closes in 3 days", "Closes today", "Closed 2 days ago", measured against the server's clock. */
export function relative(iso: string | null, now: string): { text: string; tone: 'urgent' | 'soon' | 'ok' | 'closed' | 'none' } {
  if (!iso || !parts(iso)) return { text: '', tone: 'none' };
  const hours = (asUtc(iso) - asUtc(now)) / 3_600_000;
  if (hours < 0) {
    const d = Math.floor(-hours / 24);
    return { text: d === 0 ? 'Closed today' : `Closed ${d} day${d === 1 ? '' : 's'} ago`, tone: 'closed' };
  }
  if (hours < 48) {  // the last two days are counted in hours and minutes: that is when it matters
    const mins = Math.floor((asUtc(iso) - asUtc(now)) / 60_000);  // whole minutes: no floating-point drift
    const h = Math.floor(mins / 60), m = mins % 60;
    return { text: h === 0 ? `Closes in ${m} min` : m === 0 ? `Closes in ${h} h` : `Closes in ${h} h ${m} min`, tone: hours < 24 ? 'urgent' : 'soon' };
  }
  const d = Math.floor(hours / 24);
  return { text: `Closes in ${d} day${d === 1 ? '' : 's'}`, tone: d <= 3 ? 'soon' : 'ok' };
}

/** 851184 -> "₹8.51 lakh", 20991250 -> "₹2.10 crore". Indian units, because that is how these are quoted. */
export function inr(v: number | null | undefined, compact = true): string {
  if (v === null || v === undefined) return 'Not stated';
  if (compact) {
    if (v >= 1e7) return `₹${(v / 1e7).toFixed(2)} crore`;
    if (v >= 1e5) return `₹${(v / 1e5).toFixed(2)} lakh`;
  }
  return `₹${Math.round(v).toLocaleString('en-IN')}`;
}

export function duration(start: string, end: string | null): string {
  if (!end) return 'running';
  const s = Math.max(0, Math.round((asUtc(end) - asUtc(start)) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

export const headline = (t: { description: string; title: string }) => t.description || t.title;
export const chain = (org: string) => org.split('||').filter(Boolean);

/** Axis-sized rupees: 12500000 -> "1.25 Cr". */
export function inrShort(v: number): string {
  if (v >= 1e7) return `${+(v / 1e7).toFixed(2)} Cr`;
  if (v >= 1e5) return `${+(v / 1e5).toFixed(1)} L`;
  if (v >= 1e3) return `${+(v / 1e3).toFixed(1)}k`;
  return String(Math.round(v));
}

export const plural = (n: number, one: string, many = one + 's') => `${n.toLocaleString('en-IN')} ${n === 1 ? one : many}`;
