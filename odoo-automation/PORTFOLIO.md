# The estate

Everything here was read off Champion's own public portfolio page
(`championhotels.com/portfolio.html`) on 19 September 2026. It is a **scoping document,
not configuration**: nothing in it has been confirmed by Champion, and no hotel in it is
switched on except the six that already were.

## The headline

**172 hotels**, not the six or seven this has been built and tested against.

| Franchise | Hotels | Reader we have |
|---|---:|---|
| Wyndham | 44 | `SYNXIS` |
| Choice | 37 | `CHOICEADV` |
| Marriott | 23 | `AGILYSYS` |
| Hilton | 22 | `PEP` |
| IHG | 20 | `HOTELKEY` / `OPERA` |
| Best Western | 9 | — |
| Independent | 8 | — |
| G6 (Motel 6 / Studio 6) | 7 | — |
| Radisson | 2 | — |

Spread over 16 states: OK 86, TX 17, IN 11, KS 11, FL 9, IL 8, MO 7, NY 6, AR 4,
NE 3, NJ 3, MA 2, OH 2, CT 1, GA 1, MN 1.

**146 of the 172 are on a system we can already read.** The six systems built so far
cover 85% of the estate by hotel count, which is the good news in all of this.

**26 are not**: Best Western, Motel 6, the two Radissons and the eight independents.
Those are four more readers, and a reader cannot be estimated before a night's report has
been seen -- the two Hilton PEP layouts showed that even one vendor varies enough to need
its own reading. They are also the smallest hotels in the estate, so they may simply be
out of scope; that is Shirish's call, not ours.

## Two files

- **`config/champion_portfolio.csv`** -- all 172, as read. Reference only.
- **`config/champion_hotels.import.csv`** -- the 100 that can be imported today, in the
  shape the Hotels import screen expects. It passes the importer's checks with no errors.
  Every row is `enabled=no` except the six already live, so importing it changes nothing
  about how the nightly run behaves until each hotel is turned on deliberately.

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

72 hotels are not in the import file at all:

- **46** are on a system we can read but have no code on their page -- all 44 Wyndhams
  (the Wyndham links carry no code), one Choice, one Marriott.
- **26** have no reader.

Both groups need one night's pack per hotel before they can be added, which is the same
thing they need anyway.

## Three errors on their own page, worth mentioning to Harshil

1. Two different Residence Inns -- Northwest Expressway and OKC Airport -- link to the
   **same** Marriott page, so both resolve to `OKCRW`. The importer refused the file until
   one was blanked, which is the all-or-nothing rule doing its job. Someone has to say
   which code belongs to the airport hotel.
2. A Super 8 card is captioned `LOCATION` instead of a city, and its booking link points
   at Lees Summit, Missouri while its title says Rochester, Indiana. Read here as
   Rochester, IN, from the title.
3. Brandon Motor Lodge has its location as `BRANDON FL` with no comma.

None of this matters to the accounting. It matters because the portfolio page is the only
list of the estate anyone has handed over, and it is not clean enough to be one.

## What this changes

Nothing technical. The pipeline does not care whether it is fed 6 hotels or 146 -- the
per-hotel work is a GL mapping, an Odoo analytic account and one confirmed report id.

What it changes is the conversation with Shirish, and there are three questions now that
were not obviously there before:

1. **Is the whole estate in scope, or the Oklahoma City properties first?** 86 of the 172
   are in Oklahoma. A first phase of "OKC metro, systems we already read" is roughly 60
   hotels and needs no new code at all.
2. **One Odoo company per hotel, or one for the group?** At six hotels either works. At
   146 it is the difference between 146 company records and 146 analytic accounts, and it
   has to be settled before the first bulk import, not after.
3. **What happens to the 26 with no reader?** Out of scope, hand-entered, or four more
   parsers at a price that cannot be quoted until a report has been seen.

Until those are answered, `config/champion_hotels.import.csv` is a file to look at, not a
file to import.
