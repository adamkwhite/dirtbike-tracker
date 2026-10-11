# Setup & operations

## First-time setup

```bash
# 1. Clone / unpack the project
cd ~/code/dirtbike-tracker

# 2. Virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Verify
python src/main.py --help
```

## ntfy notifications

ntfy pushes alerts to your phone when new DEAL or GOOD listings appear.
Free, no account required.

```bash
# 1. Pick a hard-to-guess topic name (anyone with the name can read it)
export NTFY_TOPIC=adam-dirtbike-$(openssl rand -hex 4)
echo "export NTFY_TOPIC=${NTFY_TOPIC}" >> ~/.bashrc
echo "Your ntfy topic: ${NTFY_TOPIC}"

# 2. Install the ntfy app on your phone (iOS/Android/web)
#    - iOS:     https://apps.apple.com/app/ntfy/id1625396347
#    - Android: https://play.google.com/store/apps/details?id=io.heckel.ntfy
#    - Web:     https://ntfy.sh

# 3. Subscribe to your topic in the app

# 4. Test
python -c "import sys; sys.path.insert(0, 'src'); from notifier import notify; notify('Test', 'If you see this, ntfy works.')"
```

## Running the watch loop

```bash
# One-off: scrape all configured models, score, notify on new deals
python src/main.py watch

# Only scrape priority group
python src/main.py watch --group priority

# Skip notifications (useful for testing)
python src/main.py watch --no-notify

# See what's configured
python src/main.py models
```

## Scheduling via cron

```bash
# Install the cron job (runs every 20 min, 6am - 11pm local time)
./scripts/install_cron.sh

# Check it was installed
crontab -l | grep dirtbike-tracker

# Watch the live log
tail -f logs/watch.log

# Remove when done
./scripts/install_cron.sh --remove
```

Default schedule:
- **Every 20 min, 6am - 11pm**: `watch` (scrape all models, notify)
- **11:30 pm nightly**: write markdown report for CRF150F to `reports/`

Adjust the frequency in `scripts/install_cron.sh` if needed. More frequent =
faster deal detection but more risk of IP throttling.

## Adding models

Edit `config/models.yml`:

```yaml
priority:
  - slug: ttr225
    sources: [kijiji]
    target_price: 2500
    notes: "15yo's step-up option"
```

Model slugs must match one of the entries in `KNOWN_MODEL_SLUGS` in
`src/scraper.py`. Add new ones there if needed.

## LLM extraction (optional)

To parse year, hours, condition from descriptions using Claude:

```bash
# Install the Anthropic SDK
pip install anthropic

# Set your API key
export ANTHROPIC_API_KEY=sk-ant-...
echo "export ANTHROPIC_API_KEY=sk-ant-..." >> ~/.bashrc

# Use it in Python
python -c "
import sys; sys.path.insert(0, 'src')
from extractor import extract
f = extract('2015 Honda CRF150F', 'Runs great, 50 hours, needs new chain and sprockets. $2500.', prefer_llm=True)
print(f.to_dict())
"
```

Without an API key, extraction falls back to regex (handles year, hours, km,
runs/doesn't-run, 'needs X').

## Daily operations

**Normal day:**
1. Cron runs `watch` every 20 min
2. New DEAL listing → push notification to your phone
3. You tap it, review the listing, decide

**Weekly:**
- Review the nightly report in `reports/`
- Check `logs/watch.log` for scrape errors
- Trim old DEAL alerts you've reviewed: `rm data/state/notified_ids.txt`
  (next run will re-alert on anything still active; use sparingly)

**Monthly:**
- Review `config/models.yml` and adjust target prices as market shifts
- Backup the DB: `cp data/listings.db backups/listings-$(date +%F).db`

## Troubleshooting

**No results from Kijiji scrape:**
- Kijiji changed their HTML. Open the search URL in a browser, inspect, update CSS selectors in `src/scraper.py`

**No notifications arriving:**
- Verify `NTFY_TOPIC` is set in the cron environment (shell env doesn't carry over by default; the install script bakes it in)
- Test with the `notify` snippet above
- Check `logs/watch.log` for `ntfy notify failed` messages

**Cron isn't running:**
- `systemctl status cron` on Ubuntu
- WSL 1 doesn't run cron reliably. use WSL 2 or Task Scheduler

**Database locked errors:**
- SQLite serializes writes. If you run `watch` manually while cron also runs, one will block briefly. Not an error for personal use

**Duplicate notifications:**
- `data/state/notified_ids.txt` tracks what you've been alerted on. Don't delete unless you want re-alerts

## Backup and restore

```bash
# Backup
tar czf dirtbike-backup-$(date +%F).tar.gz \
  data/listings.db config/ reports/ logs/

# Restore
tar xzf dirtbike-backup-YYYY-MM-DD.tar.gz
```
