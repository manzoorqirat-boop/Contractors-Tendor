/** The accent colour: one choice that recolours tabs, links, buttons and highlights. The colours themselves are in styles.css (data-accent). */
export const ACCENTS = [
  { id: 'blue', name: 'Blue', swatch: '#2f5bea' }, { id: 'indigo', name: 'Indigo', swatch: '#4f46e5' }, { id: 'teal', name: 'Teal', swatch: '#0f766e' },
  { id: 'emerald', name: 'Emerald', swatch: '#047857' }, { id: 'violet', name: 'Violet', swatch: '#7c3aed' }, { id: 'rose', name: 'Rose', swatch: '#be123c' },
  { id: 'amber', name: 'Amber', swatch: '#a24a08' }, { id: 'slate', name: 'Slate', swatch: '#334155' },
] as const;
export type AccentId = (typeof ACCENTS)[number]['id'];
const known = (v: unknown): v is AccentId => ACCENTS.some((a) => a.id === v);

let company: AccentId = 'blue';
const listeners = new Set<() => void>();

export function userAccent(): AccentId | null {
  try { const v = localStorage.getItem('accent'); return known(v) ? v : null; } catch { return null; }
}
export const companyAccent = () => company;
export const currentAccent = (): AccentId => userAccent() ?? company;

function apply() {
  const a = currentAccent();
  if (a === 'blue') document.documentElement.removeAttribute('data-accent'); else document.documentElement.setAttribute('data-accent', a);
  listeners.forEach((l) => l());
}

/** The company's colour (Settings > AI & logo), used by everyone who has not picked their own. */
export function setCompanyAccent(v: unknown) { company = known(v) ? v : 'blue'; apply(); }

/** A person's own choice, kept in this browser. `null` goes back to the company colour. */
export function setUserAccent(v: AccentId | null) {
  try { if (v) localStorage.setItem('accent', v); else localStorage.removeItem('accent'); } catch { /* storage blocked: the choice lasts until the page closes */ }
  apply();
}

export function onAccentChange(fn: () => void) { listeners.add(fn); return () => { listeners.delete(fn); }; }
