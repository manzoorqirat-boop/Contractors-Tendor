# TendAI by QMSofts (tender dashboard)

A web service that reads the public tender lists of Indian government e-procurement portals, keeps the **civil works**
tenders, and gives you:

- a **dashboard** with analytics (new tenders per day, closing soon, by portal, category, value band, top departments);
- a searchable **tenders** list with filters and a spreadsheet download;
- a **Run now** button, with a live log, for collecting on demand;
- a **daily email digest** to a list of addresses you edit in the page;
- **settings** for recipients, send time, portals, minimum value and keywords.

| Portal | Covers | Status |
|---|---|---|
| MahaTenders | Maharashtra: PWD, municipal corporations, Zilla Parishads, irrigation, housing | Working |
| Delhi e-Procurement | Delhi government | Working |
| UP eTender | Uttar Pradesh | Working |
| CPPP (eprocure.gov.in) | Central ministries, PSUs, institutes, border forces | Working |
| CPWD e-tender | Central PWD | Not reachable from the build environment (403); see below |
| IREPS (railways) | Indian Railways | Not possible: its tender search needs a mobile-OTP login |
| Gujarat (nProcure) | Gujarat | Not reachable from the build environment; see below |

## Using the dashboard

- **Filters** (portal, category, department, location, value, closing window, search, starred) are shared by the Dashboard,
  Explore and Tenders pages. The address bar always holds the current view, so **Share**, bookmarks and the Back button work.
  **Views** saves a named set of filters and chart choices.
- **Keyboard**: `/` searches the tender list, `?` lists the shortcuts, and `g` then `d` / `e` / `t` / `b` / `v` / `r` / `s` jumps to Dashboard, Explore, Tenders, Bids, Vault, Runs or Settings. Shortcuts pause while you type in a box.
- **Amounts** can be typed the way people say them: `5,00,000`, `50 lakh`, `1.5 cr`, `75k`. The app shows back what it understood.
- **Dashboard**: summary numbers and charts for whatever the filters select. The Tenders/Value switch changes every chart.
  Click a number or bar to open the tenders behind it.
- **Explore**: pick a combination (one click) or build your own: *group by* one thing and *split by* another (portal, category,
  department, location, value band, how soon it closes, closing week or day, day found, day published), measured as number of
  tenders, total value or average value, shown as stacked bars, grouped bars or a shaded table. Every bar and cell opens exactly
  the tenders it counts. Tables can be downloaded.
- **Tenders**: cards or a table, sortable, with a detail panel, stars for a watchlist, and a download as **Excel** or CSV. Tick tenders (up to 100) to **star, start bids on, or download just those**.
- **Email alerts for saved searches**: when you save a view, tick *Email me new matches*. The daily digest then gains a section per alert view listing the tenders found since the last email that match it (up to 10 alert views).

## Bid workspace (tender filing support)

TendAI does not submit bids: portals need your DSC and their own login, and the app never goes around those. It prepares everything around the filing.

- **Start a bid** from any tender's panel. The **Bids** page is your pipeline (Interested, Preparing, Submitted, Bid opened, Won, Lost, Dropped) with a "Coming up" list of closing dates, EMD due, pre-bid meetings and query deadlines.
- **Progress and readiness**: a five-step strip (Decide, Checklist, Price, Letters, Result) shows how far a bid has got, and a **Ready to submit** percentage lists what is still missing (unticked checklist items, papers missing or expired in the Vault, document not read, eligibility not checked, no price recorded). The last two days before closing are counted in hours and minutes.
- **AI-read details need a human check**: after the AI reads a tender notice, the bid asks you to compare the details with the notice and press *I checked them against the notice*. It records who and when, and the mark is cleared if the document is read again.
- **Team work**: give a checklist item (or the whole bid) to a person from the account list; they are emailed, and **Assigned to me** on the Bids page lists their open items by closing date. The bid's **Team** tab has a discussion (write `@name` to email someone; the bid's owner is told of every comment) and an **approval to bid** step: a bidder asks, an administrator approves or declines with a note, and everyone involved is emailed. Turn on *Require approval before bidding* in Settings > People to stop a bid moving from Interested to Preparing until it is approved (administrators are not held up). Add your email under Account & security to receive these notices.
- **Checklist** per bid: standard steps plus, after *Read tender document*, the required documents and conditions. Each required document is matched to the **Vault** and flagged *Not in vault*, *Expired* or *Expires before closing*.
- **Vault**: reusable papers (optionally with the file, up to 10 MB) with expiry dates, and **Company facts** (turnover, class, similar work) that the AI uses and never guesses.
- **Eligibility check** (AI): each condition marked met, gap or unclear, with what to do.
- **Deadline reminders** (Settings > Reminders): bids in progress send a reminder by email 3 days and 1 day before the closing date, the EMD date, the pre-bid meeting and the last date for queries, and again 6 hours before a bid closes. Each is sent once; a bid started late gets one reminder, not a burst; nothing is sent between 10 pm and 7 am except the final closing reminder. They can also be posted to a **webhook** (a Slack or Teams incoming webhook, or a WhatsApp/SMS gateway that accepts a JSON `{"text": ...}`; https only, never a private address).
- **Calendar**: Settings > Reminders makes a private address you subscribe to in Google Calendar, Outlook or an iPhone, so every bid deadline and expiring Vault paper appears in your own calendar with a one-day alert. The address is the secret: make a new one to cut off old subscribers.
- **Change watch**: every run looks again at tenders you are bidding for and records moved closing/opening dates, new corrigenda and the pre-bid meeting date. The tender list and daily email follow the new date. Corrigenda are read from the tender's detail page, so a portal that words them differently may show none: still check the portal.
- **Bid pack**: **Download bid pack** on the Checklist is one zip of the Vault papers the checklist asks for, numbered in checklist order, with the saved tender notice and any drafts, and an `00-index.txt` that lists every line: the file used, or what is missing, expired or about to expire. A paper that answers two lines is packed once. It needs a bidder account because it contains company papers.
- **Letter templates** (no AI needed): Vault > Letters holds your wording with placeholders such as `{{tender_id}}`, `{{closing}}`, `{{organisation}}`, `{{emd}}` and `{{company_<fact name>}}` (every company fact becomes a placeholder). On a bid's Letters tab, pick a template and the letter is filled in. Every save is kept as a version; any earlier version can be put back (as a new version). A placeholder with no value is left in the text and listed so it is not sent by mistake.
- **Compare tenders**: tick two to four tenders in the list and press Compare: value, EMD and EMD as a share of value, dates, time left and fit side by side, with the better figure in each row marked.
- **Corrigenda by hand**: under a bid's dates, *Record a corrigendum* logs what a notice changed and, if it moved the closing date, updates the bid, the tender list and the reminders. It is the way to keep a hand-added tender (which the app cannot watch) up to date.
- **Draft letters** (AI): covering letter, declaration/undertaking, pre-bid query, or your own instruction. Unknown details are left in [brackets].
- **Who bid and competitors**: on a submitted bid's Result tab, list every bidder with their quote and rank, by hand or by pasting or uploading an **award notice** or comparative statement for the AI to read (you check it, mark your own row, then Save). The lowest qualified quote becomes the winning price and your row fills in your price, unless you already typed them. Bids > Results gains a **Competitors** table: how often each rival bid against you, how often it won, and how far from the estimate it usually quotes. Spellings of a name (M/s Rao Infra Pvt. Ltd., RAO INFRA PRIVATE LIMITED) count as one company. Award notices are read from what you give the app; it does not collect results from portals.
- **Results**: record your price, the winning price and the outcome. Win rate, gap to L1 and your record by work type, department and size appear under Bids > Results, and on similar tenders.
- The **daily email** gains a "Your bids" section (deadlines in the next days, new changes).

Uploaded vault files are stored in a `vault` folder next to the database (so on the `/data` volume), or in `VAULT_DIR` if set. Back it up with the database.

## BOQ and risk (price-bid support)

On a bid's page, below the checklist:

- **Bill of quantities**: load your **item catalogue** once (CSV exported from your ERP: items and rates, optionally composite activity rates, a schedule of rates such as DSR, and a location cost index). Then upload the tender's BOQ (`.xlsx`, `.xlsm`, `.xls`, `.csv` or a `.pdf` with a table). Each line is matched to your catalogue by unit, schedule code and wording, with Claude checking technical specs (concrete grade, pipe class and so on) when `ANTHROPIC_API_KEY` is set. Lines marked **Check** need your confirmation; confirming teaches the app, so the same wording matches instantly next time. The page shows your cost for the lines it could price, how much of the BOQ value that covers, and the break-even quote. Anything it cannot price is carried at the buyer's estimate and says so.
- **Risk and margin**: Claude reads the NIT and special conditions for 12 hidden cost drivers (escalation, approved makes, retention, site lab, statutory deductions and so on) and the simulator adds them to your BOQ cost as a range, with a margin check against an expected L1 price. The percentages are planning allowances, not rules.

The BOQ data is stored in `boq.db` next to the main database (set `BOQ_DB_PATH` to change it). **PDF BOQs** are read by rules (the first 80 pages; repeated headers on later pages are dropped) and always flagged for checking; a scan or an unusual layout goes to Claude when `ANTHROPIC_API_KEY` is set, otherwise the app asks for the Excel file. **Lump-sum lines** and items outside the catalogue can't be priced from rates, so each line has an **Enter cost** box: your figure for that line is used in the cost, coverage and break-even.

**Fetch from portal** on a bid also brings in the BOQ and the risks, when the portal gives them without a login: it finds the BOQ file among the tender's documents (a spreadsheet, or a document named BOQ, Bill of Quantities or Price Bid), loads and matches it, and has Claude read the tender notice for risks (once; the notice is kept so you can read it again later without uploading). A BOQ you already loaded is never replaced by a fetch. If the portal asks for a login, OTP or captcha, the app says so and stops: it never tries to get past them. Fetching is per bid and on request, with a pause between requests, not a bulk crawl of every tender.

## Backups

Everything is in the `/data` volume: `tender_state.db` (tenders, bids, settings), `boq.db` (BOQ catalogue) and the `vault` folder (your papers and saved notices).

- **Daily copy**: each day the service copies both databases into `/data/backups` (the last 7 days are kept; `BACKUP_KEEP_DAYS`). Each copy is checked with SQLite's integrity check. This protects against a bad edit or a damaged file, **not** against losing the volume.
- **Download backup** (Settings > Backup): one zip with both databases and every vault file. **Save it somewhere other than Railway** (weekly is a good habit, and before any server change). It contains your company papers, so keep it private.
- **Restore** (on the server, e.g. `railway ssh`, with the zip uploaded there): `python -m tender_digest.backup restore backup.zip --target-dir /data --force`, then restart the service. The zip's databases are checked first; existing data is moved aside (`name.before-restore-TIME`), never deleted. If you changed `VAULT_DIR` or `BOQ_DB_PATH`, copy those two items by hand.
- **Off-site copy** (recommended): every night the two databases (compressed) and, once a week, the full zip with the Vault go to a bucket you own, so losing the Railway volume loses nothing. It works with Amazon S3, Cloudflare R2, Backblaze B2 and MinIO. Create a bucket and an access key limited to it (write, list, delete), then set:

  | Variable | Purpose |
  |---|---|
  | `BACKUP_S3_BUCKET`, `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY` | Required together. Secret. |
  | `BACKUP_S3_ENDPOINT` | `https://<account>.r2.cloudflarestorage.com` for R2; blank for Amazon S3 |
  | `BACKUP_S3_REGION` | `auto` for R2 (default); e.g. `ap-south-1` for Amazon S3 |
  | `BACKUP_S3_PREFIX` | Folder in the bucket (default `tendai/`) |
  | `BACKUP_S3_KEEP_DAILY`, `BACKUP_S3_KEEP_WEEKLY` | How many nightly and weekly copies to keep (14 and 8) |

  Settings > Backup shows whether it is working, any error from the bucket, and a **Send a full backup now** button. A failed upload is retried within the hour. To restore, download the `.zip` from the bucket and use the restore command above. The bucket holds your company papers: keep it private, and turn on its own encryption if it offers it.

Long actions (reading a notice, matching a BOQ, fetching from a portal, labelling tenders) run in the background and the page waits for the answer, so a slow one is not cut off by Railway's proxy. If the service restarts in the middle, the page says so and you press the button again.

## Tenders the app cannot collect (add by hand)

Some tenders never reach the lists the app reads: a portal behind a captcha, login or OTP, a newspaper notice, an NIT sent to you.
Press **Add a tender** (Tenders page, Bids page, or the "check these by hand" notice on the Dashboard):

1. Upload the notice (PDF) or paste its text. With the AI assistant on, the Tender ID, work description, department, place, value, EMD and dates are read from it.
2. **Check and correct the details.** Nothing is saved until you press Save. The closing date is required; a date the notice gives without a time is filled in as 17:00 and flagged.
3. It is saved as a normal tender under the portal name **Added by hand**, marked civil, and not counted as "new since the last digest". Optionally the notice is also read for requirements and documents to submit.
4. From there everything works as for a collected tender: Start a bid, checklist, eligibility, drafts, BOQ and risk.

The notice is kept with the tender (in `vault/sources` beside the database, or under `VAULT_DIR`), so the risk step can reuse it without another upload. The same happens when you read a document on any tender.
A Tender ID already in the app is refused, so a hand-added tender cannot duplicate a collected one. **Edit details** corrects a hand-added tender and its bid together; it can be removed once it has no bid.

Limits: the app cannot see corrigenda or moved dates on a hand-added tender, because there is no portal session to re-read. The bid has a **Last checked on the portal** date and reminds you in the last five days before closing until you record a check.

## Deploy on Railway (one service)

1. New Railway service from this repo. Railway builds the `Dockerfile` (React page + Python service in one image).
2. Add a **Volume** mounted at `/data`. This is where everything is kept. Without it, a redeploy loses all tenders and history.
3. Set the variables below.
4. Open the service URL, log in with `APP_PASSWORD`, go to **Settings**, add recipients and press *Send a test email*,
   then go to **Runs** and press *Run now*. **The first run reads every recent tender and takes 20 to 40 minutes.**

The service runs the daily schedule itself (default 8:00 AM India time, changeable in Settings). Keep it at **one replica**:
the scheduler and the "one run at a time" lock live inside the process.

### Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `APP_PASSWORD` | **Yes** | Login password for the page (at least 8 characters). The service will not start without it. Secret. |
| `APP_USERNAME` | Optional | Login username (default `admin`, not case-sensitive). |
| `SECRET_KEY` | Recommended | Long random string that signs login cookies. Without it everyone is logged out on every restart. Secret. |
| `BREVO_API_KEY` | For email | Brevo API key. Secret. |
| `MAIL_FROM` | For email | A sender address verified in Brevo. |
| `STATE_PATH` | Yes on Railway | `/data/tender_state.db` (the Dockerfile already defaults to this). |
| `RECIPIENTS` | Optional | Starting list of email ids (commas or spaces). After you save the list in Settings, the page's list wins. |
| `ANTHROPIC_API_KEY` | Optional | Turns on the **Summarise with Claude** button in the tender panel. Secret. Unset = feature off. |
| `BOQ_DB_PATH` | Optional | Where the BOQ catalogue and parsed BOQs are kept. Default: `boq.db` next to `STATE_PATH`. |
| `ANTHROPIC_MODEL` | Optional | Default `claude-sonnet-5-5`. Used for summaries, bid briefs and reading documents. |
| `ANTHROPIC_FAST_MODEL` | Optional | Default `claude-haiku-5-5`. Used to label many tenders at once. |
| `AI_MAX_PER_RUN` | Optional | Default `150`. The most tenders Claude labels in one run, which caps the cost. |
| `MAIL_FROM_NAME` | Optional | Default `TendAI by QMSofts`. |
| `COOKIE_SECURE` | Optional | Default `true`. Set `false` only for local testing without HTTPS. |
| `TZ_NAME` | Optional | Default `Asia/Kolkata`. |
| `SESSION_IDLE_HOURS` | Optional | Default `24`. A login unused for this long ends. Logins always end after 7 days. |
| `TRUSTED_PROXY_HOPS` | Optional | Default `1` (Railway). How many proxies sit in front of the app; the caller's address is taken that many entries from the END of `X-Forwarded-For`. `0` ignores the header. |
| `AI_MAX_CALLS_PER_DAY` | Optional | Default `300`. Most AI requests people can start from the page in a day (`0` = no limit). Cached answers cost nothing. Settings > AI shows today's count. The daily run's labelling has its own cap, `AI_MAX_PER_RUN`. |
| `AI_MAX_CALLS_PER_MINUTE` | Optional | Default `20` (`0` = no limit). |
| `BACKUP_KEEP_DAYS` | Optional | Default `7`. How many days of automatic database copies to keep on the volume. |
| `OWNER_TOTP_SECRET` | Optional | A base32 secret (for example from an authenticator app's "setup key"). When set, the owner login also needs the 6-digit code from the app. Secret. To lose the requirement, remove the variable. |
| `APP_URL` | Optional | This site's public address, for example `https://tend.example.com`. Used for the "Open the bid" links in reminder emails and the calendar. |
| `DATA_ENCRYPTION_KEY` | Optional | A long private phrase (16+ characters). When set, two-step and webhook secrets and every uploaded file (the Vault and saved tender notices) are stored encrypted, and anything stored earlier is encrypted at the next start. **Keep it somewhere other than the server: without it those files, and a restored backup, cannot be read.** Secret. |
| `DATA_ENCRYPTION_KEY_OLD` | Optional | To change the key: set the new one in `DATA_ENCRYPTION_KEY` and the old one here, restart, then remove this. Commas separate several old keys. |
| `ALLOW_EPHEMERAL_STORAGE` | Optional | Set `true` to silence the "no volume" warning on Railway (not recommended: a redeploy then loses all data). |
| `PORTAL_HOST_SUFFIXES` | Optional | Which website endings can be added as portals in the page. Default `.gov.in,.nic.in`. Example: `.gov.in,.nic.in,.nprocure.com`. |
| `MIN_VALUE`, `LOOKBACK_DAYS`, `CLOSING_SOON_DAYS`, `CIVIL_KEYWORDS`, `EXCLUDE_KEYWORDS`, `ORG_INCLUDE`, `PORTALS`, `MAX_DETAIL_FETCHES`, `REQUEST_DELAY` | Optional | Filter and politeness tuning; see `tender_digest/config.py`. Most can also be set in the page. |

`PORT` is set by Railway. **The Brevo key and the password are never shown in the page or sent to the browser.**

### Brevo

- Create an API key and verify the sender address or domain (Brevo, Senders).
- If Brevo's *authorised IP* restriction is on, Railway's outgoing addresses change and sends are refused with an
  "unrecognised IP" error. Turn the restriction off for this key. The page shows Brevo's reason if a send is refused.

## AI features (optional)

Set `ANTHROPIC_API_KEY` and these appear. Fill in **Settings > AI assistant > About your company** for fit scores and bid briefs.

- **Work type and fit score** on every open tender (1 to 5 against your company profile). Sort by *Best fit*, filter by *Work type*.
- **May not be civil** tag on tenders Claude doubts (an option hides them instead).
- **Ask bar**: type a question in plain words and it becomes filters.
- **Tender panel**: summary, bid / no-bid brief, and *Read tender document* (upload a PDF up to 10 MB, or paste text).
- **Same work tendered again** warning, found by comparing wording within a department (no AI cost).
- **Daily email** opens with a short "worth a look today" paragraph.

Labelling runs after each collection, newest tenders first, up to `AI_MAX_PER_RUN`. **Label tenders now** in Settings does 45 at a time.
AI can be wrong: check every figure and date on the portal before bidding.

## Language, install and search

- **Hindi and Marathi**: choose the language under the menu (bottom of the sidebar) or on the sign-in page; it is remembered in that browser and the page reloads. The page's own words are translated: menus, sign-in, the dashboard, the tender list and filters, the main bid screens, the Vault and common buttons. **Not translated**: messages from the server, text written by the AI, tender titles and anything people typed, and many longer help texts, which stay in English. `npm run i18n` (also run by CI) checks that Hindi and Marathi cover the same texts and that nothing the code asks for is missing. To add a language, copy `web/src/lang/hi.ts`, translate the values, and list it in `web/src/i18n.ts`.
- **Install as an app**: on Chrome, Edge or Android the menu offers **Install app**; on an iPhone use Share > Add to Home Screen. The installed app opens from the phone's home screen. With no network it opens and says it is offline; it never stores tenders, bids or papers on the device (the service worker leaves `/api` alone). There are no push notifications: deadline reminders come by email and the optional webhook.
- **Search** in the tender list needs no AI: every word must match (work, department, place or Tender ID), a word also finds its civil-works synonyms (drain finds nala, sewer, storm water), `"quoted words"` must appear together, `-word` leaves a word out, and amounts become value limits (`bridge above 2 crore`, `road under 50 lakh`). A line under the filters says how the search was read, and a misspelt word gets a "Did you mean" from the words in collected tenders.
- **First run**: the Dashboard shows a short checklist worked out from what is stored (company profile and facts, Vault papers, daily email, first collection, first bid, item catalogue). Administrators can load **sample tenders** (marked "Sample data", never emailed, never sent to the AI) to try every page, and remove them again with any bids started on them.
- **More portals**: Settings > Portals > Add a portal offers suggestions (CPWD and several state portals on the same software). They are **not tested from this server**: Add and check reads the portal and says whether it works.

## People and roles

Sign in with a username and password. Administrators add people in **Settings > People** and give each a role:

| Role | Can do |
|---|---|
| **Viewer** | Look at everything (tenders, bids, charts, the Vault list). Change nothing. Cannot download Vault files. |
| **Bidder** | Everything a viewer can, plus star, start and edit bids, use the Vault and the AI assistant, add tenders by hand, run a collection, keep the company profile and facts. |
| **Admin** | Everything, plus Settings, portals, people, backups and the Activity log. |

- The login in the server variables (`APP_USERNAME` / `APP_PASSWORD`) is the **owner**: always an admin, always able to sign in, and the way back in if every other account is lost. It is not stored in the database and is changed only on the server.
- A password an administrator sets is temporary: the person must choose their own (12 or more characters) before doing anything else. Resetting a password or switching a person off signs them out at once; a role change applies at their next click.
- Passwords are stored as salted scrypt hashes. Signing in has the same limits as before (per address, never a lock-out of the owner).
- **Two-step sign-in** (optional): each person can turn it on under **Account & security** with any authenticator app (Google/Microsoft Authenticator, Authy, 1Password): add the setup key shown, then type a code to confirm. At sign-in a right password is followed by a request for the current code; each code works once. An administrator can reset a lost phone in Settings > People. The secret is stored in the database, so keep backups private. For the owner login set `OWNER_TOTP_SECRET`.
- **Devices** (Account & security) lists where you are signed in ("Chrome on Windows", last used) and signs out any one of them, or all the others.
- **Forgot password**: on the sign-in page, a person types their username or email and gets a link (works once, for an hour) to choose a new password. Needs email set up and an email address on the account (Settings > People); three links an hour per person, and the page says the same thing whether or not the account exists. Using the link signs the person out everywhere. The owner login is changed only on the server. Two-step sign-in is not bypassed by a reset.
- **API tokens** (Settings > Integrations, administrators): for a script or another system. Sent as `Authorization: Bearer tnd_...`. A token is view-only or bidder, never admin, can expire, can be revoked, cannot use account settings, and its actions appear in the Activity log as `token:<name>`. Only a hash is stored; the token is shown once. Wrong tokens count against the same per-address limit as wrong passwords.
- **Outgoing webhooks** (Settings > Integrations): TendAI POSTs a JSON message when a bid is started, changes stage, has a date or corrigendum change, or has its bidders recorded. Each webhook has its own secret and signs every message: `X-TendAI-Signature: sha256=` + HMAC-SHA256(secret, `X-TendAI-Timestamp` + `.` + body). Deliveries wait in a queue and are retried after 1 minute, 5 minutes, 30 minutes and 2 hours before giving up. The address must be https and public; redirects are not followed. Check the timestamp is recent when verifying, to refuse replays.
- **Your data**: anyone can download what is kept about them (Account & security), and an administrator can download or erase a person's data (Settings > People). Erasing needs the account switched off first and the username typed to confirm: it removes the name, email, password, two-step secret and sign-ins, and replaces the person's name on comments, tasks, approvals and the activity log with `erased-N`. The company's records (bids, comments, the log) stay. Names typed by hand into notes are not changed.
- **Retention** (Settings > Backup): how many days the activity log (default 365) and collected tenders (default 120) are kept.
- **Settings > Activity** records who signed in, what each person changed, refusals ("view-only account tried to ...") and downloads of backups, Vault files and the spreadsheet. Reading pages is not logged. Entries are kept for the retention period (default 365 days), and at most the last 20,000.
- Rules are enforced on the server (one table, `required_role` in `tender_digest/accounts.py`); the page only hides what a person cannot use.

## Security

- Everything except the login and a health check needs the password. Cookies are `HttpOnly`, `SameSite=Strict` and signed.
- Logins are kept on the server: **Log out ends the session for good** (a copied cookie stops working), a login unused for `SESSION_IDLE_HOURS` ends, and **changing `APP_PASSWORD` signs everyone out**.
- Wrong-password attempts are limited per address (8 in 10 minutes), and the count survives a restart. Many wrong guesses from many addresses only slow logins down; they never lock the owner out. Use a password of 12 or more characters.
- The portal reader only ever talks to the portal's own host: a link or a redirect to any other address (or to a private network) is refused, so a portal page cannot aim this server elsewhere.
- Headers: strict `Content-Security-Policy` (no inline styles or scripts), HSTS on https, `Permissions-Policy`, `Cross-Origin-Opener-Policy`.
- AI use is capped per day and per minute (`AI_MAX_CALLS_PER_DAY`, `AI_MAX_CALLS_PER_MINUTE`), so a stuck button or a stolen session cannot run up the bill.
- The container runs as an unprivileged user. Its entrypoint starts as root only to give the `/data` volume to that user, then drops privileges. If you set Railway's `RAILWAY_RUN_UID`, the container starts non-root and the entrypoint just runs the app.
- Every Python version is pinned in `requirements.txt`, and CI runs `pip-audit` and `npm audit` on every change and once a week.
- Request bodies are capped: 16 MB on upload routes (a 10 MB file), 1 MB elsewhere. Writes from other sites are refused.
- `/api/health` fails (503) when the database cannot be read or its folder is not writable, so Railway restarts the service. On Railway, the page shows a red notice if the database is not on a mounted volume.
- Spreadsheet downloads neutralise cells that start with `=`, `+`, `-` or `@`, since tender text comes from outside.
- Words typed in Settings are escaped, never used as patterns.
- The owner login is one shared server password; give everyone else their own account. Do not put the owner password in chat; set it only as a Railway variable.
- **Encryption at rest** is on when `DATA_ENCRYPTION_KEY` is set (see the variable above). It covers secrets and uploaded files, not the databases themselves, so use a Railway volume on an encrypted disk (the platform's job) and keep backups private. Settings > Backup shows whether it is on and whether anything is still unencrypted.

## How "civil" is decided

1. Every department's list is read (department names say little: Zilla Parishads, universities and power utilities all tender civil work).
2. Each new tender's detail page is read. It is kept if its title, description or category mentions civil work, and dropped
   if its category is electrical, mechanical, supply, services or similar, or it matches an excluded word.
3. Tenders with no stated value are kept, never silently dropped.

## How it reads the portals

These portals ask for a captcha on their "Active Tenders" search, which this **never tries to get around**. It reads the
public department-wise lists instead. It waits 0.6 s between requests and identifies itself; do not shorten that.
Tender links on these portals expire within minutes, so the page and the email give the **Tender ID** and a link to the
portal's search page; paste the ID there.

Portals are HTML pages, not an API. If one redesigns, runs report it as unavailable and the others carry on. The parsing is
in `tender_digest/gepnic.py`, tested against real saved pages.

### Adding portals from the page

**Settings > Add a portal**: type a name and the portal's https address (it must end in `.gov.in` or `.nic.in`; see `PORTAL_HOST_SUFFIXES`).
The app checks the address straight away.

- **It can read the portal** (a NIC "GePNIC" portal): the portal is switched on and used by Run now and the daily run.
- **It cannot**: the portal is still saved, but it is **not** run. It goes on the **Check these by hand** list (Settings, with a notice
  on the Dashboard and a line in the daily email) with the reason: captcha, login needed, OTP login needed, refuses this server,
  not a portal type the app can read, or not reachable. Open it in your own browser and look yourself.

The reason is worked out from the portal's answer and the wording of its page, so treat it as a good guess. **Check all portals now**
asks every portal again. Every run also records what it found. The app never tries to get past a captcha, a login or an OTP.
Portals added in the page are stored in the database (on the `/data` volume); the built-in ones stay in `tender_digest/portals.json`.
Removing one keeps the tenders already collected from it.

### Not covered yet

- **CPWD** (`etender.cpwd.gov.in`) refused connections (403) from the build environment, probably blocking data-centre
  addresses. It is offered as a suggestion under Settings > Portals > Add a portal: add it from Railway and the check says
  whether it can be read. (CPWD tenders also appear on CPPP, which is covered.)
- **Gujarat** (`tender.nprocure.com`) is different software and closed the connection when contacted. Needs its own reader.
- **Railways**: IREPS needs a mobile-OTP login. Some railway units also publish on CPPP, which is covered.

## Develop

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                  # no network needed

APP_PASSWORD=choose-a-password COOKIE_SECURE=false STATE_PATH=./local.db python -m tender_digest.web   # API on :8000

cd web && npm ci && npm run dev         # page on :5173, forwards /api to :8000
npm run build                           # then the Python service serves web/dist itself
```

Accessibility: `npm run a11y` (in `web/`) opens every screen and dialog in light and dark, on desktop and phone, and runs axe-core (WCAG 2.2 AA). It needs a running app with demo data (`A11Y_URL`, `A11Y_PASS`) and a browser (`npx playwright install chromium`); CI runs it on every change and fails on any violation.

A command-line mode still exists for headless use: `python -m tender_digest --dry-run --portals delhi --preview out.html`
reads live and builds the email without sending; `--send-test` sends a test email.
