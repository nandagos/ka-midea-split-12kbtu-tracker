# AC Watcher — Midea PortaSplit 12000 BTU, Karlsruhe edition

A small script that polls German retailers + resale listings for the Midea
PortaSplit 12000 BTU, and pushes a notification straight to your phone the
moment one appears **in stock at ≤ 900 €**, telling you whether it's
walkable, taxi-reachable, or too far given you have no car.

## What it checks

| Site | Type |
|---|---|
| Amazon.de (both colour variants) | direct retailer |
| MediaMarkt.de | direct retailer |
| Saturn.de | direct retailer |
| OBI.de | direct retailer |
| Bauhaus.info | direct retailer |
| Böttcher AG | direct retailer |
| eBay Kleinanzeigen (nationwide) | private resale — geocoded & distance-filtered |
| bestell.bar | third-party restock aggregator already tracking this product across many German shops — used as an extra cross-check |

All URLs in `config.yaml` are real product pages found via research on
2026-07-15. Retailers occasionally change their product-page URLs when they
restock a "new" listing — if a link 404s, search the site for "Midea
PortaSplit" and swap in the fresh URL.

## 1. Install

```bash
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Set up phone notifications (2 minutes)

1. Install the **ntfy** app: [iOS](https://apps.apple.com/app/ntfy/id1625396347) / [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy).
2. In `config.yaml`, change `notifications.ntfy_topic` to something random
   only you know (treat it like a password — anyone who guesses it can see
   your alerts). The placeholder already looks like `ac-watch-CHANGE-ME-xk29fz`;
   just change the random suffix.
3. In the ntfy app, tap **+** and subscribe to that exact same topic name.
4. Test it:
   ```bash
   curl -d "test message" ntfy.sh/YOUR-TOPIC-NAME
   ```
   You should get a push notification within seconds.

5. Once you've installed the requirements (`pip install -r requirements.txt`),
   run the included test script, which sends a fake "AC available" alert
   through the *actual* notification code the watcher uses in production —
   this catches config typos that the raw `curl` test above can't:
   ```bash
   python test_notification.py
   ```

(Optional) For extra delivery reliability you can also enable Pushover in
the config — it's a paid app (~5€ one-time) but has stronger delivery
guarantees than free ntfy if you want a backup channel.

## 3. Edit `config.yaml`

- `product.max_price_eur` — currently 900, per your budget.
- `location.max_walk_km` / `max_taxi_km` — tune if you disagree with the
  4 km / 40 km defaults. The AC ships as one ~60 kg, ~80×70×70 cm box, so
  "on foot" really means "with a hand trolley."
- Add/remove `targets` as you find more shops.

## 4. Run it

**Single test pass** (checks everything once, prints what it found, exits):
```bash
python main.py --once
```

**Continuous watching** (checks every ~4 minutes forever):
```bash
python main.py
```

You'll want this running unattended. Pick one:

### Option A — Leave it running on your own computer
Simplest, but only works while your computer is on and awake. On Linux/macOS
you can use `nohup` or `tmux`; on any OS, just leave a terminal window open.

### Option B — Raspberry Pi / old laptop as a 24/7 mini-server
Use a `systemd` service so it survives reboots:
```ini
# /etc/systemd/system/ac-watcher.service
[Unit]
Description=AC Watcher
After=network.target

[Service]
WorkingDirectory=/home/pi/ac-watcher
ExecStart=/home/pi/ac-watcher/venv/bin/python main.py
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
```
Then: `sudo systemctl enable --now ac-watcher`.

### Option C — GitHub Actions (free, no hardware needed, recommended)
This is the easiest genuinely-free cloud option: no server to maintain,
no credit card, and it's already set up in `.github/workflows/watch.yml`
(runs `main.py --once` every 10 minutes and commits `state.json` back so
progress persists between runs).

Setup:
1. Create a **public** GitHub repo (public = unlimited free Actions minutes;
   a private repo only gets 2,000 free minutes/month, which isn't quite
   enough at a 10-minute interval).
2. Push this whole `ac-watcher` folder to it.
3. Because the repo is public, don't put your real ntfy topic name in
   `config.yaml` — instead go to **Settings → Secrets and variables →
   Actions → New repository secret**, name it `NTFY_TOPIC`, and paste your
   topic there. `main.py` already reads this env var and overrides
   whatever's in `config.yaml`, so the secret never appears in your commits.
4. Done — check the **Actions** tab to watch it run, or trigger a manual
   run anytime via **Run workflow**.

Why the 60-day auto-disable rule (which normally kills unused scheduled
workflows on public repos) isn't a problem here: every run commits
`state.json`, which counts as repository activity and resets that clock —
so as long as it keeps finding "not in stock," it keeps itself alive.

If you'd rather keep the repo private, that works too — just lower the
frequency (e.g. `cron: "*/20 * * * *"`, every 20 min) to stay inside the
2,000 free minutes/month.

### Option D — Skip the DIY route, use bestell.bar directly
Worth knowing: **bestell.bar** (a German restock-tracking site) is *already*
watching this exact product across Amazon, BAUHAUS, toom, OBI and Joybuy,
and can email/alert you per-retailer with one click, no coding required:
https://www.bestell.bar/p/MTpH/midea-portasplit
It won't do the "no-car, walk vs. taxi" logic or the ≤900€ filter across
resale listings the way this script does, but it's a good free second line
of defense in case this script's selectors ever break before you notice.

## How it decides "buyable"

For every target, each check produces: in stock? (yes/no), price (if
found), and — for pickup-only listings (mainly Kleinanzeigen) — a
geocoded distance from Karlsruhe classified as walk / taxi / too far.
A push notification fires only on the *transition* into "in stock AND
≤900 €" (so you're not spammed every 4 minutes while a low-stock item
stays available) — `state.json` is what remembers this between runs;
delete it if you ever want to reset and be re-notified about the current
state next run.

## Legal / etiquette notes

- This polls public product pages at a low frequency (default every 4
  minutes ± jitter) for personal, non-commercial use — checking stock
  yourself, just automated. It does not bypass any login, paywall, or
  CAPTCHA.
- Some sites' Terms of Service technically restrict automated access;
  scraping enforcement against low-volume personal stock-checking is
  essentially never pursued, but you're using this at your own discretion.
  If you want to stay strictly on the safe side of ToS, prefer the sites
  that offer their own native restock alerts (Böttcher AG and bestell.bar
  both do — see above) and only script-check the rest.
- If a site starts returning errors/CAPTCHAs, the script logs it and skips
  that target rather than retrying aggressively — don't remove that
  behavior, it's what keeps this polite.
- Retailer HTML changes over time. If a target stops reporting sensibly
  (e.g. `main.py --once` shows "no marker matched" for a site you know is
  in stock), open the page in a browser, find the actual "add to cart" /
  "sold out" text it currently uses, and update `in_stock_markers` /
  `out_of_stock_markers` in `config.yaml` — no code changes needed.

## Known limitations

- **Some retailers block cloud/CI IP ranges outright (HTTP 403).** MediaMarkt.de,
  Saturn.de, and Bauhaus.info were observed returning 403 when polled from
  GitHub Actions — this is bot-detection (Akamai/PerimeterX-style) rejecting
  the request based on the runner's IP, not a bug in the scraper, and no
  amount of header-tweaking reliably fixes it. The script treats this as a
  "blocked, not a stock signal" result (logged once, not retried, no crash)
  rather than pretending it means anything about availability. If those
  three matter a lot to you, your realistic options are: (a) rely on
  Amazon.de, OBI.de, Böttcher AG, Kleinanzeigen, and bestell.bar instead
  (they don't block GitHub's IPs), or (b) run just those specific targets
  from your own home network instead of GitHub Actions (residential IPs
  are far less likely to be blocklisted) — ask if you want a small
  standalone script for that.
- **Stock detection prefers schema.org JSON-LD structured data** (the same
  machine-readable "Product"/"Offer" markup Google uses for search rich
  snippets) over scanning visible page text, specifically because text
  scanning can false-positive on unrelated "add to cart" buttons in
  cross-sell/recommended-product widgets elsewhere on the page — this is
  exactly what caused an early false "in stock" alert for OBI.de. When a
  page doesn't provide structured data, the script falls back to your
  configured text markers but tags that result as low-confidence in both
  the logs and the push notification itself (⚠ UNVERIFIED) — treat those
  as "worth a look," not "confirmed in stock."

## Files

```
config.yaml          — what to watch, budget, location, notification settings
main.py               — the watch loop
ac_watcher/checkers.py   — fetch + parse each site type
ac_watcher/location.py   — geocode + walk/taxi/too-far distance logic
ac_watcher/notifier.py   — ntfy.sh / Pushover push notifications
ac_watcher/state.py      — remembers what's already been notified about
state.json            — created automatically on first run
```
