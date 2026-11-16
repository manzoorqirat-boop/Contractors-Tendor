// Checks every accent colour pair in styles.css, in light and dark, against WCAG: text and button labels 4.5:1, focus rings and underlines 3:1.
// Fails (exit 1) on any pair that is too faint. Run: npm run colours
const fs = require('fs');
const path = require('path');

const css = fs.readFileSync(path.join(__dirname, '..', 'src', 'styles.css'), 'utf8');
const hex = (h) => { h = h.replace('#', ''); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)); };
const lum = (c) => { const [r, g, b] = c.map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const ratio = (a, b) => { const [x, y] = [lum(hex(a)), lum(hex(b))].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
const vars = (block) => Object.fromEntries([...block.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})\b/g)].map((m) => [m[1], m[2]]));
const first = (re) => { const m = css.match(re); if (!m) throw new Error('not found in styles.css: ' + re); return vars(m[1]); };

const light = first(/^:root \{([^}]*)\}/m);
const dark = first(/^:root\[data-theme='dark'\] \{([^}]*)\}/m);
const sets = [{ name: 'blue (default)', light: { accent: light.accent, soft: light['accent-soft'], on: light['accent-text'] }, dark: { accent: dark.accent, soft: dark['accent-soft'], on: dark['accent-text'] } }];
for (const m of css.matchAll(/^:root\[data-accent='(\w+)'\] \{([^}]*)\}/gm)) {
  const l = vars(m[2]);
  const d = css.match(new RegExp(`^:root\\[data-theme='dark'\\]\\[data-accent='${m[1]}'\\] \\{([^}]*)\\}`, 'm'));
  if (!d) throw new Error(`no dark colours for ${m[1]}`);
  const dv = vars(d[1]);
  sets.push({ name: m[1], light: { accent: l.accent, soft: l['accent-soft'], on: l['accent-text'] }, dark: { accent: dv.accent, soft: dv['accent-soft'], on: dv['accent-text'] } });
}

const problems = [];
const need = (label, a, b, min) => { const r = ratio(a, b); if (r < min) problems.push(`${label}: ${a} on ${b} is ${r.toFixed(2)}:1 (needs ${min}:1)`); };
for (const [mode, base] of [['light', light], ['dark', dark]]) {
  need(`${mode} body text`, base.text, base.bg, 4.5); need(`${mode} body text on cards`, base.text, base.surface, 4.5);
  need(`${mode} muted text`, base.muted, base.surface, 4.5); need(`${mode} muted text on the page`, base.muted, base.bg, 4.5);
  for (const k of ['ok', 'warn', 'bad']) need(`${mode} ${k} text`, base[k], base.surface, 4.5);
}
for (const s of sets) {
  for (const [mode, base] of [['light', light], ['dark', dark]]) {
    const c = s[mode];
    const at = `${s.name} (${mode})`;
    need(`${at} link and tab text on cards`, c.accent, base.surface, 4.5);
    need(`${at} link and tab text on the page`, c.accent, base.bg, 4.5);
    need(`${at} button label`, c.on, c.accent, 4.5);
    need(`${at} highlighted menu item`, c.accent, c.soft, 4.5);
    need(`${at} focus ring and underline`, c.accent, base.surface, 3);
  }
}
if (problems.length) { console.error(`${problems.length} colour problem(s):\n - ` + problems.join('\n - ')); process.exit(1); }
console.log(`Colours OK: ${sets.length} accent colours in light and dark, every pair meets WCAG (text 4.5:1, focus and underline 3:1).`);
