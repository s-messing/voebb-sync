# voebb

Read-only programmatic access to a [VÖBB](https://www.voebb.de) library account
(Verbund der Öffentlichen Bibliotheken Berlins) from Python.

VÖBB has no public API, so this is a scraping client for the aDIS/BMS web app.
It reads your current loans, searches the catalogue, and mirrors due dates into
a Nextcloud calendar so you get reminded before anything is overdue. It
deliberately does **not** renew, reserve, or cancel anything.

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

uv run voebb sync-calendar --dry-run      # show what would change
uv run voebb sync-calendar                # write reminders to Nextcloud
uv run voebb sync-calendar --alarm-days 5 --calendar "Bibliothek"
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

## Due-date reminders

`voebb sync-calendar` writes one all-day event per borrowed item into a
Nextcloud calendar over CalDAV, each with a reminder N days before the due date
(`VOEBB_ALARM_DAYS`, default 3).

The sync is **idempotent and reconciling**: run it as often as you like.

- Each event's UID is derived from the item's own library barcode
  (`voebb-<barcode>@voebb.local`). That identifier survives renewals, so a
  renewed item's existing event *moves* to the new due date rather than a second
  event appearing next to it.
- Items you have returned have their events deleted, so the calendar always
  mirrors your account.
- **Only events in this account's own UID namespace are ever modified or
  deleted.** Anything else in the target calendar is left strictly alone, so it
  is safe to point this at a calendar you also use for other things.
- Because UIDs are namespaced per account (`voebb-<account>-<barcode>`),
  **several people can share one calendar** and each sync only reconciles its
  own events. `VOEBB_ACCOUNT` sets the label; it defaults to a short hash of
  `VOEBB_USER`, so the library card number never appears in a UID that syncs
  out to your devices.
- `--dry-run` prints the full plan and writes nothing at all — not even
  creating the calendar.
- Event descriptions carry only durable facts (branch, shelf mark, media
  number, renewal count, and hints like `nicht verlängerbar`). Wording that is
  relative to the day it was scraped — the site's `Heute verlängert` — is
  dropped, so a description cannot go stale and does not churn the calendar
  when the site stops saying it. `Loan.note` keeps the raw text, so
  `voebb loans --json` still shows exactly what the site said.

If a session drops mid-run — aDIS expires them on its own schedule and signals
it by silently bouncing to the start page rather than erroring — the client
rebuilds the session and retries once, so an unattended daily run does not skip
a day over a transient hiccup.

Configure it in `.env` (see `.env.example`). Use a Nextcloud **app password**
(Settings → Security → Create new app password), not your login password —
it is revocable and works with 2FA. `NEXTCLOUD_URL` accepts either the bare host
or a full `/remote.php/dav` root.

### Home Assistant

You do not need a Home Assistant-specific path: point HA's built-in **CalDAV**
integration at the same Nextcloud account and it will pick up the calendar as a
`calendar.*` entity. Automations can then trigger with an offset, e.g.
`offset: "-3 0:0:0"` to fire three days before an item is due.

### Running it daily

Nothing is installed for you. A `systemd --user` timer is the tidiest option on
Debian:

```ini
# ~/.config/systemd/user/voebb-sync.service
[Unit]
Description=Sync VOEBB loans to Nextcloud calendar
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=%h/git/voebb
ExecStart=%h/git/voebb/.venv/bin/voebb sync-calendar
```

```ini
# ~/.config/systemd/user/voebb-sync.timer
[Unit]
Description=Daily VOEBB loan sync

[Timer]
OnCalendar=*-*-* 07:00:00
RandomizedDelaySec=30min
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now voebb-sync.timer
```

`Persistent=true` catches up after the machine was off. Note that user timers
only fire while you are logged in unless you enable lingering:
`sudo loginctl enable-linger $USER`.

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
is a one-file fix. Table columns are located by header text, not position, and
the title cell is split on `<br>` into title, shelf mark and barcode rather than
being flattened into one string.

In `calendar_sync.py` the diffing (`plan_sync`) is a pure function kept separate
from the CalDAV I/O (`sync`), so all the reconcile logic is tested without a
server. Event comparison uses a signature of the fields that matter rather than
raw bytes, because `DTSTAMP` changes on every build and would otherwise make
every run look like an update.

## Development

```bash
uv sync                          # includes the dev group
uv run pre-commit install        # once, to enable the git hook
```

Linting, formatting and type checking are enforced by a pre-commit hook:
[ruff](https://docs.astral.sh/ruff/) (check + format) and
[ty](https://github.com/astral-sh/ty). Run them over everything at any time:

```bash
uv run pre-commit run --all-files
uv run ruff check --fix .
uv run ruff format .
uv run ty check
```

Two notes on the configuration:

- `ty` is still pre-1.0 (0.0.x), so treat a new release as capable of changing
  what it flags. Pin bumps in `.pre-commit-config.yaml` deserve a real look.
- `caldav` ships no type information, so its classes resolve to `object`. The
  one place that matters carries a targeted `# ty: ignore[call-non-callable]`
  rather than the rule being switched off globally.

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
