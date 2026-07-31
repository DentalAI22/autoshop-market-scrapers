"""
Sunbelt Atlanta — auto repair / auto body shop listings scraper.

Sunbelt Atlanta Business Brokers (sunbeltatlanta.com) is a HubSpot-hosted
regional Sunbelt franchise office with its own server-rendered "Businesses
for Sale" grid (all industries mixed together, no per-industry URL). This
scraper pulls that grid and keeps only cards that are clearly automotive
repair/body-shop businesses (keyword filter on title + teaser text), skips
anything already flagged "Sale Pending" on the grid, then fetches each
matching detail page for the full asking price, revenue, EBITDA, listing ID,
and broker contact.

Source: https://www.sunbeltatlanta.com/atlanta-businesses-for-sale
Output: output/sunbeltatlanta_raw.csv
"""

from __future__ import annotations

import csv
import logging
import os
import re
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from utils import get_session, polite_delay, parse_price, clean_text, parse_location

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("sunbeltatlanta")

BASE_URL = "https://www.sunbeltatlanta.com"
LISTINGS_URL = f"{BASE_URL}/atlanta-businesses-for-sale"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "sunbeltatlanta_raw.csv")

FIELDNAMES = [
    "source_id", "title", "city", "state", "asking_price", "annual_revenue",
    "shop_type", "description", "broker_name", "broker_phone", "broker_email",
    "listing_url", "num_bays", "listing_code",
]

# Only keep grid cards that are genuinely automotive repair/service shops —
# Sunbelt Atlanta's grid mixes every industry together with no filter URL.
POSITIVE_KEYWORDS = [
    "auto body", "auto repair", "automotive repair", "collision repair",
    "collision & repair", "transmission shop", "transmission repair",
    "tire shop", "tire & brake", "quick lube", "brake shop", "auto glass",
    "mechanic shop", "automotive service",
]
# Exclude adjacent-but-not-repair businesses even if they mention "auto".
NEGATIVE_KEYWORDS = [
    "detailing", "ceramic coating", "car wash", "equipment sales",
    "equipment distribution", "dealership", "parts store", "window tint",
]

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


def is_relevant(text: str) -> bool:
    low = text.lower()
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
    for card in soup.select("div.content-card.element-item"):
        if card.select_one(".sale_pending"):
            continue  # already under contract — not open for new buyers
        title_el = card.select_one(".price_grid_title h2 a")
        if not title_el:
            continue
        title = clean_text(title_el.get_text())
        teaser_el = card.select_one(".price_grid_text")
        teaser = clean_text(teaser_el.get_text()) if teaser_el else ""
        if not is_relevant(title + " " + teaser):
            continue
        href = title_el.get("href", "")
        url = urljoin(BASE_URL, href.split("?")[0])
        price_el = card.select_one(".price span")
        asking_price = parse_price(price_el.get_text()) if price_el else None
        revenue = None
        for li in card.select("ul li"):
            li_text = clean_text(li.get_text())
            if li_text.lower().startswith("revenue"):
                span = li.find("span")
                if span:
                    revenue = parse_price(span.get_text())
        candidates.append({
            "title": title, "teaser": teaser, "url": url,
            "asking_price": asking_price, "annual_revenue": revenue,
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

    soup = BeautifulSoup(resp.text, "lxml")
    full_text = clean_text(soup.get_text(" ", strip=True))

    m = re.search(r"Listing Id:\s*([A-Za-z0-9]+)", full_text)
    listing_code = m.group(1) if m else ""

    # The "Business location" block repeats the location before and after
    # the broker's name (HubSpot module quirk):
    #   "Business location {LOC} {NAME} {LOC} {PHONE} {EMAIL}"
    # A backreference on the repeated {LOC} isolates {NAME} precisely.
    m = re.search(
        r"Business location\s+([A-Za-z ,.'-]+?)\s+([A-Z][A-Za-z.'-]+(?:\s[A-Z][A-Za-z.'-]+){0,2})\s+\1\s+"
        r"([\d.]{3}-\d{3}-\d{4}(?:\s*X\d+)?)\s+([\w.]+@sunbeltatlanta\.com)",
        full_text,
    )
    if m:
        location_text = m.group(1).strip()
        broker_name = m.group(2).strip()
        broker_phone = m.group(3).strip()
        broker_email = m.group(4).strip()
    else:
        location_text, broker_name, broker_phone, broker_email = "", "", "", ""
        m2 = re.search(r"Business location\s+([A-Za-z ,.'-]+?)(?:\s+[A-Z][a-z]+ [A-Z][a-z]+\s+[\d.]{3}-\d{3}-\d{4}|$)", full_text)
        if m2:
            location_text = m2.group(1).strip()
        m3 = re.search(r"([\d.]{3}-\d{3}-\d{4}(?:\s*X\d+)?)\s+([\w.]+@sunbeltatlanta\.com)", full_text)
        if m3:
            broker_phone, broker_email = m3.group(1).strip(), m3.group(2).strip()

    if not location_text:
        # fall back to the teaser's "for sale in <region>" phrasing
        m4 = re.search(r"for sale in ([A-Za-z .'-]+?)(?:\s+provides|\s+specializes|,|\.)", cand["teaser"])
        location_text = m4.group(1).strip() if m4 else ""
    city, state = parse_location(location_text)

    return {
        "source_id": "sba-{}".format(listing_code or re.sub(r"[^a-z0-9]", "-", cand["title"].lower())[:30]),
        "title": cand["title"],
        "city": city,
        "state": state,
        "asking_price": cand["asking_price"],
        "annual_revenue": cand["annual_revenue"],
        "shop_type": infer_shop_type(cand["title"]),
        "description": cand["teaser"],
        "broker_name": broker_name or "Sunbelt Atlanta",
        "broker_phone": broker_phone,
        "broker_email": broker_email,
        "listing_url": cand["url"],
        "num_bays": None,
        "listing_code": listing_code,
    }


def run() -> List[Dict]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = get_session()
    logger.info("Collecting Sunbelt Atlanta candidates...")
    candidates = collect_candidates(session)
    logger.info("Found %d automotive-relevant candidates; fetching details...", len(candidates))

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
