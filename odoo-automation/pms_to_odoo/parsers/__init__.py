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

# Marriott **Fosse** is deliberately absent.  It came up as the seventh system in the estate --
# Marriott's older property-management system, still running at properties that had not been
# moved to Agilysys -- and was carried as an open item for a while because no sample of it had
# ever been seen.  Champion have since confirmed those properties are not part of this project,
# so it was never built rather than built and left untested.  Nothing here has read a Fosse
# report; the Marriott property in scope (OKCAW) is on Agilysys, and that parser was written
# against a real export.
#
# If a Fosse property is ever added, this is a new parser and a real piece of work -- not a
# configuration change.  Ask for one night's report first: a format nobody has seen cannot be
# estimated, and the two Hilton PEP layouts showed that even one vendor's own report varies
# enough to need its own reading.
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
