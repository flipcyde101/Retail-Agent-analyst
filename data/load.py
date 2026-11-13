"""Clean the Open Prices dumps and load them into PostgreSQL.

Usage (from the repo root, with the database running):
    uv run python -m data.load

Re-runnable: it drops and recreates the tables every time. Each cleaning rule counts the
rows it removes and the totals are printed at the end, so every decision is visible.
"""

import gzip
import json
import os
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql

from data.chains import normalise_chain

ROOT = Path(__file__).parent.parent
RAW = ROOT / "data" / "raw"
SQL = ROOT / "sql"

COUNTRY = "FR"
MAX_PRICE = Decimal("200")  # above this it is not a supermarket shelf price (99.9 % are < 92)


def read_jsonl(name: str):
    with gzip.open(RAW / name, "rt", encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)


def clean_locations() -> dict[int, tuple]:
    """Stores in COUNTRY, with the chain name normalised."""
    rows = {}
    for loc in read_jsonl("locations.jsonl.gz"):
        if loc.get("osm_address_country_code") != COUNTRY:
            continue
        rows[loc["id"]] = (
            loc["id"],
            normalise_chain(loc.get("osm_brand"), loc.get("osm_name")),
            loc.get("osm_brand"),
            loc.get("osm_name"),
            loc.get("osm_address_city"),
            loc.get("osm_address_postcode"),
            COUNTRY,
        )
    return rows


def clean_products() -> dict[str, tuple]:
    """Products that have a name. Size is kept only when it is in g or ml."""
    rows = {}
    for p in read_jsonl("products.jsonl.gz"):
        name = (p.get("product_name") or "").strip()
        if not name:
            continue
        tags = p.get("categories_tags") or []
        unit = p.get("product_quantity_unit")
        quantity = p.get("product_quantity")
        if unit not in ("g", "ml") or not quantity or quantity <= 0:
            unit, quantity = None, None
        rows[p["code"]] = (
            p["code"],
            name,
            (p.get("brands") or "").strip() or None,
            tags[-1].split(":", 1)[-1] if tags else None,  # most specific tag, no "en:" prefix
            tags,
            quantity,
            unit,
        )
    return rows


def clean_prices(locations: dict, products: dict) -> tuple[list[tuple], Counter]:
    """Apply the cleaning rules in order. Each dropped row is counted under one reason."""
    dropped: Counter[str] = Counter()
    by_key: dict[tuple, tuple] = {}

    for p in read_jsonl("prices.jsonl.gz"):
        if p["location_id"] not in locations:
            continue  # other countries: out of scope, not counted as dropped
        dropped["0_total_in_country"] += 1

        if p["type"] != "PRODUCT":
            dropped["category_price_no_barcode"] += 1
            continue
        if p.get("duplicate_of") is not None:
            dropped["flagged_duplicate_by_source"] += 1
            continue
        if p.get("currency") != "EUR":
            dropped["not_eur"] += 1
            continue
        if not p.get("date"):
            dropped["no_date"] += 1
            continue
        try:
            price = Decimal(p["price"])
        except (InvalidOperation, TypeError):
            dropped["price_not_a_number"] += 1
            continue
        if price <= 0 or price > MAX_PRICE:
            dropped["price_out_of_range"] += 1
            continue
        if p["product_code"] not in products:
            dropped["product_without_name"] += 1
            continue

        # Same product, store and day reported twice: keep the latest report (highest id).
        key = (p["product_code"], p["location_id"], p["date"])
        row = (
            p["id"],
            p["product_code"],
            p["location_id"],
            price,
            "EUR",
            p["date"],
            bool(p.get("price_is_discounted")),
        )
        previous = by_key.get(key)
        if previous is not None:
            dropped["same_product_store_day"] += 1
            if previous[0] > row[0]:
                continue
        by_key[key] = row

    return list(by_key.values()), dropped


def connect() -> psycopg.Connection:
    return psycopg.connect(
        host="localhost",
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def copy_rows(cur: psycopg.Cursor, table: str, columns: list[str], rows) -> None:
    statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns))
    )
    with cur.copy(statement) as copy:
        for row in rows:
            copy.write_row(row)


def create_readonly_role(cur: psycopg.Cursor) -> None:
    """The role the agent connects with. It can read and nothing else.

    This is the real safety barrier: even if the SQL validation of day 3 had a bug, the
    database itself refuses to write.
    """
    role = sql.Identifier(os.environ["AGENT_DB_USER"])
    password = sql.Literal(os.environ["AGENT_DB_PASSWORD"])
    database = sql.Identifier(os.environ["POSTGRES_DB"])

    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (os.environ["AGENT_DB_USER"],))
    verb = sql.SQL("ALTER") if cur.fetchone() else sql.SQL("CREATE")
    cur.execute(sql.SQL("{} ROLE {} LOGIN PASSWORD {}").format(verb, role, password))

    cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, role))
    cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
    cur.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(role))
    cur.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(role))
    cur.execute(sql.SQL("ALTER ROLE {} SET statement_timeout = '5s'").format(role))


def main() -> None:
    load_dotenv(ROOT / ".env")

    locations = clean_locations()
    products = clean_products()
    prices, dropped = clean_prices(locations, products)

    # Load only what the prices reference: no orphan products or stores.
    used_products = {row[1] for row in prices}
    used_locations = {row[2] for row in prices}

    with connect() as conn, conn.cursor() as cur:
        cur.execute((SQL / "schema.sql").read_text(encoding="utf-8"))
        copy_rows(
            cur,
            "products",
            ["code", "name", "brand", "category", "categories", "quantity", "unit"],
            (products[code] for code in used_products),
        )
        copy_rows(
            cur,
            "locations",
            ["id", "chain", "store_brand", "name", "city", "postcode", "country"],
            (locations[i] for i in used_locations),
        )
        copy_rows(
            cur,
            "prices",
            ["id", "product_code", "location_id", "price", "currency", "date", "is_discounted"],
            prices,
        )
        cur.execute((SQL / "views.sql").read_text(encoding="utf-8"))
        create_readonly_role(cur)
        cur.execute("ANALYZE")

    total = dropped.pop("0_total_in_country")
    print(f"Prices in {COUNTRY}: {total:,}")
    for reason, n in dropped.most_common():
        print(f"  - {reason}: {n:,} ({n / total:.1%})")
    print(
        f"Loaded: {len(prices):,} prices, {len(used_products):,} products, "
        f"{len(used_locations):,} stores"
    )


if __name__ == "__main__":
    main()
