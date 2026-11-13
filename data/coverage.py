"""Day 1 check: how many Open Prices rows exist per country, chain and year.

Usage:
    uv run python data/coverage.py            # top countries + ES and FR detail
    uv run python data/coverage.py ES FR DE   # detail for the given countries

Expects the dumps in data/raw/ (see README for the download commands).
"""

import gzip
import json
import sys
from collections import Counter
from pathlib import Path

RAW = Path(__file__).parent / "raw"


def read_jsonl(name: str):
    with gzip.open(RAW / name, "rt", encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)


def main(countries: list[str]) -> None:
    locations = {loc["id"]: loc for loc in read_jsonl("locations.jsonl.gz")}

    per_country: Counter[str | None] = Counter()
    stats = {
        c: {"chains": Counter(), "years": Counter(), "products": set(), "stores": set()}
        for c in countries
    }

    for price in read_jsonl("prices.jsonl.gz"):
        loc = locations.get(price["location_id"]) or {}
        country = loc.get("osm_address_country_code")
        per_country[country] += 1
        if country not in stats:
            continue
        s = stats[country]
        s["chains"][loc.get("osm_brand") or "(no brand)"] += 1
        if price.get("date"):
            s["years"][price["date"][:4]] += 1
        if price.get("product_code"):
            s["products"].add(price["product_code"])
        s["stores"].add(price["location_id"])

    print(f"Total prices: {sum(per_country.values()):,}")
    print("Top countries:", per_country.most_common(10))
    for c, s in stats.items():
        print(
            f"\n== {c}: {per_country[c]:,} prices, {len(s['products']):,} products, "
            f"{len(s['stores']):,} stores"
        )
        print("  by year:", sorted(s["years"].items()))
        print("  top chains:", s["chains"].most_common(15))


if __name__ == "__main__":
    main(sys.argv[1:] or ["ES", "FR"])
