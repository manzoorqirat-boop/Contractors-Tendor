// Checks the translation tables: both languages cover the same English keys, every {placeholder} in the English text survives in the
// translation, and every tr('English text') used in the code has a translation. Fails (exit 1) on any gap. Run: npm run i18n
const fs = require('fs');
const path = require('path');

const src = path.join(__dirname, '..', 'src');
function table(file) {
  const text = fs.readFileSync(path.join(src, 'lang', file), 'utf8');
  const out = {};
  for (const m of text.matchAll(/^\s+("(?:[^"\\]|\\.)*"):\s+("(?:[^"\\]|\\.)*"),$/gm)) out[JSON.parse(m[1])] = JSON.parse(m[2]);
  return out;
}
const tables = { hi: table('hi.ts'), mr: table('mr.ts') };
const problems = [];

const keys = Object.keys(tables.hi);
for (const lang of Object.keys(tables)) {
  for (const k of keys) if (!(k in tables[lang])) problems.push(`${lang}: missing "${k}"`);
  for (const k of Object.keys(tables[lang])) if (!(k in tables.hi)) problems.push(`${lang}: extra key "${k}"`);
}
const holes = (s) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(',');
for (const lang of Object.keys(tables)) {
  for (const [k, v] of Object.entries(tables[lang])) {
    if (holes(k) !== holes(v)) problems.push(`${lang}: placeholders differ in "${k}"`);
    if (!v.trim()) problems.push(`${lang}: empty translation for "${k}"`);
  }
}

const used = new Set();
(function walk(dir) {
  for (const f of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, f.name);
    if (f.isDirectory()) { if (f.name !== 'lang') walk(p); continue; }
    if (!/\.(tsx?|ts)$/.test(f.name)) continue;
    const text = fs.readFileSync(p, 'utf8');
    for (const m of text.matchAll(/\btr\(\s*'((?:[^'\\]|\\.)*)'/g)) {
      const key = m[1].replace(/\\'/g, "'").replace(/\\\\/g, '\\');
      used.add(key);
      if (!(key in tables.hi)) problems.push(`${path.relative(src, p)}: tr('${key}') has no translation`);
    }
  }
})(src);
const unused = keys.filter((k) => !used.has(k));
for (const k of unused) problems.push(`unused translation (the text is not wrapped in tr() anywhere): "${k}"`);

if (problems.length) { console.error(`${problems.length} translation problem(s):\n - ` + problems.join('\n - ')); process.exit(1); }
console.log(`Translations OK: ${keys.length} texts in Hindi and Marathi, ${used.size} used in the code.`);
