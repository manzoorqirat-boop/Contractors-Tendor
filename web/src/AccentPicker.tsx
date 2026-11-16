import { useSyncExternalStore } from 'react';
import { ACCENTS, companyAccent, currentAccent, onAccentChange, setUserAccent, userAccent, type AccentId } from './theme';
import { tr } from './i18n';

/** Eight colours to choose from. A person's choice is kept in this browser; "Company colour" goes back to the one set in Settings. */
export function AccentPicker() {
  const now = useSyncExternalStore(onAccentChange, currentAccent);
  const own = userAccent();
  return (
    <div className="accent-picker" role="radiogroup" aria-label={tr('Colour')}>
      <span className="muted small">{tr('Colour')}</span>
      <span className="accent-row">
        {ACCENTS.map((a) => (
          <button key={a.id} type="button" role="radio" aria-checked={now === a.id} aria-label={a.name} title={a.name} className={`accent-dot${now === a.id ? ' on' : ''}`}
            style={{ background: a.swatch }} onClick={() => setUserAccent(a.id as AccentId)} />
        ))}
      </span>
      {own && own !== companyAccent() && <button type="button" className="link-btn small" onClick={() => setUserAccent(null)}>{tr('Company colour')}</button>}
    </div>
  );
}
