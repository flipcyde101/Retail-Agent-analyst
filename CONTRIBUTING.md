# Contributing

Thanks for looking. Issues, questions and pull requests are welcome. Issues labelled
[good first issue](https://github.com/pitelet222/Retail-Agent-analyst/labels/good%20first%20issue),
when there are any, are small and self-contained.

## Set up

You need [uv](https://docs.astral.sh/uv/), Docker and a bash shell (on Windows, WSL or Git
Bash).

```bash
./setup.sh --sample   # PostgreSQL with a dozen made-up rows: enough for the tests
./setup.sh            # or the real data (downloads about 100 MB)
```

No API key is needed for the tests: every test replaces the model with a scripted one.

## Before you open a pull request

```bash
uv run ruff check .
uv run python -m pytest
```

Continuous integration runs the same lint and tests against a real PostgreSQL on every
push and pull request.

## Ground rules

- **Never commit secrets.** API keys and passwords go in `.env`, which is gitignored.
- **Measure changes to the agent.** A change to the prompt is a new version in
  `agent/prompts.py` (the current one is `PROMPT_VERSION` in `agent/agent.py`), not an edit
  of an old one, so runs stay comparable. Run `uv run python -m evals.run --prompt vN` and
  put the figures in the pull request. A full run costs about 0.04 EUR with Claude Haiku.
- **Open data only.** The data is Open Prices by Open Food Facts, under the
  [ODbL](https://opendatacommons.org/licenses/odbl/1-0/). Do not add scraped data.
- **Keep the agent read-only.** SQL goes through `agent/sql_guard.py` and runs as a
  read-only role. Changes there need a test that shows what is rejected.
- **Small pull requests.** One change each, with a test when it changes behaviour.

## Reporting a bug

Use the [bug report form](https://github.com/pitelet222/Retail-Agent-analyst/issues/new/choose).
For a wrong answer, include the question, what the agent answered and the SQL it showed.

## License

By contributing you agree that your contribution is released under the [MIT License](LICENSE).
