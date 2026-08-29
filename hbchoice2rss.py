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

# Delivery methods that indicate a real game (vs. e.g. the IGN Plus coupon).
_GAME_DELIVERY_METHODS = {"steam", "epic", "gog", "origin", "uplay", "battle-net"}

_DATA_ATTR_RE = re.compile(r'data-content-choice-data="([^"]*)"')
_ORDER_ATTR_RE = re.compile(r'data-display-order="([^"]*)"')
_TITLE_RE = re.compile(r'<meta content="([^"]*)" property="og:title"\s*/?>')

_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def fetch_html(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def parse_lineup(page):
    """Return (month_name, items) from the membership page HTML."""
    order_match = _ORDER_ATTR_RE.search(page)
    data_match = _DATA_ATTR_RE.search(page)
    title_match = _TITLE_RE.search(page)

    if not order_match or not data_match:
        raise RuntimeError(
            "Could not find the Choice lineup in the page. "
            "Humble Bundle may have changed their markup."
        )

    order = json.loads(html.unescape(order_match.group(1)))
    raw = json.loads(html.unescape(data_match.group(1)))

    items = []
    for machine_name in order:
        entry = raw.get(machine_name) or {}
        delivery = entry.get("delivery_methods") or []
        is_game = any(m in _GAME_DELIVERY_METHODS for m in delivery)

        copy = (entry.get("recommendation_copy_dict") or {}).get("copy")
        description = None
        if copy:
            text = re.sub(r"<br\s*/?>", "\n", copy, flags=re.IGNORECASE)
            text = re.sub(r"<[^>]+>", "", text)
            text = re.sub(r"[ \t]+", " ", text).strip()
            description = text

        items.append(
            {
                "machine_name": machine_name,
                "title": entry.get("title") or machine_name,
                "is_game": is_game,
                "genres": entry.get("genres") or [],
                "platforms": entry.get("platforms") or [],
                "delivery_methods": delivery,
                "msrp": entry.get("msrp"),
                "user_rating": entry.get("user_rating"),
                "image": entry.get("image"),
                "youtube_links": entry.get("youtube_links") or [],
                "description": description,
            }
        )

    month_name = "Humble Choice"
    if title_match:
        month_name = html.unescape(title_match.group(1))
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
        except FileNotFoundError:
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
