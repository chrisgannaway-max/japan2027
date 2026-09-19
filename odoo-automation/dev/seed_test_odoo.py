"""Give a *test* Odoo the master data one property's mapping expects, so a night can be posted.

The connection check names what is missing; this creates it. Only worth running against a
throwaway instance -- see `dev/odoo-compose.yml` -- because on a real server the chart of
accounts is the client's accountant's work and not something a script should invent.

    ODOO_URL=... ODOO_DB=... ODOO_TRANSPORT=xmlrpc ODOO_USER=... ODOO_API_KEY=... \
        python3 dev/seed_test_odoo.py OKCON --yes-this-is-a-test-instance

It creates only what is absent: the journal, the property's analytic account, and any GL code
in the mapping that the server does not have. Run it twice and the second run does nothing.

Account types are guessed from the code's leading digit, which is the USALI convention the
example mappings use (1 asset, 2 liability, 4 revenue, 6 expense). They only have to be
plausible enough for Odoo to accept a balanced entry; nobody is reading these books.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pms_to_odoo.mapping import GLMapping                      # noqa: E402
from pms_to_odoo.odoo_client import OdooClient, OdooError, OdooSettings  # noqa: E402
from pms_to_odoo.pipeline import load_properties, resolve      # noqa: E402

GUARD = "--yes-this-is-a-test-instance"

#: leading digit -> an Odoo account type that will accept the postings we make
TYPE_BY_FIRST_DIGIT = {
    "1": "asset_current",
    "2": "liability_current",
    "3": "equity",
    "4": "income",
    "5": "expense",
    "6": "expense",
}


def account_type(code: str) -> str:
    return TYPE_BY_FIRST_DIGIT.get(code[:1], "asset_current")


def analytic_plan(client: OdooClient) -> int:
    """Odoo 17 requires every analytic account to sit in a plan."""
    rows = client.search_read("account.analytic.plan", [], ["id", "name"], limit=1)
    if rows:
        return rows[0]["id"]
    return client.create("account.analytic.plan", {"name": "Properties"})


def main(code: str) -> int:
    props = load_properties(Path("config/properties.example.yaml"))
    prop = props.get(code)
    if prop is None:
        print(f"No property {code!r}. Known: {', '.join(sorted(props))}")
        return 1
    mapping = GLMapping.load(resolve(prop, "gl_mapping"))
    client = OdooClient.connect(OdooSettings.from_env())
    made = []

    wanted = {str(r.account): account_type(str(r.account)) for r in mapping.rules if r.account}
    have = {r["code"] for r in client.search_read("account.account", [], ["code"])}
    for acct, kind in sorted(wanted.items()):
        if acct in have:
            continue
        client.create("account.account", {"code": acct, "name": f"{acct} (test)",
                                          "account_type": kind})
        made.append(f"account {acct} ({kind})")

    if mapping.journal:
        if not client.search_read("account.journal", [("code", "=", mapping.journal)], ["id"], limit=1):
            client.create("account.journal", {"name": "Night Audit", "code": mapping.journal,
                                              "type": "general"})
            made.append(f"journal {mapping.journal}")

    if mapping.analytic:
        found = client.search_read("account.analytic.account",
                                   ["|", ("code", "=", mapping.analytic),
                                    ("name", "=", mapping.analytic)], ["id"], limit=1)
        if not found:
            client.create("account.analytic.account", {"name": mapping.analytic,
                                                       "code": mapping.analytic,
                                                       "plan_id": analytic_plan(client)})
            made.append(f"analytic account {mapping.analytic}")

    if made:
        print(f"Created {len(made)} thing(s) for {code}:")
        for m in made:
            print("  ", m)
    else:
        print(f"Nothing to do: this Odoo already has everything {code} needs.")
    if mapping.company:
        print(f"\nNote: the mapping posts to company {mapping.company!r}, which was not created "
              "-- companies are a structural decision, not test data. Clear `company:` in "
              f"{resolve(prop, 'gl_mapping').name} to post to whatever company this server has.")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != GUARD]
    if GUARD not in sys.argv[1:] or not args:
        print(__doc__)
        print(f"Refusing to run without {GUARD} and a property code.")
        raise SystemExit(2)
    try:
        raise SystemExit(main(args[0]))
    except OdooError as e:
        print(f"Odoo refused: {e}")
        raise SystemExit(1)
