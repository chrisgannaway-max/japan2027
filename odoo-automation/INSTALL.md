# Installing Night Audit to Odoo

Two programs in one repository:

* **the portal** (`portal/`) — the website. Hotel managers upload the night-audit pack and
  vendor invoices; the office downloads the day's entries or sends them straight to Odoo.
* **the reader** (`pms_to_odoo/`) — the parsers and a command line. The portal calls it, and
  you can run it on its own to see exactly what a report turns into.

Nothing costs money until section 7. Everything before that, including e-mail and posting to
Odoo, can be tested with no account anywhere.

| Section | You end up with |
|---|---|
| 1–2 | It runs on your machine and parses a real report |
| 3 | The hotels, logins and account mappings are loaded |
| 4 | E-mail and Odoo tested for free |
| 5–6 | PostgreSQL and Supabase Storage, so nothing lives on one disk |
| 7–8 | Running on a host the hotels can reach |
| 9–11 | Mail service, Odoo, and what the daily routine looks like |
| 12–15 | Backups, upgrades, troubleshooting, every setting |

---

## 1. Install it

**Needs:** Python 3.11 or newer, and `git`. Nothing else is required.

```bash
git clone git@github.com:chrisgannaway-max/night-audit-odoo.git && cd night-audit-odoo
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt          # requirements-dev.txt as well, to run the tests

# Optional: OCR, for invoices that are photographs or scans with no text layer.
# Skip it and every other feature still works; scanned invoices are simply typed in by hand.
sudo apt-get install tesseract-ocr       # macOS: brew install tesseract
```

Start it:

```bash
cp config/properties.example.yaml config/properties.yaml
cp config/users.example.yaml       config/users.yaml
cp config/expense_categories.example.yaml config/expense_categories.yaml

PORTAL_SECRET=$(python3 -c "import secrets;print(secrets.token_hex(32))") \
  python3 -m portal serve --port 8000
```

The first line it prints tells you which backends it picked:

```
[portal] config from yaml; database: SQLite at ./data/portal.db; files: local disk at ./data
```

Open <http://localhost:8000> and sign in as `admin` / `admin`.

**Check it works:** on the Night audit page, upload one of the sample PDFs. You should get
*Balanced, ready to post* and a debit total. If you have no sample to hand,
`python3 -m pytest -q` runs the suite against the bundled fixtures: 95 pass and 1 skips, because
the skipped one reads the original sample PDFs, which are deliberately not in the repository.

**Before anyone else can reach it**, replace the example passwords. `python3 -m portal hash
'the real password'` prints a hash; paste it into `config/users.yaml` as `password_hash`. The
file never contains a password in the clear.

---

## 2. Understand the switches

Everything in this document is the same program with different settings. Two variables decide
its shape, and both default to the simplest thing:

| Variable | Default | Set it when |
|---|---|---|
| `PORTAL_STORE` | `yaml` — hotels, logins and mappings come from `config/` | the client wants to add a property or reset a login themselves, in the browser |
| `DELIVERY_MODE` | `download` — the office downloads a CSV and imports it | Odoo's API is available and you want the portal to post directly |

Two more decide what the site *is*:

| Variable | Default | Set it when |
|---|---|---|
| `HOTEL_UPLOADS` | `on` — hotels sign in and upload | packs arrive by e-mail instead; `off` closes the upload page and hides its link |
| `INVOICES` | `off` | the client wants vendor bills read as well as night audits |

`HOTEL_UPLOADS=off` hides the hotel side rather than removing it. A group that later wants its
GMs to see their own nights turns one variable back on, which is far cheaper than building it a
second time. A manager who signs in while it is off gets a plain line telling them packs are sent
by e-mail, rather than a redirect to a page that is not there.

Leave the first two alone to start. Section 8 turns on the configuration store; section 10 turns
on posting to Odoo.

---

## 3. Load the hotels, the logins and the mappings

**`config/properties.yaml`** — one block per hotel: its code, name, which PMS it runs, how to
recognise its reports, and the Odoo company/journal/analytic account to book to. The example
file documents every field.

**`config/gl_mapping/<property>.yaml`** — which report line goes to which GL account. Start
from the example for that PMS (`hilton_pep.example.yaml`, `choice_advantage.example.yaml`,
`ihg_hotelkey.example.yaml`, `ihg_opera.example.yaml`, `marriott_agilysys.example.yaml`,
`wyndham_synxis.example.yaml`) and change the account numbers to the client's chart of accounts.

You do not have to write these by hand, and for a new property you should not. Upload a report
and the portal lists exactly the lines it could not map, with a box for each account number;
saving writes the rules back into the YAML. For the first property on each PMS, the faster route
is the account-mapping worksheet: the client fills in one column per tab, and **Import a filled
mapping worksheet** on `/admin` turns every tab into rules in one go. It reads a workbook whose
tabs are named after the PMS and whose row 4 carries the headers *Section*, *PMS code*, *Line as
printed on the report* and *Your account code*.

**`config/users.yaml`** — who can log in, their role (`admin` or `manager`) and which properties
a manager may upload for. Managers see only their own hotels.

Sanity check before going further:

```bash
python3 -m pms_to_odoo check                     # config valid?  Odoo reachable?
python3 -m pms_to_odoo inspect --file report.pdf # every line the reader found
```

---

## 4. Test e-mail and Odoo without signing up for anything

### E-mail

Run a mail catcher in one terminal:

```bash
python3 -m aiosmtpd -n -l 127.0.0.1:8025
```

and point the portal at it:

```bash
SMTP_HOST=127.0.0.1 SMTP_PORT=8025 SMTP_SECURITY=none \
SMTP_FROM=nightaudit@example.com PORTAL_BASE_URL=http://localhost:8000 \
PORTAL_SECRET=dev python3 -m portal serve
```

Every message the portal sends is printed in the first terminal, password-reset links included.
That proves the whole forgotten-password flow before anyone pays a mail provider.

### Odoo

Odoo Community is free and has no API restriction. One command gives you a throwaway copy:

```bash
docker run -d --name odoo-db -e POSTGRES_PASSWORD=odoo -e POSTGRES_USER=odoo postgres:16
docker run -d --name odoo -p 8069:8069 --link odoo-db:db odoo:17
```

Open <http://localhost:8069>, create a database, install **Invoicing**, then create a user with
Accounting rights and an API key under *Preferences → Account Security*. Point the portal at it:

```bash
ODOO_URL=http://localhost:8069 ODOO_DB=<database> ODOO_TRANSPORT=xmlrpc \
ODOO_USER=<login> ODOO_API_KEY=<key> DELIVERY_MODE=odoo python3 -m portal serve
```

`python3 -m pms_to_odoo check` says whether it connected.

---

## 5. PostgreSQL instead of SQLite

Skip this if the portal will live on one machine with a disk that stays put. Do it if the host
replaces the container on each deploy, or you ever want two copies running.

```bash
export DATABASE_URL=postgresql://user:password@host:5432/dbname
```

That is the whole change -- the driver is in `requirements.txt`, because the moment that variable
is set it is not optional, and a container that has to be told to install it separately fails on
its first start with a missing module. Tables are created on first start, and the startup line will say
`database: PostgreSQL ...` with the password masked. The same test suite passes on both, so
this is a supported configuration rather than a hopeful one.

**Where Supabase keeps the connection string:** the **Connect** button at the top of the project
dashboard, not in Settings. It offers a direct connection, a session pooler (5432) and a
transaction pooler (6543).

Prefer the **session pooler**. All three work, though: prepared statements are switched off for
PostgreSQL (`prepare_threshold = None`), which is what would otherwise break behind a
transaction-mode pooler -- the statement belongs to a server connection the next query may not
get, and it fails as `prepared statement already exists` under load rather than at once. A night
audit is a few dozen queries a day, so preparing them buys nothing and rules out a connection
string for no reason.

If a connection simply times out, that is the other Supabase trap: direct connections and the
pooler are reachable over IPv6, and over IPv4 only with the add-on. Try a different one of the
three before assuming the credentials are wrong.

---

## 6. Supabase Storage instead of the local disk

The portal keeps every pack and every invoice, because they are the evidence behind an
accounting entry. On one machine the data directory is fine. On a replaceable container they
belong in object storage.

In the Supabase dashboard: **Storage → New bucket**, name it `night-audit`, and leave **Public
bucket off**. Then **Settings → API Keys** for the URL and a server-side key.

Supabase has two generations of key and either works here. The legacy `service_role` key is a
JWT; the newer `sb_secret_...` keys are not, and Supabase rejects those in an `Authorization:
Bearer` header. The adapter always sends `apikey`, which the gateway reads for both, and adds
`Authorization` only when the key really is a JWT — so you can paste whichever your project
shows without thinking about it.

```bash
export SUPABASE_URL=https://<project>.supabase.co
export SUPABASE_SERVICE_KEY=<the service_role key>
export SUPABASE_BUCKET=night-audit
```

All three or none: with any one missing, files go to the data directory as before. The startup
line will say `files: Supabase Storage bucket 'night-audit' ...`.

The service key bypasses Supabase's row-level security. It belongs in the host's environment
and must never reach the repository or the browser.

Nothing else changes. A file's location is recorded as `supabase://bucket/key`, and rows written
before you switched keep working, because a plain path is still read from disk.

---

## 7. Put it somewhere the hotels can reach

| What | Service | Plan | Cost |
|---|---|---|---|
| Hosting | Render, Railway or Fly.io, from the `Dockerfile` | 2 GB instance | ~$25/mo |
| Database and files | Supabase | Pro | ~$25/mo |
| E-mail | Resend or Postmark | free tier is enough | $0 |
| Domain | any registrar | — | ~$15/yr |

A free hosting tier is fine for a demonstration: it sleeps when idle and wakes on the first
request, which is only awkward if someone is watching. Supabase's free tier is a real Postgres
database and a real bucket, so you can run the exact production shape at no cost first — the
paid plan is the same thing with backups and no pausing. Do not run OCR on a 512 MB instance;
rendering a scanned page at 300 dpi will exhaust it.

The repository has a `Dockerfile` that installs Tesseract and serves on port 8000. Most hosts
need nothing but the repository and these variables, set in their dashboard, never in the repo:

```
PORTAL_SECRET=<64 random hex characters>
PORTAL_BASE_URL=https://<the public address>
PORTAL_SECURE_COOKIES=always
PORTAL_STORE=db
PORTAL_MFA_ROLES=admin

DATABASE_URL=postgresql://...
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_SERVICE_KEY=<service role key>
SUPABASE_BUCKET=night-audit
```

`PORTAL_SECRET` signs the session cookies. If it is not set, a random one is generated at
startup, which means **everyone is logged out every time the app restarts**. Set it once and
leave it; changing it later logs everyone out on purpose.

Health check for the host: `GET /health` returns `ok`.

### On Render specifically

*New → Web Service*, connect the GitHub repository, then:

| Setting | Value |
|---|---|
| Language | **Docker** |
| Branch | whichever branch holds the code |
| Instance Type | Free to try it; **Standard (2 GB)** before anyone relies on it, and required if you want OCR |
| Health Check Path | `/health` |
| Persistent Disk | none — that is what the database and storage settings are for |

Add the environment variables above under *Environment*, and let Render generate `PORTAL_SECRET`
for you (*Generate* next to the value) so it is never typed anywhere.

The container listens on whatever port Render gives it (`$PORT`, falling back to 8000), so there
is nothing to configure there. Pushing to the chosen branch redeploys automatically.

A free instance sleeps after about fifteen minutes and takes a few seconds to wake. Harmless
for a trial, wrong for hotels uploading at 3 a.m. — a night auditor will read the delay as the
site being broken and go back to the spreadsheet.

---

## 8. Move the configuration into the database

With `PORTAL_STORE=db`, the hotels, logins and mappings live in the database and are edited on
the Settings page instead of in `config/`.

**A fresh database seeds itself on the first start**, from the same config files, and says so:

```
[portal] empty database: seeded 6 properties and 7 users from the config files -- change the passwords now
```

It has to: the logins live in the database, and the button that fills it is behind the login, so
an empty database with no seeding is a locked door. Take that message seriously — until you
change them, the example passwords are live on a public address.

To re-import later, after editing the config files, use the **Import from the config files**
button on `/admin`, or a shell if the host gives you one:

```bash
python3 -m portal seed [--overwrite]
```

From then on: add a property, change its report id, Odoo company, journal or analytic account,
edit its mapping YAML (validated on save, live immediately), add a login or reset a password —
all in the browser. The `config/` files stay as documentation and as the seed for a fresh
install.

---

## 9. E-mail for real

Used for password resets, and for the daily note about who has not uploaded.

Ask the client for a **subdomain**, for example `mail.champion-hotels.com`, and add the mail
service's DNS records there. Do not touch the root domain's SPF record: they run Microsoft 365
and a wrong edit there stops their ordinary business e-mail.

Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_SECURITY`
(`starttls`, `ssl` or `none`) and `PORTAL_BASE_URL`. The **E-mail setup** page under `/admin`
holds the same settings for whoever runs this day to day, and has a *Send a test message*
button.

Anything set as an environment variable on the host is shown locked on that page and cannot be
changed from the browser, so a pinned credential stays pinned. The password is never rendered
back into the page.

---

## 10. Connect Odoo

| Variable | Meaning |
|---|---|
| `ODOO_URL` | `https://company.odoo.com`, or the on-premise address |
| `ODOO_API_KEY` | API key of a dedicated bot user (*Preferences → Account Security*) |
| `ODOO_DB` | database name; needed when the server hosts several |
| `ODOO_TRANSPORT` | `json2` (default, Odoo 19+), `xmlrpc` (Odoo 18 and earlier), or `demo` (a stand-in, below) |
| `ODOO_USER` | the bot user's login, `xmlrpc` only |
| `DELIVERY_MODE` | `odoo` to offer *Send to Odoo*; `download` (default) for CSV |

In Odoo: a miscellaneous journal for the night audit (`NA` in the examples), one analytic
account per property if several hotels share a company, and a bot user with Accounting rights.

**Odoo Online only enables the external API on the *Custom* plan** — not *One App Free* and not
*Standard*. Odoo.sh and self-hosted have no such restriction. If the client is on Standard, the
portal's CSV download is the route, and it is a supported one: the office imports the file.

Posting the same night twice is safe. Each entry carries the reference `PMS-PROPERTY-DATE`; a
second post finds the existing move rather than making another, and a re-upload supersedes the
earlier run instead of adding to it.

---

## 11. The daily routine

**Each hotel, after the night audit:** sign in, upload the pack on the Night audit page. The
screen says immediately whether it balanced. A report that is really an invoice can be moved
across with one button.

**The office, each morning:** the dashboard lists every property for that business date —
uploaded and balanced, uploaded with a problem, or nothing yet. Download the day's CSV, or send
the day to Odoo. A day already downloaded is not handed out again unless you ask for it
explicitly, so nothing gets booked twice by accident.

**Invoices:** upload, check the vendor and total the reader found, and it becomes a draft bill
in Odoo. The expense account is assigned automatically — from the vendor's history, or the
category rules — so the office does not assign accounts by hand.

**Who has not reported:** `/missing` shows one row per property and one column per night, with
a CSV of the gaps.

---

## 12. Backups

* **Database** — the entries, the run history, the invoices, the logins. On Supabase Pro this is
  automatic and point-in-time; on the free tier it is not, so take your own dump:
  `pg_dump "$DATABASE_URL" > nightaudit-$(date +%F).sql`.
* **Files** — the uploaded packs and invoices. In Supabase Storage they inherit the project's
  durability; on a local install they are the `PORTAL_DATA` directory and belong in whatever
  backs up that machine.
* **`config/`** — in git, which is the backup.

The one thing with no copy anywhere is `PORTAL_SECRET`. Keep it with the client's other
credentials; losing it only logs everyone out, but there is no way to recover it.

---

## 13. Upgrading

```bash
git pull
pip install -r requirements.txt
# restart the app
```

Schema changes are applied on start: tables are created if missing and new columns are added to
existing tables, so there is no migration step and no downtime beyond the restart. Roll back by
checking out the previous commit and restarting; the database stays readable by both.

---

## 14. When something is wrong

| Symptom | Cause |
|---|---|
| Everyone logged out after a deploy | `PORTAL_SECRET` is unset, so it changes on every start |
| Startup says SQLite when you set `DATABASE_URL` | the variable is not reaching the process — check the host's dashboard, not the repo |
| `prepared statement ... already exists` | you are on Supabase's transaction pooler (6543); use 5432 |
| Uploads vanish after a deploy | files are on the container's disk — set the three `SUPABASE_*` variables |
| *Property not recognised* | the report has no id this property matches; add `report_id` in `properties.yaml` or pick the hotel in the dropdown |
| *Some lines are not mapped* | expected on a new property — the screen lists them and asks for the account numbers |
| Invoice read as gibberish | a photo or scan with no text layer; Tesseract is not installed, or the manager should type the fields |
| E-mail silently does nothing | `SMTP_HOST` and `SMTP_FROM` are what turn it on; the admin page says which are set |
| Sessions end during a long shift | they last 12 hours from sign-in, by design |

---

## 15. Time

The portal keeps two clocks apart on purpose.

* **What it shows and what it decides** runs on the hotels' clock — `TIMEZONE`, which defaults
  to `America/Chicago` for Oklahoma. "Due by 06:00" is six in the morning there, a night-audit
  pack filed at 7pm belongs to that day's business date, and the *When* column reads
  `09/18/2026 5:38 pm CDT`. Daylight saving is handled by the zone, so nothing needs changing
  in March or November.
* **What it stores** is UTC, to the second. Timestamps are compared as plain text in a dozen
  places — which of two uploads supersedes the other, whether a password-reset link has
  expired — and local time cannot do that job: on the first Sunday in November it runs the
  same hour twice.

A host in a different timezone therefore changes nothing. Render runs in UTC and the site reads
in Central. To move it, set `TIMEZONE` to any name from the IANA database and restart; the
startup line says which zone it took and what time it is there. An unknown name falls back to
UTC with a warning rather than refusing to start.

## 16. Rehearsing Odoo before there is an Odoo

Set `DELIVERY_MODE=odoo` with `ODOO_TRANSPORT=demo` and no URL or key, and the portal behaves
exactly as it will against the real server: *Send to Odoo* works, the entry comes back with a
number, the run turns **posted**, and the same night sent twice finds the first instead of
writing a second. Nothing leaves the process.

It is there for the weeks before the client's Odoo exists — to prove that a night which parsed,
mapped and balanced survives the trip to the API and back, and to show somebody the finished
thing without asking for credentials first.

Two things to know. It **invents master data on demand**: ask it for GL account 4010 or a
journal called NA and it makes one up, so a rehearsal fails on the things that will really fail
(an entry that does not balance, a night already sent) rather than on a chart of accounts
nobody has loaded. And it **forgets everything on restart**, while the portal's own record does
not — a run can say "posted as NA/DEMO/0001" long after the stand-in has forgotten writing it.

Every screen that can show it says `DEMO`, and so does the startup line. Take the variable away
and the real connection is the only thing left.

## 17. Every setting

| Variable | Default | What it does |
|---|---|---|
| `TIMEZONE` | `America/Chicago` | the hotels' timezone: cut-offs, business dates and every time on screen |
| `PORTAL_SECRET` | random per start | signs session cookies — **set it** |
| `PORTAL_BASE_URL` | — | public address, used to build links in e-mail |
| `PORTAL_DATA` | `./data` | database and uploads when nothing else is configured |
| `PORTAL_CONFIG` | `config/properties.yaml` | the property list |
| `PORTAL_USERS` | `config/users.yaml` | the logins |
| `PORTAL_STORE` | `yaml` | `db` to manage hotels and logins in the browser |
| `PORTAL_SECURE_COOKIES` | `auto` | `always` behind HTTPS |
| `PORTAL_MFA_ROLES` | empty | e.g. `admin` to require two-step sign-in for admins |
| `DELIVERY_MODE` | `download` | `odoo` to post from the dashboard |
| `HOTEL_UPLOADS` | `on` | `off` where packs arrive by e-mail and only the office signs in: the upload page closes and its link goes |
| `INVOICES` | `off` | `on` to enable the vendor-invoice pages |
| `SCHEDULER` | `off` | `on` to drain the posting queue and send the morning list on a timer |
| `SCHEDULER_INTERVAL` | `600` | seconds between passes |
| `REPORT_TO` | unset | where the morning list and the held-night notices go; also settable on the E-mail page |
| `DATABASE_URL` | — | PostgreSQL instead of SQLite |
| `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` / `SUPABASE_BUCKET` | — | all three: Supabase Storage instead of the disk |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` / `SMTP_SECURITY` / `SMTP_REPLY_TO` | — | outgoing mail; also settable on `/admin` |
| `ODOO_URL` / `ODOO_API_KEY` / `ODOO_DB` / `ODOO_TRANSPORT` / `ODOO_USER` | — | the Odoo connection |
| `REPORT_AT` | unset | `06:30` to fix when the morning list goes out; unset means after the latest property cut-off. Also settable on `/admin` |
| `ODOO_AUTOPOST` | `no` | `yes` posts entries in Odoo on arrival instead of leaving drafts; also switchable on `/admin` unless pinned here |
| `INTAKE_TOKEN` | unset | shared secret for the inbound-mail webhook; **unset means the route is off** |
| `INVOICE_READER` | rules | `claude` to use the AI invoice reader |
| `ANTHROPIC_API_KEY` | — | only for `INVOICE_READER=claude`; night audit never uses it |

Secrets — `PORTAL_SECRET`, `SMTP_PASSWORD`, `ODOO_API_KEY`, `SUPABASE_SERVICE_KEY`,
`ANTHROPIC_API_KEY`, and the password inside `DATABASE_URL` — belong in the host's environment
and never in the repository.

---

## Night-audit packs arriving by e-mail

A hotel's PMS, or its GM, can send the pack to an address instead of anyone signing in. What
arrives runs through the same pipeline an upload does.

**Testing, with no domain and no accounts:** drop `.eml` files (or bare reports) into a folder
and read them with `FolderSource`. Re-scanning is safe -- de-duplication decides what is new --
so the same folder can be replayed as often as you like.

**In production:** an inbound mail provider POSTs each message to `/intake/mail`. Postmark is the
easy start because it gives you an address on its own domain, `<guid>@inbound.postmarkapp.com`,
so nothing needs DNS until the hotels have a domain to point at it. Then:

1. Postmark -> Servers -> your server -> **Inbound**. Copy the inbound address.
2. Set the **Inbound Webhook URL** to `https://<your site>/intake/mail?token=<INTAKE_TOKEN>`.
3. Set `INTAKE_TOKEN` on the host to a long random string. The provider does not sign its
   requests, so that secret is the only thing between the route and whoever guesses the URL.
   Leave it unset and the route returns 404, which is the right default for an endpoint that
   files attachments.
4. Have one hotel send, or forward, a pack to the inbound address.

Moving to the hotels' own domain later changes the address and adds MX records. The webhook,
the token and everything downstream stay as they are.

De-duplication is three layers, because each alone has a hole: the `Message-Id` catches a plain
re-delivery but forwarding mints a new one; the SHA-256 of the attachment is the real test, since
the same bytes are the same report however many people forwarded it; and the `PMS-CODE-DATE`
reference means a genuine re-run with corrections supersedes the earlier one instead of posting
twice.

## Sending nights to Odoo on their own

With `DELIVERY_MODE=odoo`, a night that parses and balances goes to Odoo without anyone pressing
anything. It happens *after* the response to whoever delivered the pack, so an arriving e-mail
never waits on Odoo, and Odoo being slow never makes a mail provider decide the delivery failed
and send it again.

One property per call, deliberately. Seven hotels posted as a batch fail as a batch, and then
nobody can say which hotels are in the books. Sent one at a time, a failure is one row still
waiting and a retry touches only that row.

A night that fails records the reason and is tried again on the next pass, up to five times.
After that it stops and waits for a person -- a missing GL account or a bot user without rights
is not something retrying will fix, and the daily report is where it should appear. If Odoo
itself is unreachable, nothing is counted against any night, because that is not any night's
fault.

Re-running is safe twice over: a posted run is no longer selected, and even if it were, the
reference is looked up in Odoo first and the existing move is returned rather than a second one
created.

Anything still waiting can be sent by hand, or from cron:

```bash
python3 -m portal post
```

## The morning list, and the loop that sends it

With packs arriving by e-mail and nobody signing in but the bookkeeper, this is the only thing
watching. A GM who uploads sees in seconds whether the night balanced; a GM who e-mails sees
nothing, so a night can go missing in silence.

One list, four kinds of trouble, because they are all the same job -- find out what happened to
last night:

```
Night audit for 2025-11-10

6 of 6 properties need attention.

Nothing has arrived:
  CANDLEWOOD-MOORE - Candlewood Suites Moore Oklahoma
      due by 06:00

Waiting for account codes:
  TXI47 - Comfort Inn Wichita Falls near University
      Pet Fee, Resort Fee, Parking and 1 more
      https://.../runs/2

Does not balance:
  OKCMD - Holiday Inn Express & Suites Oklahoma City Airport
      debits 11191.64 vs credits 11106.22, out by 85.42

Odoo would not take it:
  OKCON - Embassy Suites by Hilton Oklahoma City Northwest
      GL account with code '4915' not found in Odoo (company 3)
```

Properties that arrived and balanced get one line at the bottom: a list that shows the fine ones
as loudly as the broken ones gets skimmed. When everything is fine it says so and stops.

Read the same thing any time at `/daily`, or `python3 -m portal report`.

### Told at once when a night is held

A pack that lands at 3am and waits until the 6am list has lost three hours of a window only a
few hours wide. So a night held for account codes is reported as soon as it arrives, to the same
`REPORT_TO` address, with the lines that need codes and a link straight to the screen that takes
them.

One message covering everything waiting, not one per hotel -- it is the same visit to the same
screen either way, and a separate mail per property is how a useful notice becomes one people
filter. Each night is mentioned once; a night still unmapped a week later is the morning list's
job, not a reminder every time another pack arrives. If the send fails, nothing is marked, so the
next pack tries again.

### Two-part reports (Wyndham)

SynXis sends the revenue and the ledger movements as separate reports, and on a schedule they
arrive as separate e-mails. Neither balances alone, so the first to land is recorded as *waiting
for the other half* -- not as "does not balance", which would send somebody hunting for an error
that is really a file that has not arrived. When the second half lands, the first is brought
alongside it and the pair is read together; the half that was waiting is superseded, so the
night appears once.

Whichever half arrives second does the joining, because which one that is depends on the mail
server rather than on us. A half still on its own appears on the morning list under *Only half
the report arrived*.

### Turning the loop on

```
SCHEDULER=on
SCHEDULER_INTERVAL=600        # seconds; the default
REPORT_TO=office@example.com  # also settable on the E-mail setup page
```

It is off unless asked for, because something that writes to a client's accounting system on a
timer should be switched on deliberately. Once on it does two things: drains the posting queue,
so a night that failed at 3am is retried within the hour rather than waiting for tomorrow's
e-mail; and sends the list once, after the last property's cut-off, recording the date so a
restart cannot send it twice.

The cut-off is 06:00, overridden per property with `due_by: "05:30"` in its configuration. A
property with nothing yet reads as *not due* before its cut-off and *nothing has arrived* after.

06:00 means six in the morning **where the hotels are**, not on the host. See *Time* above.

By default the list goes out after the latest cut-off among the properties, so no hotel is
called late before it was due. Set a time outright with `REPORT_AT`, or on the **Settings**
page under *On a timer*, which also shows when the list will next go, what is still waiting
for Odoo, and two buttons that do either job this minute rather than at the next pass:

* **Send the morning list now** — the same list, sent for today, whatever the time is.
* **Send waiting nights to Odoo now** — the same call the loop makes. Use it after an Odoo
  outage: an unreachable server never counts against a night's five attempts, so the nights
  are still queued and one press clears them.

Neither button needs `SCHEDULER=on`. If you would rather not run the loop at all, these two
and the cron commands below cover everything it does.

If you would rather use the host's own scheduler than the built-in loop, leave `SCHEDULER` off
and run `python3 -m portal post` and `python3 -m portal report --send` from cron.

## Still needed from the client

The software is finished ahead of these; each one is a value to fill in, not work to do.

1. The chart of accounts, and the mapping worksheet filled in.
2. Where Odoo is, which version, and a bot user with an API key.
3. A Fosse sample — the seventh format, still live at the unconverted properties, and the only
   one never seen.
4. The sending address and access to the DNS for that subdomain.
