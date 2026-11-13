# Retail analyst agent

An LLM agent that answers natural-language questions over a PostgreSQL database of
supermarket prices. It shows the SQL it ran, a table or chart, and a short answer, and it
comes with an automated evaluation that measures how often it is right, what it costs and
where it fails.

> Work in progress. Day 1 of 10.

## Data

Source: [Open Prices](https://prices.openfoodfacts.org) by Open Food Facts, licensed under
the [ODbL](https://opendatacommons.org/licenses/odbl/1-0/). The dumps are downloaded, not
committed:

```bash
curl -o data/raw/prices.jsonl.gz https://prices.openfoodfacts.org/data/prices.jsonl.gz
curl -o data/raw/locations.jsonl.gz https://prices.openfoodfacts.org/data/locations.jsonl.gz
curl -o data/raw/products.jsonl.gz https://prices.openfoodfacts.org/data/products.jsonl.gz
```

### Coverage and country decision (dump of 2026-11-12)

Counted with `uv run python data/coverage.py`. The dump has 343,724 prices in total.

| | Spain | France |
|---|---|---|
| Prices | 1,154 | 220,100 |
| Products with barcode | 1,033 | 78,877 |
| Stores | 262 | 2,485 |
| Date range | 2017 – Nov 2026 | 2010 – Nov 2026 |
| Prices dated 2024–2026 | 1,097 | 198,995 |
| Largest chains | Bonpreu 162, Supeco 146, Mercadona 138, Lidl 104 | E.Leclerc ~58,000, Auchan ~26,000, Carrefour ~34,000 (all formats) |

**Decision: France only.** The threshold to use Spain was about 20,000 prices across several
chains; Spain has 1,154, roughly one price per product, which cannot support questions about
price evolution or comparisons between chains. France has 64 % of the whole dataset.

Things the counts already show that the cleaning step (day 2) must handle:

- Chain names are not normalised: `E.Leclerc`, `E. Leclerc` and `Centre Commercial E.Leclerc`
  are the same chain; Carrefour and Intermarché are split by store format.
- About 11 % of French prices come from stores with no `osm_brand`.
- A few prices are not in EUR (ADP, XPF, etc.) and about 4 % are `CATEGORY` prices (loose
  produce with no barcode).
- Before 2024 the data is sparse: 90 % of French prices are from 2024 onwards.

## Run it

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```bash
cp .env.example .env        # then edit the password
docker compose up -d        # PostgreSQL on localhost:5432
uv sync                     # Python 3.11 + dev tools
uv run ruff check .
uv run python -m pytest
```

## Repo layout

```
data/         download, load and cleaning scripts (not the data)
sql/          schema and views
agent/        agent, tools, Pydantic models
mcp_server/   MCP server exposing the same tools
evals/        questions, runner, results
app/          Streamlit demo
tests/
```
