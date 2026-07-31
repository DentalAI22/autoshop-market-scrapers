#!/usr/bin/env python3
"""
Master auto shop scraper runner — mirrors the veterinary/dental TDPM
run_all.py pattern.

Usage:
    python run_all.py               # Run all scrapers + normalize
    python run_all.py --only sunbeltatlanta
    python run_all.py --normalize   # Re-normalize existing CSVs (no scraping)

Sources (all public, no-login, polite-fetch — same discipline as dental/vet):
    sunbeltatlanta  Sunbelt Atlanta Business Brokers
    sunbeltsfl      Sunbelt Business Brokers of South Florida
    synergybb       Synergy Business Brokers
    vrbrokers       VR Business Brokers (4 regional offices)

BLOCKED (never scraped — same blocklist as the rest of the network):
BizBuySell, BizQuest, LoopNet, DealStream, BusinessBroker.net,
sunbeltnetwork.com (corporate umbrella search, Cloudflare-gated — regional
office domains are used instead, each is that office's own site).
"""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("run_all")


def _csv_data_rows(path):
    """Data rows (excluding header) in a CSV; 0 if missing/empty/unreadable."""
    if not os.path.exists(path):
        return 0
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return max(0, sum(1 for _ in f) - 1)
    except OSError:
        return 0

# (display_name, module_name)
SCRAPERS = [
    ("Sunbelt Atlanta Business Brokers", "sunbeltatlanta"),
    ("Sunbelt Business Brokers of South Florida", "sunbeltsfl"),
    ("Synergy Business Brokers", "synergybb"),
    ("VR Business Brokers (regional offices)", "vrbrokers"),
]


def run_scraper(name, module_name):
    logger.info("=" * 60)
    logger.info("STARTING: %s", name)
    logger.info("=" * 60)

    # Guard against transient source outages (e.g. a broker blocking the CI
    # runner IP, or a real momentary 0-listing state). If the scraper writes
    # an EMPTY CSV over a previously non-empty one, restore the last-good CSV
    # so the source retains its prior listings instead of vanishing from
    # listings.json. The live site pulls listings.json, so a single blocked
    # broker must never wipe real inventory.
    out_csv = os.path.join(OUTPUT_DIR, module_name + "_raw.csv")
    prev_rows = _csv_data_rows(out_csv)
    prev_content = None
    if prev_rows > 0:
        with open(out_csv, encoding="utf-8") as f:
            prev_content = f.read()

    count = 0
    try:
        mod = importlib.import_module(module_name)
        results = mod.run()
        count = len(results) if results else 0
        logger.info("%s: %d listings", name, count)
    except Exception as e:
        logger.error("%s failed: %s", name, e)

    if _csv_data_rows(out_csv) == 0 and prev_content is not None:
        with open(out_csv, "w", encoding="utf-8") as f:
            f.write(prev_content)
        logger.warning("%s returned 0 rows (likely blocked/transient) — RESTORED "
                       "last-good %d rows; source keeps prior listings.",
                       name, prev_rows)
        return prev_rows

    return count


def main():
    parser = argparse.ArgumentParser(description="Run auto shop listing scrapers")
    parser.add_argument("--only", type=str, help="Run one scraper by module name")
    parser.add_argument("--normalize", action="store_true", help="Only normalize existing CSVs")
    args = parser.parse_args()

    start = time.time()
    results = {}

    if not args.normalize:
        if args.only:
            matched = False
            for name, module_name in SCRAPERS:
                if module_name == args.only:
                    results[name] = run_scraper(name, module_name)
                    matched = True
                    break
            if not matched:
                logger.error("Unknown scraper: %s", args.only)
                logger.info("Available: %s", ", ".join(m for _, m in SCRAPERS))
                return 1
        else:
            for name, module_name in SCRAPERS:
                results[name] = run_scraper(name, module_name)

    logger.info("=" * 60)
    logger.info("STARTING: Normalizer")
    logger.info("=" * 60)
    try:
        import normalizer
        merged = normalizer.run()
        results["normalized"] = len(merged) if merged else 0
    except Exception as e:
        logger.error("Normalizer failed: %s", e)
        results["normalized"] = 0

    elapsed = time.time() - start
    logger.info("=" * 60)
    logger.info("AUTO SHOP SCRAPER RUN COMPLETE — %.1fs", elapsed)
    logger.info("=" * 60)
    for source, count in results.items():
        logger.info("  %-42s %d", source, count)

    total = results.get("normalized", 0)
    print("\nDone. {} total auto shop listings in listings.json ({:.1f}s)".format(total, elapsed))
    # Unlike the larger vet/dental datasets, this vertical may legitimately
    # have very few (or, per JI's explicit ruling, even a handful of) real
    # listings — do not fail the run just because the count is small. Only
    # a hard scrape failure (exception) drops rows, handled above per-source.
    return 0


if __name__ == "__main__":
    sys.exit(main())
