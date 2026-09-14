"""PMS parsers without a sample report yet.

Marriott (Agilysys) and Wyndham (SynXis) start as the generic label+amount table parser.
When their night-audit packs arrive, give them a dedicated module like pep.py / choice.py.
"""
from __future__ import annotations

from .generic_table import GenericTableParser


class MarriottAgilysysParser(GenericTableParser):
    """Marriott - Agilysys (Stay / LMS).  TODO: sample report needed."""
    pms = "AGILYSYS"
    brand = "Marriott"


class WyndhamSynxisParser(GenericTableParser):
    """Wyndham - SynXis Property Hub.  TODO: sample report needed."""
    pms = "SYNXIS"
    brand = "Wyndham"
