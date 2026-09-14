"""One parser class per PMS.

Each class starts life as a thin GenericTableParser with brand-specific defaults.
The ``TODO`` notes record what we still need from the sample reports (attached to
Harshil's email) before the layout can be finalised.  Where a system exports a clean
CSV/XLSX, replace the regex layout with a column-based ``parse`` override.
"""
from __future__ import annotations

from .generic_table import DEFAULT_LAYOUT, GenericTableParser


class HiltonPEPParser(GenericTableParser):
    """Hilton - PEP (Property Engagement Platform, Hilton's OnQ successor).

    Typical night-audit outputs: "Daily Revenue Report", "Trial Balance", "Manager's
    Report", "Payment Type Summary".  PEP reports usually come as PDF with Today / MTD
    / YTD columns; we keep column 0 (Today).
    TODO: confirm exact report name(s) and section headers from the sample.
    """
    pms = "PEP"
    brand = "Hilton"
    layout = {**DEFAULT_LAYOUT, "amount_column": 0}


class MarriottAgilysysParser(GenericTableParser):
    """Marriott - Agilysys (Stay / LMS).  Agilysys can export CSV/XLSX; if so, override
    ``parse`` with a column reader.
    TODO: confirm export format and whether payments are on the same report as revenue.
    """
    pms = "AGILYSYS"
    brand = "Marriott"
    extensions = (".pdf", ".csv", ".xlsx", ".txt")


class IHGHotelKeyParser(GenericTableParser):
    """IHG - HotelKey.  Daily Summary / Revenue Journal, usually PDF or CSV.
    TODO: confirm sample.
    """
    pms = "HOTELKEY"
    brand = "IHG"


class IHGOperaParser(GenericTableParser):
    """IHG - Oracle Opera (PMS / Cloud).  Night-audit pack usually contains
    'Trial Balance', 'Manager Report', 'Journal by Transaction Code' and
    'Payment Type Summary'.  Opera lists *transaction codes* alongside labels, so the
    row regex allows a leading numeric code.
    TODO: confirm which report(s) the property uses and the transaction-code list.
    """
    pms = "OPERA"
    brand = "IHG"
    layout = {
        **DEFAULT_LAYOUT,
        "row": r"^(?:(?P<code>\d{3,6})\s+)?(?P<label>[A-Za-z][A-Za-z0-9 &/%'().,\-_]*?)\s*[:.]*\s+"
               r"(?P<amounts>(?:[-(]?\$?[\d,]+(?:\.\d{1,2})?[-)]?(?:\s*CR)?\s*)+)$",
    }


class ChoiceAdvantageParser(GenericTableParser):
    """Choice - choiceADVANTAGE.  'Daily Revenue Report' / 'Guest Ledger Summary' /
    'Credit Card Reconciliation', typically PDF exports from the browser.
    TODO: confirm sample.
    """
    pms = "CHOICEADV"
    brand = "Choice"


class WyndhamSynxisParser(GenericTableParser):
    """Wyndham - SynXis Property Hub.  Daily Business Summary / Transaction Journal;
    exports as PDF or CSV.
    TODO: confirm sample.
    """
    pms = "SYNXIS"
    brand = "Wyndham"
