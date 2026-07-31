"""Shared utilities for auto shop scrapers.

Ported faithfully from the veterinary market rig (~/market-network/
veterinary-scrapers/utils.py), which itself was ported from the dental TDPM
rig. Same polite-fetch discipline: real browser UA, 1.5-3.5s random delays,
tolerant price parsing.
"""

from __future__ import annotations

import re
import logging
import time
import random
from typing import Dict, Optional, Tuple

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


def get_session() -> requests.Session:
    """Create a requests session with browser-like headers."""
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def polite_delay(min_sec: float = 1.5, max_sec: float = 3.5) -> None:
    """Sleep a random interval to be polite to servers."""
    time.sleep(random.uniform(min_sec, max_sec))


def parse_price(text: Optional[str]) -> Optional[int]:
    """Extract a dollar amount from text like '$455,000' or '$1.2M'."""
    if not text:
        return None
    text = text.strip().replace(",", "").replace("$", "")
    if not text or text.upper() in ("N/A", "NA", "TBD", "CALL", "BID"):
        return None
    # "1.2 mil" / "1.2 million" / "1.2M"
    m = re.search(r"([\d.]+)\s*(?:mil(?:lion)?\b|M\b)", text, re.I)
    if m:
        return int(float(m.group(1)) * 1_000_000)
    # "600 K" / "600k"
    m = re.search(r"([\d.]+)\s*[Kk]\b", text)
    if m:
        return int(float(m.group(1)) * 1_000)
    # plain number — only accept a full contiguous integer (avoid grabbing the
    # "13" out of "$1.35mil"). Require >= 4 digits to be a plausible dollar sum.
    m = re.fullmatch(r"\d+", text)
    if m and len(text) >= 4:
        return int(text)
    m = re.search(r"\b(\d{4,})\b", text)
    if m:
        return int(m.group(1))
    return None


def clean_text(text: Optional[str]) -> str:
    """Collapse whitespace and strip a string."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


# --- US state helpers ---------------------------------------------------

STATE_ABBRS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}

STATE_NAME_TO_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}


def parse_location(text: Optional[str]) -> Tuple[str, str]:
    """Best-effort (city, state) from a free-text location string.

    Handles 'San Antonio, TX', 'Orange County, CA', 'South Georgia',
    'Central Virginia'. Returns ("", "") if nothing parseable.
    """
    if not text:
        return "", ""
    text = clean_text(text)

    # "City, ST"
    m = re.search(r"([A-Za-z .'-]+?),\s*([A-Z]{2})\b", text)
    if m and m.group(2) in STATE_ABBRS:
        return m.group(1).strip().title(), m.group(2)

    # "City, State Name"
    m = re.search(r"([A-Za-z .'-]+?),\s*([A-Za-z ]+)$", text)
    if m:
        st = STATE_NAME_TO_ABBR.get(m.group(2).strip().lower())
        if st:
            return m.group(1).strip().title(), st

    # bare state name anywhere (e.g. "South Georgia", "Central Virginia")
    low = text.lower()
    for name, abbr in STATE_NAME_TO_ABBR.items():
        if re.search(r"\b" + re.escape(name) + r"\b", low):
            return "", abbr

    # bare 2-letter code
    m = re.search(r"\b([A-Z]{2})\b", text)
    if m and m.group(1) in STATE_ABBRS:
        return "", m.group(1)

    return "", ""


# --- shared "broker listing template" detail-page parser -----------------
#
# Several independently-owned regional franchise sites (Sunbelt Business
# Brokers of South Florida, and every VR Business Brokers regional office)
# happen to run the same WordPress listing-detail template ("Price:",
# "Location:", "Industry:", "Listing ID:", "Listing Status:", a
# "LISTING BROKER"/"LISTING OWNER" contact block). One shared parser avoids
# duplicating this logic across scraper files.

STATUS_INACTIVE_RE = re.compile(
    r"under\s*contract|\bsold\b|pending|withdrawn|closed", re.I
)


def parse_broker_template_detail(html: str) -> Dict:
    """Parse the common Sunbelt/VR WordPress listing-detail template.

    Returns a dict with whatever fields could be found: price, location,
    industry, listing_id, status, description, broker_name, broker_phone,
    broker_email, total_sales, cash_flow. Missing fields are omitted.
    """
    soup = BeautifulSoup(html, "lxml")
    text = clean_text(soup.get_text(" ", strip=True))
    out: Dict = {}

    m = re.search(r"Price:\s*\$?([\d,]+)", text)
    if m:
        out["price"] = parse_price(m.group(1))

    m = re.search(r"Location:\s*([^:]+?)\s*Industry:", text)
    if m:
        out["location"] = m.group(1).strip()

    m = re.search(r"Industry:\s*([^:]+?)\s*Listing ID:", text)
    if m:
        out["industry"] = m.group(1).strip()

    m = re.search(r"Listing ID:\s*([A-Za-z0-9#\- ]+?)\s*Listing Status:", text)
    if m:
        out["listing_id"] = m.group(1).strip()

    m = re.search(r"Listing Status:\s*([A-Za-z ]+?)(?:\s{2,}|\s+Description\b)", text)
    if m:
        out["status"] = m.group(1).strip()

    m = re.search(r"Total Sales:\s*\$?([\d,]+|N/A)", text)
    if m:
        out["total_sales"] = parse_price(m.group(1))

    m = re.search(r"Cash Flow:\s*\$?([\d,]+|N/A)", text)
    if m:
        out["cash_flow"] = parse_price(m.group(1))

    m = re.search(r"Description\s+(.*?)\s+(?:LISTING DETAILS|Listing #)", text)
    if m:
        out["description"] = m.group(1)[:900].strip()

    # Scope broker name/phone/email extraction to the text AFTER the
    # "LISTING BROKER"/"LISTING OWNER" marker — the page header/footer
    # usually carries the office's main switchboard number earlier in the
    # text, which must not be mistaken for the individual broker's line.
    m = re.search(r"LISTING (?:BROKER|OWNER)\s+(.{0,300})", text)
    broker_block = m.group(1) if m else ""

    m = re.search(r"^([A-Z][A-Za-z.' -]{2,40}?)\s*(?:\(\d{3}\)|Phone:)", broker_block)
    if m:
        out["broker_name"] = m.group(1).strip()

    m = re.search(r"\((\d{3})\)\s*(\d{3})-(\d{4})", broker_block)
    if m:
        out["broker_phone"] = "({}) {}-{}".format(*m.groups())

    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", broker_block) or re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    if m:
        out["broker_email"] = m.group(0)

    return out


def is_inactive_status(status: Optional[str]) -> bool:
    """True if a listing's published status marks it as no longer available
    for purchase (under contract, sold, pending, withdrawn)."""
    if not status:
        return False
    return bool(STATUS_INACTIVE_RE.search(status))
