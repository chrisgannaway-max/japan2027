"""Parser registry: look a parser up by PMS name, or detect the PMS from report text."""
from __future__ import annotations

import re
from typing import Optional

from .base import BaseParser, collapse, read_text
from .agilysys import MarriottAgilysysParser
from .choice import ChoiceAdvantageParser
from .excel_template import ExcelTemplateParser
from .generic_table import GenericTableParser
from .hotelkey import IHGHotelKeyParser
from .opera import IHGOperaParser
from .pep import HiltonPEPParser
from .synxis import WyndhamSynxisParser

PARSERS: dict[str, type[BaseParser]] = {
    "PEP": HiltonPEPParser,              # Hilton
    "AGILYSYS": MarriottAgilysysParser,  # Marriott
    "HOTELKEY": IHGHotelKeyParser,       # IHG
    "OPERA": IHGOperaParser,             # IHG
    "CHOICEADV": ChoiceAdvantageParser,  # Choice
    "SYNXIS": WyndhamSynxisParser,       # Wyndham
    "GENERIC": GenericTableParser,
}
ALIASES = {"HILTON": "PEP", "MARRIOTT": "AGILYSYS", "CHOICE": "CHOICEADV", "CHOICEADVANTAGE": "CHOICEADV",
           "WYNDHAM": "SYNXIS", "IHG": "HOTELKEY", "HOTELKEY": "HOTELKEY", "OPERACLOUD": "OPERA"}


def get_parser(pms: str, **kwargs) -> BaseParser:
    key = pms.upper().replace(" ", "").replace("-", "").replace("_", "")
    key = ALIASES.get(key, key)
    if key not in PARSERS:
        raise KeyError(f"Unknown PMS '{pms}'. Known: {', '.join(sorted(PARSERS))}")
    return PARSERS[key](**kwargs)


def detect_pms(text: str) -> Optional[str]:
    """Guess the PMS from report text (used by `batch` and `inspect`)."""
    flat = collapse(text)
    low = flat.lower()
    # Not every Hilton property labels its code "Hotel ID" -- some print it on a bare line under
    # the hotel name -- so the report's own column heading is the surer mark of a PEP pack.
    if "final audit" in low and ("hotel id" in low or "net today" in low or "hotel balance" in low):
        return "PEP"
    if "trial balance report" in low and "net change" in low and "usd" in low:
        return "HOTELKEY"
    if "property code:" in low and ("hotel journal summary" in low or "final transaction closeout" in low
                                    or "software version" in low):
        return "CHOICEADV"
    if "trial_balance" in low or ("trial balance" in low and "balance yesterday" in low):
        return "OPERA"
    if "agilysys" in low or ("ledger summary" in low and "transaction type :" in low) \
            or low.startswith("category subcategory transaction type transaction item"):
        return "AGILYSYS"
    if "transaction totals summary" in low or "hotel ledger comparison" in low or re.search(r"\bsynxis\b", low):
        return "SYNXIS"
    return None


__all__ = ["BaseParser", "GenericTableParser", "ExcelTemplateParser", "PARSERS", "get_parser",
           "detect_pms", "read_text"]
