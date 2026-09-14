# Odoo accounting automation: preliminary approach

Prepared from Harshil's email ahead of the call with Shirish. The report attachments and the
Hilton Excel template were not available in this workspace, so everything below is designed to
be finished quickly once they are.

## 1. What we are replacing

| Today                                                     | Proposed                                                     |
|-----------------------------------------------------------|--------------------------------------------------------------|
| GM reads PMS night-audit pack, types numbers into Excel   | Report file is parsed automatically                          |
| Accounting re-keys the Excel into the ledger              | Balanced journal entry created in Odoo, draft for review     |
| Vendor invoices typed into AP by hand                     | Invoice read by Claude, draft bill created with PDF attached |

The old Peachtree automation did the first half of this. The difference now is that Odoo has a
real API, so we post straight into it instead of generating import files.

## 2. Daily entries

**Input.** One night-audit report per property per day. Five PMS families:
PEP (Hilton), Agilysys (Marriott), HotelKey and Opera (IHG), choiceADVANTAGE (Choice),
SynXis (Wyndham). Each has its own layout but the content is the same: revenue by type, taxes,
payments by type, ledger movements, and statistics.

**Processing.**
1. A parser per PMS converts the file to a list of `(label, amount, section)` lines.
   Most reports are "label ... Today MTD YTD" tables, so a shared, config-driven parser covers
   them; a PMS that exports clean CSV gets a column reader instead.
2. A per-property YAML mapping turns labels into GL accounts and debit/credit sides.
   Revenue and tax are credits, payments and AR are debits, ledger changes are signed.
3. The entry must balance. If it doesn't, or a label has no mapping, the run stops and names
   the line. Nothing partial reaches Odoo.
4. The entry is created in Odoo as a draft journal entry with a unique reference
   (`PEP-HGI-XXX-2026-09-13`), so reruns are harmless. Optionally posted immediately.

**Interim path.** The GM spreadsheet can be the input for the same pipeline (cell map config),
so properties can stop double-entry before every PMS parser is tuned.

**Delivery.** A command line tool that a scheduler runs against a folder where the reports land.
Getting the reports into that folder is the piece that differs per PMS: scheduled email export
(PEP, Opera, choiceADVANTAGE support this), SFTP, or a manual save-as. The tool itself is
stateless and can run on a small VM, a workstation, or inside Odoo as a scheduled action later.

## 3. Vendor invoices

Two options, not mutually exclusive:

**A. Odoo's built-in digitization.** Odoo Accounting can OCR bills sent to a mailbox alias
(`bills@...`) using paid IAP credits, and creates draft bills. Zero code, per-page cost, good for
clean, typed invoices. Vendor matching and account coding still need review.

**B. Claude-based extraction (built here).** A PDF or photo goes to Claude with a strict output
schema (vendor, tax id, number, dates, lines, totals, category hint, confidence, review notes).
The result is checked arithmetically, matched to the Odoo vendor by tax id, email or name, and a
draft bill is created with the file attached and the confidence/notes in the bill's narration.
Handles scans, hand-written notes and odd layouts better, and the category hint lets us
default the expense account per vendor/category. Cost is a few cents per invoice.

Recommendation: start with B for the invoice flow because it gives us control over vendor
matching and account defaults; keep A in mind if Shirish prefers everything inside Odoo.
Either way bills stay in draft until an accountant approves, which is also what auditors expect.

## 4. Odoo specifics that affect the design

- **Version and hosting.** Odoo 19 exposes a JSON API (`/json/2/model/method`, API-key bearer
  auth). Earlier versions use XML-RPC. Both are implemented; we pick by version.
- **Odoo Online plan.** External API access requires the *Custom* plan. On Odoo.sh or
  self-hosted this does not apply. Worth confirming before the go-live date.
- **Companies vs analytic accounts.** If each hotel is its own legal entity, Odoo will be
  multi-company and each property maps to a company. If several hotels sit in one entity, we
  use analytic accounts per property for property-level P&L. The tool supports both.
- **Chart of accounts.** Mapping files use account codes, so the chart must be settled first.
  `python -m pms_to_odoo accounts` dumps it to CSV to build mappings from.
- **Bot user.** A dedicated API user with Accounting rights and an API key, per Odoo's own
  recommendation, so postings are attributable and the key can be rotated.

## 5. What I need from the call with Shirish

1. The sample reports (one per PMS) and the Hilton Excel template. Two or three consecutive
   days per property is ideal so we can see ledger movements.
2. Odoo version, hosting (Online / Odoo.sh / self-hosted) and plan.
3. Company structure in Odoo: one company per hotel, or one company with analytic accounts?
4. The chart of accounts, or confirmation that we should propose a USALI-style one.
5. How the existing spreadsheet maps to accounts today (this becomes the first mapping file).
6. Where night-audit reports can be delivered automatically per PMS (email, SFTP, portal).
7. Approval policy: post night-audit entries automatically after a trial period, or always
   review? Same question for bills above a threshold.
8. Invoice volume per month and where invoices arrive today (email inbox, paper, portals).
9. Bank/credit-card settlement: do we reconcile card deposits in Odoo (bank feeds), and should
   the daily entry post card receipts to a clearing account per processor?

## 6. Suggested phasing

| Phase | Scope                                                                   | Depends on          |
|-------|-------------------------------------------------------------------------|---------------------|
| 0     | This scaffold; connect to the Odoo sandbox; dump chart of accounts       | API access          |
| 1     | One Hilton property end to end from the PMS report, draft entries        | PEP sample, mapping |
| 2     | Remaining PMS parsers, one pilot property per brand                      | samples             |
| 3     | Invoice intake for one property; tune vendor matching and categories     | invoice samples     |
| 4     | Scheduling, report collection, switch pilots to auto-post                | delivery method     |
| 5     | Roll out to all properties                                               |                     |

## 7. What exists in this repo now

Working and tested against a synthetic Hilton-style report: parsing, GL mapping, balance check,
idempotent journal creation, Odoo client for both API styles, invoice extraction pipeline and
draft bill creation, CLI, example configs. See `README.md` for usage.
