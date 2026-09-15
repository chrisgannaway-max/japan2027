"""Does this file look like a vendor invoice?

Used to catch an upload that landed on the wrong page.  Deliberately dependency-free so the
night-audit pipeline can import it without pulling in the invoice reader.

This is a hint, never a decision: a "yes" only offers to move the file to the invoice page.
"""
from __future__ import annotations

import re

STRONG = [
    r"\binvoice\s*(?:no\.?|number|#)",
    r"\b(?:total|amount|balance)\s+due\b",
    r"\bremit\s+(?:to|payment)\b",
    r"\bbill\s+to\b",
    r"\bpurchase\s+order\b|\bP\.?O\.?\s*(?:no\.?|#)",
    r"\bpayment\s+terms\b|\bnet\s+\d{1,3}\b",
]
WEAK = [r"\binvoice\b", r"\bsubtotal\b", r"\bsales\s+tax\b", r"\bquantity\b|\bqty\b",
        r"\bunit\s+price\b", r"\bdue\s+date\b", r"\bvendor\b|\bsupplier\b", r"\bship\s+to\b"]
#: night-audit vocabulary; its presence argues against this being an invoice
AUDIT = [r"\bnight\s+audit\b", r"\btrial\s+balance\b", r"\bguest\s+ledger\b", r"\bledger\s+summary\b",
         r"\boccupancy\b", r"\brevpar\b", r"\badr\b", r"\brooms?\s+sold\b", r"\bbusiness\s+date\b"]


def invoice_score(text: str) -> tuple[int, list[str]]:
    """(score, what matched).  Strong signals count double; night-audit words count against."""
    flat = re.sub(r"\s+", " ", text or "")
    score, why = 0, []
    for pat in STRONG:
        if re.search(pat, flat, re.I):
            score += 2; why.append(pat)
    for pat in WEAK:
        if re.search(pat, flat, re.I):
            score += 1; why.append(pat)
    for pat in AUDIT:
        if re.search(pat, flat, re.I):
            score -= 2
    return score, why


def looks_like_invoice(text: str, threshold: int = 4) -> bool:
    return invoice_score(text)[0] >= threshold
