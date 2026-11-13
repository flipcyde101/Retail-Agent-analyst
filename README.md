# Retail analyst agent

An LLM agent that answers natural-language questions over a PostgreSQL database of
supermarket prices. It shows the SQL it ran, a table or chart, and a short answer, and it
comes with an automated evaluation that measures how often it is right, what it costs and
where it fails.

> Work in progress. Day 5 of 10: the agent runs from the terminal and the 50 evaluation
> questions are written; the first measurement comes next.

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

### Cleaning decisions

`data/load.py` applies these rules in order and prints how many rows each one removes.
Of the 220,100 French prices, 185,337 are loaded (84 %), for 65,826 products in 2,285 stores.

| Rule | Rows removed | Why |
|---|---|---|
| Product has no name | 16,984 (7.7 %) | The agent finds products by name; a bare barcode cannot be asked about. |
| `CATEGORY` price (no barcode) | 8,336 (3.8 %) | Loose produce priced per kilo or per piece; it has no product row to join to. |
| Flagged as duplicate by the source | 5,424 (2.5 %) | Open Prices already marks these with `duplicate_of`. |
| Same product, store and day | 3,739 (1.7 %) | Two reports of one shelf price; the most recent report is kept. |
| Not in EUR | 174 (0.1 %) | Overseas territories (XPF) and data-entry errors (ADP). |
| Price ≤ 0 or > 200 EUR | 106 (0.0 %) | 99.9 % of prices are under 92 EUR; above 200 it is not a supermarket shelf price. |

Other decisions:

- **Chains.** Store formats and spellings collapse into one chain (`Carrefour Market`,
  `Carrefour City` → `Carrefour`; three spellings of `E.Leclerc` → one) with the rules in
  `data/chains.py`. The original value stays in `locations.store_brand`. Stores with no
  brand that match no rule are independent shops and get `chain = NULL` (14,067 prices, 7.6 %).
- **Units.** Sizes come from Open Food Facts already converted to g or ml. Any other unit is
  stored as unknown. `v_price_per_unit` gives EUR per kilo or litre and leaves out products
  with unknown size or a size under 5 g/ml (165,995 prices remain).
- **Categories.** `products.category` is the most specific Open Food Facts tag;
  `products.categories` keeps the whole path so a broad category can be filtered too.

Known limitations, left in on purpose and relevant for the evaluation:

- **Multipacks.** Some prices look like the price of a pack (1 l of own-brand UHT milk at
  9.30 EUR) while the product size says 1 l, so the unit price is wrong. The data has no
  field to detect this reliably.
- **Uneven coverage.** Prices are crowdsourced: 52 % of products have a single observation,
  and data before 2024 is sparse. Averages over few rows are fragile.
- **Not only food.** There are cosmetics and household goods, with unit prices in the
  thousands of EUR per litre that are correct.

## Evaluation set

`evals/questions.yaml` holds 50 questions, 10 per category, 10 of them in Spanish:

| Category | What it tests | Example |
|---|---|---|
| lookup | One table, one filter | How many Lidl stores are in the database? |
| aggregation | Grouping, medians, percentages | Which five chains have the most price records? |
| comparison | Chains against each other, unit prices | Which chain has the cheapest whole milk per litre? |
| evolution | Change over time | By what percentage did olive oil change from 2024 to 2026? |
| refusal | Questions the data cannot answer | How many jars of Nutella did Carrefour sell in 2025? |

Each question has a reference SQL and the answer it returns. Decisions:

- **Expected answers are computed, not typed.** `uv run python -m evals.questions --update`
  runs every reference SQL and writes its result into the file; without `--update` it checks
  that the file still matches the database.
- **Reference queries obey the agent's rules.** They run through the same `run_sql` tool:
  one SELECT, a LIMIT, the read-only role, the 5 s timeout.
- **Questions are worded to have one right answer.** They say average or median, with or
  without promotions, and the minimum number of records for a chain to count.
- **Refusals carry proof where there is one.** Four of the ten have a query that shows the
  data is absent (no Mercadona store, nothing before 2010); the other six ask for things
  the schema does not have (sales, margins, stock, customers), for a forecast, or are
  ambiguous.

## Run it

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```bash
cp .env.example .env        # then edit the password
docker compose up -d        # PostgreSQL on localhost:5432
uv sync                     # Python 3.11 + dev tools
uv run python -m data.load  # clean the dumps and load the database
uv run python -m agent.cli "Which chain has the cheapest whole milk per litre?"
uv run python -m evals.questions   # check the 50 questions against the database
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
