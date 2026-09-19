# Installing Night Audit to Odoo

Two programs in one repository:

* **the portal** (`portal/`) — the website. Hotel managers upload the night-audit pack; the
  office downloads the day's entries or sends them straight to Odoo.
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

# Only if you ever turn the dormant invoice side on, and only for invoices that are
# photographs or scans. Nothing else needs it, and the Docker image leaves it out unless
# built with --build-arg INVOICE_OCR=1.
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
| `INVOICES` | `off` — vendor bills are Odoo's job, not this tool's | only if Odoo's own per-document digitization is ever not what they want |

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
That proves the whole forgotten-password flow before anyone pays a mail provider. (The test
suite does the same thing in-process, so a real SMTP conversation is covered without a
provider: see `tests/test_mail_setup.py`.)

### Odoo

Odoo Community is free and, unlike Odoo Online, has no plan gate on the external API — so a
throwaway copy answers the same calls the client's will. Worth having one: the alternative is
finding out what their server thinks of an entry by sending it a real night.

```bash
docker compose -f dev/odoo-compose.yml up -d
```

Open <http://localhost:8069>, create a database, install **Invoicing**, then create a user with
Accounting rights and an API key under *Preferences → Account Security*. Point the portal at it:

```bash
ODOO_URL=http://localhost:8069 ODOO_DB=<database> ODOO_TRANSPORT=xmlrpc \
ODOO_USER=<login> ODOO_API_KEY=<key> DELIVERY_MODE=odoo python3 -m portal serve
```

Then press **Check the Odoo connection** on Settings, which asks that server everything a night
needs and reports it in one go. `docker compose -f dev/odoo-compose.yml down -v` removes it.

**When the machine in front of you is not yours to install Docker on.** A day-job laptop is a
common and good reason not to. Three ways, cheapest first:

1. **GitHub Codespaces.** Open the repository in a codespace and use it as it comes: with no
   dev-container configuration, GitHub builds from its own universal image, which already has
   Python and Docker. That is on GitHub's machine, not yours. Personal Free accounts include
   120 core hours and 15 GB a month — roughly 60 hours on a 2-core box — far more than this
   needs. **Stop the codespace when you finish**, because storage keeps counting while it
   exists.

   There is deliberately no `.devcontainer/` here, and it should stay that way. One was tried:
   the `devcontainers/python` image has moved to Debian trixie, the docker-in-docker feature
   still defaults to installing Moby packages that trixie does not carry, and the codespace
   came up in recovery mode before any work could start. It is fixable — `"moby": false` on
   the feature, or an older base — but the default image already has Python and Docker, so a
   config file here buys nothing and adds a way to fail.
2. **A small cloud box.** Any $5-a-month VPS runs the compose file above. Delete it when the
   integration is proven.
3. **Render**, where the portal already lives. It will work — a Docker service, a managed
   database and a disk for the filestore — but Odoo wants around 2 GB, so it is a paid instance
   rather than the free tier, and you are then running somebody's accounting system. Fine for a
   week of testing; not somewhere to leave it.

Whichever you pick, it is a rehearsal room and not a stage. Champion's Odoo is hosted by Odoo,
and nothing built on a test copy moves across to it.

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

### Adding the hotels from a file

A portfolio arrives as a list, and filling in the same form fifteen times is where a wrong PMS
or a mistyped report id slips in — neither of which announces itself until a pack arrives at six
in the morning and lands nowhere. On `/admin`, under **Properties**, download the template:

| Column | Required | What goes in it |
|---|---|---|
| `code` | yes | Short, and what goes in the journal reference: `OKCON`, `TXI47`. Letters, digits, dot, dash, underscore. |
| `pms` | yes | `PEP`, `CHOICEADV`, `HOTELKEY`, `OPERA`, `AGILYSYS`, `SYNXIS` or `GENERIC` — **or just the brand**: Hilton, Choice, IHG, Marriott, Wyndham all resolve to the right reader. |
| `name`, `brand` | no | For the screen. |
| `pms_property_id` | see below | The id the report prints — `Hotel ID : OKCON`. |
| `pms_property_name` | see below | The hotel name as the report prints it, for the systems that print no id (Opera, Agilysys). |
| `company`, `analytic`, `journal` | no | Odoo. Fill these once the client's chart of accounts exists. |
| `due_by` | no | This hotel's own cut-off, `05:30`. Blank means 06:00. |
| `enabled` | no | `yes` or `no`. Blank means yes. |

One of `pms_property_id` or `pms_property_name` is what lets a pack arriving by e-mail find its
hotel on its own. A row with neither is imported and flagged, not refused — the hotel can still
be chosen by hand on the upload page until you have seen a real report from it.

**A code that already exists is an update, and a narrow file updates only what it names.** A file
of `code,pms,name` changes those three and leaves the Odoo company, the analytic account and the
GL mapping exactly as they were. The mapping is never in this file: it is a page of YAML per
hotel and belongs on the property's own screen or in the worksheet import.

As with the logins, **nothing is written unless every row is good**.

### Adding the logins from a file

Seven hotels means seven managers plus whoever is in the office, and typing them in one at a
time is both slow and where a wrong property code slips in unnoticed. On `/admin`, under
**Logins**, download the template and fill it in:

| Column | Required | What goes in it |
|---|---|---|
| `username` | yes | Letters, digits, dot, dash, underscore. 2–64 characters, no spaces. |
| `role` | yes | `manager` (sees only their hotels) or `admin` (sees everything, can post to Odoo). |
| `properties` | for a manager | One or more property codes. Comma, space or semicolon — whatever you reached for. Blank for an admin, who already sees all of them. |
| `email` | yes | How they set their own password, and the only way they can reset it later. |
| `enabled` | no | `yes` or `no`. Blank means yes. |

Extra columns are ignored, the header is read case-insensitively, and a spreadsheet's trailing
blank lines do not count as rows.

**There is no password column, on purpose.** A file of passwords gets e-mailed, sits in
Downloads and eventually reaches a repository, and the first thing anyone does with one is give
everybody the same password. Each imported account is created with a password nobody knows, and
the import hands back a single-use link per person, good for seven days, for choosing their own.
Where mail is configured the links are sent; otherwise they are shown once on the result page
for you to pass on. Until somebody follows theirs, their account cannot be signed into at all.

**Nothing is written unless every row is good.** One bad row stops the file and names the line,
because a half-finished import leaves you wondering which four of seven managers exist.

A username that already exists is an update, not an error: role, properties, e-mail and enabled
are changed and the password is left alone. The account doing the importing is refused — change
your own role on your own account page, so a typo in a spreadsheet cannot lock you out.

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
holds the same settings for whoever runs this day to day.

Start there rather than with the variables. The page:

* **lists what is missing** before you send anything — no From address, a password with no
  user, port 465 set to starttls;
* **fills in a service for you** — pick Resend, Postmark, Brevo, SendGrid, SES or Gmail and it
  sets the host, port, security and the fixed login that service uses. Those three are where a
  setup goes wrong, and 465-with-starttls fails in a way that reads exactly like a bad
  password;
* **sends a test message**, and when that fails gives you the server's own words *and* what
  they usually mean. "Authentication failed" is nearly always the API key in the wrong box:
  for most services `SMTP_PASSWORD` is an API key and `SMTP_USER` is a fixed word — `resend`,
  `apikey`, or the token again — not an e-mail address.

The order that works: pick the service, verify a sending domain with them, paste the API key,
put the verified address in `SMTP_FROM`, set `PORTAL_BASE_URL` to the public address of this
site, put the office address in `REPORT_TO`, then send yourself a test.

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
screen says immediately whether it balanced.

**The office, each morning:** the dashboard lists every property for that business date —
uploaded and balanced, uploaded with a problem, or nothing yet. Download the day's CSV, or send
the day to Odoo. A day already downloaded is not handed out again unless you ask for it
explicitly, so nothing gets booked twice by accident.

**Invoices** are not part of this. Odoo digitizes vendor bills itself; `INVOICES` is off, so
the pages are not served and the link is not in the navigation. The reader is still in the tree,
dormant — see the README for what it does if it is ever switched on.

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

### Check the connection before a night depends on it

**Settings → Check the Odoo connection.** It asks that server everything a night will need, in
one pass, and reports all of it at once: whether it is reachable and the key is accepted, how
many companies, journals and GL accounts it has, **which analytic field this version wants**,
and then per property — its company, its journal, its analytic account, and every GL code in
its mapping that does not exist there.

It creates nothing, so it is safe against a client's live server as often as you like.

Without it each of those arrives as a separate failure on a separate morning, and each one
costs a round trip to somebody at the client.

One thing it is worth knowing it checks: Odoo 17 replaced `analytic_account_id` (one id) with
`analytic_distribution` (a map of account to percentage). Rather than infer that from a version
number, the client asks `account.move.line` which field it has and tags accordingly — including
the case where the analytic module is not installed at all, where a line goes untagged rather
than the entry being refused.

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
| `INVOICES` | `off` | the dormant vendor-invoice pages; Odoo digitizes bills itself, so leave it off |
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
| `INVOICE_READER` | rules | `claude` to use the AI invoice reader; only matters with `INVOICES=on` |
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
3. One night's pack from **each hotel**, not one per system. Two hotels on the same PMS can
   print differently enough to need separate work: Embassy Suites and the Hilton Garden Inn are
   both PEP "Final Audit", and the Garden Inn's came out $1,132.35 over until its layout was
   read on its own terms.
4. The sending address and access to the DNS for that subdomain.

**Settled, and no longer waiting on anything:** Marriott **Fosse** was carried here for a while
as the seventh format — the one no sample had ever been seen of. Champion have confirmed the
properties still on it are not part of this project, so it was never built. If that changes it
is a new parser and a real piece of work; ask for a night's report before estimating it.
