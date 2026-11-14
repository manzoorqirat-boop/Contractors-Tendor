import { useEffect, useState } from 'react';
import { api } from './api';
import { chain, fmtDate, headline, inr, relative } from './format';
import { AiSections, FitBadge, RelatedNote } from './ai';
import { Drawer, Skeleton, useToast } from './ui';
import type { Tender, TenderDetail } from './types';

export function StarButton({ t, onChange }: { t: Pick<Tender, 'key' | 'starred'>; onChange: (starred: boolean) => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  async function toggle(e: React.MouseEvent) {
    e.stopPropagation();
    setBusy(true);
    const next = !t.starred;
    onChange(next); // shown at once; put back if the server says no
    try { await api.star(t.key, next); }
    catch (err) { onChange(!next); toast((err as Error).message, 'bad'); }
    finally { setBusy(false); }
  }
  return <button type="button" className={`star${t.starred ? ' on' : ''}`} onClick={toggle} disabled={busy} aria-pressed={t.starred} aria-label={t.starred ? 'Remove star' : 'Star this tender'} title={t.starred ? 'Starred' : 'Star to keep an eye on it'}>{t.starred ? '★' : '☆'}</button>;
}

export function TenderDrawer({ tenderKey, now, onClose, onStar }: { tenderKey: string; now: string; onClose: () => void; onStar: (key: string, starred: boolean) => void }) {
  const toast = useToast();
  const [t, setT] = useState<TenderDetail | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let stop = false;
    setT(null); setError('');
    api.tender(tenderKey).then((r) => { if (!stop) setT(r); }, (e: Error) => { if (!stop) setError(e.message); });
    return () => { stop = true; };
  }, [tenderKey]);

  async function copy() {
    if (!t) return;
    try { await navigator.clipboard.writeText(t.tender_id); toast('Tender ID copied'); } catch { toast('Could not copy', 'bad'); }
  }

  const rel = t ? relative(t.closing, now) : null;
  return (
    <Drawer title="Tender details" onClose={onClose}>
      {error && <div className="alert bad">{error}</div>}
      {!t && !error && <><Skeleton h={22} style={{ marginBottom: 10 }} /><Skeleton h={14} w="70%" style={{ marginBottom: 20 }} /><Skeleton h={160} /></>}
      {t && (
        <>
          <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
            <StarButton t={t} onChange={(s) => { setT({ ...t, starred: s }); onStar(t.key, s); }} />
            <h3 className="d-title">{headline(t)}</h3>
          </div>
          <p className="muted" style={{ margin: '6px 0 14px' }}>{chain(t.organisation).join(' › ')}</p>
          {rel?.text && <div className={`when-banner ${rel.tone}`}>{rel.text}</div>}
          {(t.work_type || t.fit || t.ai_civil === 0) && (
            <div className="chips" style={{ margin: '0 0 12px' }}>
              {t.work_type && <span className="badge accent">{t.work_type}</span>}
              <FitBadge fit={t.fit} notCivil={t.ai_civil} />
              {t.fit_reason && <span className="muted small">{t.fit_reason}</span>}
            </div>
          )}
          <RelatedNote t={t} />
          <dl className="facts">
            <dt>Estimated value</dt><dd><strong>{t.value === null ? 'Not stated' : inr(t.value, false)}</strong>{t.value !== null && t.value >= 1e5 && <span className="muted"> ({inr(t.value)})</span>}</dd>
            <dt>EMD</dt><dd>{t.emd ? inr(t.emd, false) : '-'}</dd>
            <dt>Closes</dt><dd>{fmtDate(t.closing, true)}</dd>
            <dt>Bid opening</dt><dd>{fmtDate(t.opening, true)}</dd>
            <dt>Published</dt><dd>{fmtDate(t.published, true)}</dd>
            <dt>First found</dt><dd>{fmtDate(t.first_seen, true)}</dd>
            <dt>Portal</dt><dd>{t.portal_name}</dd>
            <dt>Category</dt><dd>{t.category || '-'}</dd>
            <dt>Location</dt><dd>{t.location || '-'}</dd>
            <dt>Tender ID</dt><dd className="mono">{t.tender_id}</dd>
          </dl>
          <div className="d-actions">
            <button className="btn" onClick={copy}>Copy Tender ID</button>
            <a className="btn primary" href={t.search_url} target="_blank" rel="noopener noreferrer">Find on portal</a>
          </div>
          <AiSections t={t} onChange={(patch) => setT((cur) => (cur ? { ...cur, ...patch } : cur))} />
          <p className="muted small-note">Portal links expire within minutes, so paste the Tender ID into the portal's search. Confirm every detail on the portal before bidding.</p>
        </>
      )}
    </Drawer>
  );
}
