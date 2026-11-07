"""Data Ingestion Module (Stage 1).

Scrapes San Diego open data portal dataset pages, downloads location and
transaction CSVs and dictionaries, skips unchanged files, and produces
data/raw/manifest.json.
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAW_DIR = Path("data/raw")
LOCATIONS_DIR = RAW_DIR / "locations"
TRANSACTIONS_DIR = RAW_DIR / "transactions"
MANIFEST_PATH = RAW_DIR / "manifest.json"

PAGE_LOCATIONS = "https://data.sandiego.gov/datasets/parking-meters-locations/"
PAGE_TRANSACTIONS = "https://data.sandiego.gov/datasets/parking-meters-transactions/"

HEADERS = {
    "User-Agent": "SanDiegoParkingFinder/1.0 (https://github.com/HackForHumanity; contact: data@sandiego-parking.local)"
}


def compute_sha256(filepath: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def count_lines(filepath: Path) -> int:
    """Count lines in file minus header (approximate row count)."""
    lines = 0
    with open(filepath, "rb") as f:
        for _ in f:
            lines += 1
    return max(0, lines - 1)


def scrape_csv_links(page_url: str) -> List[str]:
    """Scrape links ending in .csv from a dataset page."""
    with httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True) as client:
        resp = client.get(page_url)
        resp.raise_for_status()
        links = re.findall(r'href=["\']([^"\']+\.csv)["\']', resp.text)
        return sorted(list(set(links)))


def download_file(url: str, dest_path: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Download a file if new or updated, returning file metadata."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    filename = dest_path.name
    existing_meta = manifest.get(filename)

    with httpx.Client(headers=HEADERS, timeout=600.0, follow_redirects=True) as client:
        head_resp = client.head(url)
        etag = head_resp.headers.get("etag")
        last_modified = head_resp.headers.get("last-modified")
        remote_size = int(head_resp.headers.get("content-length", 0))

        if dest_path.exists() and dest_path.stat().st_size > 0:
            local_size = dest_path.stat().st_size
            if existing_meta:
                if etag and existing_meta.get("etag") == etag:
                    logger.info(f"Skipping {filename}: ETag matches.")
                    return existing_meta
                if last_modified and existing_meta.get("last_modified") == last_modified:
                    logger.info(f"Skipping {filename}: Last-Modified matches.")
                    return existing_meta
            if remote_size > 0 and local_size == remote_size:
                logger.info(f"Skipping {filename}: File exists with matching size ({local_size} bytes).")
                if existing_meta:
                    return existing_meta
                # generate metadata for existing file
                sha256 = compute_sha256(dest_path)
                return {
                    "url": url,
                    "filename": filename,
                    "downloaded_at": datetime.now(timezone.utc).isoformat(),
                    "sha256": sha256,
                    "bytes": local_size,
                    "row_count": count_lines(dest_path),
                    "etag": etag,
                    "last_modified": last_modified,
                }

        logger.info(f"Downloading {url} -> {dest_path} ({remote_size / (1024*1024):.1f} MB)...")
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                    f.write(chunk)

    local_size = dest_path.stat().st_size
    sha256 = compute_sha256(dest_path)
    row_count = count_lines(dest_path)
    logger.info(f"Downloaded {filename}: {local_size / (1024*1024):.1f} MB, {row_count:,} rows.")

    return {
        "url": url,
        "filename": filename,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "sha256": sha256,
        "bytes": local_size,
        "row_count": row_count,
        "etag": etag,
        "last_modified": last_modified,
    }


def fetch_all(current_year_only: bool = True) -> Dict[str, Any]:
    """Fetch all necessary datasets and write manifest."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {}
    if MANIFEST_PATH.exists():
        try:
            with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as e:
            logger.warning(f"Could not load existing manifest: {e}")

    # 1. Locations
    logger.info("Scraping locations links...")
    loc_links = scrape_csv_links(PAGE_LOCATIONS)
    logger.info(f"Discovered location links: {loc_links}")

    # Prioritize parking_meters_current.csv and dictionary
    for link in loc_links:
        fname = link.split("/")[-1]
        if "current" in fname or "dictionary" in fname:
            manifest[fname] = download_file(link, LOCATIONS_DIR / fname, manifest)

    # 2. Transactions
    logger.info("Scraping transaction links...")
    trans_links = scrape_csv_links(PAGE_TRANSACTIONS)
    logger.info(f"Discovered transaction links: {trans_links}")

    # Find dictionaries and newest year (e.g. 2026)
    current_year = datetime.now().year
    for link in trans_links:
        fname = link.split("/")[-1]
        if "dictionary" in fname:
            manifest[fname] = download_file(link, TRANSACTIONS_DIR / fname, manifest)
        elif current_year_only:
            # Download current year or latest year matching
            if f"_{current_year}_" in fname or "2026" in fname:
                manifest[fname] = download_file(link, TRANSACTIONS_DIR / fname, manifest)
        else:
            manifest[fname] = download_file(link, TRANSACTIONS_DIR / fname, manifest)

    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    logger.info(f"Manifest written to {MANIFEST_PATH}")
    return manifest


if __name__ == "__main__":
    fetch_all()

