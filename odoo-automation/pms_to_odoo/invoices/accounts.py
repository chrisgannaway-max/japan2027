"""Automatic expense-account assignment for vendor invoices.

Precedence: remembered vendor account -> vendor patterns -> keyword patterns -> default.
The "remembered" table is filled by the portal whenever an invoice is exported or posted
with an account, so the second invoice from a vendor never needs anyone's attention.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Optional

import yaml


class AccountAssigner:
    def __init__(self, rules_path: Optional[Path], remembered: Optional[Callable[[str], Optional[str]]] = None):
        data = {}
        if rules_path and Path(rules_path).exists():
            data = yaml.safe_load(Path(rules_path).read_text()) or {}
        self.default = str(data.get("default_account") or "") or None
        self.vendors = [(re.compile(r["match"], re.I), str(r["account"])) for r in data.get("vendors", [])]
        self.keywords = [(re.compile(r["match"], re.I), str(r["account"])) for r in data.get("keywords", [])]
        self.remembered = remembered or (lambda vendor: None)

    def assign(self, vendor_name: str, description: str = "") -> tuple[Optional[str], str]:
        """(account_code, how) where how is remembered | vendor-rule | keyword | default | none."""
        vendor_name = (vendor_name or "").strip()
        if vendor_name:
            acct = self.remembered(vendor_name)
            if acct:
                return acct, "remembered"
            for pat, acct in self.vendors:
                if pat.search(vendor_name):
                    return acct, "vendor-rule"
        text = f"{vendor_name} {description or ''}"
        for pat, acct in self.keywords:
            if pat.search(text):
                return acct, "keyword"
        return (self.default, "default") if self.default else (None, "none")
