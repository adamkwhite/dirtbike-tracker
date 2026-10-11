# Facebook Marketplace scraping notes

## The problem

Facebook Marketplace has significant used dirt bike inventory, especially
in regional markets where Kijiji has thin coverage. But it is hostile to
scraping:

1. **Auth required**: unauthenticated requests return a login wall
2. **JS-rendered**: content is injected via React after page load
3. **Rate limits**: aggressive IP-based throttling, soft bans within hours
4. **Anti-automation**: fingerprinting, puzzle challenges, device checks
5. **Terms of service**: automated access is explicitly prohibited

## Two viable paths

### Path 1: Cookie-based requests

**How it works:**
- Log into Facebook in a browser
- Export cookies (`c_user`, `xs`, `fr`, `datr`)
- Pass them with `requests` calls to Marketplace search URLs
- Parse the embedded JSON from `<script>` tags (`handle` and `handleWithCustomApplyEach` are the common patterns)

**Pros:** lightweight, no browser dependency, works with same scraper architecture

**Cons:**
- Cookies expire or get invalidated when FB detects unusual activity
- You risk your personal account getting rate-limited or locked
- Parsing the embedded JSON is brittle (schema changes frequently)
- Multi-session management is painful

**Mitigation**: use a dedicated FB account, not your personal one. Even then, FB may require phone verification eventually.

### Path 2: Playwright with headless Chromium

**How it works:**
- Launch headless Chromium with Playwright
- Log in once, persist cookies to a profile dir
- Navigate to Marketplace search, let React render
- Parse the rendered DOM
- Handle any auth challenges that appear

**Pros:** survives schema changes, handles JS challenges, closer to how a real browser works

**Cons:**
- 300MB+ Chromium install
- Significantly slower (seconds per page vs ms)
- Must run with a display or Xvfb on Linux
- Still needs an account that could get flagged
- More code to maintain

**Example skeleton:**

```python
from playwright.sync_api import sync_playwright

def scrape_fb(model_slug: str, region: str = "toronto"):
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir="./fb_profile",
            headless=False,
        )
        page = browser.new_page()
        page.goto(f"https://facebook.com/marketplace/{region}/search/?query={model_slug}")
        page.wait_for_selector("[aria-label='Marketplace']", timeout=30000)
        # ... parse DOM ...
        browser.close()
```

## Recommendation for this project

**Do not build FB Marketplace integration unless Kijiji + AutoTrader
coverage is clearly insufficient.** For southern Ontario, they are not.

If you later find a specific regional market (e.g. remote northern Ontario)
where FB has material inventory Kijiji lacks, pick Path 2 with a dedicated
account. Budget 8-12 hours for the initial build, 2-4 hours per month on
maintenance when FB changes something.

A cheaper alternative worth trying first:
- **Manual review**: open FB Marketplace in a browser twice a week
- **Request alerts**: use FB's own "notify me of new listings" feature
- **Group hunting**: join regional dirt bike groups where people post for sale with less friction

The time cost of maintaining a FB scraper rarely beats a manual sweep for
a personal project where you only need to catch 2-3 bikes a year.

## Legal and ethical

Facebook's ToS prohibits automated access. Personal use is a gray area
courts have not clearly resolved. For a hobby project:

- Keep scrape rates low (one request per 5+ seconds)
- Do not republish or sell the data
- Do not interact (message, bid, comment) via automation
- Accept that your account could be restricted at any time

For anything commercial, use the official Facebook Marketplace Partner
Program (requires business review and approval) or buy data from a
licensed aggregator.
