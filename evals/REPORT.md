# Evaluation report

What failed, what fixed it and what it cost. Every figure comes from the files in
`evals/results/`; `summary.csv` has one line per run. All runs are from 2026-11-13 and ask the
questions one at a time, so the latency is what a single user would see.

## Summary

| | Haiku 5.5, prompt v1 | Haiku 5.5, prompt v3 | Sonnet 5.5, prompt v3 |
|---|---|---|---|
| Accuracy, core set (50 questions) | 98 % (49) | 100 % (50), twice | 98 % (49) |
| Correct refusals | 10 of 10 | 10 of 10 | 9 of 10 |
| Answers in the wrong language | 13 | 0 | 0 |
| SQL valid at first try | 86.7 % | 95.6 % and 97.8 % | 100 % |
| Failed queries | 6 | 2 and 1 | 1 |
| Cost of the run | 0.055 EUR | 0.056 and 0.057 EUR | 1.695 EUR |
| Latency, median / p90 / max | 4.7 / 7.9 / 8.9 s | 4.3 / 7.0 / 17.5 s and 4.9 / 7.4 / 15.3 s | 6.8 / 12.2 / 14.6 s |

The three columns use the same 50 questions as they are today. Two earlier runs of v1, on
the questions before two of them were corrected, gave 90 % and 98 %.

On the natural set (22 questions worded like a user's), prompt v3 with Haiku gets 20 of the
20 graded automatically, 4 of 4 refusals and no answer in the wrong language; the other two
are read by hand.

Read the accuracy with its limits: 50 questions make each one worth 2 points, and the same
agent on the same questions has given 90 % and 98 %. The difference between 98 % and 100 %
is one question. What the three versions show beyond noise is the language (13 to 0) and the
failed queries (6 to 1 or 2).

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
- After: 0 in three runs of v2 and v3 on the core set.

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

Each grader change has a test built from the case that revealed it. In the v3 runs, every
pass that rests on the answer text and both manual answers were read by hand: all hold.

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
| Latency, median / p90 | 4.7 / 7.9 s | 4.6 / 8.7 s | 4.3 / 7.0 s | 4.9 / 7.4 s |

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
| Latency, median / p90 / max | 4.9 / 7.4 / 15.3 s | 6.8 / 12.2 / 14.6 s |

- Sonnet writes cleaner SQL and explores more: two and a half times as many queries.
- Its one failure is that thoroughness. On R06 (milk prices in 2005) it ran 11 queries
  looking for the data and hit the limit of 12 model calls per question, so the run ended
  without the refusal. Haiku refuses after one or two queries.
- For this task the larger model costs 30 times more per question, is slower, and is not
  more accurate. Haiku 5.5 stays the model of the demo. One run of Sonnet is not enough to
  say it is worse; it is enough to say it is not worth 30 times the price here.

## Against the targets

| Target | Result (Haiku 5.5, prompt v3) |
|---|---|
| Accuracy of at least 80 % | 100 % on the core set, twice; 20 of 20 graded on the natural set |
| 9 of 10 correct refusals | 10 of 10 |
| Answer in under 15 s | Median 4.3-4.9 s, p90 7.0-7.4 s; one question per run took longer (17.5 s and 15.3 s) |

## What this evaluation does not tell

- **Accuracy in real use.** The core questions say average or median and name the category.
  The natural set is closer to a user, but small, and its questions were kept only if they
  have one defensible answer. Open comparisons ("where is milk cheapest?") depend on the
  method and are read by hand.
- **Small differences.** Below 4 to 6 points, two runs of the same agent differ as much as
  two versions do.
- **Quality of the text and the chart choice.** Not graded.
- **The language check is a word count** over English, Spanish and French. It returns
  "unknown" for short texts, and an unknown answer is never counted as wrong.
- **The data itself.** Multipacks and thin coverage make some correct queries return
  misleading figures; the agent flags small samples, but nothing measures how well.
