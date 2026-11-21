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

### Price advisor and price history

The **Price advisor** on a costed bid turns your cost range into a quote: for each possible quote it shows the profit left and the chance of winning, which is the share of similar past results where the lowest bidder (L1) priced at or above it. A result is one number: L1's price as a percentage above or below the buyer's estimate.

What it learns from decides how good it is, so results from several places are pooled (**Bids > Results > Price history**):

- your own won and lost bids (with the winning price recorded), automatically;
- **award results you import**: paste rows from a spreadsheet or choose a CSV with at least the buyer's estimated cost and the awarded amount (department, work type, number of bidders, award date and tender ID are optional but improve the advice). Column names are matched loosely, amounts can be written `5,00,000`, `Rs. 5,00,000` or `2.5 crore`, dates are day first. A price under a third of the estimate, or over twice it, is left out as a typing error; the same tender imported twice is kept once; a tender you bid on yourself is not counted twice. Public award lists on the portals, or your own records, are good sources: the app does not fetch them itself.

The estimate uses the most specific group with enough results (same department and work type, then department, size, work type, then everything) and **borrows from wider groups** when a group is thin: a group with 2 results barely moves the answer, one with 40 mostly decides it. Newer results count more (half-life two years). It is a small, explainable statistical model on purpose: with a few dozen results a neural network would only memorise noise.

**It tests itself.** With 16 or more dated results, each result is predicted from the earlier ones only, and the card shows how often the real L1 fell in the predicted middle half (about 50% is right) and middle 80% (about 80% is right), and how far its middle guess was off against simply averaging everything. Treat the advice with the confidence that test supports. It is only as good as the results behind it: with fewer than 15 similar results the panel says it is a rough guide, and with fewer than 3 it shows no win chance. It does not know about things outside the numbers (a competitor who needs the work, a rate escalation clause), so it supports the decision and never makes it.

### What to expect: tender volume and tenders that come round again

On the Dashboard, **What to expect** says how many new civil tenders are likely in the next four weeks, and which tenders come round again about this time each year.

**Why it needs its own memory.** Tenders themselves are pruned after 45 days by default, so a year of tenders does not exist to learn from. The app therefore keeps a permanent tiny record: how many new tenders were found each day per department, and a short line for every tender (kept about two years). Both fill from the day this version first runs (and are seeded from the tenders then held), so **a new installation has nothing to forecast for about six weeks, and cannot allow for the seasons for a year**; the card says so rather than guessing.

**Volume.** The next four weeks are the recent weekly level (the last eight observed weeks, newest weighted most) times four, adjusted for the time of year when there is a year of history (this time last year against that year's average, believed only halfway and capped). Weeks when the app was not collecting (no successful run on at least four of the seven days) are left out, never counted as "no tenders". The range comes from how the method missed in the past. **It tests itself**: each four-week window is predicted from the weeks before it and compared with just repeating the last four weeks; the forecast is presented as reliable only when it is at least 3% better, otherwise the card says to treat it as no better than "the same as lately". On simulated histories it proves itself for steady markets (about two times in three) and rarely for steadily growing ones, which it does not model.

**Coming round again.** Tenders published in the next ~75 days of last year's calendar (annual repair and maintenance works, yearly contracts) that have no look-alike from the same department in the last 120 days, matched on department and the words of the title. It needs a year of history, and it is a prompt to watch, not a promise.

### Which bids to submit

On the Bids page (pipeline view), **Which bids to submit** answers a cash question: every bid you submit ties up its EMD until the result, so which of the open bids deserve the free cash you have? Enter the free cash you can put into EMDs (`5,00,000` or `5 lakh`) and your margin. For each open bid (Interested or Preparing, not yet closed) it works out the expected profit, **chance of winning × the estimate × your margin**, and picks the set with the most total expected profit within the cash. This is solved exactly (a knapsack over the EMD amounts), not by a rule of thumb: for example two small EMDs that together earn more are chosen over one large one. Every bid left out says why, and the page says how much more cash would add the best one that was left out.

The chance of winning comes from your win model when it has earned a score, else your overall win rate (after 10 decided bids), else an assumed 20%; each row says which. It is a rough guide: the estimate stands in for the contract value, the margin is the same for every bid, each EMD is assumed to come back, the cash is one pool, and a bid with no EMD on record is counted as needing none (and flagged). It cannot know your capacity, your relationships or strategy. For administrators and bidders only (it shows cash figures).

### Who usually wins here

On a bid's Decide tab, **Who usually wins here** lists the companies that have won tenders like this one (same department and kind of work, widening to the department, the kind of work, then everything when there are fewer than five): their wins and share, the price they usually win at against the estimate (the middle of their winning prices, so one odd price does not bend it), when they last won, and how you have fared against them in your own bids (tenders you both bid on, and how often you finished ahead; ranks decide, or the lower price when there are none). Company names are matched across spellings (M/s Rao Infra Pvt. Ltd. and RAO INFRA PRIVATE LIMITED are one).

It is built from two sources: the bidder lists on your own bids (the bidder ranked first won), and award results you import that name the winner (a column such as *Awarded to*, *Winner*, *Successful bidder* or *Name of contractor* in the Price history import). A tender you bid on and also imported is counted once. Fewer than three winners on record shows no pattern. When 60% or more of the awards went to three firms the page says so, and says what it can and cannot mean: it reports who won, never why.

### Win chance

On a bid's Decide tab (and in Bids > Results) the app can show the chance of winning, learned from **your own won and lost bids**. It is a small, explainable model on purpose: each feature of a bid (work type, department, size, portal, how well the AI said it fitted you, how you priced against the estimate) has a win rate among your past bids, pulled towards your overall win rate in proportion to how few bids it rests on, then combined and damped so overlapping features are not counted twice. The page lists what moved the number ("Department PWD: you won 38 of 43 such bids, which raises it").

**A score is shown only when it has been earned.** It needs 40 decided bids. Then, for each past bid in date order, the model predicts from the bids before it, and the score appears only if those predictions beat simply guessing your overall win rate (Brier score at most 95% of the baseline's). If they do not, the page says your history does not yet show what makes you win and shows no number. On simulated histories with a real difference between departments, the model earns its score about 40% of the time with 40 bids, 55% with 60 and 65% with 100; on histories where nothing matters it wrongly earns one about 12%, 4% and 1% of the time. So a shown score is probably real but not guaranteed, and a missing one means "cannot tell yet", not "no pattern".

It cannot know who else is bidding or what the department wants this year, and it only sees the bids you chose to make. Use it as a hint, never as the decision.

### Reading tender documents you can check

A wrong EMD or completion period costs money, so the document tools are built so that every claim can be checked in a second:

- **Conditions found in the text** (Bid > Checklist > *Find conditions in the tender text*, no AI and no API key needed): fixed rules find the earnest money, bid validity, completion period, defect liability period, performance security, retention, liquidated damages (rate and cap), similar-work and turnover requirements, mobilisation advance, and whether price variation is allowed. Each is shown with **the sentence it came from**. A condition the text does not state is not listed (nothing is guessed), and a fixed-price contract is flagged as a risk. Paste the text or choose a PDF; a scanned PDF has no text layer, and the page says so.
- **The AI reading is checked against the document.** When the AI assistant reads a tender document it must quote the words behind each requirement, penalty and red flag. The app then looks for each quote in the document's own text (read locally from a PDF's text layer) and marks it *found in the document* or *not found word for word: check the document*. A quote that is not really there is the sign of a paraphrase or a mistake. A scanned PDF cannot be checked and is marked *not checked*, never *verified*.
- **Corrigenda** (Bid > Decide > *Record a corrigendum* > *Read it for me*): paste the corrigendum or choose its PDF. The fixed rules list the conditions that differ from the tender's (earlier found with *Find conditions*) or are new, with their sentences; with the AI assistant on it also lists every change with its impact (price, who can bid, dates, documents), what to do, and the new closing date, each change with a checked quote. *Use this as the note* fills in the record form; nothing is recorded or moved until a person presses Record.

The quote check is only as strong as the text it checks against; it proves the words exist in the document, not that the AI understood them. It catches invented and paraphrased quotes, not a correct quote read wrongly.

The BOQ data is stored in `boq.db` next to the main database (set `BOQ_DB_PATH` to change it). **PDF BOQs** are read by rules (the first 80 pages; repeated headers on later pages are dropped) and always flagged for checking; a scan or an unusual layout goes to Claude when `ANTHROPIC_API_KEY` is set, otherwise the app asks for the Excel file. **Lump-sum lines** and items outside the catalogue can't be priced from rates, so each line has an **Enter cost** box: your figure for that line is used in the cost, coverage and break-even.

**Fetch from portal** on a bid also brings in the BOQ and the risks, when the portal gives them without a login: it finds the BOQ file among the tender's documents (a spreadsheet, or a document named BOQ, Bill of Quantities or Price Bid), loads and matches it, and has Claude read the tender notice for risks (once; the notice is kept so you can read it again later without uploading). A BOQ you already loaded is never replaced by a fetch. If the portal asks for a login, OTP or captcha, the app says so and stops: it never tries to get past them. Fetching is per bid and on request, with a pause between requests, not a bulk crawl of every tender.

## Backups

Everything is in the `/data` volume: `tender_state.db` (tenders, bids, settings), `boq.db` (BOQ catalogue) and the `vault` folder (your papers and saved notices).

- **Daily copy**: each day the service copies both databases into `/data/backups` (the last 7 days are kept; `BACKUP_KEEP_DAYS`). Each copy is checked with SQLite's integrity check. This protects against a bad edit or a damaged file, **not** against losing the volume.
- **Download backup** (Settings > Backup): one zip with both databases and every vault file. **Save it somewhere other than Railway** (weekly is a good habit, and before any server change). It contains your company papers, so keep it private.
- **Write-ahead mode and the data folder**: while the app runs, each database has two small side files next to it (`tender_state.db-wal`, `tender_state.db-shm`). They are part of the database, so **never copy or replace only the `.db` file**: use *Download backup* (it uses SQLite's own copy, which includes them). A power cut can lose the last few saves but never corrupts the database; an app crash loses nothing.
- **Restore** (on the server, e.g. `railway ssh`, with the zip uploaded there): `python -m tender_digest.backup restore backup.zip --target-dir /data --force`, then restart the service. The zip's databases are checked first; existing data, including those two side files, is moved aside (`name.before-restore-TIME`), never deleted. If you changed `VAULT_DIR` or `BOQ_DB_PATH`, copy those two items by hand.
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

## Selling it: health, proof of coverage, one deployment per customer

These three exist because a tender service is only worth paying for if it keeps working and says so when it does not.

### Portal health: no silent breakage

A portal can fail without any error: the site moves, refuses this server, or changes its layout so the reader finds nothing and reports "0 new tenders" every day, which looks exactly like a quiet week. Every read of a portal (a daily run, or a Check) is remembered (the last 60 per portal), and a portal is flagged when:

| State | Meaning |
|---|---|
| **Stopped working** | It had worked, and the last 2 reads in a row failed. |
| **Finding nothing** | It answers, but the last 2 daily reads listed nothing, or under a fifth of what it usually lists (it must usually list 10 or more, so a small portal is never flagged). |
| **Not read lately** | The daily run has not read it for 3 days. |

One failed read is only noted, since portals have bad days. A portal that never worked and needs a captcha, login or OTP is on the *check by hand* list, not here. Problems show as a red notice on the Dashboard, in the **Is it working?** column of Settings > Portals, in the daily email (and a day with a problem is never sent as "quiet"), and as the webhook events **`portal.unhealthy`** and **`portal.recovered`**, sent once per change (checked hourly), not every hour. `GET /api/portal-health` gives the same. The thresholds are constants at the top of `tender_digest/health.py`.

### When a portal changes its page: reading by content

Portals are redesigned without notice. A reader that expects "the table with this id, tenders in columns 1 to 5" then finds nothing, and the portal looks quiet. Most changes are small (a column added or moved, a different date format, a renamed table), and the data is all still there, so when the exact layout finds nothing the reader now works out what each column **is** from what it contains:

- a **date column** is one whose cells parse as dates (in the usual Indian formats); of the date columns, a header such as "Closing Date" decides which is which, otherwise the earliest dates are the publish dates, then closing, then opening;
- the **title column** is the one that carries the bracketed `[title] [reference] [tender id]` text, or a tender-ID-looking code; the **organisation** is the longest remaining text;
- a **department list** is a table of rows that each carry a link and a number.

It does not guess tenders into existence: a row needs real dates and a tender ID, and a table with none gives nothing. It also catches the quiet failure where the exact layout still "works" but reads the wrong columns: when most rows close before they were published, the columns are swapped, and the by-content reading is used instead. When it is used, the run notes it (**Layout changed: read by content** in Settings > Portals, with what it did), the portal stays healthy, and it keeps being read. **Settings > Portals > Diagnose** (administrators) looks at the portal's pages right now and says what the reader can and cannot make of them: the tables found, the tenders read by each method, a sample, and a verdict in words, so that a page the fallback cannot read can be reported with a sample (*Download a sample*) and corrected.

This is deliberately not "an AI rewrites the reader": generated code is never run. It handles layout changes of the kinds above, not a portal that moves to a different technology, hides lists behind scripts, or adds a captcha; for those the health check still flags the portal and Diagnose explains what is on the page. Tested against the real saved portal page and altered copies of it (renamed table, columns added or swapped, other date formats, titles without brackets, nested tables), not against live portals.

### Coverage proof: which portals this installation can really read

Whether a portal works depends on where the app runs, so the only proof is a test run on the customer's own server. **Settings > Portals > Coverage proof > Test all switched-on portals** reads each portal in depth: its department list, the tenders of its busiest department, and a few detail pages, then counts what came out. A portal is **Proven** only when nearly every tender listed had an ID and a closing date and a detail page was read; **Partly** means it answers but the reader is missing details; **Answers, no tenders**; or **Cannot be read** with the reason (captcha, login, OTP, refuses this server, not reachable). **Download the report (CSV)** gives the customer a record of what is and is not covered. The same test runs from a terminal on the server:

```
python -m tender_digest.coverage                      # every portal in the list; results are kept and shown in Settings
python -m tender_digest.coverage --catalogue          # also the known portals you have not added (nothing is added or switched on)
python -m tender_digest.coverage --only kerala-etenders,delhi --csv report.csv --no-save
```

It makes a handful of polite requests per portal, so a full catalogue run takes several minutes. **Do not tell a customer a portal is covered until it shows as Proven from their own server.**

### One deployment per customer

Each customer gets their own service, volume and database, so one customer can never see another's tenders or papers. (Many customers in one database is deliberately not built: it needs every query, file, setting, backup and search index separated by customer, and one missed filter would leak a customer's bids to another.) Setting each one up is two commands:

```
python -m tender_digest.provision new --name "Acme Infra" --url https://tend.acme.in \
    --recipients a@acme.in,b@acme.in --mail-from tenders@acme.in [--brevo-key-env BREVO_KEY]
TENDAI_PASSWORD=... python -m tender_digest.provision verify --url https://tend.acme.in
```

`new` writes `customers/acme-infra/`: a settings file to paste into the new service (a fresh password, signing key and encryption key, generated and never reused; readable by you only; the folder is kept out of git and nothing secret is printed), and a go-live checklist for that customer. `verify` checks the running site from outside and prints PASS, WARN or FAIL for: https, login, the data folder being on a volume, encryption, a daily backup, off-site backup, email being set up, portals proven readable, and no portal having stopped working. It exits 1 if anything must be fixed before handing over, and takes the password from the environment so it stays out of shell history. A new site will show WARN for backups until the first night has passed, and for the logo until one is uploaded.

### Company logo on the sign-in screen

Settings > AI & logo > **Logo picture** (administrators): choose a PNG, JPEG or WebP file under 400 KB (200 pixels wide is plenty; a transparent background looks best). It is saved as soon as you choose it, replaces the letter tile on the sign-in screen and in the menu, and becomes the browser-tab icon. **Remove the picture** brings the letters back. SVG and GIF are refused on purpose (an SVG can carry scripts), and the file is judged by its contents, not its name. The picture is public (the sign-in screen has to show it before anyone signs in), is kept in the database so backups carry it, and changes are in the activity log. The *Logo letters* and company colour settings still apply.

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
| `SSO_ISSUER`, `SSO_CLIENT_ID`, `SSO_CLIENT_SECRET` | Optional (all three or none) | Turns on single sign-on with any OpenID Connect provider (Google Workspace: `https://accounts.google.com`; Microsoft Entra ID: `https://login.microsoftonline.com/<tenant-id>/v2.0`; Okta, Keycloak, Auth0). Secret. See *Single sign-on* below. |
| `SSO_ALLOWED_DOMAINS` | With SSO | Email domains allowed to sign in, e.g. `example.com,example.org`. **Without it nobody is let in.** |
| `SSO_DEFAULT_ROLE` | Optional | `viewer` (default) or `bidder`, for people created by their first SSO sign-in. Never `admin`. |
| `SSO_BUTTON_TEXT` | Optional | Default `Sign in with SSO`; e.g. `Sign in with Google`. |
| `DATA_ENCRYPTION_KEY` | Optional | A long private phrase (16+ characters). When set, two-step and webhook secrets and every uploaded file (the Vault and saved tender notices) are stored encrypted, and anything stored earlier is encrypted at the next start. **Keep it somewhere other than the server: without it those files, and a restored backup, cannot be read.** Secret. |
| `DATA_ENCRYPTION_KEY_OLD` | Optional | To change the key: set the new one in `DATA_ENCRYPTION_KEY` and the old one here, restart, then remove this. Commas separate several old keys. |
| `DB_WAL` | Optional | `1` (default) runs both databases in SQLite's write-ahead mode, which saves much faster (about 8 times on 1,500 tenders). Set `0` only if your storage cannot do it (some network file systems); the app then logs nothing wrong and simply saves more slowly. See *Backups* for what WAL means for files. |
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

- **Not interested**: select tenders and press **Not interested** (or use the button in a tender's panel) to hide them from the list, the counts, the charts, saved-search emails and the daily digest. You can also hide a tender's whole **department**; new tenders from it are hidden as they arrive. **Show ignored** in the filter bar lists what is hidden, and **Restore** brings tenders back. Hidden departments are managed in Vault > Company. A tender hidden by you stays hidden until restored; it is forgotten along with its tender when retention removes it.
- **Fits my size**: set the smallest and largest tender you take in Vault > Company (the largest can come from your *Bid capacity* fact). The **Fits my size** button in the tender list then hides tenders outside that range; a tender with no stated value is always kept. Saved views and email alerts can use it too.
- **Clean up closed tenders** (Settings > Backup): shows how many closed tenders older than N days would go (never starred ones or ones with a bid, which stay), plus the placeholders kept for tenders judged not civil, and only deletes after you confirm.
- **Colours**: the menu offers light, dark or automatic, and eight accent colours (blue, indigo, teal, emerald, violet, rose, amber, slate) that recolour tabs, links, buttons and highlights. A person's choice is kept in their browser; an administrator sets the **company colour** everyone else sees (Settings > AI & logo), which also shows on the sign-in screen. `npm run colours` (also run by CI) checks every accent pair in light and dark against WCAG: text and button labels 4.5:1, focus rings and underlines 3:1. To add a colour, add its three blocks to `web/src/styles.css`, its entry to `web/src/theme.ts` and its name to `ACCENTS` in `tender_digest/appsettings.py`; the check fails if it is too faint.
- **Short windows**: the left menu scrolls when the window is short (a laptop with a bookmarks bar, a phone held sideways), the rarely used theme, language and colour choices fold under **Appearance**, filter panels slide or open upward to stay inside the window, and dialogs and panels scroll inside themselves. A row of tabs that scrolls sideways on a phone shows a soft edge where there is more and keeps the chosen tab in view. `npm run fit` (run by CI) opens each of these on four small window sizes and fails if one is cut off or Log out cannot be reached.
- **Type**: 14px body text on the page, 12px for secondary text and nothing smaller, 16px for form fields on phones (below that, iPhones zoom the page when a field is focused), and more line height for Hindi and Marathi.
- **Hindi and Marathi**: choose the language under the menu (bottom of the sidebar) or on the sign-in page; it is remembered in that browser and the page reloads. The page's own words are translated: menus, sign-in, the dashboard, the tender list and filters, the main bid screens, the Vault and common buttons. **Not translated**: messages from the server, text written by the AI, tender titles and anything people typed, and many longer help texts, which stay in English. `npm run i18n` (also run by CI) checks that Hindi and Marathi cover the same texts and that nothing the code asks for is missing. To add a language, copy `web/src/lang/hi.ts`, translate the values, and list it in `web/src/i18n.ts`.
- **Install as an app**: on Chrome, Edge or Android the menu offers **Install app**; on an iPhone use Share > Add to Home Screen. The installed app opens from the phone's home screen. With no network it opens and says it is offline; it never stores tenders, bids or papers on the device (the service worker leaves `/api` alone). There are no push notifications: deadline reminders come by email and the optional webhook.
- **Search** in the tender list needs no AI: every word must match (work, department, place or Tender ID), a word also finds its civil-works synonyms (drain finds nala, sewer, storm water), `"quoted words"` must appear together, `-word` leaves a word out, and amounts become value limits (`bridge above 2 crore`, `road under 50 lakh`). A line under the filters says how the search was read, and a misspelt word gets a "Did you mean" from the words in collected tenders.
- **Fast search**: search runs on a full-text index inside the database (SQLite FTS5), so it stays quick with tens of thousands of tenders (30,000 tenders: about 9 ms against about 900 ms reading every row) and answers exactly as the row-by-row search did (a test compares the two on about fifty queries). The index is kept in step by the database itself and is rebuilt on start-up if it ever drifts. When searching, **Best text match** appears in the sort list and puts tenders with the words in the title first. If a server's SQLite has no FTS5, the old row-by-row search is used automatically. Nothing a person types is read as search syntax.
- **Recommended order** (the default sort): every open tender gets a priority from 0 to 100, added up from parts you can read: **closing time** (up to 25: a week or two is ideal, under a day is too late, far away can wait), **fit** (up to 25: the AI's 1 to 5 score when the AI assistant has labelled it), **size** (up to 15: inside the range set in Vault > Company), **what you have chosen** (-20 to +25, below) and **starred** (10). Each card says why ("closes in 7 days: time to prepare; good fit (5/5); you often bid on or star PWD") and the tender's panel shows the same with its number. Choose any other sort to leave it. It is re-worked after every star, bid, hide and size change, after each run, and hourly (closing times move).
- **It learns from your choices**: tenders you star, start a bid on (or take to a later stage) count as chosen; tenders you hide, whole departments you hide and bids you drop count as avoided. For each thing a tender has (department, category, type of work, place, portal and the main words of its title) it counts how often you chose or avoided tenders with it, with a small allowance (3) so one stray click decides nothing. Nothing is learned until there are 5 choices. **Vault > Company** lists what it picked up in plain words, and **Settings > Civil filter** switches learning off (the order then uses only closing time, fit, size and stars). It is a count, not a hidden model: no AI, and nothing leaves the server.
- **Add many portals at once** (Settings > Portals > Add more portals): a tick-list of 31 known government portals (state and central portals on the same software as the built-in ones, plus GeM, IREPS and nProcure with their limits noted). Ticking and pressing **Add and check** saves them without contacting anything, then checks each one in turn, and only switches on the ones this server can read; the rest go on the check-by-hand list with the reason. **The addresses are from public knowledge and none has been opened from this build environment**, so any may be wrong, moved or refuse data centres: that costs one failed check, nothing more. More portals make the daily collection longer, so add the ones you bid in.
- **Money in bids** (the **Money** tab on a bid, and a card on the Dashboard): record each payment for a bid, whether a tender or portal fee, earnest money (EMD), performance security or retention, with how it was paid (online, draft, bank guarantee, fixed deposit), the reference and bank, the date, when a guarantee runs out and when a refund is expected. Status moves from planned to paid to refunded, forfeited or adjusted. The app works out **cash locked in bids** (refundable money paid by online, draft or deposit and not yet back) and **bank guarantees in use** (a bank limit, counted apart), and flags what needs a person: a refund overdue, a lost or dropped bid whose EMD is still held, a guarantee, draft or deposit running out within 30 days or already out, and an EMD not yet paid when the bid (or its EMD due date) is within 3 days. The urgent ones also appear under *Your bids* in the daily email. **Download spreadsheet** gives one row per payment. It records what a person types: nothing talks to a bank. Bidders and administrators only: view-only accounts cannot see it (it holds guarantee and draft numbers). Changes appear in the activity log by name.
- **First run**: the Dashboard shows a short checklist worked out from what is stored (company profile and facts, Vault papers, daily email, first collection, first bid, item catalogue). Administrators can load **sample tenders** (marked "Sample data", never emailed, never sent to the AI) to try every page, and remove them again with any bids started on them.
- **More portals**: Settings > Portals > Add a portal offers suggestions (CPWD, several state portals on the same software, GeM, IREPS and nProcure; see *Not covered yet* for what each can and cannot do). They are **not tested from this server**: Add and check reads the portal and says whether it works, and *Download a sample* saves what a portal returned.

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
- **Retention** (Settings > Backup): how many days the activity log (default 365) and collected tenders (default 45) are kept. Tenders are counted from when they were found and only removed once closed: an open tender, a starred one and one with a bid are never removed by retention (a bid also keeps its own copy).
- **Settings > Activity** records who signed in, what each person changed, refusals ("view-only account tried to ...") and downloads of backups, Vault files and the spreadsheet. Reading pages is not logged. Entries are kept for the retention period (default 365 days), and at most the last 20,000.
- Rules are enforced on the server (one table, `required_role` in `tender_digest/accounts.py`); the page only hides what a person cannot use.

## Single sign-on

With SSO, people sign in with the company's own account (Google Workspace, Microsoft Entra ID, Okta, Keycloak...) instead of a separate password here, and the provider's two-step check applies.

1. In the provider, register a **web application** with the redirect address shown in **Settings > Integrations > Single sign-on** (`<APP_URL>/api/sso/callback`; set `APP_URL` so it is right) and note its client ID and secret.
2. Set `SSO_ISSUER`, `SSO_CLIENT_ID`, `SSO_CLIENT_SECRET` and `SSO_ALLOWED_DOMAINS` on the server and restart. A "Sign in with SSO" button appears on the sign-in page.
3. The first sign-in of a person creates their account with `SSO_DEFAULT_ROLE`; an existing account with the same email address is linked instead of duplicated. An administrator changes roles in Settings > People, and switching a person off keeps them out even though the provider still signs them in. SSO accounts have no local password.

How it is checked: authorization-code flow with PKCE, the one-time state and nonce, the ID token's signature against the provider's published keys (RS256 or ES256 only; `none` and shared-secret algorithms are refused), and its issuer, audience and expiry. Only email addresses the provider marks as verified, at an allowed domain, get in. The owner login (`APP_USERNAME`/`APP_PASSWORD`) keeps working, so a provider outage or a wrong setting never locks everyone out. Tested against a fake provider (all the ways a token can be wrong) and in a real browser round trip; **not yet tried against Google, Microsoft or Okta themselves**.

## WhatsApp assistant

Ask the app a question from your phone and get the answer in the chat. Send **closing** (tenders closing in 3 days), **new** (found since yesterday), **bids** (your bids in progress), **deadlines** (what is coming up on your bids), **find drain nashik** (a search), **bid 2026_PWD_123** (how one bid stands) or **cash** (money locked in EMDs: bidders and administrators only). With the AI assistant on, a plain question ("any drain tenders near Nashik this week?") is turned into a search. It **only reads**: nothing a message says can change a bid, a setting or any data.

It uses Meta's WhatsApp Business Cloud API. Meta calls `https://<your site>/api/whatsapp/webhook` for each message to your business number. The app accepts a call only if it is signed with your **app secret** (anything else is refused), answers only numbers you have **listed** (any other number gets no reply at all), ignores a message Meta resends, and drops a number that sends more than 20 messages in 10 minutes. Cash figures need a number listed as `bidder` or `admin`, like the same figures in the app. Settings > Integrations shows whether it is set up; the numbers appear only as their last four digits.

**Set up**
1. At developers.facebook.com create an app of type *Business* and add the **WhatsApp** product. Note the **Phone number ID** of your business number (Meta gives a test number to start with) and, under App settings > Basic, the **App secret**.
2. Create a permanent access token (a *system user* with the whatsapp_business_messaging permission).
3. Set these variables on the service and restart:

   | Variable | Purpose |
   |---|---|
   | `WHATSAPP_VERIFY_TOKEN` | A phrase you choose; Meta sends it when you register the webhook. |
   | `WHATSAPP_APP_SECRET` | The app secret. Secret. |
   | `WHATSAPP_ACCESS_TOKEN` | The permanent token, for sending replies. Secret. |
   | `WHATSAPP_PHONE_NUMBER_ID` | The business number's id. |
   | `WHATSAPP_ALLOWED` | Who may ask, `country code first, no +`, with a role: `919876543210:bidder,919812345678:viewer`. A number with no role is a viewer. |
   | `WHATSAPP_API_VERSION` | Optional, default `v21.0`. |

4. In the Meta app, WhatsApp > Configuration: set the callback URL to `https://<your site>/api/whatsapp/webhook` and the verify token to yours, then subscribe to the **messages** field. Set `APP_URL` so Settings shows the full address.
5. From a listed phone, send *help* to the business number.

**Be aware.** This has been built to Meta's documented message formats and signature rules and tested with stand-ins (signed sample messages, a fake sender); it has **not been tested against a live WhatsApp account**, so try it with your own number before telling anyone it works. Replies are sent inside the 24 hours after the person's own message, which Meta allows as free-form text; the app never starts a conversation. Meta's own pricing and business-verification rules apply and can change. WhatsApp messages are not end-to-end private from Meta once they reach the business API, so the answers include tender titles and values but never papers from the Vault.

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
- **GeM** (`bidplus.gem.gov.in`, Government e-Marketplace) has an **experimental reader** (`tender_digest/gem.py`): it searches the public bid page for ten civil-works words, reads the rows the page itself loads, and keeps only civil bids. It was written from how that page behaves **without being able to reach it from the build environment, so it is unproven**. Add it under Settings > Portals > Add a portal (suggestion list). If the check fails, *Download a sample* on its row saves exactly what GeM returned, which is what is needed to correct the reader. GeM bids carry no estimated value, and there is no corrigendum list to watch.
- **Gujarat** (`tender.nprocure.com`) is different software and has **no reader**. It needs `.nprocure.com` added to `PORTAL_HOST_SUFFIXES` before it can be added at all; once added, *Download a sample* saves what it returns so a reader can be written from real data rather than guessed.
- **Railways (IREPS)**: its tender lists need a mobile-OTP login. The app never gets past a login, captcha or OTP, so **no reader will be built**: IREPS stays on the check-by-hand list, and a tender is brought in with *Add a tender* (paste or upload its notice). Some railway units also publish on CPPP, which is covered.

## Develop

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                  # no network needed

APP_PASSWORD=choose-a-password COOKIE_SECURE=false STATE_PATH=./local.db python -m tender_digest.web   # API on :8000

cd web && npm ci && npm run dev         # page on :5173, forwards /api to :8000
npm run build                           # then the Python service serves web/dist itself
```

Where the code is: `tender_digest/web.py` builds the app once (`create_app`: the stores, settings, sign-in and the helpers every route shares) and then calls
`routes.register_all`. The routes themselves are one module per area in `tender_digest/routes/` (`bids.py`, `money.py`, `portals.py`, `tenders.py` and so on, each
with a `register(app, ctx)`), the shapes of what the API receives are in `tender_digest/api_common.py`, and `routes/context.py` lists what the shared setup
holds. To add a route, put it in the module for its area, or add a module and list it in `routes/__init__.py` (a test fails if a module is left out). Order matters
only where two paths could match one address (a fixed `/api/bids/stats` before `/api/bids/{bid_id}`); a test covers the known cases.

Code checks (the same ones CI runs, settings in `pyproject.toml`, tool versions pinned in `requirements-dev.txt`): `ruff check .` (mistakes, likely bugs, old syntax, import order) and `mypy` (types). Both must stay clean. A "noqa" or "type: ignore" comment is allowed only with its reason beside it. CI runs the tests on Python 3.12 (what the Docker image uses) and 3.13, which bundle different SQLite versions. Dependabot proposes weekly updates for the pinned Python and npm packages, and monthly for GitHub Actions and the Docker base images.

Frontend unit tests: `npm test` (in `web/`, Vitest, no browser needed) covers the page's plain logic: date and rupee formatting, parsing an amount typed as "50 lakh", "closes in 3 h" wording, and the filters with their web addresses (a filter must survive the trip to a link and back). CI runs it on every change. Anything that needs a screen is covered by the accessibility check below.

Accessibility: `npm run a11y` (in `web/`) opens every screen and dialog in light and dark, on desktop and phone, and runs axe-core (WCAG 2.2 AA). It needs a running app with demo data (`A11Y_URL`, `A11Y_PASS`) and a browser (`npx playwright install chromium`); CI runs it on every change and fails on any violation.

A command-line mode still exists for headless use: `python -m tender_digest --dry-run --portals delhi --preview out.html`
reads live and builds the email without sending; `--send-test` sends a test email.
