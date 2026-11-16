// Fit check: on short windows (a laptop with a bookmarks bar, a phone held sideways) every menu, popover, drawer and dialog must fit inside the window
// or scroll inside itself, and "Log out" must be reachable. Fails (exit 1) on any that does not.
// Usage: A11Y_URL=http://localhost:8000/ A11Y_PASS=... node a11y/fit.cjs
const { chromium } = require('playwright');

const URL_ = process.env.A11Y_URL || 'http://localhost:8000/';
const USER = process.env.A11Y_USER || 'admin';
const PASS = process.env.A11Y_PASS || '';
const problems = [];
let checks = 0;

// the last visible element matching a selector: does it fit the window, or can what overflows be scrolled to?
const measure = (s) => {
  const els = [...document.querySelectorAll(s)].filter((e) => e.offsetParent !== null || getComputedStyle(e).position === 'fixed');
  const e = els[els.length - 1];
  if (!e) return { found: false };
  const r = e.getBoundingClientRect();
  const scrolls = (x) => /(auto|scroll)/.test(getComputedStyle(x).overflowY) && x.scrollHeight > x.clientHeight + 1;
  return {
    found: true, scrolls: [e, ...e.querySelectorAll('*')].some(scrolls),
    fits: r.top >= -1 && r.bottom <= innerHeight + 1 && r.left >= -1 && r.right <= innerWidth + 1,
    box: [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)], window: [innerWidth, innerHeight],
  };
};

// can the "Log out" button be seen, or reached by scrolling something that contains it?
const logoutReachable = () => {
  const b = [...document.querySelectorAll('.sidebar button, .sidebar a')].find((e) => /log ?out|लॉग/i.test(e.textContent));
  if (!b) return { found: false };
  const r = b.getBoundingClientRect();
  if (r.top >= 0 && r.bottom <= innerHeight) return { found: true, ok: true };
  for (let e = b.parentElement; e && e !== document.body; e = e.parentElement) {
    if (/(auto|scroll)/.test(getComputedStyle(e).overflowY) && e.scrollHeight > e.clientHeight + 1) return { found: true, ok: true };
  }
  return { found: true, ok: false, top: Math.round(r.top), bottom: Math.round(r.bottom), height: innerHeight };
};

(async () => {
  if (!PASS) { console.error('Set A11Y_PASS to the sign-in password.'); process.exit(2); }
  const browser = await chromium.launch();
  for (const [name, w, h, mobile] of [['laptop 1366x640', 1366, 640, false], ['small 1000x480', 1000, 480, false], ['phone 390x640', 390, 640, true], ['landscape 740x360', 740, 360, true]]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, isMobile: mobile, hasTouch: mobile });
    const page = await ctx.newPage();
    await page.goto(URL_);
    await page.fill('input[autocomplete=username]', USER);
    await page.fill('input[autocomplete=current-password]', PASS);
    await page.click('button.login-submit');
    await page.waitForSelector('nav.nav');
    const go = async (hash) => { await page.goto('about:blank'); await page.goto(URL_ + hash); await page.waitForSelector('main#main'); await page.waitForTimeout(1000); };
    const fit = async (label, selector) => {
      checks += 1;
      const r = await page.evaluate(measure, selector);
      if (!r.found) problems.push(`${name}: ${label}: not found`);
      else if (!r.fits && !r.scrolls) problems.push(`${name}: ${label} does not fit the window and does not scroll (${r.box.join(',')} in ${r.window.join('x')})`);
    };
    try {
      await go('#/runs');
      if (mobile) { await page.click('.more-toggle'); await page.waitForTimeout(250); }
      else await page.click('.side-appearance summary');   // the fullest the menu gets
      checks += 1;
      const lo = await page.evaluate(logoutReachable);
      if (!lo.found || !lo.ok) problems.push(`${name}: Log out cannot be reached ${JSON.stringify(lo)}`);
      if (mobile) await fit('the account menu', '.side-more');
      await go('#/tenders');
      const pops = [];
      for (const handle of await page.$$('.fb-row .pop-btn')) if (await handle.isVisible()) pops.push(handle);
      for (let i = 0; i < Math.min(pops.length, 6); i++) { await pops[i].click(); await page.waitForTimeout(150); await fit(`filter popover ${i + 1}`, '.pop-panel'); await page.keyboard.press('Escape'); await page.waitForTimeout(80); }
      await page.keyboard.press('?'); await page.waitForTimeout(250); await fit('the shortcuts dialog', '[role=dialog]'); await page.keyboard.press('Escape');
      await page.click('.head-actions .btn >> nth=0'); await page.waitForTimeout(350); await fit('the add-a-tender panel', '.drawer'); await page.keyboard.press('Escape');
      await page.locator('.tender .work').first().click(); await page.waitForTimeout(500); await fit('the tender details panel', '.drawer'); await page.keyboard.press('Escape');
      await go('#/bids/1');
      await page.click('.link-btn[style*="--bad"]'); await page.waitForTimeout(250); await fit('the confirm dialog', '.confirm-box'); await page.keyboard.press('Escape');
    } catch (e) {
      problems.push(`${name}: could not be checked (${String(e.message).split('\n')[0].slice(0, 120)})`);
    }
    await ctx.close();
  }
  await browser.close();
  if (problems.length) { console.error(`\n${problems.length} fit problem(s) in ${checks} checks:\n - ` + problems.join('\n - ')); process.exit(1); }
  console.log(`Fit check passed: ${checks} checks on four window sizes.`);
})();
