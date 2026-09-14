Drop the sample night-audit reports and the GM Excel template here (git-ignored).

Suggested names so the batch command can pick them up:

    HGI-EXAMPLE_2026-09-13.pdf         PEP daily revenue report
    CY-EXAMPLE_2026-09-13.csv          Agilysys export
    ...

Then:  python -m pms_to_odoo daily --property HGI-EXAMPLE --file samples/HGI-EXAMPLE_2026-09-13.pdf --dry-run
