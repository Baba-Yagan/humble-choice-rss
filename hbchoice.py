"""Fetch the current Humble Bundle Choice lineup and print it as JSON.

The membership page (https://www.humblebundle.com/membership) is server-rendered
and embeds the full lineup as JSON inside HTML attributes:
  * data-display-order        -> ordered list of item machine-names
  * data-content-choice-data  -> dict keyed by machine-name with each item's
    title, genres, platforms, delivery methods, MSRP, ratings, etc.

There is no official public API; this parses that embedded data and prints it.

Usage:
    python3 hbchoice.py                  # JSON of all games
    python3 hbchoice.py --include-extras # also include coupons/non-game extras
    python3 hbchoice.py --game deadcells # a single game by machine name
    python3 hbchoice.py --pretty         # pretty-printed JSON
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from typing import Any, Dict, List

import requests

MEMBERSHIP_URL = "https://www.humblebundle.com/membership"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Delivery methods that indicate a real game (vs. e.g. the IGN Plus coupon).
_GAME_DELIVERY_METHODS = {"steam", "epic", "gog", "origin", "uplay", "battle-net"}

_DATA_ATTR_RE = re.compile(r'data-content-choice-data="([^"]*)"')
_ORDER_ATTR_RE = re.compile(r'data-display-order="([^"]*)"')
_TITLE_RE = re.compile(r'<meta content="([^"]*)" property="og:title"\s*/?>')


def parse_lineup(page: str) -> Dict[str, Any]:
    order_match = _ORDER_ATTR_RE.search(page)
    data_match = _DATA_ATTR_RE.search(page)
    title_match = _TITLE_RE.search(page)

    if not order_match or not data_match:
        raise RuntimeError(
            "Could not find the Choice lineup in the page. "
            "Humble Bundle may have changed their markup."
        )

    order: List[str] = json.loads(html.unescape(order_match.group(1)))
    raw: Dict[str, Any] = json.loads(html.unescape(data_match.group(1)))

    items: List[Dict[str, Any]] = []
    for machine_name in order:
        entry = raw.get(machine_name) or {}
        delivery = entry.get("delivery_methods") or []
        is_game = any(m in _GAME_DELIVERY_METHODS for m in delivery)

        # Flatten the recommendation copy into a plain-text description.
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

    return {"month_name": month_name, "items": items}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="hbchoice",
        description="Print the current Humble Bundle Choice lineup as JSON.",
    )
    parser.add_argument(
        "--include-extras",
        action="store_true",
        help="include non-game extras (e.g. the IGN Plus coupon)",
    )
    parser.add_argument(
        "--game",
        metavar="MACHINE_NAME",
        help="print a single item by machine name (e.g. deadcells)",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="pretty-print the JSON (default: compact)",
    )
    args = parser.parse_args(argv)

    resp = requests.get(MEMBERSHIP_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    lineup = parse_lineup(resp.text)

    if not args.include_extras and not args.game:
        lineup["items"] = [i for i in lineup["items"] if i["is_game"]]

    if args.game:
        for item in lineup["items"]:
            if item["machine_name"] == args.game:
                lineup = item
                break
        else:
            print(f"Item '{args.game}' not found", file=sys.stderr)
            return 1

    print(json.dumps(lineup, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
