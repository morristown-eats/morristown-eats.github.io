#!/usr/bin/env python3
"""Hard checks on a generated column. Fail-closed: anything returned here
either triggers the single corrective retry or kills the run uncommitted.

Mirrors of these rules live in the system prompt; the validator is the
enforcement layer, the prompt is the request layer.
"""

from __future__ import annotations

import re

ITEM_MIN_CHARS = 40
ITEM_MAX_CHARS = 280
ITEMS_MIN = 3
ITEMS_MAX = 8
WEATHER_LINE_MAX = 90

# Same emoji range the proven meal-note script uses.
EMOJI_RE = re.compile(
    "[\U0001f000-\U0001fbff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff⬀-⯿️]"
)

CAT_PUN_RE = re.compile(
    r"\b(paw\w*|purr\w*|meow\w*|whisker\w*|claw\w*|feline|kitt(?:y|en)\w*"
    r"|catnip|fur-?ever|nine lives|litter box|hiss\w*)\b",
    re.I,
)

INFLUENCER_RE = re.compile(
    r"\b(hidden gem|must.?try|amazing|elevated|obsessed|foodie|to die for"
    r"|omg|you have to|game.?changer|yum\w*|delish)\b",
    re.I,
)

# Items touching these topics must be explicitly present in the cited sources.
ALLEGATION_RE = re.compile(
    r"\b(clos(?:ed|ing|ure)|shut\s?down|health (?:code|inspector|violation)"
    r"|rodent|roach|lawsuit|evict\w*|bankrupt\w*)\b",
    re.I,
)

ALLCAPS_RE = re.compile(r"\b[A-Z]{4,}\b")

# Repetition is only meaningful for per-story URLs (news articles). These are
# stable landing pages shared by every item from their source — citing them
# twice across days is normal. The textual ALREADY-REPORTED prompt block is
# what prevents content-level repeats for these sources.
REPEAT_EXEMPT_SUBSTRINGS = (
    "api.weather.gov",
    "google.com/maps",
    "mayoarts.org",
    "townofmorristown.org",
    "primegov.com",
    "nj.gov/oag/abc",
)

# Words allowed to carry capitals at the start of an item / anywhere in caps.
BASE_LEXICON = {
    "Morristown", "Morris", "MPAC", "NJ", "NJT", "BYOB", "Speedwell",
    "South", "Street", "The", "Green", "Google", "Washington",
}


CAPWORD_RE = re.compile(r"\b[A-Z][A-Za-z0-9&'’.\-]*\b")


def build_lexicon(restaurant_names: list[str], source_items: dict | None = None) -> set[str]:
    """Proper nouns Morris may capitalize: the 40, the base set, and any
    capitalized word appearing in today's fetched sources — if a source says
    'Chicago plays MPAC', Morris may say Chicago. Grounding extends to caps."""
    lexicon = set(BASE_LEXICON)
    for name in restaurant_names:
        lexicon.update(name.split())
    for item in (source_items or {}).values():
        blob = f"{item.get('title', '')} {item.get('summary', '')}"
        lexicon.update(CAPWORD_RE.findall(blob))
    return lexicon


def check_column(
    weather_line: str,
    items: list[dict],          # [{"text": str, "tags": ["S1", ...]}]
    closer: str,
    source_items: dict,         # id -> raw source item dict
    lexicon: set[str],
    recent_urls: set[str],      # URLs cited in the last 5 committed columns
) -> list[str]:
    problems: list[str] = []

    # 1. structure
    if not weather_line:
        problems.append("weather line is missing")
    elif len(weather_line) > WEATHER_LINE_MAX:
        problems.append(f"weather line is {len(weather_line)} chars (max {WEATHER_LINE_MAX})")
    if not (ITEMS_MIN <= len(items) <= ITEMS_MAX):
        problems.append(f"column has {len(items)} items (need {ITEMS_MIN}-{ITEMS_MAX})")

    all_text = " ".join([weather_line, closer] + [i["text"] for i in items])

    # 4. banned patterns, column-wide
    if EMOJI_RE.search(all_text):
        problems.append("column contains an emoji")
    if "!" in all_text:
        problems.append("column contains an exclamation point")
    for regex, label in ((CAT_PUN_RE, "cat pun"), (INFLUENCER_RE, "influencer phrase")):
        hit = regex.search(all_text)
        if hit:
            problems.append(f"column contains {label} {hit.group(0)!r}")

    for idx, item in enumerate(items, 1):
        text, tags = item["text"], item["tags"]

        # 1. structure per item
        if not (ITEM_MIN_CHARS <= len(text) <= ITEM_MAX_CHARS):
            problems.append(f"item {idx} is {len(text)} chars (need {ITEM_MIN_CHARS}-{ITEM_MAX_CHARS})")

        # 2. grounding — every item cites at least one real fetched source
        if not tags:
            problems.append(f"item {idx} has no source tag")
        else:
            bad = [t for t in tags if t not in source_items]
            if bad:
                problems.append(f"item {idx} cites unknown source tag(s) {','.join(bad)}")

        # 3. allegation guard — serious claims must appear in the cited sources
        allegation = ALLEGATION_RE.search(text)
        if allegation:
            stem = allegation.group(0).lower()[:5]
            cited_raw = " ".join(
                json_dumps_safe(source_items[t]) for t in tags if t in source_items
            ).lower()
            if stem not in cited_raw:
                problems.append(
                    f"item {idx} makes a serious claim ({allegation.group(0)!r}) "
                    "not present in its cited sources"
                )

        # 5. register — lowercase deadpan, capitals only for known proper nouns
        first = text.lstrip()[:1]
        if first.isupper():
            first_word = text.lstrip().split()[0].strip(".,;:()\"'")
            if first_word not in lexicon:
                problems.append(f"item {idx} starts uppercase with {first_word!r} (not a known proper noun)")
        for caps in ALLCAPS_RE.findall(text):
            if caps not in lexicon:
                problems.append(f"item {idx} contains ALL-CAPS word {caps!r}")

        # 6. repetition — don't re-cite a per-story URL from the last 5 columns
        for tag in tags:
            url = source_items.get(tag, {}).get("url", "")
            if url in recent_urls and not any(s in url for s in REPEAT_EXEMPT_SUBSTRINGS):
                problems.append(f"item {idx} re-cites a URL already used in a recent column ({url})")

    return problems


def json_dumps_safe(obj) -> str:
    import json
    try:
        return json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(obj)
