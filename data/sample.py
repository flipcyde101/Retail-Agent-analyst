"""Load a handful of made-up rows instead of the real dumps.

Usage:
    uv run python -m data.sample

For continuous integration, where downloading 100 MB of dumps on every push makes no sense.
It builds the same tables, view and read-only role as data.load, so the tests of the tools
and of the role run against a real PostgreSQL. The rows are invented and marked as such:
nothing measured in this project uses them.
"""

from datetime import date
from decimal import Decimal

from dotenv import load_dotenv

from data.load import ROOT, load_rows, report_callable

# code, name, brand, category, categories, quantity, unit
PRODUCTS = [
    ("0000000000011", "Sample whole milk 1 l", "Sample", "whole-milks",
     ["en:dairies", "en:milks", "en:whole-milks"], 1000, "ml"),
    ("0000000000028", "Sample butter 250 g", "Sample", "butters",
     ["en:dairies", "en:butters"], 250, "g"),
    ("0000000000035", "Sample olive oil 75 cl", "Sample", "olive-oils",
     ["en:olive-oils"], 750, "ml"),
    ("0000000000042", "Sample pasta 500 g", None, "pastas", ["en:pastas"], 500, "g"),
    ("0000000000059", "Sample item of unknown size", None, None, [], None, None),
]  # fmt: skip

# id, chain, store_brand, name, city, postcode, country
LOCATIONS = [
    (1, "Carrefour", "Carrefour Market", "Sample store 1", "Paris", "75001", "FR"),
    (2, "E.Leclerc", "E.Leclerc", "Sample store 2", "Lyon", "69001", "FR"),
    (3, "Lidl", "Lidl", "Sample store 3", "Grenoble", "38000", "FR"),
    (4, None, None, "Sample independent shop", "Paris", "75011", "FR"),
]

# id, product_code, location_id, price, currency, date, is_discounted
PRICES = [
    (number, code, location, Decimal(price), "EUR", date(year, month, 15), discounted)
    for number, (code, location, price, year, month, discounted) in enumerate(
        [
            ("0000000000011", 1, "1.45", 2025, 3, False),
            ("0000000000011", 2, "1.30", 2025, 3, False),
            ("0000000000011", 3, "1.10", 2026, 1, False),
            ("0000000000011", 4, "1.80", 2026, 1, False),
            ("0000000000028", 1, "3.20", 2025, 6, False),
            ("0000000000028", 2, "2.60", 2025, 6, True),
            ("0000000000028", 3, "2.40", 2026, 2, False),
            ("0000000000035", 1, "9.90", 2024, 11, False),
            ("0000000000035", 2, "9.10", 2026, 2, False),
            ("0000000000042", 3, "0.95", 2026, 2, False),
            ("0000000000042", 4, "1.40", 2026, 3, False),
            ("0000000000059", 4, "4.00", 2026, 3, False),
        ],
        start=1,
    )
]


def main() -> None:
    load_dotenv(ROOT / ".env")
    still_callable = load_rows(PRODUCTS, LOCATIONS, PRICES)
    print(
        f"Loaded the sample: {len(PRICES)} prices, {len(PRODUCTS)} products, "
        f"{len(LOCATIONS)} stores"
    )
    report_callable(still_callable)


if __name__ == "__main__":
    main()
