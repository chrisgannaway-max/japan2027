# PMS night audit and vendor invoices -> Odoo Accounting

Automation for two manual processes at the hotels:

1. **Daily night-audit entries.** Each property's PMS prints a night-audit pack. Today GMs
   re-key it into a spreadsheet and accounting re-keys that into the books. This tool parses
   the pack, maps every line to a GL account, checks the entry balances, and creates it in
   Odoo (draft by default, posted if you ask).
2. **Vendor invoices.** A PDF/photo of an invoice is read by Claude into a validated structure
   (vendor, number, dates, lines, totals), matched to the vendor in Odoo and created as a
   **draft vendor bill** with the original file attached for review.

## Status

Parsers are built and tested on real sample reports for five of the six PMS formats. Each
sample produces an entry that balances to the cent against the report's own control totals.

| Brand    | PMS              | Report the parser reads                         | Sample     | Parser key  |
|----------|------------------|-------------------------------------------------|------------|-------------|
| Hilton   | PEP              | **Final Audit** (FinalAudit PDF, 8 pages)       | OKCON      | `PEP`       |
| Choice   | choiceADVANTAGE  | Night-audit pack: **Final Transaction Closeout** + **Hotel Journal Summary** (+ Hotel Statistics) | TXI47 | `CHOICEADV` |
| IHG      | HotelKey         | **Trial Balance Report** (PDF, or the .eml it is attached to) | OKCMD | `HOTELKEY` |
| IHG      | OPERA            | Daily **Trial Balance** (trial_balance)          | Candlewood Moore | `OPERA` |
| Marriott | Agilysys Stay    | **Ledger Summary** grouped by ledger (City / Deposit / Guest) | OKCAW | `AGILYSYS`  |
| Wyndham  | SynXis           | generic label+amount parser until a sample arrives | none    | `SYNXIS`    |

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
- **Idempotent.** Journal ref is `PMS-PROPERTY-DATE`; rerunning finds the existing move.
  Bills dedupe on vendor + invoice number.
- **Draft first.** Entries and bills are created in draft. `--post` posts night-audit entries
  immediately once the mapping is trusted.
- **Audit trail.** `logs/daily_entries.csv` and `logs/invoices.csv`; every parsed line carries
  its source file and line number.

## Setup

```bash
cd odoo-automation
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
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
| `ANTHROPIC_API_KEY` | for invoice extraction                                             |

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
- **Agilysys Stay Ledger Summary**: one block per PMS ledger with BEGINNING/ENDING balances
  (the ledger movement) and PAYMENTS / REVENUE / TRANSFERS types. The REVENUE type mixes
  revenue and tax items; taxes are recognised by code `T…` and the word Tax. Transfers
  between ledgers net to zero and are not booked. The report has a GL CODE column, empty on
  the sample: if GL codes are maintained in Agilysys the mapping can use them directly.
- **OPERA Trial Balance**: payments are negative on the report; ledger movement = Balance
  Today - Balance Yesterday per ledger. "Direct Billing/City Ledger" is a transfer. If AR
  payments show up under "AR Ledger Payments" they are booked to an AR receipts clearing
  account; confirm on a day with AR activity.

## Scheduling

The CLI is stateless, so any scheduler works: a cron job or Windows Task Scheduler running
`batch --dir` against a folder where the packs land (a mailbox rule saving attachments, an
SFTP drop, or a manual save-as). HotelKey already e-mails its reports; PEP and
choiceADVANTAGE can schedule report e-mails as well.

## Tests

```bash
python -m pytest -q
```

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
    brands.py          SynXis placeholder (generic table parser)
    generic_table.py   config-driven label+amount parser
    excel_template.py  GM spreadsheet reader
  invoices/            Claude extraction (schema-validated) and vendor bill creation
  cli.py               inspect / daily / batch / invoice / accounts / check
config/                property list, GL mappings per PMS, expense categories, Excel cell map
tools/                 extract_layout_text.py (build fixtures, debug parsing)
tests/                 fixtures + tests
```
