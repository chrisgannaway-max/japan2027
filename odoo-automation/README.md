# PMS night audit and vendor invoices -> Odoo Accounting

Automation for two manual processes at the hotels:

1. **Daily night-audit entries.** Each property's PMS prints a daily revenue / trial-balance
   pack after night audit. Today GMs re-key it into a spreadsheet and accounting re-keys that into
   the books. This tool parses the report, maps every line to a GL account, checks the entry
   balances, and creates it in Odoo (draft by default, posted if you ask).
2. **Vendor invoices.** A PDF/photo of an invoice is read by Claude into a validated structure
   (vendor, number, dates, lines, totals), matched to the vendor in Odoo and created as a
   **draft vendor bill** with the original file attached for review.

Status: **preliminary scaffold, built before receiving the sample reports.** The parsing
framework, GL mapping, balancing, Odoo connector, invoice pipeline and tests all work against a
synthetic Hilton-style report. Per-PMS layouts and real account codes are filled in once we have
the attachments from Harshil's email. See `PROPOSAL.md` for the approach and open questions.

Supported PMS (one parser class each, in `pms_to_odoo/parsers/brands.py`):

| Brand    | PMS              | Parser key  |
|----------|------------------|-------------|
| Hilton   | PEP              | `PEP`       |
| Marriott | Agilysys         | `AGILYSYS`  |
| IHG      | HotelKey         | `HOTELKEY`  |
| IHG      | Opera            | `OPERA`     |
| Choice   | choiceADVANTAGE  | `CHOICEADV` |
| Wyndham  | SynXis           | `SYNXIS`    |

Plus `ExcelTemplateParser`, which reads the GM spreadsheet the properties already fill in, so
the Odoo push can go live before every PMS parser is finished.

## How it works

```
PMS report (PDF/CSV/XLSX)  --parser-->  DailyReport (label, amount, section)
                                              |
                          config/gl_mapping/<property>.yaml (regex -> account, debit/credit side)
                                              v
                                       JournalEntry (balanced or it refuses)
                                              |
                                  Odoo account.move via JSON-2 (19+) or XML-RPC (<=18)
```

Design rules baked in:

- **Balanced or nothing.** An entry that does not balance within `tolerance` is never sent.
- **Every line must map.** A new revenue code on a report stops the run with the label named,
  instead of silently landing in the wrong account. (Optional `suspense_account` changes this.)
- **Idempotent.** The journal ref is `PMS-PROPERTY-DATE`; rerunning the same day finds the
  existing move and does nothing. Bills dedupe on vendor + invoice number.
- **Draft first.** Entries and bills are created in draft for accountant review. `--post` posts
  night-audit entries immediately once you trust the mapping.
- **Audit trail.** `logs/daily_entries.csv` and `logs/invoices.csv` record every run; each
  parsed line carries its source file and line number.

## Setup

```bash
cd odoo-automation
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp config/properties.example.yaml config/properties.yaml      # edit property list
cp config/gl_mapping/hilton_pep.example.yaml config/gl_mapping/<property>.yaml
cp config/expense_categories.example.yaml config/expense_categories.yaml
```

Odoo connection (environment variables):

| Variable         | Meaning                                                            |
|------------------|--------------------------------------------------------------------|
| `ODOO_URL`       | `https://yourcompany.odoo.com` or the on-premise URL              |
| `ODOO_API_KEY`   | API key of a dedicated bot user (Preferences -> Account Security)  |
| `ODOO_DB`        | database name (needed on servers hosting several databases)        |
| `ODOO_TRANSPORT` | `json2` (default, Odoo 19+) or `xmlrpc` (Odoo 18 and earlier)      |
| `ODOO_USER`      | bot user login, only for `xmlrpc`                                  |
| `ANTHROPIC_API_KEY` | for invoice extraction                                          |

Odoo Online note: the external API is only enabled on the *Custom* plan, not *One App Free* or
*Standard*. Odoo.sh and self-hosted have no such restriction.

In Odoo, create: a miscellaneous journal for night audit (code `NA` in the examples), one
analytic account per property if you want property-level P&L in one company, and the bot user
with Accounting rights.

## Usage

```bash
# verify config files and the Odoo connection
python -m pms_to_odoo check

# dump the chart of accounts to build mapping files
python -m pms_to_odoo accounts --company "Example Hotel LLC" > coa.csv

# one report, show mapping coverage + the T-account, send nothing
python -m pms_to_odoo daily --property HGI-EXAMPLE --file report.pdf --dry-run

# create the draft entry in Odoo (add --post to post it)
python -m pms_to_odoo daily --property HGI-EXAMPLE --file report.pdf

# GM spreadsheet instead of the PMS report
python -m pms_to_odoo daily --property HGI-EXAMPLE --file daily.xlsx --from-excel

# a folder of reports named <PROPERTY>_<YYYY-MM-DD>.pdf, e.g. from an email drop
python -m pms_to_odoo batch --dir inbox/ --done-dir processed/

# vendor invoice -> draft bill with the PDF attached
python -m pms_to_odoo invoice --file invoice.pdf --company "Example Hotel LLC" --dry-run
python -m pms_to_odoo invoice --file invoice.pdf --company "Example Hotel LLC" --create-vendor
```

Dry-run output for the bundled synthetic PEP report (`tests/fixtures/pep_daily_sample.txt`):

```
Journal entry PEP-HGI-EXAMPLE-2026-09-13  date=2026-09-13  journal=NA  company=Example Hotel LLC
  Account  Description                                Debit         Credit
  4000     Room Revenue                                          12,480.00
  ...
  1120     Visa/MC                                 8,914.22
  1210     Guest Ledger Net Change                   333.00
           TOTAL                                  14,705.91      14,705.91
```

## Adding a property

1. Add it to `config/properties.yaml` with its PMS key.
2. Run `daily --dry-run` on one real report. The coverage table shows each report label and the
   account it would hit, or `UNMAPPED`.
3. Add a rule or an `ignore` pattern for every `UNMAPPED` label in the property's mapping YAML
   until the entry balances. Mapping file format is documented at the top of
   `pms_to_odoo/mapping.py`.
4. Run for a week in draft, compare with the GM spreadsheet, then switch on `--post`.

## Scheduling

The CLI is stateless, so any scheduler works: a cron job or Windows Task Scheduler running
`batch --dir` against a folder that the PMS emails/exports land in, or an Odoo scheduled action
calling this via a small wrapper. The "collect the reports" step depends on what each PMS can do
(scheduled email, SFTP export, manual download), which is one of the open questions.

## Tests

```bash
python -m pytest -q
```

Tests use an in-memory fake of the Odoo API (`tests/fake_odoo.py`), so they need no server.

## Layout

```
pms_to_odoo/
  models.py            ReportLine / DailyReport / JournalEntry
  odoo_client.py       JSON-2 + XML-RPC transports, lookups, idempotent create
  mapping.py           YAML rules -> balanced JournalEntry
  journal.py           JournalEntry -> account.move payload, draft/post
  parsers/             base helpers, generic label+amount parser, per-PMS classes, Excel template
  invoices/            Claude extraction (schema-validated) and vendor bill creation
  cli.py               daily / batch / invoice / accounts / check
config/                example property list, GL mappings, expense categories, Excel cell map
tests/                 fixtures + tests
```
