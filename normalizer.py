#!/usr/bin/env python3
"""
Auto Shop listings normalizer.

Ported from the veterinary/dental TDPM normalizer pattern. Reads every
output/<source>_raw.csv, maps each row to the site's Listing schema
(mirrors ~/market-network/autoshop/src/lib/types.ts), assigns a persistent
TASM-XXXXX siteId from site_id_registry.json (never renumbers, never
collides with any other vertical's prefix), dedupes within + across
sources, and writes:
  - listings.json                       (canonical, this dir)
  - ../autoshop/public/data/listings.json   (site consumer, local dev only)

Schema (per Listing interface):
  id, source, source_url, type, state, city, asking_price, annual_revenue,
  annual_collections, key_metric_value, broker_name, broker_company,
  broker_url, broker_phone, broker_email, description,
  business_name_redacted, scraped_date, is_new  (+ siteId, broker_ref)
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("normalizer")

HERE = os.path.dirname(__file__)
OUTPUT_DIR = os.path.join(HERE, "output")
BROKER_CODES_JSON = os.path.join(HERE, "broker_codes.json")
SITE_ID_REGISTRY = os.path.join(HERE, "site_id_registry.json")
LISTINGS_JSON = os.path.join(HERE, "listings.json")

# Site consumer — local dev convenience write only (see note in run() below).
SITE_DATA_TARGETS = [
    os.path.join(HERE, "..", "autoshop", "public", "data", "listings.json"),
]

SITE_PREFIX = "TASM"
BASE_SITE_ID = 1  # TASM-00001 is the first

_codes = None

NICE_NAMES = {
    "General Repair": "Auto Repair Shop",
    "Transmission": "Transmission Shop",
    "Body Shop/Collision": "Auto Body Shop",
    "Tire & Brake": "Tire & Brake Shop",
    "Quick Lube": "Quick Lube Shop",
    "Specialty/Import": "Specialty Auto Shop",
    "Fleet Service": "Fleet Service Business",
    "Other": "Auto Shop",
}

VALID_TYPES = {
    "General Repair", "Transmission", "Body Shop/Collision", "Tire & Brake",
    "Quick Lube", "Specialty/Import", "Fleet Service", "Other",
}


def load_codes() -> Dict:
    global _codes
    if _codes is None:
        with open(BROKER_CODES_JSON) as f:
            _codes = json.load(f)
    return _codes


def to_int(v) -> Optional[int]:
    if v in (None, "", "None"):
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


# --- siteId registry (persistent, stable, never renumber) -------------------

def load_registry():
    if os.path.exists(SITE_ID_REGISTRY):
        with open(SITE_ID_REGISTRY) as f:
            d = json.load(f)
        return d.get("next_id", BASE_SITE_ID), d.get("map", {})
    return BASE_SITE_ID, {}


def save_registry(next_id: int, id_map: Dict) -> None:
    with open(SITE_ID_REGISTRY, "w") as f:
        json.dump({"prefix": SITE_PREFIX, "base": BASE_SITE_ID,
                   "next_id": next_id, "map": id_map}, f, indent=2)


def assign_site_ids(listings: List[Dict]) -> None:
    """Assign stable TASM-XXXXX siteIds keyed by source_id (registry-backed)."""
    next_id, id_map = load_registry()
    used = set(id_map.values())
    for l in listings:
        key = l["source_id"]
        if key in id_map:
            num = id_map[key]
        else:
            while next_id in used:
                next_id += 1
            num = next_id
            id_map[key] = num
            used.add(num)
            next_id += 1
        l["siteId"] = "{}-{:05d}".format(SITE_PREFIX, num)
    save_registry(next_id, id_map)


# --- normalization ----------------------------------------------------------

def broker_ref(source_key: str, listing_code: str) -> str:
    codes = load_codes()
    meta = codes.get("sources", {}).get(source_key, {})
    prefix = meta.get("ref_prefix", source_key.upper())
    code = (listing_code or "").strip()
    if code and not re.fullmatch(r"[A-Za-z]{1,6}\d{1,6}[A-Za-z]?", code):
        return prefix
    return "{} #{}".format(prefix, code) if code else prefix


def redacted_name(shop_type: str) -> str:
    """Never store real business names. Emit a generic descriptor."""
    t = (shop_type or "").strip()
    return NICE_NAMES.get(t, "Auto Shop")


def normalize_row(source_key: str, row: Dict, today: str, recent_cutoff: str) -> Optional[Dict]:
    codes = load_codes()
    meta = codes.get("sources", {}).get(source_key, {})

    title = (row.get("title") or "").strip()
    state = (row.get("state") or "").strip().upper()
    if not title:
        return None

    scraped = row.get("scraped_date") or today
    is_new = scraped >= recent_cutoff

    shop_type = (row.get("shop_type") or "General Repair").strip()
    if shop_type not in VALID_TYPES:
        shop_type = "Other"

    num_bays = to_int(row.get("num_bays"))
    broker_name = (row.get("broker_name") or meta.get("broker_name", "")).strip()

    return {
        "source_id": row.get("source_id") or "",  # internal key (dropped before write)
        "id": row.get("source_id") or "",
        "siteId": "",  # filled by assign_site_ids
        "source": source_key,
        "source_url": row.get("listing_url") or meta.get("broker_url", ""),
        "type": shop_type,
        "state": state,
        "city": (row.get("city") or "").strip(),
        "asking_price": to_int(row.get("asking_price")),
        "annual_revenue": to_int(row.get("annual_revenue")),
        "annual_collections": None,
        "key_metric_value": num_bays,  # site keyMetric field = bays
        "num_bays": num_bays,
        "broker_name": broker_name or meta.get("broker_name", ""),
        "broker_company": (row.get("office") or meta.get("broker_name", "")) if source_key == "vrbrokers" else meta.get("broker_name", ""),
        "broker_url": meta.get("broker_url", ""),
        "broker_phone": (row.get("broker_phone") or "").strip(),
        "broker_email": (row.get("broker_email") or "").strip(),
        "broker_ref": broker_ref(source_key, row.get("listing_code", "")),
        "description": (row.get("description") or "").strip(),
        "business_name_redacted": redacted_name(shop_type),
        "scraped_date": scraped,
        "is_new": is_new,
    }


def dedupe(listings: List[Dict]) -> List[Dict]:
    """Cross-source dedupe. Same source_id, or same (state, asking_price,
    annual_revenue) signature with a very similar title, collapses to one
    (keep the richer)."""
    by_key: Dict[str, Dict] = {}
    order: List[str] = []
    for l in listings:
        sig_bits = [l.get("state", ""), str(l.get("asking_price") or ""),
                    str(l.get("annual_revenue") or "")]
        title_norm = re.sub(r"[^a-z0-9]", "", (l.get("title") or l.get("id") or "").lower())[:24]
        strong = (l.get("asking_price") or l.get("annual_revenue"))
        key = l["source_id"]
        if strong and title_norm:
            key = "|".join(sig_bits + [title_norm])
        if key in by_key:
            def score(x):
                return sum(1 for k in ("asking_price", "annual_revenue",
                                       "num_bays", "city", "description",
                                       "broker_phone", "broker_email")
                           if x.get(k))
            if score(l) > score(by_key[key]):
                by_key[key] = l
        else:
            by_key[key] = l
            order.append(key)
    return [by_key[k] for k in order]


def run() -> List[Dict]:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    recent_cutoff = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
    codes = load_codes()
    known = set(codes.get("sources", {}).keys())
    stem_to_source = {
        "sunbeltatlanta": "sunbeltatlanta", "sunbeltsfl": "sunbeltsfl",
        "synergybb": "synergybb", "vrbrokers": "vrbrokers",
    }

    all_norm: List[Dict] = []
    if os.path.isdir(OUTPUT_DIR):
        for fname in sorted(os.listdir(OUTPUT_DIR)):
            if not fname.endswith("_raw.csv"):
                continue
            stem = fname[:-len("_raw.csv")]
            source_key = stem_to_source.get(stem, stem)
            if source_key not in known:
                logger.warning("Skipping unknown source file: %s", fname)
                continue
            path = os.path.join(OUTPUT_DIR, fname)
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            n = 0
            for r in rows:
                # title stashed on the row dict via 'title' key is used for
                # dedupe signature; keep it around through normalization.
                nr = normalize_row(source_key, r, today, recent_cutoff)
                if nr:
                    nr["title"] = r.get("title", "")
                    all_norm.append(nr)
                    n += 1
            logger.info("%-16s %d rows -> %d normalized", source_key, len(rows), n)

    before = len(all_norm)
    all_norm = dedupe(all_norm)
    logger.info("Deduped %d -> %d", before, len(all_norm))

    assign_site_ids(all_norm)

    all_norm.sort(key=lambda x: (not x.get("is_new"), x.get("state", "")))

    public = []
    for l in all_norm:
        d = dict(l)
        d.pop("source_id", None)
        d.pop("title", None)  # internal-only, business_name_redacted is what ships
        public.append(d)

    with open(LISTINGS_JSON, "w") as f:
        json.dump(public, f, indent=2)

    for target in SITE_DATA_TARGETS:
        site_root = os.path.dirname(os.path.dirname(os.path.dirname(target)))
        if not os.path.isdir(site_root):
            logger.info("Skipping sibling write (not present): %s",
                        os.path.relpath(target, HERE))
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as f:
            json.dump(public, f, indent=2)
        logger.info("Wrote %d listings -> %s", len(public), os.path.relpath(target, HERE))

    logger.info("Wrote %d listings -> listings.json", len(public))
    return public


if __name__ == "__main__":
    out = run()
    print("Done. {} listings normalized.".format(len(out)))
