#!/usr/bin/env python3
"""Daily gossip writer — work/sources.json -> Claude -> src/content/gossip/<date>.md.

Clones the proven morristown_eats.py pattern: model fallback on NotFoundError,
sentinel-delimited output, hard validator + ONE corrective retry, session.json
audit trail. Fail-closed: residual violations after the retry mean exit 1 and
nothing is written to src/content — a missed day beats a bad column.

Usage:
  python3 scripts/gossip/write.py --dry-run    # print exact prompts, no API call
  python3 scripts/gossip/write.py              # full run (needs ANTHROPIC_API_KEY)
  python3 scripts/gossip/write.py --force      # overwrite an existing post for today
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validator  # noqa: E402

PRIMARY_MODEL = "claude-fable-5"
FALLBACK_MODEL = "claude-opus-4-8"
MAX_TOKENS = 2000

REPO_ROOT = Path(__file__).resolve().parents[2]
WORK_DIR = REPO_ROOT / "work"
GOSSIP_DIR = REPO_ROOT / "src" / "content" / "gossip"
RESTAURANTS_PATH = REPO_ROOT / "public" / "restaurants.json"
NY = ZoneInfo("America/New_York")

RECENT_COLUMNS = 5
NEWSY_SOURCES = ("green_rss", "abc", "planning")
THIN_DAY_THRESHOLD = 3

SYSTEM_PROMPT = """\
You are Morris, a fat tabby cat who files a short daily gossip column about the
Morristown, NJ food scene at morristowneats.com.

VOICE — non-negotiable:
- Garfield's worldview filtered through a Morristown local: dry, slightly bored,
  opinionated in a way that feels like he's doing the reader a favor. Morris cares
  about comfort, warmth, food smells, and being left alone. He has no interest in
  trendiness or hype. He is a cat, but subtly — if a reader doesn't notice, fine.
- lowercase register: write in lowercase except proper nouns (restaurant names,
  street names, Morristown, MPAC, people's names).
- NEVER: cat puns of any kind (no paw/purr/whisker/claw/feline/fur wordplay),
  influencer language ("hidden gem", "must-try", "amazing", "elevated",
  "obsessed", "foodie"), emoji, exclamation points, Yelp-review tone.
- dry beats clever. specific beats evaluative. short beats long.

GROUNDING — absolute:
- Every item must come from the numbered SOURCE DIGEST. End each item with its
  source tag(s) in square brackets: [S3] or [S2,S7].
- Never state news that is not in a source. Morris may add his opinion or read
  on a sourced fact, but the fact itself must be in the source.
- Closures, health/safety issues, legal trouble: only if a source explicitly
  says so, in the source's own words. No speculation on these topics, even hedged.
- Thin news day: fewer, shorter items. Weather-food pairings (citing the weather
  source) and MPAC crowd logistics (citing the MPAC source) are honest filler.
  "no news. the bagels remain." is the house register, not a failure.
- MPAC listings may extend weeks out — only mention shows happening in the next
  few days, and only for their crowd/dinner-logistics effect on the town.

COLUMN SHAPE:
- 3 to 8 items, each 1-2 sentences, 40-280 characters.
- Do not re-report anything under ALREADY REPORTED.
- The reader is a Morristown local deciding where to eat. useful beats cute.

OUTPUT FORMAT — exactly these sentinels:
===WEATHER LINE===
<morris's one-line read on today's forecast, max 90 chars>
===ITEMS===
- <item text> [S1]
- <item text> [S4,S7]
===CLOSER===
<one optional sign-off line, or the word NONE>

REGISTER EXEMPLARS (reference only — never copy):
- someone filed a liquor license application for the storefront on Speedwell. morris knows who. morris isn't saying. (the agenda says. it's on the agenda.) [S4]
- MPAC lets out at 9:40 thursday. South Street will be unwalkable at 9:45. eat early or eat far. [S6]
- 52 degrees and raining wednesday. that's pho weather. you know what to do. [S2]
"""


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def load_sources() -> dict:
    path = WORK_DIR / "sources.json"
    if not path.is_file():
        sys.exit(f"error: {path} not found — run scripts/gossip/ingest.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def source_item_map(bundle: dict) -> dict[str, dict]:
    out = {}
    for name, src in bundle["sources"].items():
        for item in src["items"]:
            item["_source"] = name
            out[item["id"]] = item
    return out


def recent_columns(today: str) -> tuple[list[str], set[str]]:
    """(item texts, cited URLs) from the last N committed columns before today."""
    texts: list[str] = []
    urls: set[str] = set()
    if not GOSSIP_DIR.is_dir():
        return texts, urls
    files = sorted(
        (p for p in GOSSIP_DIR.glob("*.md") if p.stem != today), reverse=True
    )[:RECENT_COLUMNS]
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r'^  - text: (".*")$', line)
            if m:
                try:
                    texts.append(json.loads(m.group(1)))
                except json.JSONDecodeError:
                    texts.append(m.group(1).strip('"'))
            m = re.match(r'^      - "(.+)"$', line)
            if m:
                urls.add(m.group(1))
    return texts, urls


def build_user_prompt(bundle: dict, source_items: dict, restaurants: list[dict],
                      reported: list[str], thin_day: bool) -> str:
    date_ny = bundle["date_ny"]
    weekday = dt.date.fromisoformat(date_ny).strftime("%A, %B %-d, %Y").lower()

    digest_lines = []
    for sid, item in source_items.items():
        title = item.get("title") or item.get("text") or ""
        summary = (item.get("summary") or "")[:250]
        line = f"[{sid}] ({item['_source']}) {title}"
        if summary and summary != title:
            line += f" — {summary}"
        digest_lines.append(line)

    failed = [k for k, v in bundle["sources"].items() if not v["ok"]]
    the40 = ", ".join(
        f"{r['name']} ({r['neighborhood']})" for r in restaurants
    )
    reported_block = "\n".join(f"- {t}" for t in reported) or "(nothing yet)"

    parts = [
        f"DATE: {weekday} (america/new_york)",
        "",
        "SOURCE DIGEST — the only facts you may report:",
        "\n".join(digest_lines) or "(no items fetched)",
        "",
        f"sources that failed to fetch today (do not guess at their content): {', '.join(failed) or 'none'}",
        "",
        "THE 40 — restaurants this column covers. for evergreen weather-food or",
        "event-logistics pairings only (cite the weather/MPAC source); never invent news about them:",
        the40,
        "",
        "ALREADY REPORTED — do not re-report:",
        reported_block,
    ]
    if thin_day:
        parts += ["", "NOTE: thin news day — fewer, shorter items welcome. the bagels remain."]
    parts += ["", "Write today's column per the format rules."]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# API (cloned from scripts/morristown_eats.py in the vault)
# ---------------------------------------------------------------------------

def get_client():
    try:
        import anthropic  # noqa: PLC0415
    except ImportError:
        sys.exit("error: the `anthropic` package is not installed. Fix: pip install anthropic")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("error: ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=api_key)


def call_api(client, messages: list[dict]) -> tuple[str, str]:
    import anthropic  # noqa: PLC0415

    last_err: Exception | None = None
    for model in (PRIMARY_MODEL, FALLBACK_MODEL):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=messages,
            )
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            return model, text
        except anthropic.NotFoundError as exc:
            last_err = exc
            print(f"  model {model} not available, trying fallback…", file=sys.stderr)
            continue
    sys.exit(f"error: no usable model (tried {PRIMARY_MODEL}, {FALLBACK_MODEL}): {last_err}")


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

TAG_RE = re.compile(r"\[(S\d+(?:\s*,\s*S\d+)*)\]\s*$")


def parse_response(text: str) -> tuple[str, list[dict], str]:
    def section(name: str, blob: str) -> tuple[str, str]:
        parts = re.split(rf"^==={name}===\s*$", blob, maxsplit=1, flags=re.M)
        return (parts[0], parts[1]) if len(parts) == 2 else (blob, "")

    _, after_weather = section("WEATHER LINE", text)
    weather_blob, after_items = section("ITEMS", after_weather)
    items_blob, closer_blob = section("CLOSER", after_items)

    weather_line = weather_blob.strip().splitlines()[0].strip() if weather_blob.strip() else ""

    items = []
    for line in items_blob.strip().splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        body = line[2:].strip()
        tags: list[str] = []
        m = TAG_RE.search(body)
        if m:
            tags = [t.strip() for t in m.group(1).split(",")]
            body = body[: m.start()].strip()
        items.append({"text": body, "tags": tags})

    closer = closer_blob.strip().splitlines()[0].strip() if closer_blob.strip() else ""
    if closer.upper() == "NONE":
        closer = ""
    return weather_line, items, closer


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render_markdown(date_ny: str, generated_at: str, weather_line: str,
                    weather_short: str, items: list[dict], closer: str,
                    source_items: dict, sources_fetched: list[str],
                    thin_day: bool, model: str) -> str:
    pretty = dt.date.fromisoformat(date_ny).strftime("%B %-d").lower()
    lines = [
        "---",
        f"title: {json.dumps(f'morris hears things — {pretty}')}",
        f'date: "{date_ny}"',
        f'generated_at: "{generated_at}"',
        f"weather_line: {json.dumps(weather_line)}",
    ]
    if weather_short:
        lines.append(f"weather_short: {json.dumps(weather_short)}")
    lines.append("items:")
    for item in items:
        urls: list[str] = []
        for tag in item["tags"]:
            url = source_items.get(tag, {}).get("url", "")
            if url and url not in urls:
                urls.append(url)
        lines.append(f"  - text: {json.dumps(item['text'])}")
        lines.append("    sources:")
        for url in urls:
            lines.append(f'      - "{url}"')
    if closer:
        lines.append(f"closer: {json.dumps(closer)}")
    lines.append(f"thin_day: {'true' if thin_day else 'false'}")
    lines.append(f"sources_fetched: [{', '.join(json.dumps(s) for s in sources_fetched)}]")
    lines.append(f"model: {json.dumps(model)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write today's gossip column")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the exact prompts and exit — no API call, no files")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite an existing post for today")
    args = parser.parse_args(argv)

    bundle = load_sources()
    date_ny = bundle["date_ny"]
    out_path = GOSSIP_DIR / f"{date_ny}.md"
    if out_path.exists() and not args.force and not args.dry_run:
        print(f"{out_path} already exists — nothing to do (use --force to regenerate)")
        return 0

    source_items = source_item_map(bundle)
    restaurants = json.loads(RESTAURANTS_PATH.read_text(encoding="utf-8"))
    reported, recent_urls = recent_columns(date_ny)

    newsy = sum(
        len(bundle["sources"][s]["items"]) for s in NEWSY_SOURCES if s in bundle["sources"]
    )
    thin_day = newsy < THIN_DAY_THRESHOLD

    user_prompt = build_user_prompt(bundle, source_items, restaurants, reported, thin_day)

    if args.dry_run:
        print("=== DRY RUN — prompts that would be sent (no API call) ===\n")
        print(f"[model] {PRIMARY_MODEL} (fallback: {FALLBACK_MODEL})")
        print(f"[date]  {date_ny}  [thin_day] {thin_day}\n")
        print("--- system prompt ---")
        print(SYSTEM_PROMPT)
        print("--- user prompt ---")
        print(user_prompt)
        return 0

    lexicon = validator.build_lexicon([r["name"] for r in restaurants], source_items)
    client = get_client()
    messages = [{"role": "user", "content": user_prompt}]
    print(f"Calling API ({PRIMARY_MODEL})…")
    model_used, response_text = call_api(client, messages)
    weather_line, items, closer = parse_response(response_text)

    problems = validator.check_column(
        weather_line, items, closer, source_items, lexicon, recent_urls
    )
    retried = False
    if problems:
        retried = True
        print(f"  validator failed ({'; '.join(problems[:6])}) — one corrective retry…")
        messages = messages + [
            {"role": "assistant", "content": response_text},
            {"role": "user", "content": (
                "Your column broke these rules: " + "; ".join(problems) + ". "
                "Rewrite the ENTIRE column, fixing every violation. Same format, same sentinels."
            )},
        ]
        model_used, response_text = call_api(client, messages)
        weather_line, items, closer = parse_response(response_text)
        problems = validator.check_column(
            weather_line, items, closer, source_items, lexicon, recent_urls
        )

    now_ny = dt.datetime.now(NY).isoformat(timespec="seconds")
    weather_items = bundle["sources"].get("weather", {}).get("items", [])
    weather_short = ""
    if weather_items:
        w = weather_items[0]
        weather_short = f"{w['temp_f']}°f · {w['short'].lower()}"
    sources_fetched = [k for k, v in bundle["sources"].items() if v["ok"]]

    session = {
        "tool": "gossip/write.py",
        "ran_at": now_ny,
        "date_ny": date_ny,
        "thin_day": thin_day,
        "model_requested": PRIMARY_MODEL,
        "model_used": model_used,
        "system_prompt": SYSTEM_PROMPT,
        "user_prompt": user_prompt,
        "raw_response": response_text,
        "validator_retry_used": retried,
        "validator_problems_remaining": problems,
    }
    WORK_DIR.mkdir(exist_ok=True)
    (WORK_DIR / "session.json").write_text(
        json.dumps(session, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    if problems:
        print("\nvalidator still failing after retry — NOT publishing:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(f"(audit trail in {WORK_DIR / 'session.json'})", file=sys.stderr)
        return 1

    md = render_markdown(
        date_ny, now_ny, weather_line, weather_short, items, closer,
        source_items, sources_fetched, thin_day, model_used,
    )
    GOSSIP_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"\nWrote {out_path} ({len(items)} items, thin_day={thin_day}, model={model_used})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
