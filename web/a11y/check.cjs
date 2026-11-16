// Accessibility check: opens every main screen and dialog in light and dark, on desktop and phone, and runs axe-core (WCAG 2.2 AA plus best practice).
// Fails (exit 1) on any violation. Usage: A11Y_URL=http://localhost:8000/ A11Y_USER=admin A11Y_PASS=... node a11y/check.cjs
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const URL_ = process.env.A11Y_URL || 'http://localhost:8000/';
const USER = process.env.A11Y_USER || 'admin';
const PASS = process.env.A11Y_PASS || '';
const AXE = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');
const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'];
const problems = [];
let scans = 0;

async function scan(page, name) {
  await page.evaluate(AXE); // evaluate (not a script tag): the page's own security policy forbids injected scripts
  const found = await page.evaluate(async (tags) => (await axe.run(document, { runOnly: { type: 'tag', values: tags } })).violations
    .map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.slice(0, 3).map((n) => n.target.join(' ')) })), TAGS);
  scans += 1;
  for (const v of found) problems.push(`${name}: [${v.impact}] ${v.id}: ${v.help}\n      ${v.nodes.join('\n      ')}`);
}

(async () => {
  if (!PASS) { console.error('Set A11Y_PASS to the sign-in password.'); process.exit(2); }
  const browser = await chromium.launch();
  const modes = [['desktop light', { width: 1440, height: 900 }, 'light', false], ['desktop dark', { width: 1440, height: 900 }, 'dark', false], ['phone light', { width: 390, height: 844 }, 'light', true]];
  for (const [mode, viewport, scheme, mobile] of modes) {
    const ctx = await browser.newContext({ viewport, colorScheme: scheme, reducedMotion: 'reduce', isMobile: mobile, hasTouch: mobile });
    const page = await ctx.newPage();
    await page.goto(URL_);
    await page.waitForSelector('input[autocomplete=username]');
    await scan(page, `${mode} sign-in`);
    await page.fill('input[autocomplete=username]', USER);
    await page.fill('input[autocomplete=current-password]', PASS);
    await page.click('button.login-submit');
    await page.waitForSelector('nav.nav');
    const visit = async (name, hash, act) => {
      try {
        await page.goto('about:blank'); await page.goto(URL_ + hash); await page.waitForSelector('main#main', { timeout: 15000 }); await page.waitForTimeout(1800);
        if (act) { await act(); await page.waitForTimeout(300); }
        await scan(page, `${mode} ${name}`);
      } catch (e) { problems.push(`${mode} ${name}: could not be reached for the check (${String(e.message).split('\n')[0].slice(0, 100)})`); }
    };
    await visit('dashboard', '#/dashboard');
    await visit('explore', '#/explore');
    await visit('tenders', '#/tenders');
    await visit('tenders table', '#/tenders', () => page.click('button:has-text("Table")'));
    await visit('tender details', '#/tenders', () => page.locator('.tender .work').first().click());
    await visit('bids', '#/bids');
    await visit('bid', '#/bids/1');
    for (const t of ['Checklist', 'Price', 'Letters']) await visit(`bid ${t}`, '#/bids/1', () => page.click(`[role=tab]:has-text("${t}")`));
    await visit('vault', '#/vault');
    await visit('runs', '#/runs');
    for (const t of ['Daily email', 'Portals', 'Civil filter', 'AI & logo', 'People', 'Activity', 'Backup']) await visit(`settings ${t}`, '#/settings', () => page.click(`[role=tab]:has-text("${t}")`));
    await visit('account', '#/dashboard', async () => { if (mobile) await page.click('.more-toggle'); await page.click('text=Account & security'); });
    await visit('shortcuts', '#/dashboard', () => page.keyboard.press('?'));
    await visit('add a tender', '#/tenders', async () => { await page.click('text=Add a tender'); await page.click('button:has-text("Continue"), button:has-text("Type the details myself")'); });
    await visit('confirm dialog', '#/bids/1', () => page.click('text=Remove this bid'));
    await ctx.close();
  }
  await browser.close();
  if (problems.length) {
    console.error(`\n${problems.length} accessibility problem(s) in ${scans} screens:\n`);
    for (const p of problems) console.error(' - ' + p);
    process.exit(1);
  }
  console.log(`Accessibility check passed: ${scans} screens, no violations.`);
})();
