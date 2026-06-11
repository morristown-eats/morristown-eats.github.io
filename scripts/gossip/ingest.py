#!/usr/bin/env python3
"""Daily gossip ingest — runs every fetcher best-effort, writes work/sources.json.

Each source is isolated: a dead scrape records {"ok": false, "error": ...} and
the run continues. The only fatal case is ALL sources failing — then there is
nothing to ground a column on and we exit non-zero (no column today; the site's
napping banner covers it).

Usage:
  python3 scripts/gossip/ingest.py            # full run (Places needs GOOGLE_PLACES_API_KEY)
  python3 scripts/gossip/ingest.py --skip-places
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetchers  # noqa: E402
import places  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
WORK_DIR = REPO_ROOT / "work"
NY = ZoneInfo("America/New_York")


def run_source(name: str, fn) -> dict:
    try:
        items = fn()
        print(f"  {name}: ok ({len(items)} items)")
        return {"ok": True, "items": items}
    except Exception as exc:  # noqa: BLE001 — isolation is the point
        print(f"  {name}: FAILED ({type(exc).__name__}: {exc})", file=sys.stderr)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "items": []}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch all gossip sources")
    parser.add_argument("--skip-places", action="store_true",
                        help="Skip the Google Places fetch (no API key needed)")
    args = parser.parse_args(argv)

    now_ny = dt.datetime.now(NY)
    today = now_ny.date().isoformat()
    print(f"Ingest for {today} (America/New_York)")

    sources: dict[str, dict] = {}
    sources["green_rss"] = run_source("green_rss", fetchers.fetch_green_rss)
    sources["weather"] = run_source("weather", fetchers.fetch_weather)
    sources["mpac"] = run_source("mpac", fetchers.fetch_mpac)
    sources["abc"] = run_source("abc", fetchers.fetch_abc_notices)
    sources["planning"] = run_source("planning", fetchers.fetch_planning)

    places_key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if args.skip_places:
        sources["places"] = {"ok": False, "error": "skipped (--skip-places)", "items": []}
        print("  places: skipped")
    elif not places_key:
        sources["places"] = {"ok": False, "error": "GOOGLE_PLACES_API_KEY not set", "items": []}
        print("  places: skipped (GOOGLE_PLACES_API_KEY not set)", file=sys.stderr)
    else:
        sources["places"] = run_source(
            "places", lambda: places.fetch_places(places_key, today)
        )

    # Number every item S1..Sn across sources — these IDs are the grounding
    # contract between the digest, the model's citations, and the validator.
    counter = 0
    for src in sources.values():
        for item in src["items"]:
            counter += 1
            item["id"] = f"S{counter}"

    if not any(s["ok"] for s in sources.values()):
        print("error: every source failed — nothing to ground a column on", file=sys.stderr)
        return 1

    WORK_DIR.mkdir(exist_ok=True)
    bundle = {
        "date_ny": today,
        "generated_at": now_ny.isoformat(timespec="seconds"),
        "sources": sources,
    }
    out = WORK_DIR / "sources.json"
    out.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ok_names = [k for k, v in sources.items() if v["ok"]]
    print(f"Wrote {out} — {counter} items from {len(ok_names)} live sources: {', '.join(ok_names)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
