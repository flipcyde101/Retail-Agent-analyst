# Retail analyst agent

An LLM agent that answers natural-language questions over a PostgreSQL database of
supermarket prices. It shows the SQL it ran, a table or chart, and a short answer, and it
comes with an automated evaluation that measures how often it is right, what it costs and
where it fails.

> Work in progress. Day 7 of 10: the agent is measured, its failures analysed and fixed, and
> the whole project reviewed ([evaluation report](evals/REPORT.md)); the demo comes next.

## Data

Source: [Open Prices](https://prices.openfoodfacts.org) by Open Food Facts, licensed under
the [ODbL](https://opendatacommons.org/licenses/odbl/1-0/). The dumps are downloaded, not
committed:

```bash
mkdir -p data/raw
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

## How the agent works

One question goes through a loop written with [PydanticAI](https://ai.pydantic.dev):

1. The model gets the instructions, which include the database schema with the comment of
   every column, and one tool, `run_sql`.
2. It writes a query. `agent/sql_guard.py` parses it with sqlglot and accepts only a single
   SELECT over the tables of this project, with a LIMIT of at most 200 and no function that
   acts on the server; anything else goes back to the model with a message that says what
   to fix.
3. The query runs as a PostgreSQL role that can only read, with a 5 s timeout. A database
   error (unknown column, timeout, a result too large to send to a model) also goes back to
   the model. After three failed queries in a row the run stops.
4. The model returns a structured answer: the text, the SQL behind it, a chart type and a
   confidence. The answer is rejected unless that SQL is a query that actually ran, which
   is what keeps invented figures out. It is also rejected if it is empty or written in
   another language than the question.

A run is limited to 12 model calls. If the model gives up or the database is down, the
answer says so instead of guessing.

What protects the database, in order:

- **The SQL check**, on the syntax tree and not on the text: one SELECT, only the three
  tables and the view, no function that acts on the server (`pg_terminate_backend`,
  `query_to_xml`, `pg_sleep`...), a LIMIT.
- **The role.** It can only read, and the same functions are revoked from it, so the
  database still refuses if the check has a bug. `data/load.py` creates it.
- **Limits on what comes back**, applied by `run_sql` itself whatever the text of the query
  said: 5 s, 200 rows, 200,000 characters.

A review that tried to break the first version of the check got past it four ways (the
[report](evals/REPORT.md#7-what-a-review-of-the-sql-check-found) has them). Each one is now
a test. This makes the tool fit for a public demo over public data; it is not a sandbox to
put in front of data that must stay private.

The prompt version is a parameter (`--prompt v1` to `v4` in the command line and in the
evaluation), and each version keeps the checks it was measured with, so an old result can
be reproduced.

| File | What it holds |
|---|---|
| `agent/agent.py` | The loop, the answer model and the checks on the final answer |
| `agent/prompts.py` | The instructions, by version, so any past measurement can be repeated |
| `agent/tools.py` | `describe_schema` and `run_sql`, plain functions with no framework |
| `agent/sql_guard.py` | The SQL rules |
| `agent/language.py` | Guesses English, Spanish or French from a short text |

## Evaluation set

`evals/questions.yaml` is the core set: 50 questions, 10 per category, 10 of them in Spanish:

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

## Results

Full analysis in [evals/REPORT.md](evals/REPORT.md): failure types with an example each, what
fixed them, and three comparisons (prompt versions, two models, schema fetched or given).
Core set of 50 questions, Haiku 5.5 unless said otherwise, 2026-11-13, one question at a time:

| | Prompt v1 | Prompt v3 | Prompt v4 (current) | Sonnet 5.5, v3 |
|---|---|---|---|---|
| Accuracy | 98 % (49) | 100 % (50), three runs | 100 % (50), three runs | 98 % (49) |
| Correct refusals | 10 of 10 | 10 of 10 | 10 of 10 | 9 of 10 |
| Answers in the wrong language | 13 | 0 | 0 | 0 |
| SQL valid at first try | 86.7 % | 95.6 to 97.8 % | 100 % | 100 % |
| Model calls per run | 163 | 160 to 165 | 113 to 122 | 250 |
| Cost of the run | 0.055 EUR | 0.056 to 0.059 EUR | 0.041 to 0.044 EUR | 1.695 EUR |
| Latency, median / p90 | 4.7 / 7.2 s | 5.2 / 8.4 s | 3.9 / 5.6 s | 6.8 / 11.8 s |

The latency of v3 and v4 is from two runs made one after the other (the response time of the
model API changes during the day, so runs hours apart do not compare); the other two columns
are from earlier in the day.

On the natural set (`evals/questions_natural.yaml`, 22 questions worded like a user's, run
with `--set natural`) v4 gets 19 of the 20 graded automatically in each of two runs, and 4
of 4 refusals. Both misses are right answers the grader does not recognise (one gives the
date in words); they and the two open comparisons were read by hand and are correct.

What to keep in mind when reading these numbers:

- **Each question is worth 2 points**, and the same agent on the same questions has scored
  90 % and 98 %. The clear effects are the language (13 wrong answers to 0), the failed
  queries, and the cost and latency of v4, not the last two points of accuracy.
- **A rule in the prompt did not fix the language; a check in code did.** Prompt v2 told the
  model to answer in the language of the question and 8 answers still came back in French.
- **Giving the schema beats fetching it.** v4 writes the schema into the instructions
  instead of offering a tool to read it: one model call fewer per question, about a quarter cheaper,
  about a second faster, same accuracy.
- **The larger model is not better here**: 30 times the cost per question, slower, same
  accuracy within noise.
- **Start-up is not in the latency.** Loading the model client takes 10 to 17 s once per
  process on the machine used. Until it was measured apart it showed up as one question per
  run over 15 s. With it done before the first question, the slowest question of the last
  two runs took 11.2 s and 9.7 s.
- **The evaluation had bugs too**, found by reading the failures and by a review: seven
  questions wrongly marked as failed, a 90th percentile that was really the 92nd, a 1 %
  tolerance that accepted the wrong year. They are listed in the report.
- **How the grader decides.** A number is right if it is within 1 % (exact for a year) and
  appears in the answer text or in the first row of the agent's result; a table is right if
  every expected row is in the result. The rules are in `evals/grading.py`. Cost uses the
  list prices per million tokens (Haiku 5.5: 0.10 / 0.50 USD; Sonnet 5.5: 2 / 10 USD) and
  the ECB rate of 2026-11-12 (1 EUR = 1.1081 USD).

## Run it

Requires [uv](https://docs.astral.sh/uv/) and Docker. Download the data first (see
[Data](#data)).

```bash
cp .env.example .env        # then edit the passwords and add the API key of the model
docker compose up -d        # PostgreSQL on 127.0.0.1:5432
uv sync                     # Python 3.11 and the dependencies
uv run python -m data.load  # clean the dumps, load the database, create the read-only role
uv run python -m agent.cli "Which chain has the cheapest whole milk per litre?"
```

To check and measure:

```bash
uv run ruff check .
uv run python -m pytest                        # the database tests are skipped if it is down
uv run python -m evals.questions               # the 50 expected answers still match the data
uv run python -m evals.run                     # measure the agent: about 5 minutes, 0.04 EUR
uv run python -m evals.run --set natural       # the 22 questions worded like a user's
uv run python -m evals.run --only L01 T03      # a few questions, nothing is saved
uv run python -m evals.run --prompt v3         # an older version, to compare
```

The first question of a process waits 10 to 17 s while the model client loads; the
evaluation does that before it starts the clock.

## Repo layout

```
data/         coverage count, cleaning and loading scripts (not the data)
sql/          schema, view and sanity checks
agent/        agent, prompts, tools, SQL rules, command line
evals/        question sets, grader, runner, results and the report
tests/        234 tests; the ones that need the database are skipped without it
app/          Streamlit demo (day 8)
mcp_server/   MCP server exposing the same tools (day 9)
```
