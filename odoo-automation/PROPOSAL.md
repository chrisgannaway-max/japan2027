# Odoo accounting automation: approach and findings

Prepared from Harshil's email and the sample night-audit reports (Hilton PEP, choiceADVANTAGE,
HotelKey, OPERA, Agilysys). Only the Wyndham (SynXis) sample is still needed.

## 1. What we are replacing

| Today                                                     | Proposed                                                     |
|-----------------------------------------------------------|--------------------------------------------------------------|
| GM reads PMS night-audit pack, types numbers into Excel   | Pack is parsed automatically (PDF or the e-mail it arrives in) |
| Accounting re-keys the Excel into the ledger              | Balanced journal entry created in Odoo, draft for review     |
| Vendor invoices typed into AP by hand                     | Invoice read by Claude, draft bill created with PDF attached |

The old Peachtree automation did the first half. Odoo has a real API, so we post straight
into it instead of generating import files.

## 2. What the samples showed

All five packs carry the same information in different clothes: revenue by code, taxes,
payments by type, and the movement of the PMS ledgers (guest ledger, city/AR ledger,
advance deposits). Each report also carries its own control totals, and on every sample the
identity **revenue + tax - payments = change in ledgers** holds to the cent. That identity is
what makes the journal entry balance without any plug.

| Brand / PMS            | Report to export nightly                          | Notes from the sample                                                 |
|------------------------|---------------------------------------------------|-----------------------------------------------------------------------|
| Hilton / PEP           | **Final Audit** (8 pages)                          | Book the *Net Today* column. Ledger "Net Change" rows sum to the Hotel Balance change. |
| Choice / choiceADVANTAGE | Night-audit pack, needs **Final Transaction Closeout** and **Hotel Journal Summary** | Only codes with activity print. Direct-bill and AR credit applications are internal transfers. |
| IHG / HotelKey         | **Trial Balance Report** (e-mailed PDF)            | Folio-centric signs (charges debit, payments credit); "(Offset)" rows are the ledgers. Self-balancing. |
| IHG / OPERA            | Daily **Trial Balance**                            | Payments negative; ledger movement = Balance Today - Yesterday. Direct bill is a transfer to AR. |
| Marriott / Agilysys Stay | **Ledger Summary**, Group By: Ledger             | One block per ledger with beginning/ending balances. Transfers net to zero. Has a GL CODE column: if filled in Agilysys, mapping becomes a direct code lookup. |

Reports that are *not* needed for the entry: PEP and HotelKey "Hotel Statistics", the guest
lists, aging and tax-exempt pages of the Choice pack (they are read only for occupancy/ADR
statistics or ignored).

## 3. Daily entries

1. A parser per PMS converts the report to `(label, code, amount, section)` lines with one
   sign convention: revenue and tax earned positive, payments received positive, ledger
   changes signed, internal transfers flagged and excluded.
2. A per-property YAML mapping turns labels or PMS codes into GL accounts. The side
   (debit/credit) follows from the section, so most rules are one line.
3. The entry must balance and every line must map; otherwise the run stops and names the
   line. Nothing partial reaches Odoo.
4. The entry is created in Odoo as a draft with a unique reference (`PEP-OKCON-2025-11-10`)
   so reruns are harmless. Optionally posted immediately.

Property recognition is automatic: PEP prints the Hotel ID, choiceADVANTAGE the Property
Code, HotelKey its code in the header, OPERA the hotel name. A folder of mixed PDFs and
e-mails can therefore be processed in one pass.

**Interim path.** The GM spreadsheet can feed the same pipeline (cell map config) so
double entry stops before the mapping for every property is signed off.

## 4. Vendor invoices

**A. Odoo's built-in digitization.** Bills e-mailed to an Odoo alias are OCR'd with paid IAP
credits and created as drafts. No code, per-page cost, good for clean typed invoices. Vendor
matching and account coding still need review.

**B. Claude-based extraction (built).** The PDF or photo is read into a strict schema (vendor,
tax id, number, dates, lines, totals, category hint, confidence, review notes), checked
arithmetically, matched to the Odoo vendor by tax id, e-mail or name, and created as a draft
bill with the file attached and the notes on the bill. Handles scans and odd layouts; a few
cents per invoice.

Recommendation: start with B for control over vendor matching and expense defaults; keep A in
mind if the preference is to stay entirely inside Odoo. Bills stay in draft until approved.

## 5. Odoo specifics that affect the design

- **Version and hosting.** Odoo 19 exposes a JSON API (`/json/2/model/method`, API-key bearer
  auth). Earlier versions use XML-RPC. Both are implemented; we pick by version.
- **Odoo Online plan.** External API access requires the *Custom* plan; not an issue on
  Odoo.sh or self-hosted. Confirm before go-live.
- **Companies vs analytic accounts.** One legal entity per hotel means multi-company; several
  hotels in one entity means analytic accounts per property. Supported either way.
- **Chart of accounts.** Mapping files use account codes; the chart must be settled first.
  The tool dumps it to CSV to build mappings from.
- **Bot user.** A dedicated API user with Accounting rights and an API key, per Odoo's own
  recommendation, so postings are attributable and the key can be rotated.

## 6. Open questions for Shirish

1. The Wyndham (SynXis) sample pack, and the Hilton Excel template the GMs fill in.
2. Two or three **consecutive** days per property, including a day with direct-bill
   activity and a day with an AR payment, to confirm how each PMS reports transfers (PEP
   "BILL TO COMPANY", OPERA "AR Ledger Payments").
3. Odoo version, hosting (Online / Odoo.sh / self-hosted) and plan.
4. Company structure in Odoo: one company per hotel, or one company with analytic accounts?
5. The chart of accounts, or a go-ahead to propose a USALI-style one. The Hilton Excel
   template would show how the spreadsheet maps to accounts today.
6. Report delivery: one shared mailbox that every PMS e-mails its pack to nightly (HotelKey
   already does; PEP, choiceADVANTAGE, OPERA and Agilysys can schedule exports), with a
   morning exception report listing properties that did not report or did not balance.
   Preferred over a per-property upload site, which would keep a manual nightly step.
7. Approval policy: auto-post night-audit entries after a trial period, or always review?
   Same question for bills above a threshold.
8. Invoice volume per month and where invoices arrive today.
9. Card settlements: post card receipts to a clearing account per processor and reconcile
   deposits via bank feeds in Odoo?
10. Gratuities and service charges (PEP "TIPS", "SVC CHG"): liability or revenue accounts?

## 7. Suggested phasing

| Phase | Scope                                                                   | Depends on               |
|-------|-------------------------------------------------------------------------|--------------------------|
| 0     | Parsers for the five sampled PMSs (done); connect to an Odoo sandbox; dump chart of accounts | API access |
| 1     | OKCON (PEP) end to end, draft entries, one week in parallel with the spreadsheet | chart of accounts |
| 2     | TXI47, OKCMD, Candlewood, OKCAW live; SynXis parser                      | SynXis sample, delivery method |
| 3     | Invoice intake for one property; tune vendor matching and categories     | invoice samples          |
| 4     | Scheduling and report collection; switch pilots to auto-post             |                          |
| 5     | Roll out to all properties                                               |                          |

## 8. What exists in this repo now

Parsers for PEP, choiceADVANTAGE, HotelKey, OPERA and Agilysys tested on the real samples (entries balance
to the cent), GL mapping engine, idempotent journal creation, Odoo client for both API
styles, invoice extraction pipeline and draft bill creation, CLI with auto-detection and
`.eml` support, example configs, tests. See `README.md` for usage.
