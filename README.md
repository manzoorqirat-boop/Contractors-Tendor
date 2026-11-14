# TendAI (tender dashboard)

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
- **Dashboard**: summary numbers and charts for whatever the filters select. The Tenders/Value switch changes every chart.
  Click a number or bar to open the tenders behind it.
- **Explore**: pick a combination (one click) or build your own: *group by* one thing and *split by* another (portal, category,
  department, location, value band, how soon it closes, closing week or day, day found, day published), measured as number of
  tenders, total value or average value, shown as stacked bars, grouped bars or a shaded table. Every bar and cell opens exactly
  the tenders it counts. Tables can be downloaded.
- **Tenders**: cards or a table, sortable, with a detail panel, stars for a watchlist, and a spreadsheet download.

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
| `ANTHROPIC_MODEL` | Optional | Default `claude-sonnet-5-5`. |
| `MAIL_FROM_NAME` | Optional | Default `Tender Digest`. |
| `COOKIE_SECURE` | Optional | Default `true`. Set `false` only for local testing without HTTPS. |
| `TZ_NAME` | Optional | Default `Asia/Kolkata`. |
| `MIN_VALUE`, `LOOKBACK_DAYS`, `CLOSING_SOON_DAYS`, `CIVIL_KEYWORDS`, `EXCLUDE_KEYWORDS`, `ORG_INCLUDE`, `PORTALS`, `MAX_DETAIL_FETCHES`, `REQUEST_DELAY` | Optional | Filter and politeness tuning; see `tender_digest/config.py`. Most can also be set in the page. |

`PORT` is set by Railway. **The Brevo key and the password are never shown in the page or sent to the browser.**

### Brevo

- Create an API key and verify the sender address or domain (Brevo, Senders).
- If Brevo's *authorised IP* restriction is on, Railway's outgoing addresses change and sends are refused with an
  "unrecognised IP" error. Turn the restriction off for this key. The page shows Brevo's reason if a send is refused.

## Security

- Everything except the login and a health check needs the password. Cookies are `HttpOnly`, `SameSite=Strict`, signed, 7 days.
- Wrong-password attempts are rate limited. Writes from other sites are refused.
- Spreadsheet downloads neutralise cells that start with `=`, `+`, `-` or `@`, since tender text comes from outside.
- Words typed in Settings are escaped, never used as patterns.
- It is a single shared password, not per-user accounts. Do not put it in chat; set it only as a Railway variable.

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

### Not covered yet

- **CPWD** (`etender.cpwd.gov.in`) refused connections (403) from the build environment, probably blocking data-centre
  addresses. Try `curl -I https://etender.cpwd.gov.in` from Railway. If it works it is the same NIC software and needs
  one line in `tender_digest/portals.json`.
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

A command-line mode still exists for headless use: `python -m tender_digest --dry-run --portals delhi --preview out.html`
reads live and builds the email without sending; `--send-test` sends a test email.
