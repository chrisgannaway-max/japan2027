# PMS night audit -> Odoo Accounting

Each property's PMS prints a night-audit pack. Today GMs re-key it into a spreadsheet and
accounting re-keys that into the books. This tool reads the pack, maps every line to a GL
account, checks the entry balances to the cent, and creates it in Odoo — draft by default,
posted if you ask.

That is the whole job. Vendor invoices are handled in Odoo itself, by its own digitization;
this tool does not touch them. There is a dormant invoice reader in here behind a switch that
is off — see *Vendor invoices* near the end for what it is and why it is still in the tree.

## Status

Parsers are built and tested on real sample reports for all six PMS formats. Each sample
produces an entry that balances to the cent against the report's own control totals.

| Brand    | PMS              | Report the parser reads                         | Sample     | Parser key  |
|----------|------------------|-------------------------------------------------|------------|-------------|
| Hilton   | PEP              | **Final Audit** (FinalAudit PDF, 8 pages)       | OKCON      | `PEP`       |
| Choice   | choiceADVANTAGE  | Night-audit pack: **Final Transaction Closeout** + **Hotel Journal Summary** (+ Hotel Statistics) | TXI47 | `CHOICEADV` |
| IHG      | HotelKey         | **Trial Balance Report** (PDF, or the .eml it is attached to) | OKCMD | `HOTELKEY` |
| IHG      | OPERA            | Daily **Trial Balance** (trial_balance)          | Candlewood Moore | `OPERA` |
| Marriott | Agilysys Stay    | **Ledger Summary** grouped by ledger, PDF or CSV export | OKCAW | `AGILYSYS`  |
| Wyndham  | SynXis Property Hub | **Transaction Totals Summary** + **Hotel Ledger Comparison Report** (pair, same folder) | 89051 | `SYNXIS` |

**Marriott Fosse is out of scope.** It was the seventh system in the estate and the only one
no sample was ever seen of; Champion have confirmed the properties still running it are not
part of this project. Adding one later means writing a parser, not changing a setting.

Account codes in the mapping files are USALI-style placeholders until the Odoo chart of
accounts is settled. The Hilton "Hotel Statistics" and HotelKey "Hotel Statistics" reports
are not needed for the journal entry.

## How it works

```
PMS pack (PDF / .eml)  --parser-->  DailyReport: (label, code, amount, section)
                                          |
                    config/gl_mapping/<property>.yaml (label or code -> account, side)
                                          v
                                   JournalEntry (balanced or it refuses)
                                          |
                              Odoo account.move via JSON-2 (19+) or XML-RPC (<=18)
```

Every parser normalises to one sign convention so a single mapping engine fits all PMSs:

| section      | amount means                                        | default side          |
|--------------|-----------------------------------------------------|-----------------------|
| `revenue`    | revenue earned today (allowances negative)          | credit                |
| `tax`        | tax collected today                                 | credit                |
| `settlement` | money received today (refunds negative)             | debit                 |
| `ledger`     | change in a PMS ledger balance (guest, AR, deposits)| signed, + = debit     |
| `expense`    | paid-outs                                           | signed, + = debit     |
| `transfer`   | internal moves between PMS ledgers (direct bill, AR credit applications) | not booked |

Because revenue + tax - settlements always equals the change in the PMS ledgers, the entry
balances when every line is mapped. Internal transfers are excluded on purpose: their effect
is already inside the ledger movements.

Design rules baked in:

- **Balanced or nothing.** An entry outside `tolerance` is never sent.
- **Every line must map.** A new revenue code stops the run with the label named, instead of
  silently landing in the wrong account. (Optional `suspense_account` changes this.)
- **Idempotent, on every path.** The journal reference is `PMS-PROPERTY-DATE`. Uploading the
  same night twice keeps both files for the audit trail but supersedes the older run, so one
  entry stands. Posting through the API finds the existing move and does nothing. The CSV
  export leaves out entries already downloaded or posted, because handing the same day out
  twice is how a day gets booked twice; a deliberate "download again" link is there for when
  an import genuinely failed. Bills dedupe on vendor plus invoice number.
- **Draft first.** Entries and bills are created in draft. `--post` posts night-audit entries
  immediately once the mapping is trusted.
- **Audit trail.** `logs/daily_entries.csv` and `logs/invoices.csv`; every parsed line carries
  its source file and line number.

## Setup

**Installing it, on a laptop or on a server: see [INSTALL.md](INSTALL.md).** It covers the
whole thing in order — dependencies, the hotels and mappings, testing e-mail and Odoo for free,
PostgreSQL and Supabase Storage, deploying, backups and every setting.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt        # requirements-dev.txt to run the tests
cp config/properties.example.yaml config/properties.yaml      # edit property list
cp config/gl_mapping/hilton_pep.example.yaml config/gl_mapping/okcon.yaml   # one per property
cp config/expense_categories.example.yaml config/expense_categories.yaml
```

Odoo connection (environment variables):

| Variable            | Meaning                                                            |
|---------------------|--------------------------------------------------------------------|
| `ODOO_URL`          | `https://yourcompany.odoo.com` or the on-premise URL              |
| `ODOO_API_KEY`      | API key of a dedicated bot user (Preferences -> Account Security)  |
| `ODOO_DB`           | database name (needed on servers hosting several databases)        |
| `ODOO_TRANSPORT`    | `json2` (default, Odoo 19+) or `xmlrpc` (Odoo 18 and earlier)      |
| `ODOO_USER`         | bot user login, only for `xmlrpc`                                  |
| `ANTHROPIC_API_KEY` | optional: turns on the AI invoice reader (night audit never uses it) |
| `PORTAL_MFA_ROLES`, `SMTP_*` | see the logins section below |

Odoo Online note: the external API is only enabled on the *Custom* plan, not *One App Free* or
*Standard*. Odoo.sh and self-hosted have no such restriction.

In Odoo, create: a miscellaneous journal for night audit (code `NA` in the examples), one
analytic account per property if several hotels share a company, and the bot user with
Accounting rights.

## Usage

```bash
# what does this report parse to?  (PMS auto-detected; lists every line, section, code, amount)
python -m pms_to_odoo inspect --file "samples/OKCON_2025-11-10_FinalAudit.pdf"

# one report, show mapping coverage + the T-account, send nothing
python -m pms_to_odoo daily --property OKCON --file report.pdf --dry-run

# create the draft entry in Odoo (add --post to post it)
python -m pms_to_odoo daily --property OKCON --file report.pdf

# HotelKey reports arrive as e-mails: pass the .eml, the PDF attachment is used
python -m pms_to_odoo daily --property OKCMD --file "TRIAL BALANCE HOTEL KEY.eml" --dry-run

# a folder of packs and e-mails; property recognised from the report's own id
python -m pms_to_odoo batch --dir inbox/ --done-dir processed/ --dry-run

# GM spreadsheet instead of the PMS report (interim path)
python -m pms_to_odoo daily --property OKCON --file daily.xlsx --from-excel

# vendor invoice -> draft bill with the PDF attached
python -m pms_to_odoo invoice --file invoice.pdf --company "Example Hotel LLC" --dry-run

# verify config files and the Odoo connection; dump the chart of accounts
python -m pms_to_odoo check
python -m pms_to_odoo accounts --company "Example Hotel LLC" > coa.csv
```

Dry-run of the Hilton sample (`samples/OKCON_2025-11-10_FinalAudit.pdf`) ends with:

```
Journal entry PEP-OKCON-2025-11-10  date=2025-11-10  journal=NA  company=Example Hotel LLC
  Account  Description                          Debit         Credit
  4000     GUEST ROOM                                      31,222.62
  ...
  1120     MASTER                           34,622.46
  1210     House Account Net Change         39,620.67
           TOTAL                            86,180.06      86,180.06
```

## Adding a property

1. Add it to `config/properties.yaml` with its PMS key and the id the PMS prints on the
   report (`pms_property_id`), so `batch` can route files to it.
2. Run `inspect` on one real report to see the labels and codes, then `daily --dry-run`.
   The coverage table shows each line and the account it would hit, or `UNMAPPED`.
3. Add a rule (by `match:` regex or `code:`) or an `ignore` pattern for every `UNMAPPED`
   label until the entry balances. Format is documented at the top of `pms_to_odoo/mapping.py`.
4. Run for a week in draft, compare with the GM spreadsheet, then switch on `--post`.

## Format notes (from the samples)

- **PEP Final Audit**: we book the *Net Today* column (Actual + Adjusted). Labels that wrap
  over two lines are re-joined. Ledger lines are the "Net Change" rows (In House, House
  Account, Closed Folio, Group Master, Direct Bill, Advance Deposit); their sum equals the
  Hotel Balance change and the parser warns if it does not. Open question: whether "BILL TO
  COMPANY" (under payments) and the Direct Bill ledger change double count on a day with
  direct-bill activity; both were zero on the sample (see the comment in the mapping file).
- **choiceADVANTAGE**: only codes with activity appear on the Hotel Journal Summary; the
  Closeout page supplies each code's type. Ledger movements come from the three ledger columns
  of the "Today's Total" row. AC/CI and DB/DR are transfers and are not booked.
- **HotelKey Trial Balance**: folio-centric (charges debit, payments credit); the parser flips
  the sign of ASSET rows. `(Offset)` accounts are the ledgers. Card brands all share code
  `FPCC`, so card rules match on name. Reports arrive by e-mail; `.eml` is accepted directly.
- **Agilysys Stay Ledger Summary**: PDF or the CSV export (the one the property calls "the
  Excel"); the CSV is preferred as it is exact and carries the GL CODE column. Export it *with*
  subtotals, because the ledger balance rows are what give the ledger movement. One block per PMS ledger with BEGINNING/ENDING balances
  (the ledger movement) and PAYMENTS / REVENUE / TRANSFERS types. The REVENUE type mixes
  revenue and tax items; taxes are recognised by code `T…` and the word Tax. Transfers
  between ledgers net to zero and are not booked. The report has a GL CODE column, empty on
  the sample: if GL codes are maintained in Agilysys the mapping can use them directly.
- **OPERA Trial Balance**: payments are negative on the report; ledger movement = Balance
  Today - Balance Yesterday per ledger. "Direct Billing/City Ledger" is a transfer. If AR
  payments show up under "AR Ledger Payments" they are booked to an AR receipts clearing
  account; confirm on a day with AR activity.

- **SynXis Property Hub**: two reports per night. The Transaction Totals Summary gives revenue,
  tax and payments per charge code (Base Type decides the section); the Hotel Ledger
  Comparison gives each ledger's Difference. Give the parser either file and it merges the
  companion from the same folder; without the ledger report it warns that the entry cannot
  balance. DIRECT BILL is a transfer. The GL Account column is empty on the sample; if set in
  SynXis it is captured for direct mapping.

## Web portal

Managers upload the night-audit pack; admin reviews, approves and exports.

```bash
cp config/users.example.yaml config/users.yaml      # one login per property + an admin
python -m portal hash 'a strong password'            # paste the hash into users.yaml
PORTAL_SECRET=$(openssl rand -hex 32) python -m portal serve --port 8000
# or:  docker build -t night-audit . && docker run -p 8000:8000 -v $PWD/data:/data night-audit
```

| Who      | Page               | What happens                                                                       |
|----------|--------------------|------------------------------------------------------------------------------------|
| Manager  | `/upload`          | **Night audit.** Picks their property (or lets the report identify it), uploads PDF/CSV/EML. The pack is parsed, mapped and balanced on the spot and the result is shown: balanced, needs mapping, does not balance, or not a report we book. |
| Admin    | `/` dashboard      | One row per property for a business date: not uploaded / balanced / needs mapping / posted, entry total, warnings. |
| Admin    | `/runs/<id>`       | Review screen: T-account, every report line and the account it hits, control totals. Approve, download the Odoo import CSV, send to Odoo (when `ODOO_*` is configured), or re-run after editing a mapping file. |
| Admin    | `/missing`         | **Who has not reported.** A grid of the last N nights, one row per property: green balanced, amber uploaded but stuck, red nothing received. Names who missed last night at the top, and downloads as CSV for chasing people. |
| Admin    | `/export/<day>.csv`| All balanced (or approved-only) entries for the day in Odoo's Journal Entries import layout, or a flat one-row-per-line CSV for Excel. |
| Manager  | `/invoices`        | **Vendor invoices.** A separate page: see the invoices section below. |

Rules: managers only see their own properties; a new upload for the same property and day
supersedes the previous unposted run; uploads are kept under `data/uploads/` and the run
history in `data/portal.db`. Check the Odoo import column headers once against the import
template of the client's Odoo version (Accounting > Journal Entries > Import > template).

The same pipeline (`pms_to_odoo/pipeline.py`) drives the CLI `batch` command and the portal,
so a file behaves identically on either path.

**Night audit and invoices are two separate pages**, because they have different rhythms and
may end up with different people doing them. Each page recognises the other's files rather
than mangling them: a night-audit report uploaded as an invoice is refused by name ("that is
a PEP night-audit report") and not stored, and an invoice uploaded as a night-audit report
comes back as *Looks like an invoice* with a one-click button to send it across. The check is
`pms_to_odoo/invoices/sniff.py`, and it scores every one of the six report formats firmly
negative and a real invoice firmly positive.

## Logins, password resets and two-step sign-in

Each property has its own login, listing the properties it may upload for. Passwords are
stored as salted PBKDF2 hashes, never in plain text, and the session cookie lasts 12 hours.

| Variable | Default | What it does |
|---|---|---|
| `PORTAL_SECURE_COOKIES` | `auto` | `auto` marks the session cookie Secure when the request arrived over HTTPS, honouring `X-Forwarded-Proto` behind a proxy. `always` in production if you want to be certain; `never` for local HTTP. |
| `PORTAL_MFA_ROLES` | *(empty)* | Comma-separated roles that must use two-step sign-in, e.g. `admin`. Off by default so nobody is locked out before enrolling. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_SECURITY`, `SMTP_REPLY_TO` | — | Outgoing mail. Can be set here **or** on the E-mail setup page; the host always wins. Without a host the "forgotten password" page says resets are not enabled and to ask an administrator. |
| `PORTAL_BASE_URL` | — | The public address, used to build reset links. |

**E-mail setup page** (`/admin/email`, admin only). Shows whether mail is working, lets you
fill in the server details in the browser rather than through the hosting dashboard, and has
a **Send test message** button that reports the mail server's own reason when it fails, which
is usually enough to tell a wrong password from a wrong hostname. The password is never
rendered back: leaving it blank keeps the saved one, and there is a checkbox to clear it.
Anything set as an environment variable on the host is shown locked and cannot be changed
from the browser.

Use a transactional mail service (Resend, Postmark, Amazon SES) with a sending address on a
**subdomain** you control, such as `nightaudit@mail.example.com`. A subdomain needs no change
to the records that govern the company's existing mail, so it is safe to approve, and the
portal's sending reputation stays separate from theirs.

**Password resets.** A user enters their username or e-mail on `/forgot` and gets a link that
lasts an hour and works once. Only the hash of the token is stored, so the database never
holds a usable link, and the page gives the same answer whether or not the account exists so
it cannot be used to discover who has one. Resets need the database store and an e-mail
address on the account; otherwise an administrator sets the password on the Settings page.

**Repeated wrong passwords are slowed down.** Five failures for one username from one address,
or twenty from one address across any usernames, locks that combination for fifteen minutes.
It is held in memory, so it resets when the process restarts and is per worker, which is
enough to stop guessing without another moving part to maintain.

**Two-step sign-in** uses an authenticator app, which costs nothing and works without a phone
signal. Each user enrols on their own account page by scanning a QR code. Setting
`PORTAL_MFA_ROLES=admin` makes it compulsory for administrators: they can sign in but are
sent to enrol before reaching anything else. If someone loses their phone, an administrator
clears it from the Settings page and they enrol again. It needs `PORTAL_STORE=db`, and
enforcement is skipped in file mode rather than locking anyone out.

## Account mapping, without editing YAML

The mapping files can be filled in from the portal instead of by hand.

**1. Load the chart of accounts once** on `/admin/accounts`, either from a CSV with a `code`
column or straight out of Odoo when `ODOO_URL` and `ODOO_API_KEY` are set. Every account box
in the portal then becomes a dropdown.

**2. Import the filled-in mapping worksheet** on `/admin`. Each tab is matched to its PMS and
its filled rows become rules on every property using that system, so answering the worksheet
once covers every hotel on that brand.

**3. Map whatever is left as it appears.** A report containing a line no rule covers comes
back as *needs mapping* rather than failing. The dashboard and the run page link to a screen
listing only those lines, with the amount, the PMS code, and a suggested account taken from
another property already using the same PMS. Choosing accounts writes rules at the top of
that property's mapping, re-runs the report, and lands on the finished entry. "Not an
accounting line" writes an ignore pattern instead.

Rules written this way match the exact label (or the PMS transaction code where the report
prints one), are inserted above any hand-written catch-all so they win, and are validated
before they are saved. Comments in the file are preserved, so a mapping stays readable
whether it was written by hand or through the portal.

## Vendor invoices — off, and not part of this project

**Champion use Odoo for vendor bills**, confirmed, so nothing here is used for them.
`INVOICES` is `off` by default: the pages are not served, the link is not in the navigation,
and no invoice code runs. Its two extra dependencies are not installed either — `pytesseract`
and `pillow` live in `requirements-invoices.txt`, and the Docker image leaves Tesseract out
unless built with `--build-arg INVOICE_OCR=1`. A deployment behaves as if this section did
not exist, and does not carry or patch anything for it.

It is still in the tree because it is written and tested, and because Odoo's digitization is
billed per document — if that ever stops being the right trade, this is here rather than
needing to be built. Turning it on is `INVOICES=on`; nothing else changes.

The rest of this section is what it does when it is on.

Managers upload an invoice on `/invoices`; the reader pre-fills the fields; the expense
account is assigned automatically; the bill is **ready** at once if every field is present
and it is not a duplicate. Admin's whole job is one click: *Download ready bills*, which
produces Odoo's Bills import CSV and marks them exported (or *Create draft bill in Odoo*
when the API is configured). Admin can hold or reject an invoice as an exception; nothing
requires their approval.

The expense account comes from, in order: the account this vendor received on a previous
export or post (remembered automatically), the vendor patterns in
`config/vendor_accounts.yaml`, keyword patterns on the description, then the default
account. Changing the account on one invoice teaches the portal that vendor for next time.

Two readers sit behind the same screen:

| Reader   | Needs                       | Reads                                              | Set with                  |
|----------|-----------------------------|----------------------------------------------------|---------------------------|
| `rules`  | nothing (Tesseract OCR is installed by the Dockerfile) | text PDFs directly; **photos and scanned PDFs through local OCR**. Vendor, tax id, invoice number, dates, subtotal, tax, total, PO. Per-vendor regex templates in `config/vendor_templates.yaml` make it exact for regular vendors. | default when no key is set, or `INVOICE_READER=rules` |
| `claude` | `ANTHROPIC_API_KEY`, pay per use (cents per invoice) | everything above with better accuracy on messy scans, plus line items and a confidence | `INVOICE_READER=claude` or just setting the key |

The AI reader is optional. If it is on and fails for any reason the upload falls back to the
rules reader, so nothing is ever blocked on the service. Nothing on the night-audit side uses
it at all. Duplicate invoices (same vendor and number) are flagged on the review form.

## Scheduling (alternative to the portal)

The CLI is stateless, so any scheduler works: a cron job or Windows Task Scheduler running
`batch --dir` against a folder where the packs land (a mailbox rule saving attachments, an
SFTP drop, or a manual save-as). HotelKey already e-mails its reports; PEP and
choiceADVANTAGE can schedule report e-mails as well.

## Tests

```bash
python -m pytest -q
```

The same suite runs against the other backends, which is how they are checked rather than
assumed:

```bash
DATABASE_URL=postgresql://user:pass@localhost/nightaudit python -m pytest -q   # on PostgreSQL
```

Supabase Storage needs no account to test: `tests/fake_supabase.py` is a small HTTP server
speaking the same object API, and `tests/test_storage.py` runs the portal against it, so the
adapter makes real requests rather than mocked ones.

Tests use an in-memory fake of the Odoo API (`tests/fake_odoo.py`) and layout-text fixtures
extracted from the real sample reports with `tools/extract_layout_text.py` (staff names
redacted; the figures are the real ones from the sample days). Tests that read the sample
PDFs directly run only when the PDFs are present in `samples/` (git-ignored).

## Layout

```
pms_to_odoo/
  models.py            ReportLine / DailyReport / JournalEntry + sign conventions
  odoo_client.py       JSON-2 + XML-RPC transports, lookups, idempotent create
  mapping.py           YAML rules -> balanced JournalEntry
  journal.py           JournalEntry -> account.move payload, draft/post
  parsers/
    base.py            pdfplumber layout text, .eml attachments, number/date helpers
    pep.py             Hilton PEP Final Audit
    choice.py          choiceADVANTAGE night-audit pack
    hotelkey.py        HotelKey Trial Balance Report
    opera.py           OPERA daily Trial Balance
    agilysys.py        Agilysys Stay Ledger Summary
    synxis.py          SynXis Transaction Totals Summary + Hotel Ledger Comparison (pair)
    generic_table.py   config-driven label+amount parser
    excel_template.py  GM spreadsheet reader
  invoices/            rules reader (no AI), optional Claude reader, vendor bill creation
  pipeline.py          detect -> parse -> map -> balance, shared by CLI and portal
  mapping_edit.py      writes new rules into a mapping without disturbing it; suggestions
  export.py            Odoo Journal Entries import CSV / flat CSV
  cli.py               inspect / daily / batch / invoice / accounts / check
portal/                FastAPI site: login, manager upload, admin dashboard, review, export
config/                property list, GL mappings per PMS, expense categories, Excel cell map
tools/                 extract_layout_text.py (build fixtures, debug parsing)
tests/                 fixtures + tests
```

## Files or database: the configuration switch

Everything runs from files by default. When the client is ready to manage properties and
logins themselves, flip one variable and use the Settings page instead:

| Variable | Values | Meaning |
|---|---|---|
| `PORTAL_STORE` | `yaml` (default) / `db` | `yaml`: properties, logins and GL mappings come from `config/`. `db`: they live in the portal database and are edited on `/admin` (add property, edit its report id, Odoo company, analytic, journal and mapping YAML; add or reset logins). |
| `DELIVERY_MODE` | `download` (default) / `odoo` | `download`: admin downloads the Odoo import CSV. `odoo`: the dashboard offers "Send to Odoo" for a whole day through the API (needs `ODOO_URL` and `ODOO_API_KEY`); CSV stays available. |

Switching to the database: start with `PORTAL_STORE=db`, log in with a user seeded from the
files (`python -m portal seed` does the same as the "Import from the config files" button on
`/admin`), then edit in the browser. Mapping YAML is validated on save; the pipeline picks up
edits immediately. The files stay as documentation and as the seed for a fresh install.

## Where the data lives: SQLite or Postgres, disk or object storage

The same two switches again: nothing set means one directory on one machine, which is right
for a laptop and for a single container with a persistent disk. Set them and the portal keeps
nothing locally, so the container can be replaced or run in more than one copy.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | unset | Unset: SQLite at `$PORTAL_DATA/portal.db`. Set to `postgresql://user:pass@host:5432/dbname`: PostgreSQL (needs `pip install "psycopg[binary]"`). Tables are created on first start either way. |
| `SUPABASE_URL` | unset | The project URL, e.g. `https://abcdefgh.supabase.co`. |
| `SUPABASE_SERVICE_KEY` | unset | The service role key. It bypasses row-level security, so it belongs on the host and never in the repository or the browser. |
| `SUPABASE_BUCKET` | unset | A **private** bucket, created in the Supabase dashboard. |

Uploads go to Supabase Storage only when all three `SUPABASE_*` variables are set; any of them
missing and files are written to `$PORTAL_DATA` as before. A file's location is recorded in the
database as `supabase://bucket/key` remotely or as an absolute path locally, and rows written
before the switch keep working: a plain path is still read from disk. Remote files are cached
in a temporary directory for the life of the process, because a pack is usually parsed
immediately after it is uploaded.

Storage is object storage, not a mounted disk, so a night-audit pack is uploaded once and read
back by key. Nothing else in the code knows the difference — `portal/storage.py` is the whole
adapter, and `portal/sql.py` is the equivalent for the two databases (it translates
placeholders, `RETURNING id` versus `lastrowid`, and column introspection).

A free Supabase project and a free Postgres database are enough to run the whole thing at small
scale, which makes the eventual move to paid plans a change of plan rather than a migration.
