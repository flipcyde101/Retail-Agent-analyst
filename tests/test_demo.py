"""The demo, run without a browser, a model or a database.

AppTest executes the Streamlit script in this process and exposes what it drew, so the
agent can be replaced by a function that returns a ready-made run.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from agent import agent as agent_module
from agent.agent import AgentAnswer, AgentRun, SqlAttempt
from agent.models import SqlResult
from app import ask_page
from app.evaluation_page import cost_by_version, latest_run, load_run, load_summary
from app.limits import MAX_QUESTION_CHARS, QUESTIONS_PER_SESSION, DailyBudget, problem_with

APP = str(Path(__file__).parent.parent / "app" / "streamlit_app.py")
SQL = "SELECT chain, avg(price) AS avg_price FROM prices GROUP BY chain LIMIT 5"
ROWS = SqlResult(
    columns=["chain", "avg_price"],
    rows=[["Auchan", Decimal("2.50")], ["Lidl", Decimal("1.90")]],
    row_count=2,
)


def fake_run(question: str, **answer) -> AgentRun:
    fields = {
        "answerable": True,
        "answer": "Lidl is cheaper: 1.90 EUR against 2.50 EUR at Auchan.",
        "sql": SQL,
        "chart": "bar",
        "confidence": "high",
    } | answer
    answerable = fields["answerable"]
    return AgentRun(
        question=question,
        answer=AgentAnswer(**fields),
        data=ROWS if answerable else None,
        attempts=[SqlAttempt(sql=SQL, ok=True, row_count=2)] if answerable else [],
        model="anthropic:claude-haiku-5-5",
        input_tokens=5000,
        output_tokens=500,
        requests=2,
        seconds=4.2,
    )


@pytest.fixture
def asked(monkeypatch) -> list[str]:
    """Replace the agent; return the list of questions that reach it."""
    questions: list[str] = []

    def fake_ask(question: str) -> AgentRun:
        questions.append(question)
        if "Nutella" in question:
            refusal = "The database has prices, not sales."
            return fake_run(question, answerable=False, answer=refusal, sql=None, chart="none")
        return fake_run(question)

    monkeypatch.setattr(agent_module, "ask", fake_ask)
    monkeypatch.setattr(agent_module, "warm_up", lambda: None)
    # The budget and the start-up are cached for the whole process: start each test clean.
    ask_page.daily_budget.clear()
    ask_page.start_up.clear()
    return questions


def ask(app: AppTest, question: str) -> AppTest:
    app.text_input[0].set_value(question)
    app.button[0].click()  # the form's "Ask" button
    return app.run()


def test_a_question_shows_the_answer_the_chart_the_rows_and_the_sql(asked):
    app = ask(AppTest.from_file(APP).run(), "Which chain is cheapest?")

    assert not app.exception
    assert asked == ["Which chain is cheapest?"]
    assert [t.value for t in app.text] == [
        "Which chain is cheapest?",
        "Lidl is cheaper: 1.90 EUR against 2.50 EUR at Auchan.",
    ]
    assert len(app.get("plotly_chart")) == 1
    assert app.dataframe[0].value["chain"].tolist() == ["Auchan", "Lidl"]
    assert app.code[0].value == SQL
    footer = " ".join(caption.value for caption in app.caption)
    assert "4.2 s" in footer and "2 model calls" in footer and "EUR" in footer


def test_an_example_button_asks_its_question(asked):
    app = AppTest.from_file(APP).run()
    example = next(b for b in app.button if b.label == ask_page.EXAMPLES[0])
    example.click()
    app.run()

    assert asked == [ask_page.EXAMPLES[0]]


def test_a_refusal_is_shown_as_a_warning_with_no_chart_and_no_sql(asked):
    app = ask(AppTest.from_file(APP).run(), "How many jars of Nutella were sold?")

    assert "cannot answer" in app.warning[0].value
    assert app.text[1].value == "The database has prices, not sales."
    assert len(app.get("plotly_chart")) == 0
    assert len(app.code) == 0


def test_answers_pile_up_newest_first(asked):
    app = ask(AppTest.from_file(APP).run(), "First question?")
    app = ask(app, "Second question?")

    assert [t.value for t in app.text][::2] == ["Second question?", "First question?"]


def test_an_empty_question_does_not_reach_the_agent(asked):
    app = ask(AppTest.from_file(APP).run(), "   ")

    assert asked == []
    assert "Write a question" in app.warning[0].value


def test_a_session_stops_at_its_limit(asked):
    app = AppTest.from_file(APP).run()
    for number in range(QUESTIONS_PER_SESSION + 1):
        app = ask(app, f"Question {number}?")

    assert len(asked) == QUESTIONS_PER_SESSION
    assert "per session" in app.warning[0].value


def test_the_day_stops_at_its_limit_for_everybody(asked, monkeypatch):
    # The real, cached budget with a small limit, and two visitors: what one spends the
    # other no longer has.
    monkeypatch.setattr(ask_page, "DAILY_LIMIT", 2)
    first = ask(AppTest.from_file(APP).run(), "First visitor, first question?")
    first = ask(first, "First visitor, second question?")
    second = ask(AppTest.from_file(APP).run(), "Second visitor?")

    assert len(asked) == 2
    assert "today" in second.warning[0].value
    ask_page.daily_budget.clear()


def test_a_question_the_agent_failed_on_goes_back_to_the_day_but_counts_for_the_session(
    asked, monkeypatch
):
    def broken(question: str) -> AgentRun:
        raise ConnectionError("api down")

    monkeypatch.setattr(agent_module, "ask", broken)
    app = AppTest.from_file(APP).run()
    for number in range(QUESTIONS_PER_SESSION + 2):
        app = ask(app, f"Question {number}?")

    assert ask_page.daily_budget().left(date.today()) == ask_page.DAILY_LIMIT
    assert "per session" in app.warning[0].value


def test_an_answer_is_kept_even_if_the_visitor_clicks_something_while_waiting(asked, monkeypatch):
    # Found in review: Streamlit stops the script at its next call when a widget was
    # touched, and the answer, already paid for, was stored only after that call.
    from streamlit.runtime.scriptrunner import RerunData, get_script_run_ctx

    def slow_ask(question: str) -> AgentRun:
        get_script_run_ctx().script_requests.request_rerun(RerunData())
        return fake_run(question)

    monkeypatch.setattr(agent_module, "ask", slow_ask)
    app = ask(AppTest.from_file(APP).run(), "Which chain is cheapest?")

    assert len(app.session_state["runs"]) == 1


def test_the_question_and_a_refusal_cannot_add_links_or_images_to_the_page(asked, monkeypatch):
    nasty = "Costs $2 and $3. ![x](http://evil.example/p.png) [click](http://evil.example)"

    def refusing(question: str) -> AgentRun:
        return fake_run(question, answerable=False, answer=nasty, sql=None, chart="none")

    monkeypatch.setattr(agent_module, "ask", refusing)
    app = ask(AppTest.from_file(APP).run(), "Is it **cheap** [here](http://evil.example)?")

    # Both are in st.text, the one element that shows text exactly as written.
    assert [t.value for t in app.text] == ["Is it **cheap** [here](http://evil.example)?", nasty]
    everything_else = " ".join(e.value for e in [*app.markdown, *app.warning, *app.caption])
    assert "evil.example" not in everything_else


def test_no_value_selector_when_there_is_no_chart_to_change(asked, monkeypatch):
    one_row = SqlResult(
        columns=["chain", "avg_price", "n_prices"], rows=[["A", 1.5, 10]], row_count=1
    )

    def single(question: str) -> AgentRun:
        return fake_run(question).model_copy(update={"data": one_row})

    monkeypatch.setattr(agent_module, "ask", single)
    app = ask(AppTest.from_file(APP).run(), "Which chain is cheapest?")

    assert len(app.selectbox) == 0 and len(app.get("plotly_chart")) == 0
    assert len(app.dataframe) == 1


def test_a_failure_of_the_model_api_is_a_sentence_not_a_traceback(asked, monkeypatch):
    def broken(question: str) -> AgentRun:
        raise ConnectionError("api down, key sk-secret")

    monkeypatch.setattr(agent_module, "ask", broken)
    app = ask(AppTest.from_file(APP).run(), "Which chain is cheapest?")

    assert not app.exception
    assert "could not be reached" in app.error[0].value
    assert "sk-secret" not in app.error[0].value


def test_the_evaluation_page_shows_the_recorded_runs():
    def page():
        from app import evaluation_page

        evaluation_page.render()

    # AppTest cannot follow st.navigation to a page that is a function, so it runs it alone.
    app = AppTest.from_function(page).run()

    assert not app.exception
    assert app.title[0].value == "Evaluation"
    assert app.metric[0].label == "Accuracy"
    assert len(app.dataframe) == 2


# --- the pieces without Streamlit ----------------------------------------------------


def test_the_daily_budget_counts_down_and_starts_again_the_next_day():
    budget = DailyBudget(limit=2)
    monday, tuesday = date(2026, 11, 16), date(2026, 11, 17)

    assert [budget.take(monday) for _ in range(3)] == [True, True, False]
    assert budget.left(monday) == 0
    assert budget.left(tuesday) == 2
    assert budget.take(tuesday)


def test_questions_that_are_empty_or_too_long_are_refused():
    assert problem_with("Which chain is cheapest?") is None
    assert "Write a question" in problem_with("")
    assert "too long" in problem_with("x" * (MAX_QUESTION_CHARS + 1))


def test_the_summary_and_every_run_file_can_be_read():
    summary = load_summary()
    # A run that is on record, not "the latest": the file grows with every evaluation.
    recorded = summary[summary["run_id"] == "20261113-205807_core_claude-haiku-5-5_v4"].iloc[0]
    assert (recorded["accuracy_pct"], recorded["refusals"]) == (100.0, "10 of 10")
    assert latest_run(summary, "core", "v4") is not None
    assert latest_run(summary, "core", "v99") is None
    for run_id in summary["run_id"]:
        questions = load_run(run_id)
        assert set(questions["result"]) <= {"right", "wrong", "read by hand"}
        assert {"id", "category", "seconds", "queries", "answer", "sql"} <= set(questions.columns)


def test_the_figures_of_the_current_version_are_those_of_the_demo_loop():
    summary = pd.DataFrame(
        {
            "run_id": ["a", "b"],
            "question_set": ["core", "core"],
            "model": ["claude-haiku-5-5", "claude-haiku-5-5"],
            "prompt_version": ["v5", "v5"],
            "engine": ["pydantic-ai", "langgraph"],
            "cost_per_question_eur": [0.001, 0.009],
        }
    )
    # The LangGraph run is newer, but the demo does not run it.
    assert latest_run(summary, "core", "v5")["run_id"] == "a"
    costs = cost_by_version(summary, "claude-haiku-5-5")
    assert costs["eur_per_100"].tolist() == [0.1]
    assert costs["runs"].tolist() == [1]


def test_a_summary_without_the_loop_column_is_read_as_the_demo_loop(tmp_path):
    (tmp_path / "summary.csv").write_text(
        "run_id,question_set,model,prompt_version,correct_refusals,refusal_questions\n"
        "a,core,anthropic:claude-haiku-5-5,v5,10,10\n",
        encoding="utf-8",
    )
    summary = load_summary(tmp_path)

    assert summary["engine"].tolist() == ["pydantic-ai"]
    assert latest_run(summary, "core", "v5") is not None


def test_the_evaluation_page_says_so_when_there_are_no_results(tmp_path, monkeypatch):
    def page():
        from app import evaluation_page

        evaluation_page.render()

    import app.evaluation_page as evaluation_page

    monkeypatch.setattr(evaluation_page.load_summary, "__defaults__", (tmp_path,))
    app = AppTest.from_function(page).run()

    assert not app.exception
    assert "No evaluation results" in app.info[0].value
