/* Records a captioned screen demo of TendAI in a real browser and writes an MP4 plus an SRT file of the captions.

   Use a THROWAWAY database with the demo data (python testdata/seed_demo_data.py --db demo.db), then:

     APP_PASSWORD=demo-pass-123 COOKIE_SECURE=false STATE_PATH=demo.db PORT=8767 python -m tender_digest.web &
     cd web && DEMO_URL=http://localhost:8767/ DEMO_PASS=demo-pass-123 DEMO_OUT=./demo-out node demo/record.cjs

   Optional: DEMO_USER (default admin), DEMO_CONTACT (a line of contact details for the closing card), PW_CHROMIUM (path to a Chromium binary).
   Needs ffmpeg on the PATH. The recording is real time, so a run takes as long as the video. Edit SCENES below to change what is shown. */
const { chromium } = require('playwright');
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const URL_ = process.env.DEMO_URL || 'http://localhost:8767/';
const USER = process.env.DEMO_USER || 'admin';
const PASS = process.env.DEMO_PASS || '';
const OUT = path.resolve(process.env.DEMO_OUT || './demo-out');
const CONTACT = process.env.DEMO_CONTACT || '';
const SIZE = { width: 1280, height: 720 };

// What is drawn on top of the page: a caption bar, a mouse pointer (a headless browser records none), and full-screen title cards.
const OVERLAY = `(() => {
  const css = '#demo-cap{position:fixed;left:50%;bottom:34px;transform:translateX(-50%);max-width:1000px;padding:14px 26px;border-radius:14px;background:rgba(10,18,36,.92);color:#fff;font:600 26px/1.3 Inter,system-ui,sans-serif;text-align:center;z-index:2147483000;opacity:0;transition:opacity .35s;pointer-events:none;box-shadow:0 8px 30px rgba(0,0,0,.35)}'
   + '#demo-cap.on{opacity:1}#demo-cap.left{left:252px;transform:none;max-width:540px}'
   + '#demo-cur{position:fixed;left:0;top:0;width:26px;height:26px;z-index:2147483001;pointer-events:none;transition:transform .02s linear;filter:drop-shadow(0 2px 3px rgba(0,0,0,.4))}'
   + '#demo-card{position:fixed;inset:0;z-index:2147483002;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px;background:linear-gradient(135deg,#0b1730,#1d4ed8);color:#fff;font-family:Inter,system-ui,sans-serif;text-align:center;opacity:0;transition:opacity .6s;pointer-events:none;padding:40px}'
   + '#demo-cur,#demo-cur *,#demo-cap,#demo-card{pointer-events:none!important}'
   + '#demo-card.on{opacity:1}#demo-card h1{font-size:68px;margin:0;letter-spacing:-1px}#demo-card p{font-size:30px;margin:0;opacity:.92;max-width:900px}#demo-card small{font-size:22px;opacity:.75}';
  const make = () => {
    if (document.getElementById('demo-cap')) return;
    const st = document.createElement('style'); st.textContent = css;
    const cap = document.createElement('div'); cap.id = 'demo-cap';
    const cur = document.createElement('div'); cur.id = 'demo-cur';
    cur.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26"><path d="M3 2l17 8.5-7.2 1.9L9.6 20z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    const card = document.createElement('div'); card.id = 'demo-card';
    document.documentElement.append(st, cap, cur, card);
    window.addEventListener('mousemove', (e) => { cur.style.transform = 'translate(' + e.clientX + 'px,' + e.clientY + 'px)'; }, true);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', make); else make();
  window.__cap = (t, left) => { make(); const c = document.getElementById('demo-cap'); c.classList.toggle('left', !!left); if (t) { c.textContent = t; c.classList.add('on'); } else c.classList.remove('on'); };
  window.__card = (h, p, s) => { make(); const c = document.getElementById('demo-card'); if (h) { c.innerHTML = '<h1></h1><p></p><small></small>'; c.children[0].textContent = h; c.children[1].textContent = p || ''; c.children[2].textContent = s || ''; c.classList.add('on'); } else c.classList.remove('on'); };
})();`;

const srt = [];
const stamp = (ms) => { const p = (n, w = 2) => String(Math.floor(n)).padStart(w, '0'); return `${p(ms / 3600000)}:${p((ms / 60000) % 60)}:${p((ms / 1000) % 60)},${p(ms % 1000, 3)}`; };

(async () => {
  if (!PASS) { console.error('Set DEMO_PASS to the sign-in password.'); process.exit(2); }
  fs.rmSync(OUT, { recursive: true, force: true });
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM, args: ['--no-sandbox'] } : {});

  // Sign in first, in a context that is not recorded, so the video starts on the dashboard.
  const first = await browser.newContext({ viewport: SIZE });
  const lp = await first.newPage();
  await lp.goto(URL_);
  await lp.fill('input[autocomplete=username]', USER);
  await lp.fill('input[autocomplete=current-password]', PASS);
  await lp.click('button.login-submit');
  await lp.waitForSelector('nav.nav');
  const state = await first.storageState();
  await first.close();

  const ctx = await browser.newContext({ viewport: SIZE, deviceScaleFactor: 1.5, storageState: state, bypassCSP: true });   // bypassCSP: the app's own policy would block the caption styles drawn on top
  await ctx.addInitScript(OVERLAY);
  const page = await ctx.newPage();
  global.__page = page;
  // The picture is taken from the browser's own screencast: every time the screen changes it hands over a frame with its time.
  const frames = [];
  const cdp = await ctx.newCDPSession(page);
  cdp.on('Page.screencastFrame', async (f) => { frames.push({ t: f.metadata.timestamp * 1000, data: f.data }); await cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }).catch(() => {}); });
  const t0 = Date.now();
  await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 92, maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1 });
  const wait = (ms) => page.waitForTimeout(ms);
  let mx = 640, my = 360;
  const glide = async (x, y) => { await page.mouse.move(x, y, { steps: 28 }); mx = x; my = y; };
  const centre = async (loc) => { await loc.scrollIntoViewIfNeeded(); const b = await loc.boundingBox(); return b ? [b.x + b.width / 2, b.y + b.height / 2] : [mx, my]; };
  const hover = async (loc) => { const [x, y] = await centre(loc); await glide(x, y); };
  const click = async (loc) => { await hover(loc); await wait(400); await page.mouse.down(); await wait(70); await page.mouse.up(); };   // a real click at the pointer, not locator.click(), which would refuse while the drawn pointer is on top
  const scroll = async (dy, ms, where = [760, 400]) => { await glide(...where); const n = 20; for (let i = 0; i < n; i++) { await page.mouse.wheel(0, dy / n); await wait(ms / n); } };
  const bring = async (loc, pad = 110) => { const b = await loc.boundingBox(); if (b && Math.abs(b.y - pad) > 20) await scroll(b.y - pad, 1100); };   // scroll so the element's top is near the top of the screen
  let open = null;
  const say = async (text, ms, left = false) => {
    const now = Date.now() - t0;
    if (open) srt.push({ ...open, end: now });
    open = text ? { start: now, text } : null;
    await page.evaluate(([t, l]) => window.__cap(t, l), [text, left]);
    await wait(ms);
  };
  const nav = (href) => page.locator(`a[href$="${href}"]`).first();

  await page.goto(URL_);
  await page.waitForSelector('main#main');
  await page.evaluate(() => window.__card('TendAI', 'Find the right civil tenders, bid with confidence, and never miss a deadline', 'by QMSofts'));
  await wait(3000);
  await page.evaluate(() => window.__card());
  await wait(900);

  // 1. Dashboard
  await say('Every morning: the civil tenders that suit your company, in one place', 3200);
  await hover(page.getByText(/tenders close within 7 days/));
  await say('See at a glance what closes this week and what needs attention', 3200);
  await scroll(380, 2200);
  await say('Money tied up in bids is tracked: EMD, fees, bank guarantees', 3500);
  await scroll(-380, 900);

  // 2. Tenders
  await click(nav('/tenders'));
  await wait(900);
  await say('Tenders ranked for your company, with the reason why', 2200);
  await scroll(420, 2200);
  await scroll(-420, 900);
  const search = page.getByPlaceholder(/^Search/);
  await click(search);
  await say('Search in plain words, like “culvert” or “road nashik”', 1000);
  await page.keyboard.type('culvert', { delay: 120 });
  await wait(2200);
  await search.fill('');
  await click(page.locator('.fb-toggle'));
  await say('Narrow down by value, department or location', 3200);
  await click(page.locator('.fb-toggle'));
  await wait(400);

  // 3. One tender (the caption sits on the left, clear of the panel)
  await click(page.locator('.tender .work').first());
  await say('Open a tender for its dates, amounts and ranking reason', 4000, true);
  await page.keyboard.press('Escape');
  await wait(700);

  // 4. Bids
  await click(nav('/bids'));
  await wait(900);
  await say('Every bid in one pipeline, with what to do next', 3000);
  const cash = page.getByPlaceholder(/5,00,000/);
  await click(cash);
  await say('Tell it your free cash and it picks the bids worth the earnest money', 800);
  await page.keyboard.type('10 lakh', { delay: 120 });
  await click(page.getByRole('button', { name: 'Plan my bids' }));
  await wait(1200);
  await scroll(260, 1000);
  await wait(2200);

  // 5. A bid
  await page.evaluate(() => { location.hash = '#/bids/1'; });
  await page.waitForSelector('[role=tab]');
  await wait(900);
  await say('One page per bid: what is ready, what is missing, when it closes', 4000);
  await scroll(420, 1600);
  await say('It also shows your own record with this department and size', 3200);
  await bring(page.locator('[role=tablist]').first());
  for (const [tab, text] of [['Checklist', 'A checklist built from the tender, so nothing is forgotten'], ['Price', 'Upload the BOQ and cost it from your own item rates'], ['Money', 'EMD, fees and bank guarantees, with refund reminders']]) {
    await click(page.locator(`[role=tab]:has-text("${tab}")`).first());
    await wait(400);
    await say(text, 3300);
  }

  // 6. Vault
  await click(nav('/vault'));
  await wait(900);
  await say('Your standard papers in one Vault, with warnings before they expire', 3600);

  // 7. WhatsApp assistant
  await say('', 0);
  await click(nav('/settings'));
  await say('Ask questions from your phone on WhatsApp', 600);
  await click(page.locator('[role=tab]:has-text("Integrations")').first());
  await wait(600);
  await bring(page.getByText('Try the assistant'), 90);
  await wait(900);
  await page.locator('.adv-controls select').selectOption('bidder');
  for (const word of ['closing', 'bids']) {
    await click(page.locator('.chips-row .chip', { hasText: new RegExp(`^${word}$`) }));
    await wait(2200);
  }
  await say('“What closes this week?” “How are my bids?” Answered in seconds', 2200);

  // 8. Hindi
  await click(page.locator('summary:has-text("Appearance")'));
  await Promise.all([page.waitForLoadState('load'), page.locator('.lang-switch select').selectOption('hi')]);
  await page.waitForSelector('main#main');
  await page.mouse.move(640, 300);
  await page.evaluate(() => { location.hash = '#/dashboard'; });
  await wait(900);
  await say('Works in Hindi and Marathi too', 3500);

  // Close
  await say('', 700);
  await page.evaluate(([c]) => window.__card('TendAI by QMSofts', 'Never miss a tender. Know what to bid.', c), [CONTACT]);
  await wait(4000);
  const total = Date.now() - t0;

  await cdp.send('Page.stopScreencast').catch(() => {});
  await ctx.close();
  await browser.close();

  // One image per change of the screen, each held for as long as it was on screen; ffmpeg turns that into a steady 30 frames a second.
  const dir = path.join(OUT, 'frames');
  fs.mkdirSync(dir, { recursive: true });
  const list = [];
  frames.forEach((f, i) => {
    const name = `f${String(i).padStart(5, '0')}.jpg`;
    fs.writeFileSync(path.join(dir, name), Buffer.from(f.data, 'base64'));
    const next = frames[i + 1] ? frames[i + 1].t : f.t + 500;
    list.push(`file '${name}'`, `duration ${Math.max(0.001, (next - f.t) / 1000).toFixed(3)}`);
  });
  list.push(`file 'f${String(frames.length - 1).padStart(5, '0')}.jpg'`);
  fs.writeFileSync(path.join(dir, 'list.txt'), list.join('\n'));
  const skew = frames.length ? frames[0].t - t0 : 0;   // the video starts at its first frame
  srt.forEach((s) => { s.start = Math.max(0, s.start - skew); s.end = Math.max(0, s.end - skew); });
  const mp4 = path.join(OUT, 'tendai-demo.mp4');
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', path.join(dir, 'list.txt'), '-vf', 'fps=30,scale=1920:1080:flags=lanczos,format=yuv420p', '-c:v', 'libx264', '-preset', 'slow', '-crf', '18', '-movflags', '+faststart', mp4]);
  fs.writeFileSync(path.join(OUT, 'tendai-demo.srt'), srt.filter((s) => s.text).map((s, i) => `${i + 1}\n${stamp(s.start)} --> ${stamp(s.end)}\n${s.text}\n`).join('\n'));
  fs.rmSync(dir, { recursive: true, force: true });
  console.log(`Done: ${mp4} (${(total / 1000).toFixed(0)} s)`);
})().catch(async (e) => { console.error(e); if (global.__page) await global.__page.screenshot({ path: path.join(OUT, 'error.png') }).catch(() => {}); process.exit(1); });
