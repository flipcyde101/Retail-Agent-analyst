"""Which chart comes out of which rows. The rows are shaped like real results of the agent."""

from datetime import date
from decimal import Decimal

from agent.models import SqlResult
from app.charts import build_chart, time_column, to_frame, value_columns


def result(columns: list[str], rows: list[list]) -> SqlResult:
    return SqlResult(columns=columns, rows=rows, row_count=len(rows))


CHAINS = result(
    ["chain", "n_obs", "median_eur_per_l", "mean_eur_per_l"],
    [
        ["E.Leclerc", 40, Decimal("1.41"), Decimal("2.00")],
        ["Auchan", 39, Decimal("1.55"), Decimal("1.83")],
        ["Carrefour", 67, Decimal("2.02"), Decimal("3.69")],
    ],
)
YEARS = result(["year", "prices"], [[2022, 761], [2023, 2103], [2024, 31866], [2025, 44736]])
MONTHS_BY_CHAIN = result(
    ["month", "chain", "avg_price"],
    [
        [date(2026, 1, 1), "Auchan", Decimal("2.10")],
        [date(2026, 1, 1), "Lidl", Decimal("1.90")],
        [date(2026, 2, 1), "Auchan", Decimal("2.20")],
        [date(2026, 2, 1), "Lidl", Decimal("1.95")],
    ],
)


def test_decimals_become_floats_so_they_can_be_plotted():
    frame = to_frame(CHAINS)
    assert frame["median_eur_per_l"].tolist() == [1.41, 1.55, 2.02]


def test_the_price_is_plotted_before_the_number_of_observations():
    assert value_columns(to_frame(CHAINS)) == ["median_eur_per_l", "mean_eur_per_l", "n_obs"]


def test_bars_compare_names_by_the_value_the_question_is_about():
    figure = build_chart("bar", CHAINS)
    bars = figure.data[0]
    assert bars.type == "bar"
    assert list(bars.x) == ["E.Leclerc", "Auchan", "Carrefour"]  # the order of the query
    assert list(bars.y) == [1.41, 1.55, 2.02]


def test_the_page_can_choose_another_value_to_plot():
    figure = build_chart("bar", CHAINS, value="n_obs")
    assert list(figure.data[0].y) == [40, 39, 67]


def test_long_names_get_horizontal_bars_with_the_first_row_on_top():
    products = result(
        ["product_name", "price"],
        [["Lait entier bio de montagne 1 l", 1.5], ["Beurre doux gastronomique 250 g", 2.5]],
    )
    bars = build_chart("bar", products).data[0]
    assert bars.orientation == "h"
    # Plotly draws the first category at the bottom, so the rows go in reversed.
    assert list(bars.y)[-1] == "Lait entier bio de montagne 1 l"


def test_a_line_runs_over_years_as_labels():
    figure = build_chart("line", YEARS)
    line = figure.data[0]
    assert line.type == "scatter"
    assert list(line.x) == [2022, 2023, 2024, 2025]
    assert list(line.y) == [761, 2103, 31866, 44736]
    assert figure.layout.xaxis.type == "category"


def test_the_axes_make_room_for_their_labels():
    # Found on screen: without automargin the years and the y figures were cut off.
    for figure in (build_chart("line", YEARS), build_chart("bar", CHAINS)):
        assert figure.layout.xaxis.automargin is True
        assert figure.layout.yaxis.automargin is True


def test_a_line_per_chain_when_the_rows_have_a_name_column():
    figure = build_chart("line", MONTHS_BY_CHAIN)
    assert sorted(trace.name for trace in figure.data) == ["Auchan", "Lidl"]
    assert time_column(to_frame(MONTHS_BY_CHAIN)) == "month"


def test_year_and_month_as_text_is_time_too():
    months = result(["month", "prices"], [["2026-01", 10], ["2026-02", 12]])
    assert time_column(to_frame(months)) == "month"
    assert build_chart("line", months).data[0].type == "scatter"


def test_a_line_asked_for_rows_with_no_time_falls_back_to_bars():
    assert build_chart("line", CHAINS).data[0].type == "bar"


def test_no_chart_when_the_agent_asked_for_none_or_the_data_cannot_carry_one():
    one_row = result(["chain", "avg_price"], [["Auchan", 2.5]])
    only_text = result(["chain", "city"], [["Auchan", "Paris"], ["Lidl", "Lyon"]])
    same_name_twice = result(["chain", "price"], [["Auchan", 1.0], ["Auchan", 2.0]])
    assert build_chart("none", CHAINS) is None
    assert build_chart("table", CHAINS) is None
    assert build_chart("bar", None) is None
    assert build_chart("bar", one_row) is None
    assert build_chart("bar", only_text) is None
    assert build_chart("bar", same_name_twice) is None


def test_two_columns_with_the_same_name_do_not_break_the_table():
    twice = result(["chain", "avg", "avg"], [["Auchan", 1.0, 2.0], ["Lidl", 3.0, 4.0]])
    assert list(to_frame(twice).columns) == ["chain", "avg", "avg_2"]
    assert build_chart("bar", twice) is not None


def test_empty_cells_do_not_stop_a_column_from_being_numeric():
    with_gap = result(["chain", "avg_price"], [["Auchan", 2.5], ["Lidl", None], ["U", 3.0]])
    assert value_columns(to_frame(with_gap)) == ["avg_price"]
    assert build_chart("bar", with_gap) is not None


# --- found in review, each with the rows that showed it -------------------------------


def test_a_name_that_only_contains_a_count_word_is_still_the_answer():
    promotions = result(
        ["chain", "prices", "pct_discounted"],
        [["Auchan", 5200, Decimal("12.4")], ["Lidl", 800, Decimal("3.1")]],
    )
    assert value_columns(to_frame(promotions)) == ["pct_discounted", "prices"]
    observed = result(
        ["chain", "n", "last_observed_price"], [["Auchan", 12, 0.89], ["Lidl", 30, 0.75]]
    )
    assert value_columns(to_frame(observed))[0] == "last_observed_price"


def test_a_count_called_prices_comes_after_a_real_price():
    frame = to_frame(
        result(["chain", "prices", "min_price"], [["A", 5200, 0.89], ["B", 800, 0.75]])
    )
    assert value_columns(frame) == ["min_price", "prices"]


def test_ids_are_never_plotted():
    stores = result(
        ["location_id", "city", "cheapest"], [[10412, "Paris", 0.89], [20977, "Lyon", 0.75]]
    )
    assert value_columns(to_frame(stores)) == ["cheapest"]
    assert list(build_chart("bar", stores).data[0].y) == [0.89, 0.75]


def test_a_year_that_arrives_as_a_decimal_is_not_labelled_with_a_decimal_point():
    years = result(["year", "prices"], [[Decimal("2022"), 761], [Decimal("2023"), 2103]])
    assert list(build_chart("bar", years).data[0].x) == ["2022", "2023"]
    assert to_frame(years)["year"].tolist() == [2022, 2023]


def test_two_points_for_the_same_date_and_line_give_no_chart():
    quarters = result(
        ["year", "quarter", "chain", "avg_price"],
        [[2024, 1, "Auchan", 2.1], [2024, 2, "Auchan", 2.9], [2025, 1, "Auchan", 2.3]]
        + [[2024, 1, "Lidl", 1.1], [2024, 2, "Lidl", 1.9], [2025, 1, "Lidl", 1.3]],
    )
    assert build_chart("line", quarters) is None


def test_a_figure_with_a_time_word_in_its_name_is_not_the_time_axis():
    days = result(
        ["chain", "days_observed", "avg_price"], [["Auchan", 300, 2.1], ["Lidl", 120, 2.2]]
    )
    months = result(
        ["chain", "price_last_month", "price_this_month"], [["Auchan", 2, 2.1], ["Lidl", 3, 2.2]]
    )
    assert time_column(to_frame(days)) is None
    assert time_column(to_frame(months)) is None
    assert build_chart("line", days).data[0].type == "bar"


def test_a_missing_name_is_shown_as_unknown():
    brands = result(["brand", "avg_price"], [["Lactel", 2.1], [None, 9.2], ["Candia", 2.0]])
    assert list(build_chart("bar", brands).data[0].x) == ["Lactel", "(unknown)", "Candia"]


def test_renamed_columns_do_not_collide_with_a_real_one():
    clash = result(["chain", "avg", "avg", "avg_2"], [["A", 1.0, 2.0, 3.0], ["B", 3.0, 4.0, 5.0]])
    assert list(to_frame(clash).columns) == ["chain", "avg", "avg_3", "avg_2"]
    assert build_chart("bar", clash) is not None
