# Tender digest

Reads the public tender lists of Indian government e-procurement portals every morning, keeps the **civil works**
tenders, and emails one consolidated digest (with an Excel-friendly spreadsheet attached) to a list of addresses.

| Portal | Covers | Status |
|---|---|---|
| MahaTenders | Maharashtra: PWD, municipal corporations, irrigation, housing | Working |
| Delhi e-Procurement | Delhi government | Working |
| UP eTender | Uttar Pradesh | Working |
| CPPP (eprocure.gov.in) | Central ministries, PSUs, institutes, border forces. **CPWD is not here**, see below | Working |
| CPWD e-tender | Central PWD | **Not reachable** from the build environment (403); see below |
| IREPS (railways) | Indian Railways | **Not possible**: its tender search needs a mobile-OTP login |
| Gujarat (nProcure) | Gujarat | **Not reachable** from where this was built; see "Gujarat" below |

## What the email contains

- **New since the last digest**: each civil tender once, grouped by portal, biggest estimated value first, with the
  work, department, Tender ID, category, estimated value and closing date.
- **Closing in the next few days**: civil tenders already sent that are about to close.
- A note when a portal could not be read, so a quiet day is never mistaken for a broken one.
- The full list as `tenders-YYYY-MM-DD.csv` (opens in Excel). The email body shows the top 25 per portal so it stays
  small enough for Gmail.

Tender links on these portals expire within minutes, so the email gives the **Tender ID** and a link to the portal's
search page; paste the ID there.

## How "civil" is decided

1. Every department's list is read; department names say little about civil work. (`ORG_INCLUDE` can narrow this.)
2. Each new tender's detail page is read. It is kept if its title, description or category mentions civil work
   (road, bridge, building, drain, canal, plaster, repair...), and dropped if its category is electrical, mechanical,
   supply, services or similar, or it matches an exclude word (manpower, housekeeping, software...).
3. Tenders with no stated value are kept, never silently dropped.

All of this is configurable with environment variables (see `.env.example`).

## Deploy on Railway

1. Put this folder in its own GitHub repository and create a **new Railway service** from it.
2. Add a **Volume** to the service, mounted at `/data`. Without it the "already sent" memory is lost between runs and
   the same tenders are emailed again.
3. Set the variables from `.env.example` (`RECIPIENTS`, `BREVO_API_KEY` as a secret, `MAIL_FROM`, `STATE_PATH=/data/tender_state.db`).
4. `railway.json` already sets the schedule: `30 2 * * *` (UTC), which is **8:00 AM India time**. Edit it to change the time.
   Railway runs the job once a day and stops it when it finishes.

To change who gets the email, edit `RECIPIENTS` in Railway. No code change.

### Brevo

- Create an API key and verify the sender address or domain (Brevo > Senders).
- If Brevo's *authorised IP* restriction is on, Railway's outgoing addresses change and sends will be refused
  with an "unrecognised IP" error. Turn the restriction off for this key.
- Check it works before the first real run: `python -m tender_digest --send-test` sends a short test email.

## Run it yourself

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                   # 29 tests, no network needed
python -m tender_digest --dry-run --portals maharashtra --preview preview.html   # reads live, sends nothing
```

`--dry-run` reads the portals and builds the email without sending or marking anything as sent. **The first real run
can take 20 to 40 minutes** because every recent tender's detail page is read, with a pause between requests so the
government servers are not hammered. Later runs only read what is new.

## Things to know

- **Be a good neighbour.** The program waits 0.6 s between requests and identifies itself. Do not shorten that.
- **Captchas are never bypassed.** The portals' main "Active Tenders" search is behind a captcha; this reads the
  public department-wise lists instead. If a portal later puts those behind a captcha too, it is reported in the
  email as unavailable and the other portals continue.
- **Portals change.** These are HTML pages, not an API. If a portal redesigns, the day's email says it could not read
  that portal. The parsing code is in `tender_digest/gepnic.py`, with tests against real saved pages.
- **Confirm on the portal.** The digest is an aid for finding tenders. Eligibility, dates and documents must be checked
  on the portal before bidding.

### Gujarat

Gujarat's tenders are on nProcure (`tender.nprocure.com`), which is not the same software as the four portals above
and closed the connection when contacted from the build environment. It may work from Railway or may need a different
reader; try `curl -I https://tender.nprocure.com` from there. Adding it is a separate small piece of work.

### CPWD

CPWD publishes on its own portal, `etender.cpwd.gov.in`, which refused connections (HTTP 403) from the build environment,
probably because it blocks data-centre addresses. It may behave differently from Railway; try `curl -I https://etender.cpwd.gov.in`
from there. If it works it is the same NIC software and needs one line in `tender_digest/portals.json`.

### Railways

IREPS requires a mobile-OTP login before its tender search will list anything, so it cannot be read unattended.
Individual railway zones and units also publish on the central portal (CPPP), which is covered above.
