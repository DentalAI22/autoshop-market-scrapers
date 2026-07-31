# The Auto Shop Market — Refresh Guide

`theautoshopmarket.com` pulls its listings from
`autoshop-scrapers/listings.json` (this repo), via the site's `prebuild`
step (`scripts/fetch-listings.mjs`, which fetches the public raw URL below
at every build). A daily GitHub Action already keeps this repo's
`listings.json` fresh automatically — this doc is the manual override /
force-refresh path.

## Current broker list (as of 2026-07-30)

Real, currently-listed auto repair shops, scraped from each broker's OWN
site (never an aggregator), confirmed active via the broker's own published
Listing Status:

| Broker | Listings URL | Active listings found |
|---|---|---|
| Sunbelt Atlanta Business Brokers | https://www.sunbeltatlanta.com/atlanta-businesses-for-sale | 1 |
| Sunbelt Business Brokers of South Florida | https://sunbeltsfl.com/businesses-for-sale-table/ | 2 |
| Synergy Business Brokers | https://synergybb.com/businesses-for-sale/automotive-businesses-for-sale/ | 1 |
| VR Business Brokers — Los Angeles/Artesia, CA | https://bizbizbiz.com/businesses-for-sale/ | 13 |
| VR Business Brokers — Chicago/Oak Brook, IL | https://midwestvr.com/businesses-for-sale/ | 1 |
| VR Business Brokers — Richmond, VA | https://vr-rva.com/businesses-for-sale/ | 1 |
| VR Business Brokers — Greenville, SC | https://vrgreenville.com/businesses-for-sale/ | 0 (its one auto-repair listing is SOLD) |

**Total shipped: 19 real listings, 4 broker organizations (6 offices).**

### Sources checked and rejected (not scraped — see run_all.py / broker_codes.json)

- **sunbeltnetwork.com** (Sunbelt's corporate umbrella search) — Cloudflare
  returns HTTP 403 to a polite fetch; not any single office's own listings
  page. Regional office domains (sunbeltatlanta.com, sunbeltsfl.com) are used
  instead.
- **fcbb.com** (First Choice Business Brokers) — real broker, dedicated
  `/category/auto-business-for-sale` page, but listings render entirely
  client-side via an AJAX widget — no listing data in the static HTML, so a
  polite server-side fetch sees zero rows.
- **tworld.com / vrbusinessbrokers.com / www.sunbeltnetwork.com** (corporate
  parent domains) — Cloudflare-gated (403 to a scraper UA).
- **aria.net** — real automotive-focused business broker; no auto repair
  shop was in their active listings at research time (checked, none found —
  not force-included).
- Excluded as aggregators (never scraped, per network doctrine): BizBuySell,
  BizQuest, LoopNet, DealStream, BusinessBroker.net.

If a new dedicated auto repair broker with real public listings surfaces
later, add it the same way `sunbeltatlanta.py`/`synergybb.py` were added: a
new `<source>.py` scraper + a `broker_codes.json` entry + adding it to
`SCRAPERS` in `run_all.py` (and to `OFFICES` in `vrbrokers.py` if it's
another VR regional office on the same template).

## Re-running the pipeline

```bash
cd ~/market-network/autoshop-scrapers
python3 run_all.py                      # scrape all 4 sources + normalize
python3 run_all.py --only vrbrokers     # re-run just one source
python3 run_all.py --normalize          # re-normalize existing CSVs, no re-scrape
```

Output: `listings.json` in this directory, plus (when the site checkout is
present locally) a copy written straight to
`../autoshop/public/data/listings.json`.

`site_id_registry.json` assigns persistent `TASM-XXXXX` siteIds — re-running
never renumbers an existing listing. A listing that goes "Under Contract" or
"Sold" on the broker's own site is correctly dropped until/unless it goes
active again; its siteId is preserved in the registry in case it returns.

## Full refresh -> commit/push -> redeploy (manual override)

The daily GitHub Action (`.github/workflows/scrape-autoshop.yml`, 09:45 UTC)
does steps 1-3 automatically. To force a refresh right now:

```bash
# 1. Scrape + normalize
cd ~/market-network/autoshop-scrapers
python3 run_all.py

# 2. Commit + push the refreshed dataset to this repo
git add listings.json output/*.csv site_id_registry.json
git commit -m "chore(autoshop): manual refresh $(date -u +%Y-%m-%dT%H:%MZ)"
git push

# 3. Redeploy the site so it pulls the fresh listings.json at build time
cd ~/market-network/autoshop
npm run build   # runs prebuild (scripts/fetch-listings.mjs) -> next build
vercel --prod --yes
```

`scripts/fetch-listings.mjs` fetches
`https://raw.githubusercontent.com/DentalAI22/autoshop-market-scrapers/main/listings.json`
at build time and writes it to `public/data/listings.json`; if the fetch
ever fails, the build falls back to the last-committed copy in the site repo
so a GitHub outage can never break a deploy.
