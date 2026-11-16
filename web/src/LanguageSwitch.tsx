import { LANGUAGES, lang, setLang, tr, type Lang } from './i18n';

/** Chooses the language of the page's own words (English, Hindi, Marathi). The page reloads to apply it. */
export function LanguageSwitch() {
  return (
    <label className="lang-switch">
      <span className="sr-only">{tr('Language')}</span>
      <select value={lang} onChange={(e) => setLang(e.target.value as Lang)} aria-label={tr('Language')} lang={lang}>
        {LANGUAGES.map((l) => <option key={l.id} value={l.id} lang={l.id}>{l.name}</option>)}
      </select>
    </label>
  );
}
