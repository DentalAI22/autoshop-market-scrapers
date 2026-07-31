# autoshop-market-scrapers

Public scraper rig for **The Auto Shop Market** network vertical. Aggregates
real, public-source auto-repair-shop-for-sale listings from dedicated
regional business brokerages' own websites and publishes a single canonical
`listings.json` that the live site consumes.

**Live site fed by this repo:** https://theautoshopmarket.com (Vercel project `autoshop`)

Everything in this repo is scraper code + public listing data. **No secrets, no
tokens, no seller PII.** Broker office contact details that appear on public
broker listing pages are public business contact info.

## What it does

```
run_all.py  ->  per-source scrapers (sunbeltatlanta, sunbeltsfl, synergybb,
                vrbrokers)  ->  output/*_raw.csv  ->  normalizer.py
             ->  listings.json  (canonical, TASM-XXXXX siteIds, deduped)
```

- `utils.py` — real UA + polite 1.5–3.5s delays + price/state helpers, plus a
  shared parser for the common "Price: / Location: / Industry: / Listing ID:
  / Listing Status:" WordPress detail template that Sunbelt South Florida and
  every VR Business Brokers regional office happen to share.
- `broker_codes.json` — source registry, `site_prefix = TASM`.
- `site_id_registry.json` — persistent TASM- id map. **Never renumber.**
- `listings.json` — the canonical dataset. Tracked on purpose; the daily
  Action regenerates and commits it back here.

## Sources (all real brokers' own sites — never an aggregator)

| Module | Broker | Own site |
|---|---|---|
| `sunbeltatlanta` | Sunbelt Atlanta Business Brokers | sunbeltatlanta.com |
| `sunbeltsfl` | Sunbelt Business Brokers of South Florida | sunbeltsfl.com |
| `synergybb` | Synergy Business Brokers | synergybb.com |
| `vrbrokers` | VR Business Brokers — 4 independently-owned regional offices (Los Angeles/Artesia CA, Greenville SC, Chicago/Oak Brook IL, Richmond VA) | bizbizbiz.com, vrgreenville.com, midwestvr.com, vr-rva.com |

**BLOCKED (never scraped):** BizBuySell, BizQuest, LoopNet, DealStream,
BusinessBroker.net, and `sunbeltnetwork.com` (the Sunbelt *corporate umbrella*
search site — Cloudflare-gated and not any single office's own listings page;
regional Sunbelt office domains like `sunbeltatlanta.com` ARE used, since each
is that office's own site).

Every listing is verified against the broker's own **Listing Status** field
(or an explicit "SOLD"/"Under Contract" flag on the index page) before being
kept — under-contract, sold, and pending listings are dropped even when they
still appear in a broker's public grid.

## No minimum listing count

This vertical does not pad to hit a round number, and does not sit on a
"looks-thin" dataset waiting to grow it artificially. Per network policy: a
single real, currently-listed shop from one broker is worth more than
borrowed volume from an aggregator. Every field left blank in `listings.json`
(no asking price, no city, no revenue) means the broker genuinely did not
publish that field — never a placeholder or an estimate.

## Auto-refresh pipeline (refresh -> live)

`.github/workflows/scrape-autoshop.yml` runs **daily at 09:45 UTC** (plus
manual `workflow_dispatch`), staggered after the network's other daily scrape
jobs (dental/vet at 08:30 UTC, medical at 09:30 UTC) to spread GitHub Actions
load. This repo is **PUBLIC**, so GitHub Actions minutes are unlimited/free.

The Action is **self-contained — it only ever writes to THIS repo:**

1. checkout -> install deps -> `python run_all.py` (scrape + normalize).
2. **Sanity guard:** if `listings.json` collapses to exactly 0 listings (every
   source blocked/failed at once), the job **fails and refuses to commit**,
   preserving the last-good dataset. Unlike larger verticals, there is
   deliberately **no minimum-count floor above zero** here — a small honest
   dataset is the expected steady state for a vertical this new, not a bug.
3. commit `listings.json` + `output/*.csv` + `site_id_registry.json` back to
   this repo using the default `GITHUB_TOKEN` (`permissions: contents:
   write`). No PAT.

**Why no cross-repo push:** the site repo is a SEPARATE git repo. Instead of
this Action reaching into it, the **site pulls `listings.json` from this
repo's public raw URL at build time**:

```
https://raw.githubusercontent.com/DentalAI22/autoshop-market-scrapers/main/listings.json
```

So the refresh-to-live path is:

```
daily Action scrapes  ->  commits listings.json to THIS repo
       ->  a site rebuild (`vercel --prod`, or a site-side prebuild fetch step)
           pulls the fresh raw listings.json  ->  republishes.
```

The public raw file is the single source of truth. No cross-repo push
credentials are required anywhere.

## Re-run locally

```bash
pip install -r requirements.txt
python run_all.py                    # scrape all sources + normalize -> listings.json
python run_all.py --only vrbrokers   # one source
python run_all.py --normalize        # re-normalize existing CSVs (no network)
```

See `REFRESH.md` for the full refresh -> commit/push -> redeploy command
sequence used to push a fresh dataset live.

## Constraints honored

- Read-only against public broker pages only; real browser UA; 1.5–3.5s delays.
- Blocked aggregators (BizBuySell / BizQuest / LoopNet / DealStream /
  BusinessBroker.net) are **never** scraped.
- Every listing verified as currently active (not sold/under contract/pending)
  via the broker's own published status before being kept.
- Honest counts; deduped; no fabricated data; no invented prices or revenue.
