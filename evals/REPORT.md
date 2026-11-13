# Evaluation report

What failed, what fixed it and what it cost. Every figure comes from the files in
`evals/results/`; `summary.csv` has one line per run. All runs are from 2026-11-13 and ask the
questions one at a time, so the latency is what a single user would see.

## Summary

Core set of 50 questions, Haiku 5.5 unless said otherwise.

| | Prompt v1 | Prompt v3 | Prompt v4 (current) | Sonnet 5.5, v3 |
|---|---|---|---|---|
| Accuracy | 98 % (49) | 100 % (50), three runs | 100 % (50), three runs | 98 % (49) |
| Correct refusals | 10 of 10 | 10 of 10 | 10 of 10 | 9 of 10 |
| Answers in the wrong language | 13 | 0 | 0 | 0 |
| SQL valid at first try | 86.7 % | 95.6, 97.8 and 95.6 % | 100 %, three runs | 100 % |
| Failed queries | 6 | 2, 1 and 2 | 0, 0 and 2 | 1 |
| Model calls | 163 | 160, 163 and 165 | 122, 118 and 113 | 250 |
| Cost of the run | 0.055 EUR | 0.056, 0.057 and 0.059 EUR | 0.044, 0.043 and 0.041 EUR | 1.695 EUR |
| Latency, median / p90 / max | 4.7 / 7.2 / 8.9 s | 5.2 / 8.4 / 12.1 s | 3.9 / 5.6 / 9.7 s | 6.8 / 11.8 / 14.6 s |

The questions are the same in every column, as they are today. Two earlier runs of v1, on
the questions before two of them were corrected, gave 90 % and 98 %. The latency of v3 and
v4 is from runs made minutes apart, with the model client loaded beforehand; comparison 3
has every run.

On the natural set (22 questions worded like a user's), v3 gets 20 of the 20 graded
automatically and v4 gets 19 of 20 in each of two runs, with 4 of 4 refusals and no answer
in the wrong language. The two misses of v4 are correct answers that the grader does not
recognise (see failure type 6); the other two questions are read by hand.

Read the accuracy with its limits: 50 questions make each one worth 2 points, and the same
agent on the same questions has given 90 % and 98 %. What the versions show beyond noise is
the language (13 to 0), the failed queries (6 to between 0 and 2) and, for v4, a quarter
fewer model calls.

## Failure types

Found by reading every failed question and the doubtful passes of each run, not only the
totals.

### 1. Final answer before any work

The model returns its final answer as its first move, with `answerable = false` and an empty
or placeholder text. The run ends there, because returning the answer is how a run ends.

- Example (T04, v1): asked how many times more prices February 2026 has than February 2025,
  it answered "Primero necesito revisar el esquema de la base de datos" after one model call
  and no query.
- How often: 3, 1 and 0 questions of 50 in the three v1 runs. Only T04 failed twice.
- Fix: the output validator sends a refusal back if `describe_schema` was never called, and
  rejects an empty answer. The prompt also says that returning the answer ends the run.
- After: 0 in the runs of v2 and v3 on the core set. In v4 the schema is in the instructions,
  so the model has nothing left to say it must look up first: 0 in three runs.

### 2. A filter nobody asked for

The agent leaves promotional prices out of an average when the question does not mention
promotions. The answer is carefully explained and wrong against the reference.

- Example (C09, v1): average butter price per kilo at U, 12.31 EUR with promotions excluded,
  12.55 EUR over all prices. Same thing in T03 with v2: olive oil down 2.3 % instead of 2.89 %.
- Fix: prompt v3 says not to add filters the question does not mention, and to name the
  method when the question leaves it open.
- After: not seen in the two v3 runs. One occurrence per run before, so two clean runs are a
  good sign, not proof.

### 3. Answer in the wrong language

English questions answered in French, the language of the product names.

- How often: 11, 10 and 13 of 50 with v1. The grader checks figures, not language, so these
  counted as right.
- First fix, prompt v2: an explicit rule at the top of the prompt ("the data is French, but
  that never decides the language"). Result: 8 French answers. It did not work.
- Second fix, v3: the code guesses the language of the question, adds "This question is
  written in English: answer in English" to the instructions, and the validator sends back an
  answer in another language.
- After: 0 in the three v3 runs with Haiku and in the Sonnet run.

The lesson is the difference between asking and checking. A rule in the prompt lowered
nothing; a check in code removed the failure.

### 4. Stray characters after the SQL

The tool argument arrives as `SELECT ... LIMIT 200"}`. The validator rejects it and the model
has to try again. No wrong answer came from this, but it costs a model call each time.

- How often: 4 of the 7 failed queries in a sample of 12 questions run to collect the
  errors; 5 of the 6 failed queries of the last v1 run are the same parse error.
- Fix: strip trailing quotes and braces before validating. It is safe here because a valid
  query always ends in the LIMIT number, and the cleaned query is still validated.
- After: 1 to 2 queries repaired per core run, 6 on the natural set. First-try validity went
  from 84-87 % to 96-98 %. The failed queries that remain on the core set are a forgotten LIMIT.

### 5. A regression caused by a fix

Prompt v2 made the validator stricter and left the retry budget for the final answer at its
default of one. On C09 two answers in a row were sent back and the run ended with no answer.

- Fix: three retries for the final answer in the strict versions, with a test.
- This is why v2 scored 92 %, below v1. It stays in `summary.csv`.

### 6. Failures of the evaluation, not of the agent

Seven of the questions marked wrong across all runs were the evaluation's fault.

| Question | Problem | What changed |
|---|---|---|
| T06 | Right date on top of 200 sorted rows, written in words | Grader looks at the first row of the result |
| N14 | Right figures in the text, another query attached | Grader accepts rows fully stated in the text |
| T09 | Quarters returned as dates instead of 1 to 4 | Reference keeps only the counts |
| T08, T10 | "In each year from A to B" also reads "in all of them" | Reworded: "for each year separately" |
| N10 | Auchan by mean, E.Leclerc by median | Marked for manual review |
| N11 | Both chain names are in any answer | Marked for manual review |

Each grader change has a test built from the case that revealed it. In the v3 runs and in
the last v4 run, every pass that rests on the answer text and both manual answers were read
by hand: all hold.

Two more appeared with v4 on the natural set, both right answers marked as failed, and they
are counted as failures in the summary:

| Question | Problem | What changed |
|---|---|---|
| N14 | "Monoprix averages 4.88 versus 2.51 for Lidl": one figure after its name, one before | The rule that pairs names and figures accepts both orders |
| N17 | The date written as "8 July 2010", and not in the first row of the result | Nothing: reading dates in words is left to the person who reviews |

A review of the evaluation code found errors that favoured the agent or distorted a figure.
None changes a verdict of the runs that were read by hand, but they were wrong:

| Where | Problem | What changed |
|---|---|---|
| 90th percentile | It took the 46th of 50 values, which is the 92nd | Nearest rank; recomputed for every run in `summary.csv` (it fell by 0 to 1 s) |
| Tolerance of 1 % | 1 % of 2024 is twenty years: "2023" passed for 2024 | A year must be exact |
| Figures in the text | "In 2025, 30,805 prices" was read as one token and the figure lost | A space joins digits only as a thousands separator, and both readings are kept |
| Names with figures | Two names and two figures anywhere in the text passed, even swapped | Each figure must be next to its own name |
| Language check | "no" counted as Spanish, so "No data, no rows." was Spanish | Removed; "¿" and "¡" count as Spanish |
| A question that raised an error | Counted as 0 seconds in the latency | Timed like the others |

### 7. What a review of the SQL check found

The first SQL check accepted any single SELECT with a LIMIT. A review that tried to break it
succeeded four ways, each one run for real against the local database:

| Attack | What happened | Fix |
|---|---|---|
| A NUL character inside a comment, before the LIMIT | The parser saw the LIMIT, PostgreSQL stopped reading at the NUL: 185,337 rows came back | NUL is rejected, and `run_sql` counts the rows itself |
| `query_to_xml('SELECT ...')` | Ran SQL the check never saw and ignored the 5 s timeout; one variant used memory until PostgreSQL restarted | Rejected by name, and revoked from the role |
| `pg_terminate_backend(pid)` with pids read from `pg_stat_activity` | Any user of a shared demo could kill the queries of the others | Rejected; only the project's tables can be read; revoked from the role |
| `json_agg` of a whole table with LIMIT 1 | One row of 25 MB, which would be sent to the model | Results over 200,000 characters are refused |

Smaller ones: a comment after the semicolon counted as a second statement, `FETCH FIRST`
got a misleading message, and a range value broke the JSON of the result. All have tests.

The 382 different queries the agent and the references had run until then all pass the
stricter check, and the accuracy after it is unchanged (100 % on the core set).

That restart is also why one run is missing: it happened in the middle of a v4 run, 13
questions failed with "cannot connect to the database", and the run was deleted as invalid.
It did show the handling of a database that is down working as intended: each of those
questions stopped after one model call with a clear error, instead of sending the failure
to the model as a query to fix.

### 8. A slow first question that was not the question

In every run one question took over 15 s, and it was always the first one (17.5, 15.3 and
37.0 s). The time was the model client being loaded, which takes 10 to 17 s once per
process on the machine used, not the answer. The client is now loaded before the first
question (`warm_up`), and the evaluation reports that time apart. After that the slowest
question of a run took 12.1, 11.2 and 9.7 s.

### Types expected and not found

The plan expected wrong units, wrong joins and invented figures. None appeared in the
answers that were read. Invented figures are blocked by construction: an answer is rejected
unless its SQL was actually run. Units and joins are mostly absorbed by the view
`v_price_per_unit` and by the comments on every column, which the agent reads first.

## Comparison 1: prompt versions (Haiku 5.5, core set)

| | v1 | v2 | v3 | v3 again |
|---|---|---|---|---|
| Accuracy | 98 % | 92 % | 100 % | 100 % |
| Wrong language | 13 | 9 | 0 | 0 |
| SQL valid at first try | 86.7 % | 93.2 % | 95.6 % | 97.8 % |
| Failed queries | 6 | 3 | 2 | 1 |
| Runs that gave up | 0 | 1 | 0 | 0 |
| Input tokens | 443,900 | 461,705 | 478,754 | 485,147 |
| Cost | 0.055 EUR | 0.056 EUR | 0.056 EUR | 0.057 EUR |
| Latency, median / p90 | 4.7 / 7.2 s | 4.6 / 7.7 s | 4.3 / 6.3 s | 4.9 / 7.1 s |

These four runs include the loading of the model client in their first question, which moves the maximum but not the median or the p90.

- v1 is the run on today's questions; v2 was measured before T09 and T10 were corrected, and
  two of its four failures are those. The 9 of v2 includes one English fallback message on
  a Spanish question.
- The longer prompt costs about 8 % more input tokens. Fewer retries roughly pay for it:
  cost and latency are unchanged.

## Comparison 2: two models (prompt v3, core set)

| | Haiku 5.5 | Sonnet 5.5 |
|---|---|---|
| Accuracy | 100 % (50), twice | 98 % (49) |
| Correct refusals | 10 of 10 | 9 of 10 |
| SQL valid at first try | 95.6 % and 97.8 % | 100 % |
| Queries run | 59 | 150 |
| Model calls | 163 | 250 |
| Tokens in / out | 485,147 / 29,383 | 796,001 / 28,603 |
| Cost of the run | 0.057 EUR | 1.695 EUR |
| Cost per question | 0.0011 EUR | 0.034 EUR |
| Latency, median / p90 / max | 4.9 / 7.1 / 15.3 s | 6.8 / 11.8 / 14.6 s |

- Sonnet writes cleaner SQL and explores more: two and a half times as many queries.
- Its one failure is that thoroughness. On R06 (milk prices in 2005) it ran 11 queries
  looking for the data and hit the limit of 12 model calls per question, so the run ended
  without the refusal. Haiku refuses after one or two queries.
- For this task the larger model costs 30 times more per question, is slower, and is not
  more accurate. Haiku 5.5 stays the model of the demo. One run of Sonnet is not enough to
  say it is worse; it is enough to say it is not worth 30 times the price here.

## Comparison 3: schema fetched or given (Haiku 5.5, core set)

Prompts v1 to v3 offer a tool, `describe_schema`, and tell the model to call it first. The
schema is the same for every question, so that call bought nothing. v4 writes the schema
into the instructions as plain text and removes the tool.

| | v3 | v4 | v4 | v4 |
|---|---|---|---|---|
| Started at | 20:31 | 20:22 | 20:44 | 20:58 |
| Accuracy | 100 % | 100 % | 100 % | 100 % |
| Model calls | 165 | 122 | 118 | 113 |
| Queries run | 62 | 55 | 54 | 58 |
| Input tokens | 498,517 | 348,541 | 338,607 | 322,946 |
| Cost | 0.059 EUR | 0.044 EUR | 0.043 EUR | 0.041 EUR |
| Latency, median / p90 / max | 5.2 / 8.4 / 12.1 s | 4.6 / 7.3 / 37.0 s | 4.3 / 5.4 / 11.2 s | 3.9 / 5.6 / 9.7 s |

- One model call fewer per question: 2.3 instead of 3.3 on average. Cost falls by about a quarter.
- Latency falls less than the calls do, about a second at the median, because the call that
  disappeared was the quickest one. The run of 20:22 was made before the model client was
  loaded apart, which is its maximum of 37 s.
- The model API was slower in the evening than in the afternoon (v3 took 4.3 and 4.9 s at
  the median at 15:45 and 5.2 s at 20:31), so only the runs made minutes apart compare.
- The last run uses the final code: the stricter SQL check and the corrected grader.
- The schema as text is 1,884 characters; as the JSON the tool returned it was 3,076.

## Against the targets

| Target | Result (Haiku 5.5, prompt v4) |
|---|---|
| Accuracy of at least 80 % | 100 % on the core set, three runs; 19 of 20 graded on the natural set, the miss being a right answer |
| 9 of 10 correct refusals | 10 of 10 |
| Answer in under 15 s | Median 3.9 to 4.3 s, p90 5.4 to 5.6 s, slowest question 11.2 and 9.7 s, once the model client is loaded |

## What this evaluation does not tell

- **Accuracy in real use.** The core questions say average or median and name the category.
  The natural set is closer to a user, but small, and its questions were kept only if they
  have one defensible answer. Open comparisons ("where is milk cheapest?") depend on the
  method and are read by hand.
- **Small differences.** Below 4 to 6 points, two runs of the same agent differ as much as
  two versions do. Latency also moves with the time of day.
- **The start-up time**, 10 to 17 s once per process, which a user of the demo would feel if
  the first question were asked before the client is loaded.
- **Safety against a hostile user** beyond what the review tried. The SQL check and the role
  stop everything it found; nobody has tried for longer than that.
- **Quality of the text and the chart choice.** Not graded.
- **The language check is a word count** over English, Spanish and French. It returns
  "unknown" for short texts, and an unknown answer is never counted as wrong.
- **The data itself.** Multipacks and thin coverage make some correct queries return
  misleading figures; the agent flags small samples, but nothing measures how well.
