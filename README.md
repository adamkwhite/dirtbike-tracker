# dirtbike-tracker

Finds a **project bike** to wrench on with the kids: a used dirt bike that needs work, priced
right, with ownership. Scrapes Kijiji and AutoTrader for the models in `config/models.yml`,
stores listings in SQLite, rates each one against the model's 90-day market median, scores its
project fit, and pushes alerts to your phone via [ntfy](https://ntfy.sh).

## How it works

```mermaid
flowchart TB
    cron(["cron, every 20 min (prod clone)"]) --> cli
    config[/"config/models.yml"/] --> cli

    subgraph application
        cli["cli.py<br/>watch · list · stats · report · dashboard<br/>dismiss · contact · backup"]
        market["market.py<br/>snapshot(slug)"]
        cli --> market
    end

    subgraph domain
        scoring["scoring.py<br/>median · rating · project fit · trend<br/><i>pure, no I/O</i>"]
    end

    subgraph infrastructure
        kij["kijiji_scraper.py"]
        at["autotrader_scraper.py"]
        storage[("storage.py<br/>SQLite")]
        render["dashboard.py<br/>reporter.py"]
        notifier["notifier.py"]
        backup["backup.py"]
    end

    market -->|score| scoring
    market -->|read prices + listings| storage
    cli -->|scrape| kij & at
    cli -->|upsert| storage
    cli -->|render snapshot| render
    cli -->|DEAL / GOOD / target / price drop| notifier

    kijiji{{Kijiji}} --> kij
    autotrader{{AutoTrader}} --> at
    notifier --> ntfy{{"ntfy.sh → phone"}}
    notifier --> hc{{healthchecks.io}}
    cli -->|nightly| backup
    backup --> drive{{"Google Drive (rclone)"}}
```

Every 20 minutes `watch` scrapes each model in `config/models.yml`, upserts listings into
SQLite, scores them via `snapshot()`, alerts on new DEAL/GOOD/target listings and on price
drops into GOOD/DEAL, and rebuilds `dashboard.html`. The domain layer is pure: it receives prices and
listings, never queries.

Ratings are price vs market median: **DEAL** ≤ -30%, **GOOD** ≤ -10%, **FAIR** ≤ +15%,
**HIGH** above that. A listing under a model's `target_price` alerts regardless of market.

**Fit** (0-10, neutral 5) scores whether it's a good **project bike** to wrench on with kids, from
phrases in the title/description: +2 per green flag (`doesn't run`, `needs work`, `project`,
`ownership`, `trail`, ...), -3 per red flag (`no ownership`, `seized`, `blown`, `race bike`).
Needing work is the point; "runs great" is neutral. Lists live in `domain/scoring.py`. A listing
with fit below 5 never alerts, however cheap.

A model with `goal: ride` in `models.yml` (e.g. the Rebel 500, to ride in spring) flips that:
needing work, `no safety` and `sold as is` are red flags, and `runs great`, `safetied`,
`certified`, `low kms`, `new tires` are green. Ride models have no project-bike median.
Adding a model: an entry in `models.yml` (plus its slug in `KNOWN_MODEL_SLUGS`), and if
AutoTrader's keyword search misses the slug, a spelling in `autotrader_scraper.KEYWORDS`.
Run `dirtbike watch --group <group> --no-notify` once after deploying, or every listing
already on the market alerts as new.

Kijiji search pages carry a ~200-char snippet of every ad; a snippet that isn't cut off is the
whole ad. For a cut-off one that could alert (DEAL/GOOD or under target), `watch` reads the full
ad (up to 5 per model, once each) and re-scores fit on it. A red flag in the body stops the alert.

**Project bikes** (a condition phrase: `doesn't run`, `needs work`, `needs rings`, `project`, ...)
are rated against the median of other project bikes, since next to running bikes a non-runner
looks like a DEAL just for not running. Until a model has 5 project prices in 90 days they fall
back to the all-bikes median, shown as `vs all bikes*` (dashboard `-40%*`).

Layout is hexagonal (`src/dirtbike_tracker/{domain,infrastructure,application}`). See
[docs/architecture.md](docs/architecture.md) for modules, data model, dedup and scoring.

## Usage

```bash
dirtbike models                      # tracked models from config/models.yml
dirtbike watch                       # scrape all, score, notify, rebuild dashboard
dirtbike watch --no-notify --group priority
dirtbike scrape --model crf150f --source all
dirtbike list --model crf150f        # stored listings with ratings
dirtbike stats --model crf150f       # low / median / high / count
dirtbike report --model crf150f --write   # markdown snapshot to reports/
dirtbike dashboard --open
dirtbike dismiss <url-or-id>         # hide a listing you've evaluated (survives re-scrapes)
dirtbike undismiss <url-or-id>
dirtbike contact <url-or-id> --note "offered 750" [--on 2026-10-01]  # log messaging a seller (--clear to undo)
dirtbike backup [--slot weekly]      # DB + state to Google Drive (see Backups)
```

Add or remove models in `config/models.yml`. Valid Kijiji slugs live in
`infrastructure/kijiji_scraper.py:KNOWN_MODEL_SLUGS`.

### Environment

| Var | Purpose |
|---|---|
| `NTFY_TOPIC` | ntfy topic to push alerts to. Required unless `--no-notify`. |
| `NTFY_SERVER` | Defaults to `https://ntfy.sh`. |
| `HEALTHCHECK_URL` | Optional. [healthchecks.io](https://healthchecks.io) ping URL hit after every completed `watch`; it alerts when pings stop. |
| `ANTHROPIC_API_KEY` | Optional. Enables LLM fact extraction (year, hours). |

### Cron

Cron runs from a separate **prod clone** that stays on `main`, so a feature branch
checked out in the dev repo never goes live. Both share the dev repo's `data/`
(DB + notified state) and `dashboard.html`.

```bash
# one-time setup
git clone https://github.com/adamkwhite/dirtbike-tracker.git ~/Code/dirtbike-tracker-prod
cd ~/Code/dirtbike-tracker-prod
python3.13 -m venv venv && venv/bin/pip install -e .
ln -s ~/Code/dirtbike-tracker/data data
ln -s ~/Code/dirtbike-tracker/dashboard.html dashboard.html
NTFY_TOPIC=your-topic HEALTHCHECK_URL=https://hc-ping.com/<uuid> ./scripts/install_cron.sh   # every 20 min, 6am-11pm

# deploy after merging to main
git -C ~/Code/dirtbike-tracker-prod pull --ff-only

# uninstall
~/Code/dirtbike-tracker-prod/scripts/install_cron.sh --remove
```

Logs go to `~/Code/dirtbike-tracker-prod/logs/watch.log` (timestamped if `moreutils` is installed).

### Docker

An alternative to the WSL cron, e.g. for a VPS. `tracker` runs the same four jobs as
`install_cron.sh` via [supercronic](https://github.com/aptible/supercronic) (Ontario time, logs
to `docker compose logs`); `dashboard` serves the dashboard on `127.0.0.1:8080`.

```bash
mkdir -p data/state reports      # create first: docker makes missing bind dirs root-owned
printf 'NTFY_TOPIC=your-topic\nHEALTHCHECK_URL=https://hc-ping.com/<uuid>\n' > .env
docker compose up -d --build
```

Mounts `data/`, `reports/`, `config/` (read-only) and `~/.config/rclone` (override with
`RCLONE_CONFIG_DIR`). The image runs as uid 1000; build with `docker compose build --build-arg UID=$(id -u)` if
yours differs. Don't run it alongside the cron on the same `data/`: both would scrape.

### Backups

Cron runs `dirtbike backup` nightly at 23:55 (and `--slot weekly` on Sundays): a consistent
SQLite snapshot plus `data/state/`, uploaded to Google Drive via job-agent's `jobbackup` rclone
remote as `dirtbike-tracker-backups/{nightly,weekly}.tar.gz`. It refuses to upload a 0-row
snapshot, verifies the remote size, and pushes an ntfy alert on failure. Log: `logs/backup.log`.

```bash
# restore (run between cron ticks)
mkdir -p ~/dirtbike-restore
rclone cat jobbackup:dirtbike-tracker-backups/nightly.tar.gz | tar -xzf - -C ~/dirtbike-restore
python -c "import sqlite3, os; print(sqlite3.connect(os.path.expanduser('~/dirtbike-restore/listings.db')).execute('SELECT COUNT(*) FROM listings').fetchone())"
cp ~/dirtbike-restore/listings.db ~/Code/dirtbike-tracker/data/listings.db
cp ~/dirtbike-restore/state/* ~/Code/dirtbike-tracker/data/state/
```

Check the row count, not `PRAGMA quick_check`: quick_check passes on an empty file.

## Requirements

- Python 3.13+

## Development

### 1. Install Dependencies

```bash
# Create and activate virtual environment
python3.13 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install project dependencies
pip install -r requirements.txt

# Install development dependencies
pip install -e ".[dev]"
```

### 2. Set Up Pre-commit Hooks (Required)

Pre-commit hooks automatically format and lint your code before each commit, ensuring code quality.

```bash
# Install pre-commit
pip install pre-commit

# Enable pre-commit hooks
pre-commit install

# (Optional) Run against all files to test
pre-commit run --all-files
```

**What pre-commit does:**
- Auto-fixes formatting issues with Ruff
- Sorts imports automatically
- Removes trailing whitespace
- Validates YAML/JSON files
- Type checks with mypy
- Security scans with Bandit

**Your workflow:**
1. Write code (Claude or manual)
2. Run `git commit`
3. Pre-commit auto-fixes formatting
4. Commit succeeds with clean code

### 3. Tests

```bash
pytest tests/ --cov=src
```

CI runs pre-commit, pytest and SonarCloud on every push and PR.

### Exception Handling & `noqa` Conventions

Ruff enforces `BLE` (blind-except), `TRY` (tryceratops), `LOG`, and `G` (logging-format) rules. Broad `except Exception` blocks fail CI by default.

When a broad catch is intentional and the handler logs or re-raises, suppress with a plain noqa:

```python
except Exception:  # noqa: BLE001
    logger.exception("failed to process item")
```

When the broad catch is real debt that swallows silently, tag it as a FIXME:

```python
except Exception:  # noqa: BLE001  # FIXME: blind catch, tighten + log
    pass
```

Grep the FIXME tag to get the live backlog count:

```bash
grep -rn 'FIXME: blind catch' src/
```

Do not add blanket per-file `BLE` ignores for whole directories. Each suppression is inline so a real silent swallow can't hide behind a directory-wide glob.
