"""
Sunbelt Business Brokers of South Florida — auto repair listings scraper.

sunbeltsfl.com runs a WordPress "Views" listing table
(businesses-for-sale-table/) with every industry mixed in one grid; each row
carries an "Industry" column we can pre-filter on ("Auto Repair and Service
Shops") before following the per-listing detail page, which uses the same
"Price: / Location: / Industry: / Listing ID: / Listing Status:" template
shared with the VR Business Brokers regional sites (see
utils.parse_broker_template_detail). We only keep listings whose published
Listing Status is NOT under contract/sold/pending — Sunbelt's own grid mixes
active and already-spoken-for listings together with no visual distinction
on the table view, so the status field on the detail page is the only
reliable signal.

Source: https://sunbeltsfl.com/businesses-for-sale-table/
Output: output/sunbeltsfl_raw.csv
"""

from __future__ import annotations

import csv
import logging
import os
import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from utils import (get_session, polite_delay, parse_price, clean_text,
                   parse_location, parse_broker_template_detail, is_inactive_status)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("sunbeltsfl")

BASE_URL = "https://sunbeltsfl.com"
LISTINGS_URL = f"{BASE_URL}/businesses-for-sale-table/"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "sunbeltsfl_raw.csv")

FIELDNAMES = [
    "source_id", "title", "city", "state", "asking_price", "annual_revenue",
    "shop_type", "description", "broker_name", "broker_phone", "broker_email",
    "listing_url", "num_bays", "listing_code",
]

TARGET_INDUSTRY = "auto repair and service shops"

TYPE_RULES = [
    (re.compile(r"collision|body shop|auto body", re.I), "Body Shop/Collision"),
    (re.compile(r"transmission", re.I), "Transmission"),
    (re.compile(r"tire|brake", re.I), "Tire & Brake"),
    (re.compile(r"quick lube|oil change|lube", re.I), "Quick Lube"),
    (re.compile(r"fleet", re.I), "Fleet Service"),
]


def infer_shop_type(text: str) -> str:
    for pattern, label in TYPE_RULES:
        if pattern.search(text):
            return label
    return "General Repair"


def collect_candidates(session) -> List[Dict]:
    resp = session.get(LISTINGS_URL, timeout=30)
    if resp.status_code != 200:
        logger.warning("Listings table -> HTTP %d", resp.status_code)
        return []
    soup = BeautifulSoup(resp.text, "lxml")
    candidates = []
    seen_urls = set()
    for row in soup.select("div.row"):
        col1 = row.select_one(".column1 a")
        col3 = row.select_one(".column3")
        if not col1 or not col3:
            continue
        industry = clean_text(col3.get_text())
        if industry.lower() != TARGET_INDUSTRY:
            continue
        title = clean_text(col1.get_text())
        if "sold" in title.lower():
            continue
        href = col1.get("href", "")
        if not href or href in seen_urls:
            continue
        seen_urls.add(href)
        candidates.append({"title": title, "url": href})
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

    city, state = parse_location(parsed.get("location", ""))

    return {
        "source_id": "sbsfl-{}".format(parsed.get("listing_id") or re.sub(r"[^a-z0-9]", "-", cand["title"].lower())[:30]),
        "title": cand["title"],
        "city": city,
        "state": state,
        "asking_price": parsed.get("price"),
        "annual_revenue": parsed.get("total_sales"),
        "shop_type": infer_shop_type(cand["title"]),
        "description": (parsed.get("description") or "")[:600],
        "broker_name": parsed.get("broker_name") or "Sunbelt Business Brokers of South Florida",
        "broker_phone": parsed.get("broker_phone", ""),
        "broker_email": parsed.get("broker_email", ""),
        "listing_url": cand["url"],
        "num_bays": None,
        "listing_code": parsed.get("listing_id", ""),
    }


def run() -> List[Dict]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = get_session()
    logger.info("Collecting Sunbelt South Florida auto-repair candidates...")
    candidates = collect_candidates(session)
    logger.info("Found %d candidates tagged 'Auto Repair and Service Shops'; fetching details...", len(candidates))

    results = []
    seen = set()
    for i, cand in enumerate(candidates, 1):
        row = parse_detail(session, cand)
        if row and row["source_id"] not in seen:
            seen.add(row["source_id"])
            results.append(row)
            logger.info("  [%d/%d] %s — %s, %s", i, len(candidates),
                        row["listing_code"] or "?", row["city"] or "?", row["state"] or "?")

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    logger.info("Wrote %d listings to %s", len(results), OUTPUT_FILE)
    return results


if __name__ == "__main__":
    out = run()
    print("Done. {} listings saved to {}".format(len(out), OUTPUT_FILE))
