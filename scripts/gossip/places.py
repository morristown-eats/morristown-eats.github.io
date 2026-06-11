#!/usr/bin/env python3
"""Google Places review-velocity tracker for the 40.

Round-robin: PLACES_PER_DAY places per run (default 32 -> 992 calls/month,
under the 1,000 free Enterprise-SKU events; set PLACES_PER_DAY=40 to refresh
everything daily at roughly $5/month instead).

State lives in data/gossip/places_state.json (committed daily by the bot):
  {"cursor": 17, "places": {"<place_id>": {"slug": ..., "samples": [{"d","r","n"}, ...]}}}

Emits gossip candidates only on movement:
  - rating moved >= 0.1 vs the previous sample
  - review count up >= max(10, 5%) vs the oldest retained sample
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

PLACES_PER_DAY = int(os.environ.get("PLACES_PER_DAY", "32"))
SAMPLES_KEPT = 10
RATING_DELTA = 0.1
COUNT_DELTA_MIN = 10
COUNT_DELTA_PCT = 0.05
TIMEOUT = 15

REPO_ROOT = Path(__file__).resolve().parents[2]
PLACE_IDS_PATH = REPO_ROOT / "data" / "gossip" / "place_ids.json"
STATE_PATH = REPO_ROOT / "data" / "gossip" / "places_state.json"
RESTAURANTS_PATH = REPO_ROOT / "public" / "restaurants.json"


def _fetch_place(place_id: str, api_key: str) -> dict:
    req = urllib.request.Request(
        f"https://places.googleapis.com/v1/places/{place_id}",
        headers={
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "rating,userRatingCount",
            "User-Agent": "morris-hears-things/1.0 (morristowneats.com)",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _maps_url(place_id: str) -> str:
    return f"https://www.google.com/maps/place/?q=place_id:{place_id}"


def _load_json(path: Path, default):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_places(api_key: str, today_iso: str) -> list[dict]:
    """Round-robin fetch + delta detection. Returns gossip candidate items.

    Raises if place_ids.json is missing (run resolve_place_ids.py once);
    individual place failures are skipped silently (stale sample remains).
    """
    place_ids = _load_json(PLACE_IDS_PATH, None)
    if not place_ids:
        raise FileNotFoundError(
            f"{PLACE_IDS_PATH} missing or empty — run scripts/gossip/resolve_place_ids.py once"
        )
    names = {r["slug"]: r["name"] for r in _load_json(RESTAURANTS_PATH, [])}

    state = _load_json(STATE_PATH, {"cursor": 0, "places": {}})
    ordered = sorted(place_ids.items())  # stable slug order
    n = len(ordered)
    cursor = state.get("cursor", 0) % n

    candidates = []
    for offset in range(min(PLACES_PER_DAY, n)):
        slug, pid = ordered[(cursor + offset) % n]
        place_id = pid["place_id"] if isinstance(pid, dict) else pid
        try:
            data = _fetch_place(place_id, api_key)
        except Exception:
            continue  # this place keeps its old sample; never fatal
        rating = data.get("rating")
        count = data.get("userRatingCount")
        if rating is None or count is None:
            continue

        entry = state["places"].setdefault(place_id, {"slug": slug, "samples": []})
        samples = entry["samples"]
        prev = samples[-1] if samples else None
        oldest = samples[0] if samples else None
        name = names.get(slug, slug)

        if prev and prev["d"] != today_iso:
            if abs(rating - prev["r"]) >= RATING_DELTA:
                direction = "up" if rating > prev["r"] else "down"
                candidates.append({
                    "title": (
                        f"google rating for {name} moved {direction}: "
                        f"{prev['r']} -> {rating} (as of {prev['d']})"
                    ),
                    "url": _maps_url(place_id),
                })
            grew = count - oldest["n"]
            if grew >= max(COUNT_DELTA_MIN, oldest["n"] * COUNT_DELTA_PCT):
                pct = round(grew / oldest["n"] * 100) if oldest["n"] else 0
                candidates.append({
                    "title": (
                        f"google review count for {name} is up {grew} "
                        f"(~{pct}%) since {oldest['d']} ({oldest['n']} -> {count})"
                    ),
                    "url": _maps_url(place_id),
                })

        if not prev or prev["d"] != today_iso:
            samples.append({"d": today_iso, "r": rating, "n": count})
            del samples[:-SAMPLES_KEPT]

    state["cursor"] = (cursor + min(PLACES_PER_DAY, n)) % n
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return candidates
