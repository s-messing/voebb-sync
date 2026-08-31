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

Two kinds of transient failure are handled, which matters once this runs
unattended:

- **Network.** Both transports retry connection failures, read timeouts and
  429/5xx responses three times with exponential backoff, honouring
  `Retry-After`. Note that urllib3 does not retry `POST` by default and *every*
  aDIS interaction is a POST — even navigation — so the policy opts in
  explicitly. That is safe here precisely because the client never mutates the
  library account. 4xx responses are not retried: a rejected login or a missing
  page is not worth hammering a public library service over.
- **Session expiry.** aDIS expires sessions on its own schedule and signals it
  by silently bouncing to the start page rather than erroring, so the client
  rebuilds the session and retries once.

Exhausted retries surface as an error rather than an empty result, so a failed
fetch can never be mistaken for "no loans" and quietly wipe the calendar.

Configure it in `.env` (see `.env.example`). Use a Nextcloud **app password**
(Settings → Security → Create new app password), not your login password —
it is revocable and works with 2FA. `NEXTCLOUD_URL` accepts either the bare host
or a full `/remote.php/dav` root.

### Home Assistant

You do not need a Home Assistant-specific path: point HA's built-in **CalDAV**
integration at the same Nextcloud account and it will pick up the calendar as a
`calendar.*` entity. Automations can then trigger with an offset, e.g.
`offset: "-3 0:0:0"` to fire three days before an item is due.

### Container image

CI publishes a multi-arch image to the GitHub Container Registry on every push
to `main`, tagged `latest`, `main`, and `sha-<commit>`. It is one-shot: it runs
the CLI once and exits, so it schedules like any other command. Docker and
Podman both run it unchanged.

```bash
podman login ghcr.io -u <github-user>        # or docker login; PAT with read:packages
podman run --rm --env-file .env ghcr.io/s-messing/voebb:latest sync-calendar --dry-run
```

The package inherits the repository's visibility, so a login is required while
the repo is private. No credentials are baked into the image: it reads the same
environment variables as a local run, and `.dockerignore` keeps `.env` out of
the build context entirely.

### Running it daily

The sync is a one-shot command by design, so scheduling belongs to systemd
rather than to a loop inside the program or the image: a timer gives restart
handling, catch-up after downtime, and a failed run recorded in the journal,
none of which an in-process `sleep` loop reports when it dies.

It is meant for a server, so these are **system** units. Everything the service
needs lives in `/srv/voebb`:

| file | what it is |
| --- | --- |
| `compose.yaml` | the one-shot service definition |
| `.env` | credentials, `chmod 600`, root-owned |

`deploy/` holds that file, the timer, and a service unit per engine — pick the
one matching whichever of Docker or Podman you run, and install it under the
name `voebb-sync.service` so the timer finds it:

```bash
sudo install -d -m 750 /srv/voebb
sudo install -m 644 deploy/compose.yaml /srv/voebb/
sudo install -m 600 .env /srv/voebb/.env

# one of these two
sudo install -m 644 deploy/voebb-sync.podman.service /etc/systemd/system/voebb-sync.service
sudo install -m 644 deploy/voebb-sync.docker.service /etc/systemd/system/voebb-sync.service

sudo install -m 644 deploy/voebb-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now voebb-sync.timer
```

The image is private, and the unit runs as root, so the registry login must be
root's — one in your own account is not visible to it:

```bash
sudo podman login ghcr.io -u <github-user>      # or docker; PAT with read:packages
```

The two units differ only in which binary they call; both run `compose up` from
`/srv/voebb`, so the unit stays one `ExecStart` line and the parameters live in
`compose.yaml`. `--exit-code-from` is not optional: a plain `compose up` exits 0
even when the container failed, which would report every broken sync to the
timer as a success.

The pull is best-effort, so an unreachable registry falls back to the local
image instead of failing the sync. `Persistent=true` catches up after the
machine was off.

```bash
systemctl list-timers voebb-sync.timer   # last run, next run
journalctl -u voebb-sync.service -n 50   # what it did
sudo systemctl start voebb-sync.service  # run one now
```

On a workstation you can run these as `systemd --user` units instead: put the
files in `~/.config/systemd/user/`, point `WorkingDirectory` somewhere you own,
and note that `%h` resolves differently in the two scopes. User timers also
only fire while you are logged in unless `sudo loginctl enable-linger $USER` is
set — which is the reason to prefer system units on a server that runs
unattended.

To run a checkout directly instead of a container, point `ExecStart` at the
`voebb sync-calendar` entry point in its virtualenv and drop `WorkingDirectory`.

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
