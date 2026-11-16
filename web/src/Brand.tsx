import { useEffect, useState } from 'react';
import { api } from './api';
import { setCompanyAccent } from './theme';

/** The product name, the logo and the browser-tab icon, in one place so every screen says the same thing. */
export const BRAND = 'TendAI by QMSofts';
export const DEFAULT_LOGO = 'QM';

let current = DEFAULT_LOGO;
const listeners = new Set<(t: string) => void>();

const esc = (s: string) => s.replace(/[&<>"']/g, '');

/** The same blue tile as the page, as a tab icon. */
function faviconFor(text: string): string {
  const size = text.length === 1 ? 20 : text.length === 2 ? 15 : 12;
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='7' fill='#2563eb'/>`
    + `<text x='16' y='${16 + size * 0.36}' font-size='${size}' font-weight='800' font-family='Arial,Helvetica,sans-serif' text-anchor='middle' fill='white'>${esc(text)}</text></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

/** Changes the logo everywhere at once (all <Logo /> components and the tab icon). */
export function setLogoText(text: string | undefined) {
  current = (text || '').trim() || DEFAULT_LOGO;
  let link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (!link) { link = document.createElement('link'); link.rel = 'icon'; document.head.appendChild(link); }
  link.href = faviconFor(current);
  listeners.forEach((l) => l(current));
}

/** Reads the saved logo. It needs no login, so the sign-in screen can show it. A failure keeps the default. */
export function loadBranding() {
  api.branding().then((b) => { setLogoText(b.logo_text); setCompanyAccent(b.accent); }, () => { /* keep the default */ });
}

export function useLogoText(): string {
  const [text, setText] = useState(current);
  useEffect(() => { listeners.add(setText); setText(current); return () => { listeners.delete(setText); }; }, []);
  return text;
}

/** The letters set in Settings, white on the blue tile. */
export function Logo() {
  const text = useLogoText();
  return <span className={`logo len${Math.min(text.length, 3)}`} aria-hidden>{text}</span>;
}
