/** Languages for the page's own words. English is the source: every `tr()` of the English text falls back to itself, so a string without a translation just stays English.
 *  The chosen language is kept in this browser and the page reloads when it changes, so words fixed at load time (menus, tabs) change too.
 *  Not translated: messages from the server, text written by the AI, tender titles and anything people typed. */
import { HI } from './lang/hi';
import { MR } from './lang/mr';

export type Lang = 'en' | 'hi' | 'mr';
export const LANGUAGES: { id: Lang; name: string; english: string }[] = [
  { id: 'en', name: 'English', english: 'English' },
  { id: 'hi', name: 'हिन्दी', english: 'Hindi' },
  { id: 'mr', name: 'मराठी', english: 'Marathi' },
];
const TABLES: Record<Lang, Record<string, string>> = { en: {}, hi: HI, mr: MR };

function readLang(): Lang {
  try { const l = localStorage.getItem('lang'); if (l === 'hi' || l === 'mr' || l === 'en') return l; } catch { /* storage blocked: English */ }
  return 'en';
}

export const lang: Lang = readLang();
if (typeof document !== 'undefined') document.documentElement.lang = lang;

/** The text in the chosen language, with {name} placeholders filled from `vars`. */
export function tr(text: string, vars?: Record<string, string | number | null | undefined>): string {
  const out = TABLES[lang][text] ?? text;
  return vars ? out.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k] ?? '') : m)) : out;
}

export function setLang(next: Lang) {
  try { localStorage.setItem('lang', next); } catch { /* ignore */ }
  location.reload();
}
