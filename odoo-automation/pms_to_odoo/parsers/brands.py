"""PMS parsers without a sample report yet.

Wyndham (SynXis) starts as the generic label+amount table parser. When its night-audit
pack arrives, give it a dedicated module like pep.py / agilysys.py.
"""
from __future__ import annotations

from .generic_table import GenericTableParser


class WyndhamSynxisParser(GenericTableParser):
    """Wyndham - SynXis Property Hub.  TODO: sample report needed."""
    pms = "SYNXIS"
    brand = "Wyndham"
