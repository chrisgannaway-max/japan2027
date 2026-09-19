# The estate

Everything here was read off Champion's own public portfolio page
(`championhotels.com/portfolio.html`) on 19 September 2026. It is a **scoping document,
not configuration**: nothing in it has been confirmed by Champion except the scope
decision recorded below, and no hotel in it is switched on except the six that already
were.

## The headline

Champion run **172 hotels**, not the six this was built and tested against.

**146 of them are in scope.** Champion have confirmed that hotels on a system we cannot
read are **out of scope** -- so Best Western, Motel 6, the two Radissons and the eight
independents come off the list, and no new parsers are being written. That is 26 hotels,
the smallest in the estate, and it removes the only open piece of unestimatable work.

| Franchise | In scope | Reader |
|---|---:|---|
| Wyndham | 44 | `SYNXIS` |
| Choice | 37 | `CHOICEADV` |
| Marriott | 23 | `AGILYSYS` |
| Hilton | 22 | `PEP` |
| IHG | 20 | `HOTELKEY` / `OPERA` |
| **Total** | **146** | |

| Out of scope | Hotels |
|---|---:|
| Best Western | 9 |
| Independent | 8 |
| G6 (Motel 6 / Studio 6) | 7 |
| Radisson | 2 |
| **Total** | **26** |

In-scope hotels, by state: OK 72, TX 16, IN 10, KS 10, FL 8, IL 7, MO 5, AR 3, NE 3,
NJ 3, NY 3, OH 2, CT 1, GA 1, MA 1, MN 1 -- sixteen states, half of it Oklahoma.

The six readers already built cover the whole of the scoped estate. **No new parser is
required for any hotel Champion want automated**, which is the single most useful thing
this exercise established.

## Two files

- **`config/champion_portfolio.csv`** -- all 172 as read, with an `in_scope` column.
  The 26 that are out stay in the file rather than being deleted, so that if anyone asks
  later why a Motel 6 is missing, the answer is written down.
- **`config/champion_hotels.import.csv`** -- the 100 in-scope hotels that can be imported
  today, in the shape the Hotels import screen expects. It passes the importer's checks
  with no errors. Every row is `enabled=no` except the six already live, so importing it
  changes nothing about how the nightly run behaves until each hotel is turned on
  deliberately.

The 46 in-scope hotels not in the import file are there only because their page carries no
code: all 44 Wyndhams (those links have no code in them), one Choice and one Marriott.
They need nothing but a code, which the first real pack from each will supply.

## What the codes are, and what they are not

The `code` column is the brand's own code, pulled out of the booking link on their page
(`.../hotels/okcmd/hoteldetail` -> `OKCMD`). For Marriott and IHG that is the same code
the night audit prints. **For Hilton and Choice it is not**:

| Hotel | Brand-site code | Code on the report |
|---|---|---|
| Embassy Suites OKC Northwest | `OKCONES` | `OKCON` |
| Comfort Inn Wichita Falls | `TXE81` | `TXI47` |

So `pms_property_id` -- the field that matches an arriving pack to a hotel -- is left
**blank** on every row, and the importer warns about each one. That warning is correct and
should stay until a real report from that hotel has been read. A wrong id is worse than
none: it silently lands a pack on the wrong hotel. A blank one just means the hotel has to
be picked by hand on the upload page.

## Four errors on their own page, worth mentioning to Harshil

1. Two different Residence Inns -- Northwest Expressway and OKC Airport -- link to the
   **same** Marriott page, so both resolve to `OKCRW`. The importer refused the file until
   one was blanked, which is the all-or-nothing rule doing its job. Someone has to say
   which code belongs to the airport hotel.
2. The Cambria West Orange card is titled *"Aloft Secaucus Meadowlands"* -- copied from
   the card above it. Its link is right, its name is not.
3. A Super 8 card is captioned `LOCATION` instead of a city, and its booking link points
   at Lees Summit, Missouri while its title says Rochester, Indiana. Read here as
   Rochester, IN, from the title.
4. Brandon Motor Lodge has its location as `BRANDON FL` with no comma.

None of this matters to the accounting. It matters because the portfolio page is the only
list of the estate anyone has handed over, and it is not clean enough to be one. A list
confirmed by Champion should replace this file before any bulk import.

## What is settled, and what is not

**Settled:** hotels with no reader are out. 146 in scope, six readers, no new parsers.

**Still open, and both worth an answer before the first bulk import:**

1. **The whole scoped estate at once, or Oklahoma first?** 72 of the 146 are in Oklahoma.
   An OKC-metro first phase is a few dozen hotels and needs no new code at all.
2. **One Odoo company per hotel, or one for the group?** At six hotels either works. At
   146 it is the difference between 146 company records and 146 analytic accounts, and
   changing it afterwards means re-posting history.

**Worth flagging separately:** the Renaissance Tulsa Hotel & Convention Center is the only
full-service hotel in the estate -- banquets, restaurant, bar, parking. Its chart of
accounts will be materially wider than the six select-service hotels tested so far. It is
on Agilysys, so the reader exists, but it should be the first hotel a sample pack is
requested from after the ones already live.

Until the two open questions are answered, `config/champion_hotels.import.csv` is a file
to look at, not a file to import.
