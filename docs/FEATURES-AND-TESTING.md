# TendAI: features, settings and how to test them

How to read this: **Where** is the menu path. **Needs** says what must be set up first (a plain `-` means nothing). **How to test** is a manual check you can do in a few minutes; the **Expected** column is what you should see. Section 9 lists the automated checks.

Contents: 0 Quick start · 1 Server settings · 2 Settings page · 3 People and roles · 4 Tenders and search · 5 Bids · 6 Price and documents · 7 Intelligence features · 8 Portals, security, backups and integrations · 9 Automated checks · 10 What cannot be tested without outside accounts

---

## 0. Quick start: a test installation in five minutes

| # | Step | Command or action | You should see |
|---|---|---|---|
| 1 | Install | `pip install -r requirements-dev.txt` then `cd web && npm ci && npm run build` | no errors; `web/dist` exists |
| 2 | Fake tenders, vault, bids | `python testdata/seed_demo_data.py --db ./local.db` | "Tenders in list: 49 ... bids: 8" |
| 3 | Fake history for the data features | `python testdata/seed_intelligence.py --db ./local.db` | "80 decided bids, 120 imported award results, 430 days of collection history" |
| 4 | Start | `APP_PASSWORD='choose-a-long-password' COOKIE_SECURE=false STATE_PATH=./local.db python -m tender_digest.web` (Windows: `set` each variable on its own line first) | "Uvicorn running on http://0.0.0.0:8000" |
| 5 | Sign in | open `http://localhost:8000`, user `admin`, the password you set | the Dashboard, with a red "Portals that may have stopped working" notice (that is the seeded history) |

Use a **throwaway** database: both scripts only add data. Nothing here contacts a government portal, sends email or calls the AI unless you set the keys in section 1.

---

## 1. Server settings (environment variables)

Set these on the server (Railway Variables, or the command line for a local test) and restart. Only `APP_PASSWORD` is required to start.

| Variable | Needed for | Default | What it does | How to test |
|---|---|---|---|---|
| `APP_PASSWORD` | **Required** | none | The owner's password (8+ characters, use 12+). | Start without it: the service refuses to start with a clear message. Wrong password at sign-in: "Wrong username or password". |
| `APP_USERNAME` | Sign-in | `admin` | The owner's username (not case sensitive). | Set `boss`, restart, sign in as `boss`. |
| `SECRET_KEY` | Sign-in | random each start | Signs login cookies. Without it everyone is signed out at every restart. | Sign in, restart: with the key you stay in, without it you are signed out. |
| `STATE_PATH` | Data | `/data/tender_state.db` in Docker | Where the database lives. Must be on a mounted volume on Railway. | Point it at a file; restart; your data is still there. `/api/health` returns 503 if the folder cannot be written. |
| `COOKIE_SECURE` | Local testing | `true` | `false` only for http testing on your own computer. | With `true` on plain http you cannot stay signed in. |
| `TZ_NAME` | Times | `Asia/Kolkata` | Time zone for the schedule and dates. | Change it; the daily send time follows it. |
| `PORT`, `STATIC_DIR` | Hosting | `8000`, `web/dist` | Port and the folder of the built page. | Change the port and open it. |
| `APP_URL` | Links, SSO, WhatsApp | empty | The site's public address (`https://tend.example.in`), used in reminder emails, the calendar and the webhook address shown in Settings. | Set it; Settings > Integrations shows `https://.../api/whatsapp/webhook` and the SSO redirect address. |
| `BREVO_API_KEY`, `MAIL_FROM`, `MAIL_FROM_NAME` | Email | none, none, `TendAI by QMSofts` | Brevo key and a verified sender address. | Settings > Daily email > **Send a test email** arrives. |
| `RECIPIENTS` | Email | empty | Starting list of email addresses (the page's list wins once saved). | Appears in Settings > Daily email on first start. |
| `SUBJECT_PREFIX`, `SEND_EMPTY` | Email | `Civil tenders`, `true` | Start of the subject line; send even on days with nothing new. | Run now with email on; read the subject. `SEND_EMPTY=false` and an empty day: no email. |
| `MIN_VALUE`, `LOOKBACK_DAYS`, `CLOSING_SOON_DAYS`, `MAX_NEW_PER_PORTAL`, `MAX_SOON_PER_PORTAL` | Collection | `0`, `2`, `3`, `25`, `10` | Smallest tender kept (₹), how far back the first run looks, "closing soon" window, rows shown per portal in the email. | Change one, run, compare the numbers. |
| `CIVIL_KEYWORDS`, `EXCLUDE_KEYWORDS`, `ORG_INCLUDE`, `STRONG_CIVIL_ORG`, `NON_CIVIL_CATEGORY` | Civil filter | built-in lists | Patterns (regular expressions) that decide what counts as civil. Most can also be set on the page (Settings > Civil filter). | Add a word on the page, run, check a tender with it is kept. |
| `PORTALS`, `PORTALS_FILE` | Portals | all four built-in | Which built-in portals to read; or a file with your own list. | `PORTALS=delhi`, restart: only Delhi is read. |
| `PORTAL_HOST_SUFFIXES` | Adding portals | `.gov.in,.nic.in` | Which website endings may be added as portals. | Try adding `https://x.nprocure.com/...`: refused until you add `.nprocure.com`. |
| `MAX_DETAIL_FETCHES`, `REQUEST_DELAY` | Politeness | `600`, `0.6` | Detail pages read per portal per run; seconds between requests. | Lower the delay only with the portal's blessing. |
| `ANTHROPIC_API_KEY` | AI features | empty (AI off) | Turns on every AI feature. | Settings > AI & logo shows the AI as configured; the Ask bar appears on Tenders. |
| `ANTHROPIC_MODEL`, `ANTHROPIC_FAST_MODEL` | AI | `claude-sonnet-5-5`, `claude-haiku-5-5` | Models for reading and for bulk labelling. | Shown in Settings > AI & logo. |
| `AI_MAX_PER_RUN`, `AI_MAX_CALLS_PER_DAY`, `AI_MAX_CALLS_PER_MINUTE` | AI cost | `150`, `300`, `20` | Caps on labelling per run and on calls people can start. `0` = no limit. | Set `AI_MAX_CALLS_PER_DAY=1`: the second AI action says the limit is reached. |
| `SESSION_IDLE_HOURS`, `TRUSTED_PROXY_HOPS` | Security | `24`, `1` | Idle sign-out; how many proxies sit in front (Railway = 1). | Set `SESSION_IDLE_HOURS=1` and leave the page idle past an hour. |
| `OWNER_TOTP_SECRET` | Two-step for the owner | empty | A base32 secret; the owner then also needs the 6-digit code. | Sign in: after the password it asks for the code. |
| `SSO_ISSUER`, `SSO_CLIENT_ID`, `SSO_CLIENT_SECRET`, `SSO_ALLOWED_DOMAINS`, `SSO_DEFAULT_ROLE`, `SSO_BUTTON_TEXT` | Single sign-on | empty, role `viewer` | Sign in with Google/Microsoft/Okta. Without `SSO_ALLOWED_DOMAINS` nobody is let in. | A "Sign in with SSO" button appears on the sign-in page. |
| `DATA_ENCRYPTION_KEY`, `DATA_ENCRYPTION_KEY_OLD` | Encryption at rest | empty | Encrypts uploaded files and secrets (16+ characters). Keep a copy off the server. | Settings > Backup shows encryption on and "0 files not yet encrypted". |
| `DB_WAL` | Speed | `1` | SQLite write-ahead mode (about 8x faster saves). `0` to turn off. | Download a backup; it restores cleanly either way. |
| `BACKUP_KEEP_DAYS` | Backups | `7` | Days of automatic copies kept on the volume. | Settings > Backup lists the days. |
| `BACKUP_S3_BUCKET`, `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY` (+ `_ENDPOINT`, `_REGION`, `_PREFIX`, `_KEEP_DAILY`, `_KEEP_WEEKLY`) | Off-site backup | empty | Nightly copy to your own bucket (S3, R2, B2, MinIO). | Settings > Backup > **Send a full backup now**; the file appears in the bucket. |
| `VAULT_DIR`, `BOQ_DB_PATH` | Files | beside the database | Where uploaded papers and the BOQ database are kept. | Upload a Vault paper; find the file in that folder. |
| `ALLOW_EPHEMERAL_STORAGE` | Hosting | off | Silences the "no volume" warning (not recommended). | The red warning on the Dashboard disappears. |
| `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ALLOWED`, `WHATSAPP_API_VERSION` | WhatsApp assistant | empty, version `v21.0` | See section 8. `WHATSAPP_ALLOWED` = `919876543210:bidder,919812345678:viewer`. | Settings > Integrations > WhatsApp assistant says "Set up" and lists the numbers by their last four digits. |
| `LOG_LEVEL` | Support | `INFO` | How much the server logs. | `DEBUG` gives more lines. |

---

## 2. The Settings page (administrators)

| Tab | Setting | What it does | How to test | Expected |
|---|---|---|---|---|
| Daily email | Recipients | One address per line who gets the digest. | Add yourself, Save, **Send a test email**. | Email arrives. (Needs Brevo.) |
| Daily email | Send time, send every day, send even when nothing is new | The schedule of the daily run and email. | Set the time two minutes ahead, Save, wait. | A run appears on Runs; the email follows. |
| Daily email | Smallest tender (₹) | Hides tenders below the value (those with no stated value are kept). | Set `50 lakh`; Tenders. | Smaller ones disappear from the list and counts. |
| Daily email | Words to add / words to exclude | Extra civil keywords; words that drop a tender. | Add `kerb`; exclude `painting only`; Run now. | A tender with the added word is kept; one with the excluded phrase is dropped. |
| Reminders | Send deadline reminders by email | Email 3 days and 1 day before closing, the EMD date, the pre-bid meeting and the query deadline, and 6 hours before closing. | Start a bid closing in 2 days. | One reminder email (a late-started bid gets one, not a burst). |
| Reminders | Also send to a chat or message service | A web address (https) that accepts a JSON message, such as a Slack or Teams incoming webhook. | Paste it, press **Send a test message**. | The message arrives. |
| Reminders | Calendar address | A private link for Google/Outlook/iPhone calendars. | Subscribe to it; make a new address. | Bid dates appear with a one-day alert; the old link stops working. |
| Portals | Portals to read | Switch built-in and added portals on or off. | Untick one, Save, Run now. | Only ticked portals are read. |
| Portals | Add a portal / Add more portals / Check / Diagnose / Coverage proof | See section 8. | - | - |
| Civil filter | What counts as civil | The word lists above, as lines. | Add a line, Save. | Applies from the next run. |
| AI & logo | AI assistant on/off options | "Worth a look today" paragraph in the email; hide tenders the AI judges not civil. | Tick, run. | The email opens with a short paragraph; non-civil tenders are tagged or hidden. (Needs AI key.) |
| AI & logo | Label tenders now | Labels 45 tenders at a time with work type and fit. | Press it. | Tenders gain work type and fit stars. (Needs AI key.) |
| AI & logo | Logo picture / Logo letters / Company colour | Upload PNG, JPEG or WebP under 400 KB; letters shown without a picture; the colour everyone sees. | Upload a picture; log out. | Picture on the sign-in screen, menu and tab icon; **Remove** brings the letters back. |
| People | Add person, role, email, reset, switch off, erase; **Require approval before a bid moves to Preparing** | Accounts with Viewer, Bidder or Admin; an optional approval step for bids. | See section 3. Tick the approval box, then try to move a bid from Interested to Preparing. | The bid waits until an administrator approves it on its Team tab. |
| Integrations | Single sign-on, API tokens, outgoing webhooks, WhatsApp assistant | See section 8. | - | - |
| Activity | The log of who did what | Sign-ins, changes, refusals, downloads. | Do something, look. | An entry with your name and time. |
| Backup | Download a backup, automatic daily copy, copy outside Railway, retention days, clean up closed tenders, encryption status | See section 8. | - | - |

---

## 3. People and roles

| Feature | Where | Needs | How to test | Expected |
|---|---|---|---|---|
| Owner sign-in | Sign-in page | `APP_PASSWORD` | Sign in with the variables' username and password. | In; always an admin. |
| Accounts and roles | Settings > People | - | Add `vera` (Viewer, temporary password). Sign in as her. | She must choose her own 12+ character password first, then sees everything but cannot change anything (buttons missing; a forced change gets "view-only"). |
| Role rules | Whole app | - | As Viewer, try Start bid, Vault download, Money, plan. As Bidder, try Settings. | Viewer: read-only, no Vault files or money. Bidder: bids, Vault, AI, **not** Settings. Admin: all. |
| Two-step sign-in | Account & security | an authenticator app | Turn on, scan the key, enter a code; sign out and in. | Password then a 6-digit code; each code works once. |
| Devices | Account & security | - | Sign in on two browsers; sign out one from the list. | The other is signed out at once. |
| Forgot password | Sign-in page | email set up, email on the account | Request a link. | Email with a one-hour, one-use link; the same message is shown whether or not the account exists. |
| Your data | Account & security / People | - | Download your data; erase a switched-off account (type the username). | A file of what is kept; the name is replaced on comments and the log. |
| Activity log | Settings > Activity | - | Sign in as a viewer and try a change. | A "view-only account tried to ..." line. |

---

## 4. Tenders, dashboard and search

| Feature | Where | Needs | How to test | Expected |
|---|---|---|---|---|
| Daily collection and **Run now** | Runs | portals reachable | Press **Run now**. | A live log; new civil tenders appear; a portal that cannot be read is named with the reason. |
| Dashboard charts | Dashboard | tenders | Click a bar or number. | The Tenders list opens showing exactly those tenders. |
| Filters, shared address, saved views | Filter bar | - | Set filters, copy the address into a new tab; save a view. | Same view opens; the view is listed under **Views**. |
| Explore (group by / split by / measure) | Explore | - | Group by department, split by portal, measure total value. | Stacked bars; each bar opens its tenders; the table downloads. |
| Tenders list, details, stars | Tenders | - | Open a tender; star it. | Panel with details; it appears under Starred. |
| Excel / CSV download, bulk actions | Tenders | - | Tick up to 100 tenders. | Star, start bids on, or download only those. |
| Compare tenders | Tenders | - | Tick 2 to 4, press **Compare**. | Value, EMD as % of value, dates, time left side by side. |
| Search (no AI) | Tenders > search or `/` | - | Try `drain`, `"retaining wall"`, `-paver`, `bridge above 2 crore`. | Synonyms found (drain also finds nala/sewer), phrases exact, minus words removed, amounts become value limits; a line explains how it understood. |
| Amounts typed naturally | Any amount box | - | Type `5,00,000`, `50 lakh`, `1.5 cr`, `75k`. | The app shows what it understood. |
| Recommended order | Tenders (default sort) | - | Look at the order; hover a priority. | 0 to 100 from closing time, fit, size, what you chose before; each shows its reasons. |
| Learns from your choices | Settings > AI & logo (learning) | - | Star and hide several tenders of one kind. | Similar tenders rise or fall; "learned" notes name the pattern. |
| Not interested / hide department / restore | Tenders | - | Hide a tender and a department; press **Show ignored**. | Gone from lists, counts, charts, emails; **Restore** brings them back. |
| Fits my size | Vault > Company, then Tenders | - | Set a smallest and largest size. | **Fits my size** hides tenders outside the range (no-value ones stay). |
| Clean up closed tenders | Settings > Backup | - | Preview, then confirm. | Counts first; deletes only after you confirm (starred and bid tenders stay). |
| Saved-search email alerts | Views | email | Save a view with **Email me new matches**. | The next digest has a section for it. |
| Keyboard shortcuts | Any page | - | Press `?`, then `g` `b`. | List of shortcuts; jumps to Bids. |
| Language, colours, install | Menu > Appearance | - | Switch to Hindi/Marathi; pick an accent; dark mode. | Page words change; colours change; "Install app" offered on Chrome/Android. |
| Daily email digest | Email | Brevo | Run now with email on. | New tenders by portal, closing soon, your bids, saved-search sections, spreadsheet attached; portals needing a person are listed; a portal problem is listed (section 7). |
| Add a tender by hand | Tenders > Add a tender | optional AI key | Paste a notice or upload a PDF. | The listing fields are read for you to check. |

---

## 5. Bids

| Feature | Where | Needs | How to test | Expected |
|---|---|---|---|---|
| Start a bid, pipeline | Tender panel > Start bid; Bids | - | Start a bid. | Appears under Interested, with "Coming up" dates. |
| Five steps and readiness | A bid | - | Open it. | Decide, Checklist, Price, Letters, Result strip; "Ready to submit %" lists what is missing. |
| Checklist | Bid > Checklist | - | **Add standard steps**; tick items. | Steps added; progress moves. |
| Vault and matching | Vault | - | Add a paper with an expiry; read a document (below). | Each required document shows Not in vault / Expired / Expires before closing. |
| Fetch from portal | Bid > Checklist | portal reachable | Press **Fetch from portal**. | Fees, class, dates, the notice and BOQ when public; says plainly if a login/OTP/captcha stops it. |
| Read tender document (AI) | Checklist | AI key | Upload a PDF or paste text. | Checklist items, plus "I checked them against the notice" to confirm. |
| **Find conditions (no AI)** | Checklist | - | Paste a notice. | Earnest money, validity, completion, defect liability, performance security, retention, liquidated damages, similar work, turnover, mobilisation advance, price variation, each with its sentence. |
| Eligibility check (AI) | Decide | AI key + profile + facts | Press **Check eligibility**. | Each condition met / gap / unclear. |
| Bid / no-bid brief (AI) | Tender panel | AI key | Press it. | Bid / Maybe / No bid with reasons. |
| Team, comments, approval | Bid > Team | accounts | Assign an item to someone; write `@name`; ask approval. | They are emailed; an admin approves or declines. |
| Deadline reminders, calendar | Settings > Reminders | email | See section 2. | - |
| Change watch | Runs | portal | After a run. | Moved dates and new corrigenda are recorded on the bid. |
| Record / **read** a corrigendum | Bid > Decide | - (AI optional) | **Record a corrigendum > Read it for me**, paste text. | Conditions that differ from the tender's, with quotes; with AI also impact and what to do; **Use this as the note** fills the form; nothing is recorded until you press Record. |
| Bid pack | Checklist | bidder | **Download bid pack**. | One zip with the papers in order and `00-index.txt` of what is missing. |
| Letter templates | Vault > Letters; bid > Letters | - | Make a template with `{{tender_id}}`; use it. | Filled letter; every save is a version you can restore. |
| Draft letters (AI) | Bid > Letters | AI key | Choose a draft. | Covering letter, declaration or query, unknown details in [brackets]. |
| Who bid / award notice | Bid > Result | AI optional | Enter bidders or paste an award notice. | Winner and prices recorded; Competitors table fills. |
| Results and competitors | Bids > Results | - | Mark Won/Lost with prices. | Win rate, gap to L1, record by work type, department, size. |
| Money (EMD, fees, guarantees) | Bid > Money; Dashboard | bidder | Add an EMD paid online and a bank guarantee. | Cash locked and guarantees in use, refund-overdue and expiry flags, a spreadsheet. |

---

## 6. Price and documents

| Feature | Where | Needs | How to test | Expected |
|---|---|---|---|---|
| Item catalogue | Vault > Item catalogue | CSV of items and rates | Upload `tests/fixtures/erp_items.csv`. | 16 items added. |
| BOQ upload and matching | Bid > Price | catalogue | Upload `tests/fixtures/boq_sample.csv`. | Lines matched; **Check** lines await confirmation; coverage % and break-even shown. |
| Risk allowances (AI) | Bid > Price | AI key | Read the tender for risks. | Up to 12 hidden cost drivers added as a cost range. |
| **Price advisor** | Bid > Price | a costed BOQ | Open the bid with the BOQ. | A chart of win chance against quote, the recommended quote, and "Lowest bidders usually priced between X% and Y% against the estimate", based on N results. |
| **Price history** (import) | Bids > Results | - | Paste rows or choose a CSV: header `Department,Work type,Estimated cost,Awarded amount,Awarded to,Award date`. Use **Download a sample file**. | "N added, M left out" with reasons; typing errors (price under a third or over twice the estimate) refused; a re-import adds nothing. |
| Price model backtest | Bids > Results > Price history | 16+ dated results | After importing. | "Tested on N past results ... off by X points against Y for averaging everything". |

---

## 7. Intelligence features

To try these quickly, run both seed scripts (section 0).

| Feature | Where | Needs | How to test | Expected |
|---|---|---|---|---|
| **Win chance** | A bid's Decide tab; Bids > Results | 40 decided bids | Seeded: open the bid "Widening of road, Sinnar". Unseeded: mark bids Won/Lost. | With the seed: a % with "Department ... you won 38 of 43 such bids, which raises it" and a test line. Under 40 bids: "14 of 40 decided bids recorded" and no number. If your history shows no pattern: "no better than your overall win rate" and no number. |
| **Who usually wins here** | A bid's Decide tab | winners recorded or imported | Seeded bid, or import awards with an `Awarded to` column. | Table of winners: wins, share, usual winning price vs estimate, last win, "You finished ahead in 3 of 5". Fewer than 3 winners: an explanation. |
| **Which bids to submit** | Bids > Pipeline | open bids; bidder role | Enter `5 lakh` free cash, margin 8, press **Plan my bids**. | "Submit 2 of 5: uses ₹4,25,000 of ₹5,00,000 ...", every skipped bid with a reason, and how much more cash would add the next. A bad entry gets a message. |
| **What to expect** | Dashboard | a few weeks of runs (seeded: 14 months) | Seeded: open the Dashboard. | "About N new civil tenders in the next four weeks (likely between A and B)", a test line, departments, and "Likely to come round again". On a new install: "Not enough history to forecast yet". |
| Portal health | Dashboard banner; Settings > Portals; email; webhooks | history (seeded) | Seeded: Dashboard and Settings > Portals. | Red notice naming Maharashtra "Stopped working" and Delhi "Finding nothing"; an **Is it working?** column; a section in the daily email. |
| Health webhooks | Settings > Integrations | a webhook | Add a webhook to `portal.unhealthy` / `portal.recovered`; wait an hour on the seeded data. | One message when a portal turns bad or recovers, not every hour. |
| AI: work type, fit, summary, Ask bar, "may not be civil", "same work tendered again" | Tenders | AI key | Press **Label tenders now**; type a question in the Ask bar. | Work type and 1 to 5 fit; the question becomes filters. |

---

## 8. Portals, security, backups and integrations

| Feature | Where | Needs | How to test | Expected |
|---|---|---|---|---|
| Add a portal | Settings > Portals | the portal reachable | Paste `https://etenders.kerala.gov.in/nicgep`, **Add and check**. | Added and switched on if readable, else on the check-by-hand list with the reason. |
| Add many portals | Portals > Add more portals | - | Tick several, **Add and check**. | Each checked one by one; only readable ones switched on. |
| Check / sample | Portals table | - | **Check**; for a failing one **Download a sample**. | Status updates; a file of what the portal returned. |
| **Coverage proof** | Portals > Coverage proof | the portal reachable | **Test all switched-on portals**; **Download the report (CSV)**. | Each portal is Proven, Partly, Answers (no tenders) or Cannot be read, with counts of tenders and dates read. CLI: `python -m tender_digest.coverage --only delhi --csv r.csv`. |
| **Reading by content** | Runs / Portals | a changed portal page | (Unit-tested with the real page; live: on a portal whose layout changed.) | The portal stays healthy, badge **Layout changed: read by content**. |
| **Diagnose** | Portals table | portal reachable | Press **Diagnose**. | The tables found, tenders read by each method, a sample and a verdict in words. |
| Manual portals | Dashboard notice | - | A portal that needs captcha/login/OTP. | Listed with the reason and **Add a tender by hand**. |
| API tokens | Settings > Integrations | admin | Create a token; `curl -H "Authorization: Bearer tnd_..." .../api/stats`. | Works as view-only or bidder; revoke it and it stops; actions logged as `token:name`. |
| Outgoing webhooks | Integrations | a public https address | Add one for `bid.created`; **Send test**; start a bid. | Signed POST (`X-TendAI-Signature`), retried after failures. |
| Single sign-on | Integrations | SSO variables | Click **Sign in with SSO**. | Account created with `SSO_DEFAULT_ROLE`; only allowed email domains get in. |
| Backups | Settings > Backup | - | **Download a backup**; later restore with `python -m tender_digest.backup restore backup.zip --target-dir ./restored --force`. | A zip of both databases and the Vault; restore checks it first and never deletes (moves aside). |
| Off-site backup | Backup | `BACKUP_S3_*` | **Send a full backup now**. | The bucket gains daily and weekly copies; errors shown. |
| Encryption and retention | Backup | `DATA_ENCRYPTION_KEY` | Look at the status; set retention days. | "Encrypted" with counts; tenders older than the days are removed once closed (open, starred and bid tenders stay). |
| **WhatsApp assistant** | Integrations; your phone | Meta app + `WHATSAPP_*` | Send `help`, `closing`, `bids`, `find drain`, `cash` from a listed number. | Replies in the chat; `cash` only for bidder/admin numbers; an unlisted number gets nothing. Status card shows "Set up". |
| **WhatsApp assistant preview** ("Try the assistant") | Settings > Integrations (administrators) | nothing (free text also needs the AI key) | Pick who is asking (viewer, bidder, administrator), tap `closing`, `bids`, `cash` or type `find drain`. | Answers appear as chat bubbles using your own tenders and bids; `cash` is refused for a viewer. Nothing is sent to WhatsApp, and it works with no Meta set-up. |
| Company logo | Settings > AI & logo | - | Upload a picture. | On the sign-in page (also above the form on a phone). |
| First-run checklist and sample data | Dashboard | admin | Load sample tenders; remove them. | A "Sample data" label; nothing is emailed or sent to the AI; removal also deletes bids started on them. |
| Customer provisioning | Terminal | - | `python -m tender_digest.provision new --name "Acme" --url https://tend.acme.in --recipients a@acme.in --mail-from t@acme.in` then `TENDAI_PASSWORD=... python -m tender_digest.provision verify --url https://tend.acme.in` | `customers/acme/` with a settings file (readable by you only) and a go-live checklist; `verify` prints PASS / WARN / FAIL for https, login, volume, encryption, backups, email, portals, health, logo. |

---

## 9. Automated checks (run these before every release)

| What | Command | Expected |
|---|---|---|
| Backend tests (about 1,360) | `python -m pytest -q` | all pass (about 2 minutes) |
| Lint and types | `ruff check .` and `mypy` | no problems |
| Page type-check and build | `cd web && npx tsc -b && npm run build` | no errors |
| Page unit tests | `cd web && npm test` | all pass |
| Hindi/Marathi complete | `cd web && npm run i18n` | "Translations OK" |
| Colours readable | `cd web && npm run colours` | "Colours OK" |
| Accessibility (every screen, light/dark/phone, three languages) | start the app with the seeded data, then `cd web && A11Y_URL=http://localhost:8000/ A11Y_PASS=... npm run a11y` | no violations (about 10 minutes) |
| Fit on small screens | `cd web && npm run fit` (same variables) | no sideways scrolling |
| Dependency audit | runs in CI (`pip-audit`, `npm audit`) | no known vulnerabilities |
| Everything on every change | GitHub Actions (CI) | seven checks green |

---

## 10. What cannot be tested without outside accounts

| Feature | Why | What to do |
|---|---|---|
| Collecting from real portals | The build environment cannot reach them. | Run **Coverage proof** on the customer's own server. Do not call a portal "covered" until it shows **Proven** there. |
| AI features | Need an Anthropic API key and cost money per use. | Set `ANTHROPIC_API_KEY`; caps limit the spend. |
| Email | Needs a Brevo key and a verified sender. | Use **Send a test email**. |
| **WhatsApp assistant** | Built to Meta's documented formats and tested with signed sample messages and a stand-in sender, **not** against a live WhatsApp account. | Try it from your own number first (README, "WhatsApp assistant"). |
| Reading a changed portal page | Tested on the real saved page and altered copies, not live portals. | Use **Diagnose** on a portal that stopped working. |
| Single sign-on | Needs your identity provider. | Follow README, "Single sign-on". |
| Win chance, price advisor, forecast on real data | The models are tested on simulated history. | Judge them by the test line each screen shows on your own data. |
