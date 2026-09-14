"""Parser registry: look a parser up by PMS name."""
from __future__ import annotations

from .base import BaseParser
from .brands import (ChoiceAdvantageParser, HiltonPEPParser, IHGHotelKeyParser,
                     IHGOperaParser, MarriottAgilysysParser, WyndhamSynxisParser)
from .excel_template import ExcelTemplateParser
from .generic_table import GenericTableParser

PARSERS: dict[str, type[GenericTableParser]] = {
    "PEP": HiltonPEPParser,            # Hilton
    "AGILYSYS": MarriottAgilysysParser,  # Marriott
    "HOTELKEY": IHGHotelKeyParser,     # IHG
    "OPERA": IHGOperaParser,           # IHG
    "CHOICEADV": ChoiceAdvantageParser,  # Choice
    "SYNXIS": WyndhamSynxisParser,     # Wyndham
    "GENERIC": GenericTableParser,
}


def get_parser(pms: str, **kwargs) -> BaseParser:
    key = pms.upper().replace(" ", "").replace("-", "")
    aliases = {"HILTON": "PEP", "MARRIOTT": "AGILYSYS", "CHOICE": "CHOICEADV",
               "CHOICEADVANTAGE": "CHOICEADV", "WYNDHAM": "SYNXIS", "IHG": "HOTELKEY"}
    key = aliases.get(key, key)
    if key not in PARSERS:
        raise KeyError(f"Unknown PMS '{pms}'. Known: {', '.join(sorted(PARSERS))}")
    return PARSERS[key](**kwargs)


__all__ = ["BaseParser", "GenericTableParser", "ExcelTemplateParser", "PARSERS", "get_parser"]
