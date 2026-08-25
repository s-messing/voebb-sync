# voebb

Read-only programmatic access to a [VÖBB](https://www.voebb.de) library account
(Verbund der Öffentlichen Bibliotheken Berlins) from Python.

VÖBB has no public API, so this is a scraping client for the aDIS/BMS web app.
It reads your current loans and searches the catalogue. It deliberately does
**not** renew, reserve, or cancel anything.

## Setup

```bash
cp .env.example .env    # then fill in your library card number and password
uv sync
```

## CLI

```bash
uv run voebb loans                        # borrowed items, soonest due first
uv run voebb --json loans                 # same, as JSON
uv run voebb search "Kafka Verwandlung"   # catalogue search, no login needed
uv run voebb search "Kafka" -n 20
```

```
08.09.2026   (+14 Tage)  Beispieltitel : ein Hörspiel in 6 Teilen  Musterbezirk: ...
```

## Library

```python
from voebb import VoebbClient

with VoebbClient() as client:
    for loan in client.loans():
        print(loan.due_date, loan.days_left, loan.title)

    for hit in client.search("Kafka"):
        print(hit.position, hit.title, hit.year)
```

`VoebbClient()` reads `VOEBB_USER` / `VOEBB_PASSWORD` from `.env`; pass a
`Credentials` object instead if you source them elsewhere. Using the client as a
context manager logs out at the end, which frees the session server-side.

## How it works

`www.voebb.de` runs aDIS/BMS, an Apache-Tapestry-style app where all state lives
on the server. There are no meaningful URLs: each page holds one
`<form name="Form0">`, and every interaction POSTs that whole form plus one
control naming what was clicked.

Three values rotate on *every* response and must never be hardcoded — the `_sid`
path segment in the form action, the per-page `identity` token, and
`requestCount`. `AdisSession._absorb` re-reads all of them after each request;
that is the invariant the client rests on.

Navigation links are wired to JavaScript rather than hrefs. They are followed by
posting a 12-character fixed-width `selected` field — `"ZTEXT"` padded to 12
followed by a screen code (`*SBK` account, `*SZA` loans), mirroring
`htmlOnLink()` in the site's own `aDISMain.min.js`.

Account login is OIDC: opening "Mein Konto" redirects to `/oidcp/authorize`,
whose form posts `L#AUSW` (card number), `LPASSW`, and `LLOGIN` to
`/oidcp/logincheck`, then lands back in the aDIS session authenticated.

Cookie handling is left entirely to `requests`, which implements RFC 6265 path
scoping — the `_sid` cookie is scoped to `/aDISWeb/_<sid>` and hand-rolled
cookie headers silently break the session.

All VÖBB-specific selectors live in `src/voebb/parsers.py`, so a site redesign
is a one-file fix. Table columns are located by header text, not position.

## Tests

```bash
uv run pytest
```

Parser tests run against saved HTML in `tests/fixtures/` and never touch the
network. Card and item numbers in those fixtures are redacted.

`scripts/trace.py` walks the live site and dumps each page plus its form
controls — use it to refresh fixtures or re-discover selectors after a redesign:

```bash
uv run python scripts/trace.py /tmp/voebb-trace
```

## Courtesy

This hits a public library service. The client makes one request at a time with
a short delay and no concurrency; please keep it that way. Note that
`robots.txt` disallows `/aDISWeb/_*`, which is where a scraped session lives —
fine for personal, authenticated, low-volume use, worth knowing before you point
anything larger at it.
