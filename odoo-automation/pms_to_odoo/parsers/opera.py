"""Oracle OPERA - "Trial Balance" (daily) as used at the IHG Candlewood property.

Sample: "IHG - Candlewood.pdf" (2 pages).

    Daily Transactions
      Balance Brought Forward             24,592.19
      Revenue
        1000  *Accommodation               1,546.10
      Revenue Total                        1,629.74
      Non Revenue                                   <- taxes
        7100  State Tax - Room                63.60
      Payment                                       <- negative = received
        9002  Direct Billing/City Ledger     - 64.06
        9004  Visa                          - 938.70
      ...
    Guest Ledger / AR Ledger / Deposit Ledger / Package Ledger
      Balance Yesterday ... Balance Today            <- ledger movements = Today - Yesterday

"Direct Billing/City Ledger" is a move from the guest ledger to the AR ledger; it is tagged
``transfer`` (not booked) because the AR Ledger balance change already carries it.
"AR Ledger Payments" (cash received against AR) is booked as a settlement line so the
AR decrease has a matching debit.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from ..models import DailyReport
from .base import BaseParser, collapse, iter_lines, parse_date, read_text, split_row

LEDGERS = ("Guest Ledger", "AR Ledger", "Deposit Ledger", "Package Ledger", "Passerby Ledger")
TRANSFER = re.compile(r"direct bill|city ledger|a/?r transfer", re.IGNORECASE)


class IHGOperaParser(BaseParser):
    pms = "OPERA"
    brand = "IHG"
    expects = "OPERA daily 'Trial Balance' report (trial_balance)"

    def parse(self, path: str | Path, property_code: str,
              business_date: Optional[date] = None) -> DailyReport:
        text = read_text(path)
        flat = collapse(text)
        d = None
        m = re.search(r"(?:Filter )?Date:\s*(\d{2}-\d{2}-\d{2,4})", flat)
        if m:
            d = parse_date(m.group(1))
        report = DailyReport(property_code=property_code, pms=self.pms,
                             business_date=business_date or d or date.today(), source_file=str(path))
        first = next((collapse(l) for _, l in iter_lines(text)), "")
        report.property_name = re.sub(r"\s+\d{2}-\d{2}-\d{2,4}.*$", "", first).strip()
        if "trial_balance" not in flat.lower() and "trial balance" not in flat.lower():
            report.warnings.append("This does not look like an OPERA trial_balance report.")
            report.recognised = False

        section = ""
        ledger: Optional[str] = None
        yesterday: Optional[Decimal] = None
        fname = Path(path).name
        row_re = re.compile(r"^(?P<code>\d{3,6})\s+(?P<label>.+)$")
        for n, line in iter_lines(text):
            c = collapse(line)
            lc = c.lower()
            src = f"{self.pms}:{fname}:L{n}"
            if c in LEDGERS:
                ledger, yesterday, section = c, None, "ledgerblock"
                continue
            if lc == "daily transactions" or lc.startswith("filter date") or lc.startswith("trial balance"):
                section, ledger = "", None
                continue
            label, nums = split_row(line)
            if not nums:
                if lc == "revenue":
                    section = "revenue"
                elif lc == "non revenue":
                    section = "tax"
                elif lc == "payment":
                    section = "settlement"
                continue
            amt = nums[0]
            if section == "ledgerblock" and ledger:
                if lc.startswith("balance yesterday"):
                    yesterday = amt
                elif lc.startswith("balance today"):
                    if yesterday is not None:
                        report.add(f"{ledger} Net Change", amt - yesterday, "ledger", src)
                    report.stats[f"{ledger} Balance Today"] = amt
                    ledger, yesterday = None, None
                else:
                    report.stats[f"{ledger} {label}"] = amt
                continue
            rm = row_re.match(label)
            if rm and section in ("revenue", "tax", "settlement"):
                code, lbl = rm.group("code"), rm.group("label").lstrip("*").strip()
                if section == "settlement":
                    sec = "transfer" if TRANSFER.search(lbl) else "settlement"
                    report.add(lbl, -amt, sec, src, code=code)
                else:
                    report.add(lbl, amt, section, src, code=code)
                continue
            if lc.startswith("ar ledger payments"):
                report.add("AR Ledger Payments", -amt, "settlement", src)
            elif any(lc.startswith(k) for k in ("revenue total", "non revenue total", "payment total",
                                                    "transaction total today", "grand total",
                                                    "balance brought forward", "balance carried forward",
                                                    "hotel balance", "deposit ledger activity", "accruals")):
                report.stats[label] = amt
        return report
