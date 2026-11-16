import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';

export interface ConfirmOptions {
  title: string;
  /** What will happen, in plain words: what is lost, and whether it can be undone. */
  body?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** A destructive action: the button is red and the safe choice (Cancel) has the focus. */
  danger?: boolean;
}

type Ask = (o: ConfirmOptions) => Promise<boolean>;
const ConfirmCtx = createContext<Ask>(() => Promise.resolve(false));

/** `const confirm = useConfirm(); if (!(await confirm({ title: 'Remove this bid?', danger: true }))) return;` */
export const useConfirm = () => useContext(ConfirmCtx);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ opts: ConfirmOptions; done: (ok: boolean) => void } | null>(null);
  const ask = useCallback<Ask>((opts) => new Promise<boolean>((resolve) => setState({ opts, done: resolve })), []);
  const finish = (ok: boolean) => { state?.done(ok); setState(null); };
  return (
    <ConfirmCtx.Provider value={ask}>
      {children}
      {state && <Dialog opts={state.opts} onAnswer={finish} />}
    </ConfirmCtx.Provider>
  );
}

function Dialog({ opts, onAnswer }: { opts: ConfirmOptions; onAnswer: (ok: boolean) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const confirm = useRef<HTMLButtonElement>(null);
  const titleId = 'confirm-title';
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    (opts.danger ? cancel : confirm).current?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); onAnswer(false); return; }
      if (e.key !== 'Tab') return;
      const items = [cancel.current, confirm.current].filter(Boolean) as HTMLElement[];
      const i = items.indexOf(document.activeElement as HTMLElement);
      e.preventDefault();
      items[(i + (e.shiftKey ? items.length - 1 : 1)) % items.length].focus();
    };
    document.addEventListener('keydown', key);
    return () => { document.removeEventListener('keydown', key); before?.focus?.(); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="confirm-wrap" onMouseDown={(e) => { if (e.target === e.currentTarget) onAnswer(false); }}>
      <div className="confirm-box" role="alertdialog" aria-modal="true" aria-labelledby={titleId} ref={box}>
        <h2 id={titleId}>{opts.title}</h2>
        {opts.body && <div className="confirm-body">{opts.body}</div>}
        <div className="confirm-actions">
          <button type="button" className="btn" ref={cancel} onClick={() => onAnswer(false)}>{opts.cancelLabel ?? 'Cancel'}</button>
          <button type="button" className={`btn ${opts.danger ? 'danger' : 'primary'}`} ref={confirm} onClick={() => onAnswer(true)}>{opts.confirmLabel ?? 'OK'}</button>
        </div>
      </div>
    </div>
  );
}
