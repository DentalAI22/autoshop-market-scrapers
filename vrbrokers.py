"""
VR Business Brokers (regional offices) — auto repair listings scraper.

VR Business Brokers is a national brokerage franchise; each regional office
runs its own independently-owned WordPress site on the same template
("vrbb-listing-*" card classes on the grid, the same "Price: / Location: /
Industry: / Listing ID: / Listing Status:" detail template as Sunbelt South
Florida — see utils.parse_broker_template_detail). This scraper walks a
curated list of regional office domains (found via manual research — not an
aggregator, each is a distinct locally-owned VR franchise's own site),
keeps only grid cards tagged "Auto Repair and Service Shops", drops
window-tinting (cosmetic, not repair) and anything already marked SOLD in
its own title, then confirms via the detail page's Listing Status that the
business is still actually on the market.

Sources (regional VR offices):
  https://bizbizbiz.com/businesses-for-sale/         (Los Angeles/Artesia, CA)
  https://vrgreenville.com/businesses-for-sale/       (Greenville, SC)
  https://midwestvr.com/businesses-for-sale/          (Chicago/Oak Brook, IL)
  https://vr-rva.com/businesses-for-sale/             (Richmond, VA)
Output: output/vrbrokers_raw.csv
"""

from __future__ import annotations

import csv
import logging
import os
import re
from typing import Dict, List, Optional
from urllib.parse import urljoin

from utils import (get_session, polite_delay, parse_price, clean_text,
                   parse_location, parse_broker_template_detail, is_inactive_status)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("vrbrokers")

OFFICES = [
    ("https://bizbizbiz.com", "VR Business Brokers Los Angeles/Artesia, CA"),
    ("https://vrgreenville.com", "VR Business Brokers Greenville, SC"),
    ("https://midwestvr.com", "VR Business Brokers Chicago/Oak Brook, IL"),
    ("https://vr-rva.com", "VR Business Brokers Richmond, VA"),
]

# Each office's published location text is frequently just a bare county
# name with no state ("Los Angeles County", "Orange County") since the whole
# site is implicitly local to one metro. Falling back to the office's own
# home state (never a guess — it's the literal jurisdiction the office
# operates in and advertises listings for) fills in `state` honestly when
# the per-listing text alone doesn't carry it.
OFFICE_STATE_FALLBACK = {
    "VR Business Brokers Los Angeles/Artesia, CA": "CA",
    "VR Business Brokers Greenville, SC": "SC",
    "VR Business Brokers Chicago/Oak Brook, IL": "IL",
    "VR Business Brokers Richmond, VA": "VA",
}

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "vrbrokers_raw.csv")

FIELDNAMES = [
    "source_id", "title", "city", "state", "asking_price", "annual_revenue",
    "shop_type", "description", "broker_name", "broker_phone", "broker_email",
    "listing_url", "num_bays", "listing_code", "office",
]

TARGET_INDUSTRY = "auto repair and service shops"
EXCLUDE_TITLE_RE = re.compile(r"window tint", re.I)

TYPE_RULES = [
    (re.compile(r"collision|body shop|auto body", re.I), "Body Shop/Collision"),
    (re.compile(r"transmission", re.I), "Transmission"),
    (re.compile(r"tire", re.I), "Tire & Brake"),
    (re.compile(r"quick lube|oil change|\blube\b", re.I), "Quick Lube"),
    (re.compile(r"fleet|heavy equipment", re.I), "Fleet Service"),
    (re.compile(r"smog|import|mercedes|bmw|foreign|euro", re.I), "Specialty/Import"),
]


def infer_shop_type(text: str) -> str:
    for pattern, label in TYPE_RULES:
        if pattern.search(text):
            return label
    return "General Repair"


CARD_RE = re.compile(
    r'<a href="(?P<href>https://[^"]+/listing/[^"]+)">'
    r'(?:(?!<a href=).)*?'
    r'vrbb-listing-pretty-industry-name">\s*(?P<industry>[^<]+?)\s*<'
    r'(?:(?!<a href=).)*?'
    r'vrbb-listing-loc[^>]*>(?P<loc>[^<]*)<'
    r'(?:(?!<a href=).)*?'
    r'vrbb-listing-pretty-price[^>]*>(?P<price>[^<]*)<'
    r'(?:(?!<a href=).)*?'
    r'vrbb-listing-title">\s*(?:<!--.*?-->\s*)?(?P<title>[^<]+)<',
    re.S,
)


def collect_office_candidates(session, base_url: str, office_name: str) -> List[Dict]:
    url = f"{base_url}/businesses-for-sale/"
    try:
        resp = session.get(url, timeout=30)
    except Exception as e:
        logger.warning("%s: fetch failed: %s", office_name, e)
        return []
    if resp.status_code != 200:
        logger.warning("%s -> HTTP %d", office_name, resp.status_code)
        return []

    candidates = []
    for m in CARD_RE.finditer(resp.text):
        industry = clean_text(m.group("industry"))
        if industry.lower() != TARGET_INDUSTRY:
            continue
        title = clean_text(m.group("title")).replace("&amp;", "&")
        if "sold" in title.lower() or EXCLUDE_TITLE_RE.search(title):
            continue
        candidates.append({
            "title": title,
            "url": m.group("href"),
            "loc": clean_text(m.group("loc")),
            "price": parse_price(m.group("price")),
            "office": office_name,
        })
    return candidates


def parse_detail(session, cand: Dict) -> Optional[Dict]:
    polite_delay(1.5, 3.0)
    try:
        resp = session.get(cand["url"], timeout=30)
        if resp.status_code != 200:
            return None
    except Exception as e:
        logger.warning("Detail failed %s: %s", cand["url"], e)
        return None

    parsed = parse_broker_template_detail(resp.text)
    if is_inactive_status(parsed.get("status")):
        logger.info("Skipping (status=%s): %s", parsed.get("status"), cand["title"])
        return None

    loc_text = parsed.get("location") or cand["loc"]
    city, state = parse_location(loc_text)
    if not state:
        # try the other location text we have before falling back
        alt_city, alt_state = parse_location(cand["loc"] if parsed.get("location") else "")
        city, state = (city or alt_city), (state or alt_state)
    if not state:
        state = OFFICE_STATE_FALLBACK.get(cand["office"], "")
    if not city and loc_text and "county" in loc_text.lower():
        city = ""  # a county name isn't a city — leave blank rather than mislabel

    slug = cand["url"].rstrip("/").rsplit("/", 1)[-1]
    return {
        "source_id": "vr-{}".format(slug[:40]),
        "title": cand["title"],
        "city": city,
        "state": state,
        "asking_price": parsed.get("price") or cand["price"],
        "annual_revenue": parsed.get("total_sales"),
        "shop_type": infer_shop_type(cand["title"]),
        "description": (parsed.get("description") or "")[:600],
        "broker_name": parsed.get("broker_name") or cand["office"],
        "broker_phone": parsed.get("broker_phone", ""),
        "broker_email": parsed.get("broker_email", ""),
        "listing_url": cand["url"],
        "num_bays": None,
        "listing_code": parsed.get("listing_id", ""),
        "office": cand["office"],
    }


def run() -> List[Dict]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = get_session()

    all_candidates = []
    for base_url, office_name in OFFICES:
        logger.info("Collecting candidates from %s (%s)...", office_name, base_url)
        cands = collect_office_candidates(session, base_url, office_name)
        logger.info("  -> %d auto-repair candidates", len(cands))
        all_candidates.extend(cands)
        polite_delay(1.0, 2.0)

    logger.info("Total candidates across %d offices: %d; fetching details...",
                len(OFFICES), len(all_candidates))

    results = []
    seen = set()
    for i, cand in enumerate(all_candidates, 1):
        row = parse_detail(session, cand)
        if row and row["source_id"] not in seen:
            seen.add(row["source_id"])
            results.append(row)
            logger.info("  [%d/%d] %s — %s, %s (%s)", i, len(all_candidates),
                        row["listing_code"] or "?", row["city"] or "?", row["state"] or "?",
                        row["office"])

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    logger.info("Wrote %d listings to %s", len(results), OUTPUT_FILE)
    return results


if __name__ == "__main__":
    out = run()
    print("Done. {} listings saved to {}".format(len(out), OUTPUT_FILE))
