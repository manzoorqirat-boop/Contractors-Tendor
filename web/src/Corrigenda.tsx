import { useState } from 'react';
import { api } from './api';
import { useToast } from './ui';
import type { BidDetail } from './types';

/** Writes down a corrigendum read on the portal, with the closing date it set. The only way to log one on a tender the app cannot watch. */
export function CorrigendumForm({ bidId, manual, onRecorded }: { bidId: number; manual: boolean; onRecorded: (bid: BidDetail) => void }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState('');
  const [closing, setClosing] = useState('');
  const [busy, setBusy] = useState(false);
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      onRecorded(await api.recordCorrigendum(bidId, note, closing));
      toast(closing ? 'Corrigendum recorded and the closing date moved' : 'Corrigendum recorded');
      setNote(''); setClosing(''); setOpen(false);
    } catch (err) { toast((err as Error).message, 'bad'); } finally { setBusy(false); }
  }
  if (!open) return <p style={{ margin: '8px 0 0' }}><button type="button" className="link-btn" onClick={() => setOpen(true)}>Record a corrigendum{manual ? ' you read on the portal' : ''}</button></p>;
  return (
    <form className="stack-fields" onSubmit={save} style={{ marginTop: 10, display: 'grid', gap: 8, maxWidth: 560 }}>
      <label className="field" style={{ margin: 0 }}>What did it change?
        <textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} rows={3} placeholder="e.g. Corrigendum 2: closing extended; BOQ item 12 revised" autoFocus />
      </label>
      <label className="field" style={{ margin: 0 }}>New closing date, if it moved (optional)
        <input type="datetime-local" value={closing} onChange={(e) => setClosing(e.target.value)} />
      </label>
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn primary" disabled={busy || !note.trim()}>{busy ? 'Saving…' : 'Record'}</button>
        <button type="button" className="btn" onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </form>
  );
}
