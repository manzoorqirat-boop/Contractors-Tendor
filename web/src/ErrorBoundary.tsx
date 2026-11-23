import { Component, type ErrorInfo, type ReactNode } from 'react';
import { tr } from './i18n';

/** Catches a mistake in drawing a page, so one broken screen shows a message and a way out instead of leaving a blank page. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: Error | null }> {
  state: { failed: Error | null } = { failed: null };

  static getDerivedStateFromError(failed: Error) { return { failed }; }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('The page could not be drawn', error, info.componentStack);   // for whoever looks at the browser's console when reporting it
  }

  render() {
    const { failed } = this.state;
    if (!failed) return this.props.children;
    return (
      <div className="login"><main className="login-card" style={{ maxWidth: 480, margin: 'auto' }} role="alert">
        <h1>{tr('Something went wrong on this page')}</h1>
        <p className="login-sub">{tr('Your data is safe: nothing was lost or changed by this. Reload the page to carry on. If it happens again, tell whoever looks after TendAI what you were doing.')}</p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn primary" onClick={() => location.reload()}>{tr('Reload the page')}</button>
          <button className="btn" onClick={() => { location.hash = '#/dashboard'; this.setState({ failed: null }); }}>{tr('Go to the Dashboard')}</button>
        </div>
        <details style={{ marginTop: 12 }}><summary>{tr('Technical details')}</summary><pre className="mono small" style={{ whiteSpace: 'pre-wrap' }}>{failed.message}</pre></details>
      </main></div>
    );
  }
}
