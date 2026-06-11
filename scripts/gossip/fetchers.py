#!/usr/bin/env python3
"""Source fetchers for the daily gossip pipeline. Stdlib only.

Every fetcher returns a list of item dicts: {"title"/"text", "url", ...}.
Callers (ingest.py) wrap each fetcher in try/except — a dead source must
never kill the run. Timeouts are short on purpose; these run unattended.
"""

from __future__ import annotations

import html
import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

USER_AGENT = "morris-hears-things/1.0 (gossip pipeline; morristowneats.com)"
TIMEOUT = 15

GREEN_RSS_URL = "https://morristowngreen.com/feed/"
NWS_POINTS_URL = "https://api.weather.gov/points/40.797,-74.482"
MPAC_URL = "https://www.mayoarts.org/events/"
ABC_NOTICES_URL = "https://www.nj.gov/oag/abc/library_notices.htm"
PLANNING_URLS = (
    "https://www.townofmorristown.org/planningboard",
    "https://morristown.primegov.com/public/portal",
)

RSS_MAX_AGE_HOURS = 48
RSS_CAP = 12
MPAC_CAP = 6
ABC_CAP = 5
PLANNING_CAP = 5


def http_get(url: str, timeout: int = TIMEOUT, accept: str = "*/*") -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": accept}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def strip_html(raw: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Morristown Green — vanilla WordPress RSS 2.0 (the anchor news source)
# ---------------------------------------------------------------------------

def fetch_green_rss() -> list[dict]:
    root = ElementTree.fromstring(http_get(GREEN_RSS_URL, accept="application/rss+xml"))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=RSS_MAX_AGE_HOURS)
    items = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        pub_raw = (node.findtext("pubDate") or "").strip()
        summary = strip_html(node.findtext("description") or "")[:400]
        if not title or not link:
            continue
        try:
            published = parsedate_to_datetime(pub_raw)
            if published < cutoff:
                continue
            pub_iso = published.isoformat(timespec="seconds")
        except (TypeError, ValueError):
            pub_iso = pub_raw  # keep undated items rather than guessing
        items.append({"title": title, "url": link, "published": pub_iso, "summary": summary})
        if len(items) >= RSS_CAP:
            break
    return items


# ---------------------------------------------------------------------------
# NWS weather — free, no key. points endpoint -> forecast URL -> periods.
# ---------------------------------------------------------------------------

def fetch_weather() -> list[dict]:
    points = json.loads(http_get(NWS_POINTS_URL, accept="application/geo+json"))
    forecast_url = points["properties"]["forecast"]
    forecast = json.loads(http_get(forecast_url, accept="application/geo+json"))
    items = []
    for period in forecast["properties"]["periods"][:2]:
        items.append({
            "title": f"forecast — {period['name']}",
            "period": period["name"],
            "temp_f": period["temperature"],
            "short": period["shortForecast"],
            "summary": period["detailedForecast"][:300],
            "url": forecast_url,
        })
    return items


# ---------------------------------------------------------------------------
# MPAC events — static HTML, best effort. The page lists shows roughly
# chronologically, so the first handful approximates "coming up". No date
# parsing on purpose: that's the brittle part. The column prompt tells the
# model to only mention shows in the next few days.
# ---------------------------------------------------------------------------

def fetch_mpac() -> list[dict]:
    page = http_get(MPAC_URL, accept="text/html")
    items = []
    for segment in re.split(r"<h3[^>]*>", page, flags=re.I)[1:]:
        head, _, tail = segment.partition("</h3>")
        title = strip_html(head)
        snippet = strip_html(tail)[:200]  # strip THEN truncate — never slice a tag open
        if not title or len(title) < 3:
            continue
        items.append({"title": title, "summary": snippet, "url": MPAC_URL})
        if len(items) >= MPAC_CAP:
            break
    return items


# ---------------------------------------------------------------------------
# NJ ABC liquor-license notices — statewide HTML page, grep for Morristown.
# Empty most days; that is the expected result, not a failure.
# ---------------------------------------------------------------------------

def fetch_abc_notices() -> list[dict]:
    page = http_get(ABC_NOTICES_URL, accept="text/html")
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</(p|li|tr|div|h\d)>", "\n", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    items = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if "morristown" in line.lower() and len(line) > 20:
            items.append({"title": line[:300], "url": ABC_NOTICES_URL})
            if len(items) >= ABC_CAP:
                break
    return items


# ---------------------------------------------------------------------------
# Town planning/zoning — best effort against two portals. Expected to fail
# or come back empty often (the PrimeGov portal is JS-rendered). Lines that
# look like meeting/agenda references are surfaced raw for the model.
# ---------------------------------------------------------------------------

PLANNING_LINE_RE = re.compile(
    r"(planning board|zoning board|board of adjustment).{0,120}", re.I
)
# A real date: month + day number, a slash date, or a bare year. The bare word
# "may" must NOT match (it flagged regulatory boilerplate on the first live run).
DATE_HINT_RE = re.compile(
    r"\b(20\d{2}|\d{1,2}/\d{1,2}"
    r"|(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2})\b",
    re.I,
)
YEAR_RE = re.compile(r"\b(20\d{2})\b")


def fetch_planning() -> list[dict]:
    current_year = datetime.now(timezone.utc).year
    items = []
    for url in PLANNING_URLS:
        try:
            page = http_get(url, accept="text/html")
        except Exception:
            continue  # one portal down is routine; the other may answer
        text = re.sub(r"<br\s*/?>|</(p|li|tr|div|h\d)>", "\n", page, flags=re.I)
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
        for line in text.splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            if not (PLANNING_LINE_RE.search(line) and DATE_HINT_RE.search(line)):
                continue
            years = [int(y) for y in YEAR_RE.findall(line)]
            if years and max(years) < current_year:
                continue  # stale archive rows (old agendas/minutes)
            items.append({"title": line[:300], "url": url})
            if len(items) >= PLANNING_CAP:
                return items
    return items
