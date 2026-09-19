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


def company_of(client: OdooClient, wanted: str = "") -> int:
    """The company to hang everything off.

    account.account will not create without one, and says so as "IndexError: tuple index out
    of range" rather than anything a person could act on.
    """
    if wanted:
        rows = client.search_read("res.company", [("name", "=", wanted)], ["id"], limit=1)
        if rows:
            return rows[0]["id"]
    rows = client.search_read("res.company", [], ["id", "name"], limit=1)
    if not rows:
        raise OdooError("This Odoo has no companies at all, which should not be possible.")
    return rows[0]["id"]


def with_company(client: OdooClient, model: str, values: dict, company_id: int) -> dict:
    """Name the company the way this version of the model expects it.

    Odoo 17.2 turned account.account's single company_id into a company_ids many-to-many.
    Asking the model which field it has beats guessing from a version number.
    """
    fields = client.fields_of(model)
    if "company_ids" in fields:
        return values | {"company_ids": [(6, 0, [company_id])]}
    if "company_id" in fields:
        return values | {"company_id": company_id}
    return values


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

    company_id = company_of(client, mapping.company)
    # Create and read back as the same company, or Odoo hands out records the later lookups
    # cannot see.
    ctx = {"allowed_company_ids": [company_id]}

    wanted = {str(r.account): account_type(str(r.account)) for r in mapping.rules if r.account}
    have = {r["code"] for r in client.search_read("account.account", [], ["code"], context=ctx)}
    for acct, kind in sorted(wanted.items()):
        if acct in have:
            continue
        client.create("account.account",
                      with_company(client, "account.account",
                                   {"code": acct, "name": f"{acct} (test)",
                                    "account_type": kind}, company_id),
                      context=ctx)
        made.append(f"account {acct} ({kind})")

    if mapping.journal:
        if not client.search_read("account.journal", [("code", "=", mapping.journal)], ["id"],
                                  limit=1, context=ctx):
            client.create("account.journal",
                          with_company(client, "account.journal",
                                       {"name": "Night Audit", "code": mapping.journal,
                                        "type": "general"}, company_id),
                          context=ctx)
            made.append(f"journal {mapping.journal}")

    if mapping.analytic:
        found = client.search_read("account.analytic.account",
                                   ["|", ("code", "=", mapping.analytic),
                                    ("name", "=", mapping.analytic)], ["id"], limit=1,
                                   context=ctx)
        if not found:
            client.create("account.analytic.account", {"name": mapping.analytic,
                                                       "code": mapping.analytic,
                                                       "plan_id": analytic_plan(client)},
                          context=ctx)
            made.append(f"analytic account {mapping.analytic}")

    if made:
        print(f"Created {len(made)} thing(s) for {code}:")
        for m in made:
            print("  ", m)
    else:
        print(f"Nothing to do: this Odoo already has everything {code} needs.")
    names = client.search_read("res.company", [("id", "=", company_id)], ["name"], limit=1)
    here = names[0]["name"] if names else str(company_id)
    print(f"\nAll of it belongs to company {here!r}.")
    if mapping.company and mapping.company != here:
        print(f"The mapping posts to {mapping.company!r}, which this server does not have. "
              "Companies are a structural decision, not test data, so none was created: either "
              f"clear `company:` in {resolve(prop, 'gl_mapping').name}, or set it to {here!r}.")
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
