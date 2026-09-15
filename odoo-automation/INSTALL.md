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
git clone <repo> && cd odoo-automation
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

## 2. Understand the two switches

Everything in this document is the same program with different settings. Two variables decide
its shape, and both default to the simplest thing:

| Variable | Default | Set it when |
|---|---|---|
| `PORTAL_STORE` | `yaml` — hotels, logins and mappings come from `config/` | the client wants to add a property or reset a login themselves, in the browser |
| `DELIVERY_MODE` | `download` — the office downloads a CSV and imports it | Odoo's API is available and you want the portal to post directly |

Leave both alone to start. Section 8 turns on the first; section 10 turns on the second.

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
pip install "psycopg[binary]"
export DATABASE_URL=postgresql://user:password@host:5432/dbname
```

That is the whole change. Tables are created on first start, and the startup line will say
`database: PostgreSQL ...` with the password masked. The same test suite passes on both, so
this is a supported configuration rather than a hopeful one.

**A Supabase-specific trap:** the project gives you more than one connection string. Use the
**direct connection or the session pooler (port 5432)**, not the transaction pooler (port
6543). Transaction pooling does not keep prepared statements, which the database driver relies
on; the symptom is `prepared statement "_pg3_..." already exists` under load.

---

## 6. Supabase Storage instead of the local disk

The portal keeps every pack and every invoice, because they are the evidence behind an
accounting entry. On one machine the data directory is fine. On a replaceable container they
belong in object storage.

In the Supabase dashboard: **Storage → New bucket**, name it `night-audit`, and leave **Public
bucket off**. Then *Project Settings → API* for the URL and the `service_role` key.

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

---

## 8. Move the configuration into the database

With `PORTAL_STORE=db`, the hotels, logins and mappings live in the database and are edited on
the Settings page instead of in `config/`. Load the files in once:

```bash
python3 -m portal seed          # or the "Import from the config files" button on /admin
```

It prints where it landed, so you can see it went to Postgres and not to a local file:

```
imported 6 properties and 7 users into PostgreSQL postgresql://***@db.example/nightaudit
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
| `ODOO_TRANSPORT` | `json2` (default, Odoo 19+) or `xmlrpc` (Odoo 18 and earlier) |
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

## 15. Every setting

| Variable | Default | What it does |
|---|---|---|
| `PORTAL_SECRET` | random per start | signs session cookies — **set it** |
| `PORTAL_BASE_URL` | — | public address, used to build links in e-mail |
| `PORTAL_DATA` | `./data` | database and uploads when nothing else is configured |
| `PORTAL_CONFIG` | `config/properties.yaml` | the property list |
| `PORTAL_USERS` | `config/users.yaml` | the logins |
| `PORTAL_STORE` | `yaml` | `db` to manage hotels and logins in the browser |
| `PORTAL_SECURE_COOKIES` | `auto` | `always` behind HTTPS |
| `PORTAL_MFA_ROLES` | empty | e.g. `admin` to require two-step sign-in for admins |
| `DELIVERY_MODE` | `download` | `odoo` to post from the dashboard |
| `DATABASE_URL` | — | PostgreSQL instead of SQLite |
| `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` / `SUPABASE_BUCKET` | — | all three: Supabase Storage instead of the disk |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` / `SMTP_SECURITY` / `SMTP_REPLY_TO` | — | outgoing mail; also settable on `/admin` |
| `ODOO_URL` / `ODOO_API_KEY` / `ODOO_DB` / `ODOO_TRANSPORT` / `ODOO_USER` | — | the Odoo connection |
| `INVOICE_READER` | rules | `claude` to use the AI invoice reader |
| `ANTHROPIC_API_KEY` | — | only for `INVOICE_READER=claude`; night audit never uses it |

Secrets — `PORTAL_SECRET`, `SMTP_PASSWORD`, `ODOO_API_KEY`, `SUPABASE_SERVICE_KEY`,
`ANTHROPIC_API_KEY`, and the password inside `DATABASE_URL` — belong in the host's environment
and never in the repository.

---

## Still needed from the client

The software is finished ahead of these; each one is a value to fill in, not work to do.

1. The chart of accounts, and the mapping worksheet filled in.
2. Where Odoo is, which version, and a bot user with an API key.
3. A Fosse sample — the seventh format, still live at the unconverted properties, and the only
   one never seen.
4. The sending address and access to the DNS for that subdomain.
