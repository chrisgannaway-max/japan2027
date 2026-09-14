"""PMS night-audit reports and vendor invoices -> Odoo Accounting.

Package layout:
    parsers/     one parser per PMS (PEP, Agilysys, HotelKey, Opera, Choice Advantage, Synxis)
    mapping.py   report line labels -> GL account codes (YAML driven)
    journal.py   balanced journal entry (account.move) from a parsed daily report
    odoo_client  thin client over Odoo's external API (JSON-2 for 19+, XML-RPC for <=18)
    invoices/    Claude-based invoice extraction -> Odoo vendor bill
    cli.py       command line entry point (python -m pms_to_odoo ...)
"""

__version__ = "0.1.0"
