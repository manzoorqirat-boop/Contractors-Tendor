import { useEffect, useState } from 'react';
import { api } from './api';
import { setCompanyAccent } from './theme';

/** The product name, the logo and the browser-tab icon, in one place so every screen says the same thing. */
export const BRAND = 'TendAI by QMSofts';
export const DEFAULT_LOGO = 'QM';

let current = DEFAULT_LOGO;
let currentUrl: string | null = null;
const listeners = new Set<(t: string) => void>();
const urlListeners = new Set<(u: string | null) => void>();

const esc = (s: string) => s.replace(/[&<>"']/g, '');

/** The same blue tile as the page, as a tab icon. */
function faviconFor(text: string): string {
  const size = text.length === 1 ? 20 : text.length === 2 ? 15 : 12;
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='7' fill='#2563eb'/>`
    + `<text x='16' y='${16 + size * 0.36}' font-size='${size}' font-weight='800' font-family='Arial,Helvetica,sans-serif' text-anchor='middle' fill='white'>${esc(text)}</text></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

function setIcon() {
  let link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (!link) { link = document.createElement('link'); link.rel = 'icon'; document.head.appendChild(link); }
  link.href = currentUrl ?? faviconFor(current);
}

/** Changes the logo letters everywhere at once (all <Logo /> components and, when there is no picture, the tab icon). */
export function setLogoText(text: string | undefined) {
  current = (text || '').trim() || DEFAULT_LOGO;
  setIcon();
  listeners.forEach((l) => l(current));
}

/** The company's logo picture (null = none: the letters are shown). It replaces the letter tile everywhere, and the tab icon. */
export function setLogoImage(url: string | null | undefined) {
  currentUrl = url || null;
  setIcon();
  urlListeners.forEach((l) => l(currentUrl));
}

/** Reads the saved logo. It needs no login, so the sign-in screen can show it. A failure keeps the default. */
export function loadBranding() {
  api.branding().then((b) => { setLogoText(b.logo_text); setLogoImage(b.logo_url); setCompanyAccent(b.accent); }, () => { /* keep the default */ });
}

export function useLogoText(): string {
  const [text, setText] = useState(current);
  useEffect(() => { listeners.add(setText); setText(current); return () => { listeners.delete(setText); }; }, []);
  return text;
}

export function useLogoImage(): string | null {
  const [url, setUrl] = useState(currentUrl);
  useEffect(() => { urlListeners.add(setUrl); setUrl(currentUrl); return () => { urlListeners.delete(setUrl); }; }, []);
  return url;
}

/** The company's logo picture when one is uploaded, otherwise the letters set in Settings, white on the blue tile. */
export function Logo() {
  const text = useLogoText();
  const url = useLogoImage();
  if (url) return <img className="logo-img" src={url} alt="" aria-hidden />;
  return <span className={`logo len${Math.min(text.length, 3)}`} aria-hidden>{text}</span>;
}
