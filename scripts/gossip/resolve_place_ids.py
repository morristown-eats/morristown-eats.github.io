#!/usr/bin/env python3
"""One-time local setup: resolve Google Place IDs for the 40.

Reads public/restaurants.json, runs a Places API (New) Text Search per entry,
prints a review table (our name vs Google's name — eyeball mismatches), and
writes data/gossip/place_ids.json. ~40 one-time searchText calls.

Usage:
  export GOOGLE_PLACES_API_KEY=...   (then:)
  python3 scripts/gossip/resolve_place_ids.py
  python3 scripts/gossip/resolve_place_ids.py --limit 3   # smoke test

Fix any mismatch by editing data/gossip/place_ids.json by hand, then commit it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RESTAURANTS_PATH = REPO_ROOT / "public" / "restaurants.json"
OUT_PATH = REPO_ROOT / "data" / "gossip" / "place_ids.json"
TIMEOUT = 15


def search_text(query: str, api_key: str) -> dict | None:
    req = urllib.request.Request(
        "https://places.googleapis.com/v1/places:searchText",
        data=json.dumps({"textQuery": query}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress",
            "User-Agent": "morris-hears-things/1.0 (morristowneats.com)",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        places = json.loads(resp.read().decode("utf-8")).get("places", [])
    return places[0] if places else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve Google Place IDs for the 40")
    parser.add_argument("--limit", type=int, default=0, help="Only resolve the first N (smoke test)")
    args = parser.parse_args(argv)

    api_key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not api_key:
        sys.exit("error: GOOGLE_PLACES_API_KEY is not set")

    restaurants = json.loads(RESTAURANTS_PATH.read_text(encoding="utf-8"))
    if args.limit:
        restaurants = restaurants[: args.limit]

    existing = {}
    if OUT_PATH.is_file():
        existing = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        print(f"(merging into existing {OUT_PATH} — {len(existing)} entries)")

    print(f"{'slug':28} {'our name':30} -> google's name")
    print("-" * 100)
    mismatches = 0
    for r in restaurants:
        slug, name = r["slug"], r["name"]
        if slug in existing:
            print(f"{slug:28} {name:30} -> (already resolved, skipping)")
            continue
        try:
            hit = search_text(f"{name} {r['address']}", api_key)
        except Exception as exc:  # noqa: BLE001
            print(f"{slug:28} {name:30} -> FAILED: {exc}")
            continue
        if not hit:
            print(f"{slug:28} {name:30} -> NO RESULT (resolve by hand)")
            continue
        gname = hit.get("displayName", {}).get("text", "?")
        flag = ""
        if name.lower()[:8] not in gname.lower() and gname.lower()[:8] not in name.lower():
            flag = "   <-- CHECK THIS ONE"
            mismatches += 1
        print(f"{slug:28} {name:30} -> {gname}{flag}")
        existing[slug] = {"place_id": hit["id"], "google_name": gname}

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nWrote {OUT_PATH} ({len(existing)} entries)")
    if mismatches:
        print(f"⚠ {mismatches} possible mismatches flagged above — fix by editing the JSON, then commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
