# change-monitor

Watch a price, a stock status or any text on a web page and get a Telegram message, an e-mail or a webhook call the moment it changes - with a full history in SQLite.

```
$ cmon run examples/config.yaml --once
watch                            outcome   value                    note
-------------------------------  --------  -----------------------  -----------------
light-in-the-attic-price         baseline  £51.77                   baseline recorded
light-in-the-attic-stock         baseline  22                       baseline recorded
tipping-the-velvet-availability  baseline  In stock (20 available)  baseline recorded

3 watches, 0 alerts, 0 errors
```

## What you get

- A command-line tool (`cmon`) driven by one YAML file: **watches** (URL + CSS selector + condition) and **notification channels**.
- **Sensible alerting**: the first run records a silent baseline; afterwards you are notified on real changes or when a threshold is crossed - not on every run.
- **Operational awareness**: if a page goes down or its layout changes, the watch is flagged as *failing* after N consecutive errors and you get a *recovered* message once it works again.
- **History** of every check in SQLite (`cmon history`, `cmon export --csv`) - the basis for monthly reports.
- Ready-to-use **systemd timer** files for unattended operation on any Linux box, plus an `.env.example` for secrets.
- A **tested, typed Python package** (74 offline tests) with pluggable notifiers.

## Features

| Area | Details |
|---|---|
| Extraction | CSS selector (selectolax/lexbor), value from text, attribute or inner HTML, optional regex (first group), whitespace normalisation |
| Numbers | locale-tolerant parsing: `1.299,00 €`, `$1,299.00`, `£51.77`, `1 299,50`, `CHF 1'299.50`, `In stock (22 available)` -> 22; override with `decimal: ","` / `"."` |
| Conditions | `any_change`, `below`, `above`, `contains`, `not_contains`, `regex` |
| Alert rules | baseline on first run; threshold/text conditions alert when they start to hold and again on further changes while holding; never spam on unchanged values |
| Failure handling | fetch error, HTTP >= 400, selector mismatch or unparsable number count as failures; *failing* alert after `failures_before_alert` (default 3); *recovered* alert afterwards |
| Channels | Telegram Bot API, e-mail (SMTP/STARTTLS), generic webhook (JSON POST with optional headers), stdout; per-watch channel selection |
| Messages | plain text with old/new value, absolute and percentage difference, reason, link and timestamp |
| Rate limiting | per-watch `interval` (seconds) so a watch is checked at most that often, whatever the schedule; `--force` overrides |
| Operation | `--once` (cron/systemd timer) or `--loop --interval N`; meaningful exit codes; `--db` override |
| Storage | SQLite via the standard library: every observation (timestamp, value, number, hash, status, error, alerted) + per-watch state |

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cmon validate examples/config.yaml                     # check the configuration
cmon run      examples/config.yaml --once              # first run = baseline, no alerts
cmon run      examples/config.yaml --once              # later runs: alerts on changes
cmon history  examples/config.yaml --watch light-in-the-attic-price --last 20
cmon export   examples/config.yaml --csv history.csv
```

Requires Python 3.10+. Dependencies: `httpx`, `selectolax`, `pyyaml` (SQLite comes with Python).

## Configuration, explained

`examples/config.yaml` monitors two pages of the scraping sandbox [books.toscrape.com](https://books.toscrape.com):

```yaml
database: data/monitor.db        # relative to this file
interval: 0                      # default seconds between checks per watch (0 = every run)
failures_before_alert: 3         # consecutive failures before a "watch failing" alert

notifiers:
  console:
    type: stdout
  telegram:                      # reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from the environment
    type: telegram
  hook:
    type: webhook
    url: ${WEBHOOK_URL}
    header_Authorization: "Bearer ${WEBHOOK_TOKEN}"

watches:
  - name: light-in-the-attic-price
    url: https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html
    selector: ".product_main .price_color"
    number: { decimal: "." }     # "£51.77" -> 51.77
    condition: { below: 50 }     # alert when the price drops below 50
    notify: [console, telegram]

  - name: light-in-the-attic-stock
    url: https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html
    selector: ".product_main .availability"
    regex: "\\((\\d+) available\\)"   # "In stock (22 available)" -> "22"
    condition: { below: 5 }
    interval: 3600               # at most once per hour

  - name: tipping-the-velvet-availability
    url: https://books.toscrape.com/catalogue/tipping-the-velvet_999/index.html
    selector: ".product_main .availability"
    condition: any_change        # any change of the text
```

Secrets never go into the file: notifier options accept `${VARIABLE}` and `${VARIABLE:-default}` and are resolved from the environment when the channel is built. `cmon validate` warns about variables that are not set.

### When exactly do you get a message?

| Situation | Result |
|---|---|
| First successful check of a watch | **baseline** stored, no message |
| `any_change` and the value differs from the last good value | alert: *value changed* |
| `below`/`above`/`contains`/`not_contains`/`regex` starts to hold | alert, e.g. *dropped below 50 (now 45)* |
| Condition still holds and the value changed again | alert: *value changed, still below 50* |
| Condition still holds, value unchanged | no message (logged as *still below 50*) |
| Condition stops holding | no message, state reset (the next crossing alerts again) |
| N consecutive failures (page down, selector broken, no number) | one alert: *watch failing (N errors in a row)* |
| First successful check after a failing alert | alert: *recovered* |
| A channel fails (e.g. Telegram down) | logged, run continues, exit code 1 |

An alert looks like this on every channel (plain text, no Markdown surprises):

```
[change-monitor] light-in-the-attic-price: dropped below 50 (now 45)

Watch:   light-in-the-attic-price
URL:     https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html
Old:     £51.77
New:     £45.00
Change:  -6.77 (-13.1 %)
Reason:  dropped below 50 (now 45)
Checked: 2026-11-09 21:00 UTC
```

Webhooks receive the same information as JSON (`event`, `watch`, `url`, `old`, `new`, `diff`, `diff_percent`, `reason`, `timestamp`, `subject`, `body`, `text`).

## Notification channels

| `type` | Options (YAML) | Environment defaults |
|---|---|---|
| `stdout` | - | - |
| `telegram` | `token`, `chat_id`, `api_base` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| `email` | `host`, `port`, `username`, `password`, `sender`, `recipients` (comma-separated), `starttls` | `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`, `MAIL_TO` |
| `webhook` | `url`, `header_<Name>` for extra HTTP headers | `WEBHOOK_URL` |

`cmon test-notify config.yaml [--notifier NAME]` sends a test message so you can verify a channel before relying on it.

## Command-line reference

```
cmon run         CONFIG [--once | --loop --interval SECONDS] [--force] [--watch NAME ...] [--db PATH] [-v|-q]
cmon history     CONFIG --watch NAME [--last N] [--db PATH]
cmon export      CONFIG --csv FILE [--watch NAME] [--db PATH]
cmon test-notify CONFIG [--notifier NAME ...]
cmon validate    CONFIG
```

Exit codes: `0` all good - `1` at least one watch failed or a notification could not be delivered - `2` configuration error - `130` interrupted.

## Running it as a service

The `deploy/` folder contains a `systemd --user` timer that runs one pass every 15 minutes (per-watch `interval`s still apply, so a watch can be checked hourly or daily while the timer runs often):

```bash
# on the machine that should run the monitor, as the normal user
git clone https://github.com/Feldwebelkuchen/change-monitor.git ~/change-monitor && cd ~/change-monitor
python3 -m venv .venv && .venv/bin/pip install -e .
cp examples/config.yaml config.yaml                    # edit your watches
cp deploy/.env.example .env && chmod 600 .env          # fill in tokens
.venv/bin/cmon test-notify config.yaml                 # verify channels

mkdir -p ~/.config/systemd/user
cp deploy/change-monitor.service deploy/change-monitor.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now change-monitor.timer
loginctl enable-linger "$USER"                         # keep running without a login session

systemctl --user list-timers change-monitor.timer      # next run
journalctl --user -u change-monitor.service -n 50      # logs
```

`deploy/change-monitor.service` is a `Type=oneshot` unit (`cmon run config.yaml --once -q`) with `EnvironmentFile=.env` and basic hardening; `deploy/change-monitor.timer` uses `OnUnitActiveSec=15min`, `Persistent=true` and a small randomised delay. Alternatively run `cmon run config.yaml --loop --interval 900` in a long-lived process (Docker, screen, supervisor).

## Monitoring plan - what a monthly service includes

If you would rather not run this yourself, the tool is the engine behind a managed monitoring subscription. A typical monthly plan covers:

- **Setup**: up to N watches (pages x values) with the right selectors, number parsing and alert conditions; test alert on your channel.
- **Checks**: every 15 minutes (or hourly/daily per watch), 24/7, from a server you do not have to maintain.
- **Alerts**: Telegram, e-mail or webhook (Slack, Discord, n8n, Zapier, your CRM) with old/new value, difference and link.
- **Selector repair**: when a site changes its layout the *failing* alert reaches me first; the selector is fixed within one business day, no action needed on your side.
- **Monthly report**: CSV/XLSX history of all values plus a short summary (changes, min/max, uptime of the checks).
- **Changes on request**: add/remove watches, adjust thresholds, add channels.
- Fair-use scope: public pages, no login areas, no CAPTCHA circumvention, no personal data without a legal basis.

## Configuration reference

| Key | Type / default | Meaning |
|---|---|---|
| `database` | path `change-monitor.db` | SQLite file; relative paths are resolved against the config file |
| `user_agent` | string | Identify yourself; include a contact address |
| `timeout` | seconds `15` | HTTP timeout per request |
| `interval` | seconds `0` | Default minimum time between two checks of a watch (`0` = every run) |
| `failures_before_alert` | int `3` | Consecutive failures before a *failing* alert |
| `notifiers.<name>.type` | `stdout` / `telegram` / `email` / `webhook` | Channel type; other keys are channel options (see table above) |
| `watches[].name` | string | Unique identifier, used in messages and the database |
| `watches[].url` | URL | Page to fetch |
| `watches[].selector` | CSS selector | First matching element is used |
| `watches[].extract` | `text` / `attr` / `html` | What to read from the element (`attr` needs `watches[].attr`) |
| `watches[].regex` | pattern | Applied to the extracted text; first group or whole match; no match counts as a failure |
| `watches[].number` | `true` or `{decimal: auto\|,\|.}` | Parse the value as a number (implied by `below`/`above`) |
| `watches[].condition` | `any_change` or `{below: N}` `{above: N}` `{contains: S}` `{not_contains: S}` `{regex: P}` | When to alert |
| `watches[].notify` | list of notifier names | Channels for this watch (default: all) |
| `watches[].interval` | seconds | Per-watch rate limit, overrides the global `interval` |
| `watches[].failures_before_alert` | int | Per-watch override |

## Responsible use and limitations

- Static HTML only: values rendered by JavaScript after page load are not visible. Many shops expose the same data server-side or via a JSON endpoint that can be watched instead; a headless-browser fetcher would be the extension point (`fetch.py`).
- One request per watch and run, with a descriptive User-Agent. Pick intervals that a site owner would consider reasonable (minutes to hours, not seconds).
- No login automation, no CAPTCHA solving, no personal data without a legal basis. Respect the site's terms of service.

## Running the tests

```bash
pip install -e ".[dev]"
python -m pytest -q          # 74 tests, all offline (httpx.MockTransport, in-memory SQLite, fake SMTP)
python -m compileall -q src
```

Covered: configuration and environment substitution, extraction, number parsing (14 locale variants), every condition and the transition rules, SQLite store, the full monitor cycle (baseline, change, threshold, failures/recovery, rate limiting, broken channels, loop), each notifier's payload, and the CLI.

## Project layout

```
src/change_monitor/
  config.py       YAML -> Config / Watch / Condition / NotifierSpec, ${ENV} substitution
  fetch.py        httpx client
  extract.py      CSS/regex extraction (selectolax)
  numbers.py      locale-tolerant number parsing
  conditions.py   alert decision rules
  store.py        SQLite observations + watch state, CSV export
  templates.py    plain-text message templates
  monitor.py      run_once / run_loop / check_watch
  notify/         base (Message, dispatch), stdout, telegram, email, webhook, registry
  cli.py          `cmon` entry point
deploy/           change-monitor.service, change-monitor.timer, .env.example
examples/         config.yaml (books.toscrape.com); examples/data/ is created at runtime and git-ignored
tests/            74 offline tests
```

## License

MIT - see `LICENSE`.

---

### Für deutschsprachige Kunden

change-monitor überwacht Preise, Verfügbarkeiten oder beliebige Textstellen auf Webseiten (Wettbewerber, Lieferanten, Behördenseiten, Stellenbörsen) und meldet Änderungen per Telegram, E-Mail oder Webhook - mit alt/neu, Differenz und Link. Der erste Lauf legt nur die Basis fest, danach gibt es Meldungen ausschließlich bei echten Änderungen oder beim Über-/Unterschreiten einer Schwelle; fällt eine Seite aus oder ändert sich ihr Aufbau, werden Sie ebenfalls informiert. Als monatlicher Service übernehme ich Einrichtung, Betrieb, Reparatur der Selektoren bei Layout-Änderungen und einen Monatsbericht als Excel/CSV. Keine Login-Bereiche, keine Captcha-Umgehung, keine personenbezogenen Daten ohne Rechtsgrundlage.
