/** The product name and logo, in one place so every screen says the same thing. */
export const BRAND = 'TendAI by QMSofts';

/** A plain letter E, drawn with strokes so it looks the same on every device. */
export function Logo({ size = 16 }: { size?: number }) {
  return (
    <span className="logo" aria-hidden>
      <svg width={size} height={size} viewBox="0 0 32 32" fill="none" stroke="#fff" strokeWidth="3.4" strokeLinecap="round" strokeLinejoin="round">
        <path d="M11 7.5h11M11 7.5v17M11 16h9M11 24.5h11" />
      </svg>
    </span>
  );
}
