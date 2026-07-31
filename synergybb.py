"""
Synergy Business Brokers — automotive repair listings scraper.

synergybb.com is a national M&A brokerage with a server-rendered "Automotive
Businesses For Sale" category page listing every current deal (mixed with
non-repair automotive deals like truck dealerships, towing companies, and
manufacturing — this scraper keeps only genuine repair/service businesses).
Each card links to a "/listings/<slug>/" detail page with the full
description and the individual broker's name/phone/email.

Source: https://synergybb.com/businesses-for-sale/automotive-businesses-for-sale/
Output: output/synergybb_raw.csv
"""

from __future__ import annotations

import csv
import logging
import os
import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from utils import get_session, polite_delay, parse_price, clean_text, parse_location

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("synergybb")

BASE_URL = "https://synergybb.com"
LISTINGS_URL = f"{BASE_URL}/businesses-for-sale/automotive-businesses-for-sale/"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "synergybb_raw.csv")

FIELDNAMES = [
    "source_id", "title", "city", "state", "asking_price", "annual_revenue",
    "shop_type", "description", "broker_name", "broker_phone", "broker_email",
    "listing_url", "num_bays", "listing_code",
]

# Synergy's "Automotive" category also carries dealerships, towing companies,
# manufacturers, and IP/brand sales — keep only genuine repair/service shops.
POSITIVE_KEYWORDS = [
    "repair", "auto body", "collision", "transmission", "tire", "brake",
    "lube", "mechanic", "maintenance",
]
NEGATIVE_KEYWORDS = [
    "dealership", "towing", "manufactur", "intellectual property",
    "trailer", "truck dealer",
]

TYPE_RULES = [
    (re.compile(r"collision|body shop|auto body", re.I), "Body Shop/Collision"),
    (re.compile(r"transmission", re.I), "Transmission"),
    (re.compile(r"tire|brake", re.I), "Tire & Brake"),
    (re.compile(r"quick lube|oil change|\blube\b", re.I), "Quick Lube"),
    (re.compile(r"fleet", re.I), "Fleet Service"),
]


def infer_shop_type(text: str) -> str:
    for pattern, label in TYPE_RULES:
        if pattern.search(text):
            return label
    return "General Repair"


def is_relevant(title: str) -> bool:
    low = title.lower()
    if "sold" in low:
        return False
    if any(neg in low for neg in NEGATIVE_KEYWORDS):
        return False
    return any(pos in low for pos in POSITIVE_KEYWORDS)


def collect_candidates(session) -> List[Dict]:
    resp = session.get(LISTINGS_URL, timeout=30)
    if resp.status_code != 200:
        logger.warning("Listings page -> HTTP %d", resp.status_code)
        return []
    soup = BeautifulSoup(resp.text, "lxml")
    candidates = []
    seen = set()
    for a in soup.select('a[href*="/listings/"]'):
        href = a.get("href", "")
        if href in seen or "/listings/" not in href:
            continue
        title_el = a.select_one("h3, h2, .elementor-heading-title")
        title = clean_text(title_el.get_text()) if title_el else clean_text(a.get_text())
        if not title or not is_relevant(title):
            continue
        seen.add(href)
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

    soup = BeautifulSoup(resp.text, "lxml")
    full_text = clean_text(soup.get_text(" ", strip=True))

    m = re.search(r"Price:\s*\$?([\d,]+)", full_text)
    price = parse_price(m.group(1)) if m else None
    m = re.search(r"Annual Revenue:\s*\$?([\d,]+)", full_text)
    revenue = parse_price(m.group(1)) if m else None

    # Location tag is the short region string right before "Learn More"/category
    # block on the site's own category listing; on the detail page it appears
    # right after the financial summary line, before the description.
    m = re.search(r"Net Cash Flow:\s*\$?[\d,]+\s+(?:[A-Za-z /&]+?\s+)?([A-Za-z][A-Za-z .,'-]{2,30})\s+Please fill out", full_text)
    loc_text = m.group(1).strip() if m else ""
    city, state = parse_location(loc_text)

    # The narrative description sits between the NDA prompt and the "Contact
    # Us About This Listing" broker block — everything before it on the page
    # is nav/menu chrome, everything after is the broker contact card.
    m = re.search(r"Please fill out our Nda\s+(.*?)\s+Contact Us About This Listing", full_text, re.S)
    description = clean_text(m.group(1))[:700] if m else ""

    m = re.search(r"Contact Us About This Listing\s+([A-Z][A-Za-z.' -]{2,40}?)\s+(?:Senior|M&A|Broker|Advisor)", full_text)
    broker_name = m.group(1).strip() if m else ""
    m = re.search(r"\((\d{3})\)\s*(\d{3})-(\d{4})", full_text)
    broker_phone = "({}) {}-{}".format(*m.groups()) if m else ""
    m = re.search(r"[\w.+-]+@synergybb\.com", full_text)
    broker_email = m.group(0) if m else ""

    slug = cand["url"].rstrip("/").rsplit("/", 1)[-1]
    return {
        "source_id": "syn-{}".format(slug[:40]),
        "title": cand["title"],
        "city": city,
        "state": state,
        "asking_price": price,
        "annual_revenue": revenue,
        "shop_type": infer_shop_type(cand["title"]),
        "description": description,
        "broker_name": broker_name or "Synergy Business Brokers",
        "broker_phone": broker_phone,
        "broker_email": broker_email,
        "listing_url": cand["url"],
        "num_bays": None,
        "listing_code": slug[:24],
    }


def run() -> List[Dict]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = get_session()
    logger.info("Collecting Synergy Business Brokers automotive candidates...")
    candidates = collect_candidates(session)
    logger.info("Found %d relevant candidates; fetching details...", len(candidates))

    results = []
    seen = set()
    for i, cand in enumerate(candidates, 1):
        row = parse_detail(session, cand)
        if row and row["source_id"] not in seen:
            seen.add(row["source_id"])
            results.append(row)
            logger.info("  [%d/%d] %s — %s, %s", i, len(candidates),
                        row["listing_code"], row["city"] or "?", row["state"] or "?")

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    logger.info("Wrote %d listings to %s", len(results), OUTPUT_FILE)
    return results


if __name__ == "__main__":
    out = run()
    print("Done. {} listings saved to {}".format(len(out), OUTPUT_FILE))
