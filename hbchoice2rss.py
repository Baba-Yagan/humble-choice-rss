#!/usr/bin/env python3
"""Generate an RSS feed of the current Humble Bundle Choice lineup.

Modeled on digitalnicesko/aktuality2rss.py: a stdlib-only script that fetches
https://www.humblebundle.com/membership, parses the lineup embedded in the HTML,
and writes an RSS 2.0 feed to `humblechoice.rss`.

Usage:
    python3 hbchoice2rss.py            # generate humblechoice.rss
    python3 hbchoice2rss.py --fetch    # refresh cached HTML first, then generate
    python3 hbchoice2rss.py --include-extras   # also include non-game extras
"""

import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import formatdate, format_datetime
from html import escape

URL = "https://www.humblebundle.com/membership"
RSS_PATH = "humblechoice.rss"
HTML_PATH = "humblechoice.html"

HEADERS = {"User-Agent": "hbchoice2rss/1.0"}
# Ask Next.js for the raw flight (RSC) payload: same page, plain JSON instead of HTML.
RSC_HEADERS = {"User-Agent": "hbchoice2rss/1.0", "RSC": "1"}

# Delivery methods that indicate a real game (vs. e.g. the IGN Plus coupon).
_GAME_DELIVERY_METHODS = {
    "steam", "epic", "gog", "origin", "uplay", "battle-net", "blizzard",
}

_MONTH_RE = re.compile(r"Get (\w+) (\d{4}) Humble Choice")

_JSON_DECODER = json.JSONDecoder()

_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def fetch_html(url):
    """Fetch the membership page as a Next.js flight (RSC) payload.

    The page is now client-rendered, but requesting it with `RSC: 1` returns
    the serialized page data (plain JSON-ish stream) that includes the lineup.
    """
    req = urllib.request.Request(url, headers=RSC_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def _resolve_description(page, ref):
    """Resolve a Next.js flight text reference like '$70' to its HTML text.

    In the flight stream the chunk looks like: `70:T111d,<html text>`, where
    111d is the text length in hex.
    """
    if not isinstance(ref, str) or not ref.startswith("$"):
        return None
    chunk_id = ref[1:]
    m = re.search(r"\b%s:T([0-9a-fA-F]+)," % re.escape(chunk_id), page)
    if not m:
        return None
    return page[m.end() : m.end() + int(m.group(1), 16)]


def parse_lineup(page):
    """Return (month_name, items) from the Next.js flight payload."""
    marker = page.find('"subscriptionProducts":[')
    if marker == -1:
        raise RuntimeError(
            "Could not find the Choice lineup in the page. "
            "Humble Bundle may have changed their markup."
        )

    products, _ = _JSON_DECODER.raw_decode(
        page, marker + len('"subscriptionProducts":')
    )

    items = []
    for entry in products:
        if entry.get("productType") != "humble-choice":
            continue
        delivery = entry.get("deliveryMethods") or []
        delivery = [d for d in delivery if isinstance(d, str)]
        is_game = any(m in _GAME_DELIVERY_METHODS for m in delivery)

        platforms = entry.get("platforms")
        if not isinstance(platforms, list):
            platforms = []

        price = (entry.get("displayPrice") or {}).get("currentPrice") or {}
        msrp = None
        if price.get("centAmount") is not None:
            msrp = {
                "currency": price.get("currencyCode", ""),
                "amount": (price["centAmount"] or 0) / 100.0,
            }

        description = None
        raw_desc = _resolve_description(page, entry.get("description"))
        if raw_desc:
            text = re.sub(r"<br\s*/?>", "\n", raw_desc, flags=re.IGNORECASE)
            text = re.sub(r"<[^>]+>", "", text)
            text = re.sub(r"[ \t]+", " ", text).strip()
            description = text

        items.append(
            {
                "machine_name": entry.get("id") or "",
                "title": entry.get("title") or entry.get("id") or "",
                "is_game": is_game,
                "genres": [],
                "platforms": platforms,
                "delivery_methods": delivery,
                "msrp": msrp,
                "user_rating": None,
                "image": entry.get("thumbnail"),
                "youtube_links": [],
                "description": description,
            }
        )

    month_name = "Humble Choice"
    m = _MONTH_RE.search(page)
    if m:
        month_name = f"{m.group(1)} {m.group(2)} Humble Choice"
    return month_name, items


def month_first_day(month_name):
    """Parse e.g. 'August 2026 Humble Choice' -> datetime(2026, 8, 1, tz=utc)."""
    m = re.match(r"(\w+)\s+(\d{4})", month_name or "")
    if not m:
        return None
    month = _MONTH_MAP.get(m.group(1).lower())
    if not month:
        return None
    return datetime(int(m.group(2)), month, 1, tzinfo=timezone.utc)


def steam_search_url(title):
    return "https://store.steampowered.com/search/?term=" + urllib.parse.quote(title)


def _msrp_str(msrp):
    if not msrp:
        return ""
    cur = msrp.get("currency", "")
    amount = msrp.get("amount")
    if amount is None:
        return ""
    return f"{amount:g} {cur}".strip()


def build_description(item):
    parts = []
    if item.get("genres"):
        parts.append("<p><strong>Genres:</strong> " + escape(", ".join(item["genres"])) + "</p>")
    if item.get("platforms"):
        parts.append("<p><strong>Platforms:</strong> " + escape(", ".join(item["platforms"])) + "</p>")
    msrp = _msrp_str(item.get("msrp"))
    if msrp:
        parts.append("<p><strong>Retail price:</strong> " + escape(msrp) + "</p>")
    rating = item.get("user_rating")
    if rating:
        pct = rating.get("steam_percent")
        count = rating.get("steam_count")
        text = rating.get("review_text", "")
        bits = [escape(text.replace("_", " "))]
        if pct is not None:
            bits.append(f"{round(pct * 100)}% positive")
        if count:
            bits.append(f"{count} reviews")
        parts.append("<p><strong>Steam:</strong> " + ", ".join(bits) + "</p>")
    parts.append(
        '<p><a href="' + steam_search_url(item["title"]) + '">Search on Steam</a></p>'
    )
    if item.get("description"):
        parts.append("<p>" + escape(item["description"]) + "</p>")
    return "".join(parts)


def build_combined_description(items):
    """Build a single HTML description containing all games with images."""
    parts = []
    for item in items:
        parts.append(f'<h3>{escape(item["title"])}</h3>')
        if item.get("image"):
            parts.append(f'<img src="{escape(item["image"])}" alt="{escape(item["title"])}" style="max-width:300px;" /><br/>')
        parts.append(build_description(item))
        parts.append("<hr/>")
    return "".join(parts)


def gen_rss(month_name, items):
    rss = ET.Element("rss", version="2.0", attrib={"xmlns:atom": "http://www.w3.org/2005/Atom"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Humble Bundle Choice"
    ET.SubElement(channel, "link").text = URL
    ET.SubElement(channel, "description").text = "Current games in Humble Bundle Choice"
    ET.SubElement(channel, "language").text = "en"
    ET.SubElement(channel, "lastBuildDate").text = formatdate(timeval=None, localtime=False, usegmt=True)

    atom_link = ET.SubElement(channel, "{http://www.w3.org/2005/Atom}link")
    atom_link.set("href", URL)
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    pub_day = month_first_day(month_name)
    entry = ET.SubElement(channel, "item")
    ET.SubElement(entry, "title").text = month_name
    ET.SubElement(entry, "link").text = URL
    guid = f"humble-choice:{datetime.now().strftime('%Y-%m')}"
    ET.SubElement(entry, "guid", isPermaLink="false").text = guid
    ET.SubElement(entry, "description").text = build_combined_description(items)
    if pub_day is not None:
        ET.SubElement(entry, "pubDate").text = format_datetime(pub_day, usegmt=True)

    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="unicode", xml_declaration=True)


def main():
    include_extras = "--include-extras" in sys.argv

    if "--fetch" in sys.argv:
        page = fetch_html(URL)
        with open(HTML_PATH, "w", encoding="utf-8") as f:
            f.write(page)
    else:
        try:
            with open(HTML_PATH, "r", encoding="utf-8") as f:
                page = f.read()
            parse_lineup(page)  # validate cache; stale/old markup refetches below
        except (FileNotFoundError, RuntimeError, json.JSONDecodeError):
            page = fetch_html(URL)
            with open(HTML_PATH, "w", encoding="utf-8") as f:
                f.write(page)

    month_name, items = parse_lineup(page)
    if not include_extras:
        items = [i for i in items if i["is_game"]]

    rss = gen_rss(month_name, items)
    with open(RSS_PATH, "w", encoding="utf-8") as f:
        f.write(rss)
    print(f"Wrote {len(items)} items to {RSS_PATH}")


if __name__ == "__main__":
    main()
